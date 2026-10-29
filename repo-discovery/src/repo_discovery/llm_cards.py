"""V1: repository cards written by an LLM, and a closed-book control written from names alone.

Two modes share one cache and one client:

- ``packet``: the model reads a digest of the snapshot's evidence packet
  (:func:`packet_digest`): the extractive card's title line (``owner/name`` and package
  names), manifest descriptions and keywords, the README with markup stripped, docs prose
  when the README is short, docs page titles, API contract names, dependencies and the
  layout, each capped. Over an anonymized corpus (``scripts/anonymize_corpus.py``) the
  same digest carries IDs instead of names.
- ``names``: the model gets ``owner/name`` and nothing else, answers from memory, and
  opens with ``Recognised: yes`` or ``Recognised: no``. That measures what the model
  already knows about a repository.

Every card is generated once at temperature 0 by a pinned model snapshot and kept in a
SQLite file under the cache root, keyed by model, prompt version, mode, repository and
the SHA-256 of the exact text sent, so a rerun never calls the API. Each row keeps the
call's token counts and dollars, which together are the spend ledger: a build stops
submitting before the ledger plus the worst case of the calls in flight would pass its total
cap, or before its own spend would pass its batch stop.

Temperature 0 doesn't make the model deterministic. A replicate (``--replicate N``) repeats
a build under keys of its own, in the same file and ledger, so two independent card sets can
be compared; the rankers read replicate N when ``REPO_DISCOVERY_LLM_CARDS_REPLICATE=N``.

``python -m repo_discovery.llm_cards build`` fills the cache for the snapshots of a set of
case universes; ``spend`` totals the ledger by build. The rankers that read the cards
(``rankers/generated.py``) never call the API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from multiprocessing import Pool
from pathlib import Path, PurePosixPath

from repo_discovery.cards import (
    card_title,
    contract_names,
    headings,
    layout_summary,
    prose_paragraphs,
    strip_markup,
)
from repo_discovery.cases import SPLITS, read_cases, repo_key, select_cases
from repo_discovery.contract import Universe, resolve_universe
from repo_discovery.evidence import Evidence, read_evidence

MODEL = "gpt-5.4-nano-2026-03-17"
# USD per million tokens, standard tier: input, cached input, output.
PRICES: dict[str, tuple[float, float, float]] = {MODEL: (0.20, 0.02, 1.25)}
PROMPT_VERSION = "v1"
MODES = ("packet", "names")
CARD_WORDS = 250
MAX_OUTPUT_TOKENS = 700
DEFAULT_CAP_USD = 50.0
API_URL = "https://api.openai.com/v1/chat/completions"
KEY_ENV = "OPENAI_API_KEY"
REPLICATE_ENV = "REPO_DISCOVERY_LLM_CARDS_REPLICATE"

README_CHARS = 6_000
FIELD_CHARS = 1_200
DOC_TITLES = 40
TOP_LEVEL_ENTRIES = 40

PROMPTS: dict[str, str] = {
    "packet": f"""\
You write catalogue cards for software repositories. A search system matches feature \
requests and bug reports against the cards to find every repository a change would touch, \
so a card must say plainly what the repository is and does, using the specific names a \
developer would search for.

You get a digest of one repository: its name, package metadata, README, docs titles, API \
names, dependencies and file layout. Write one card of at most {CARD_WORDS} words of plain \
prose, with no headings, lists or Markdown. Cover what the digest supports:
- what kind of thing it is (library, service, CLI, app, plugin, SDK, configuration, \
documentation...) and its purpose;
- its main features and responsibilities;
- the interfaces it provides or consumes: APIs, endpoints, commands, events, protocols, \
file formats, data models;
- the languages, frameworks, platforms and services it is built on or integrates with;
- its main components or modules.
Use only the digest and don't add facts it doesn't support. Leave out installation steps, \
badges, licences and contributors. If the digest says little, write a shorter card.""",
    "names": f"""\
You write catalogue cards for software repositories. A search system matches feature \
requests and bug reports against the cards to find every repository a change would touch.

You get only a repository's GitHub owner/name. Answer from what you already know about \
that repository. Start with one line, "Recognised: yes" if you know this specific \
repository or "Recognised: no" if you don't. Then write one card of at most {CARD_WORDS} \
words of plain prose, with no headings, lists or Markdown, saying what the repository is \
and does: its purpose, main features, the interfaces it provides or consumes, the \
technologies it is built on or integrates with, and its main components. If you don't \
recognise it, write at most two sentences on what the name suggests.""",
}

_BLANK_RUNS = re.compile(r"\n\s*\n(\s*\n)+")
_SPACE_RUNS = re.compile(r"[ \t]+")
_RECOGNISED = re.compile(r"^\W*recogni[sz]ed\W*(yes|no)\b\W*", re.I)


def _clean(text: str) -> str:
    lines = (_SPACE_RUNS.sub(" ", line).strip() for line in strip_markup(text).splitlines())
    return _BLANK_RUNS.sub("\n\n", "\n".join(lines)).strip()


def _capped(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[: cut if cut > limit // 2 else limit].rstrip() + " ..."


def _listed(items: Iterable[str], limit: int = FIELD_CHARS) -> str:
    return _capped(", ".join(dict.fromkeys(i for i in items if i)), limit)


def packet_digest(evidence: Evidence) -> str:
    """The text the model reads for one snapshot: labelled, capped fields of its packet."""
    lines = [f"Repository: {card_title(evidence)}"]
    if evidence.descriptions:
        lines.append("Descriptions: " + _capped(" | ".join(evidence.descriptions), FIELD_CHARS))
    if evidence.keywords:
        lines.append("Keywords: " + _listed(evidence.keywords))
    readme = _clean(evidence.readme)
    if readme:
        lines += ["README:", _capped(readme, README_CHARS)]
    room = README_CHARS - len(readme)
    if room > FIELD_CHARS // 2 and evidence.docs:
        prose = " ".join(p for doc in evidence.docs for p in prose_paragraphs(doc.text))
        if prose:
            lines += ["Docs excerpt:", _capped(prose, room)]
    titles = [_doc_title(doc.path, doc.text) for doc in evidence.docs[:DOC_TITLES]]
    if titles:
        lines.append("Docs pages: " + _capped("; ".join(titles), FIELD_CHARS))
    if names := contract_names(evidence):
        lines.append("API names: " + _listed(names))
    if evidence.dependencies:
        lines.append("Dependencies: " + _listed(evidence.dependencies))
    if layout := layout_summary(evidence.paths):
        lines.append("Layout: " + layout)
    top = dict.fromkeys(PurePosixPath(p).parts[0] for p in evidence.paths if p)
    if top:
        lines.append("Top level: " + _listed(list(top)[:TOP_LEVEL_ENTRIES]))
    return "\n".join(lines)


def _doc_title(path: str, text: str) -> str:
    heading = next(iter(headings(text)), "")
    return f"{path} ({heading})" if heading else path


def names_digest(repo: str) -> str:
    return f"Repository: {repo}"


def parse_names_card(text: str) -> tuple[bool | None, str]:
    """``(recognised, card)`` from a names-mode answer; ``None`` when the line is missing."""
    match = _RECOGNISED.match(text.strip())
    if not match:
        return None, text.strip()
    return match.group(1).lower() == "yes", text.strip()[match.end() :].strip()


@dataclass(frozen=True)
class CardRequest:
    mode: str
    repo: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def key(self, model: str = MODEL, replicate: int = 0) -> str:
        key = f"{model}/{PROMPT_VERSION}/{self.mode}/{repo_key(self.repo)}/{self.sha256[:24]}"
        return f"{key}/r{replicate}" if replicate else key


def replicate_from_env() -> int:
    value = os.environ.get(REPLICATE_ENV, "").strip() or "0"
    if not value.isdigit():
        raise ValueError(f"{REPLICATE_ENV} must be a whole number, got {value!r}")
    return int(value)


def request_for(mode: str, evidence: Evidence) -> CardRequest:
    if mode == "packet":
        return CardRequest(mode, evidence.repo, packet_digest(evidence))
    if mode == "names":
        return CardRequest(mode, evidence.repo, names_digest(evidence.repo))
    raise ValueError(f"mode must be one of {MODES}, got {mode!r}")


def indexed_text(mode: str, evidence: Evidence, card: str) -> str:
    """What the rankers index: the title line, then the card. Names mode keeps the name only."""
    if mode == "names":
        return f"{evidence.repo}\n{parse_names_card(card)[1]}"
    return f"{card_title(evidence)}\n{card.strip()}"


# The API.


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0

    def usd(self, model: str = MODEL) -> float:
        fresh, cached, output = PRICES[model]
        uncached = self.prompt_tokens - self.cached_tokens
        dollars = uncached * fresh + self.cached_tokens * cached + self.completion_tokens * output
        return dollars / 1e6


@dataclass(frozen=True)
class Completion:
    text: str
    finish_reason: str
    model: str
    usage: Usage


class ApiError(RuntimeError):
    def __init__(self, status: int | None, message: str, fatal: bool = False) -> None:
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.fatal = fatal


Opener = Callable[..., object]


class OpenAIChat:
    """Chat Completions at temperature 0 with reasoning off; retries rate limits and 5xx.

    ``failed_attempts`` counts every attempt that returned no card, by kind. An HTTP error
    carries no usage, but a timed-out call may still have been billed.
    """

    def __init__(
        self,
        model: str = MODEL,
        key: str | None = None,
        timeout: float = 120.0,
        retries: int = 6,
        opener: Opener = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if model not in PRICES:
            raise ValueError(f"no price for {model}; add it to PRICES")
        self.model = model
        self._key = key or os.environ.get(KEY_ENV) or ""
        if not self._key:
            raise ApiError(None, f"{KEY_ENV} is not set", fatal=True)
        self.timeout = timeout
        self.retries = retries
        self._open = opener
        self._sleep = sleep
        self.failed_attempts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def _failed(self, kind: str) -> None:
        with self._lock:
            self.failed_attempts[kind] += 1

    def body(self, system: str, user: str) -> dict:
        return {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "reasoning_effort": "none",
            "max_completion_tokens": MAX_OUTPUT_TOKENS,
        }

    def complete(self, system: str, user: str) -> Completion:
        data = json.dumps(self.body(system, user)).encode("utf-8")
        status: int | None = None
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(min(60.0, 2.0**attempt) * (0.5 + random.random()))
            request = urllib.request.Request(
                API_URL,
                data=data,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with self._open(request, timeout=self.timeout) as response:
                    return self._parse(json.load(response))
            except urllib.error.HTTPError as error:
                status = error.code
                self._failed(f"http {status}")
                code, message = _error_fields(error)
                if status in (401, 403) or code == "insufficient_quota":
                    raise ApiError(status, code or "not authorized", fatal=True) from None
                if status != 429 and status < 500:
                    raise ApiError(status, message) from None
            except (TimeoutError, ConnectionError, ValueError) as error:
                status = None
                self._failed(type(error).__name__)
            except urllib.error.URLError as error:
                status = None
                reason = error.reason
                self._failed("TimeoutError" if isinstance(reason, TimeoutError) else "URLError")
        raise ApiError(status, f"gave up after {self.retries + 1} attempts")

    def _parse(self, payload: dict) -> Completion:
        choice = payload["choices"][0]
        message = choice.get("message") or {}
        usage = payload.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        returned = payload.get("model", "")
        if returned != self.model:
            self._failed("wrong model")
            raise ApiError(None, f"asked for {self.model}, got {returned}", fatal=True)
        return Completion(
            text=(message.get("content") or message.get("refusal") or "").strip(),
            finish_reason=choice.get("finish_reason") or "",
            model=returned,
            usage=Usage(
                int(usage.get("prompt_tokens", 0)),
                int(details.get("cached_tokens", 0) or 0),
                int(usage.get("completion_tokens", 0)),
            ),
        )


def _error_fields(error: urllib.error.HTTPError) -> tuple[str, str]:
    """The error's code and a short message; never the request or its headers."""
    try:
        detail = json.loads(error.read().decode("utf-8", errors="replace")).get("error") or {}
    except (ValueError, AttributeError, OSError):
        return "", ""
    return str(detail.get("code") or ""), str(detail.get("message") or "")[:300]


# The cache and ledger.


@dataclass(frozen=True)
class StoredCard:
    key: str
    repo: str
    mode: str
    text: str
    finish_reason: str
    usage: Usage
    usd: float
    build: str


LEDGER_COLUMNS = (
    "build",
    "model",
    "prompt",
    "mode",
    "cards",
    "prompt_tokens",
    "cached_tokens",
    "completion_tokens",
    "usd",
    "truncated",
    "first",
    "last",
)


class CardStore:
    """Generated cards in SQLite, one row per call that returned a card, with its cost."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cards (key TEXT PRIMARY KEY, model TEXT, prompt TEXT, "
            "mode TEXT, repo TEXT, input_sha256 TEXT, text TEXT, finish_reason TEXT, "
            "prompt_tokens INTEGER, cached_tokens INTEGER, completion_tokens INTEGER, "
            "usd REAL, build TEXT, seconds REAL, created TEXT)"
        )

    def get_many(self, keys: Iterable[str]) -> dict[str, StoredCard]:
        keys, found = list(keys), {}
        for start in range(0, len(keys), 500):
            chunk = keys[start : start + 500]
            rows = self._db.execute(
                "SELECT key, repo, mode, text, finish_reason, prompt_tokens, cached_tokens, "
                "completion_tokens, usd, build FROM cards "
                f"WHERE key IN ({', '.join('?' * len(chunk))})",
                chunk,
            )
            for key, repo, mode, text, finish, prompt, cached, completion, usd, build in rows:
                usage = Usage(prompt, cached, completion)
                found[key] = StoredCard(key, repo, mode, text, finish, usage, usd, build)
        return found

    def get(self, key: str) -> StoredCard | None:
        return self.get_many([key]).get(key)

    def put(
        self,
        request: CardRequest,
        completion: Completion,
        build: str,
        seconds: float,
        replicate: int = 0,
    ) -> float:
        usd = completion.usage.usd(completion.model)
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO cards VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                (
                    request.key(completion.model, replicate),
                    completion.model,
                    PROMPT_VERSION,
                    request.mode,
                    request.repo,
                    request.sha256,
                    completion.text,
                    completion.finish_reason,
                    completion.usage.prompt_tokens,
                    completion.usage.cached_tokens,
                    completion.usage.completion_tokens,
                    usd,
                    build,
                    round(seconds, 3),
                ),
            )
        return usd

    def spent(self) -> float:
        return float(self._db.execute("SELECT COALESCE(SUM(usd), 0) FROM cards").fetchone()[0])

    def by_build(self) -> list[dict]:
        rows = self._db.execute(
            "SELECT build, model, prompt, mode, COUNT(*), SUM(prompt_tokens), "
            "SUM(cached_tokens), SUM(completion_tokens), SUM(usd), "
            "SUM(finish_reason = 'length'), MIN(created), MAX(created) "
            "FROM cards GROUP BY build, model, prompt, mode ORDER BY MIN(created)"
        )
        return [dict(zip(LEDGER_COLUMNS, row, strict=True)) for row in rows]


def store_path(cache_dir: Path | None = None) -> Path:
    from repo_discovery.wideswe import cache_root

    return (cache_dir or cache_root()) / "llm-cards" / "cards.sqlite"


# Builds.


@dataclass
class BuildStats:
    build: str
    mode: str
    model: str = MODEL
    prompt: str = PROMPT_VERSION
    replicate: int = 0
    requests: int = 0
    cached: int = 0
    generated: int = 0
    failed: int = 0
    truncated: int = 0
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0
    spent_before: float = 0.0
    spent_after: float = 0.0
    cap_usd: float = DEFAULT_CAP_USD
    batch_usd: float | None = None
    stopped_at_cap: bool = False
    stopped_by: str = ""
    seconds: float = 0.0
    failed_attempts: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


def worst_case_usd(request: CardRequest, model: str = MODEL) -> float:
    """An upper bound on one call's cost: two characters per input token, full output."""
    fresh, _, output = PRICES[model]
    prompt_tokens = (len(PROMPTS[request.mode]) + len(request.text)) / 2 + 50
    return (prompt_tokens * fresh + MAX_OUTPUT_TOKENS * output) / 1e6


def build(
    requests: Sequence[CardRequest],
    store: CardStore,
    client: OpenAIChat,
    name: str,
    cap_usd: float = DEFAULT_CAP_USD,
    workers: int = 32,
    log: Callable[[str], None] | None = None,
    replicate: int = 0,
    batch_usd: float | None = None,
) -> BuildStats:
    """Generate the cards ``store`` lacks, stopping before the ledger could pass ``cap_usd``
    or this build's own spend could pass ``batch_usd``."""
    started = time.perf_counter()

    def key(request: CardRequest) -> str:
        return request.key(client.model, replicate)

    unique = list({key(r): r for r in requests}.values())
    modes = sorted({r.mode for r in unique})
    stats = BuildStats(
        name,
        ",".join(modes),
        client.model,
        replicate=replicate,
        cap_usd=cap_usd,
        batch_usd=batch_usd,
        requests=len(unique),
    )
    have = store.get_many(key(r) for r in unique)
    todo = [r for r in unique if key(r) not in have]
    stats.cached = len(unique) - len(todo)
    spent = stats.spent_before = store.spent()
    pending: dict[Future, tuple[CardRequest, float, float]] = {}
    queue = iter(todo)
    fatal: ApiError | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            while fatal is None and not stats.stopped_at_cap and len(pending) < 2 * workers:
                request = next(queue, None)
                if request is None:
                    break
                reserve = worst_case_usd(request, client.model)
                in_flight = sum(r for _, r, _ in pending.values()) + reserve
                if spent + in_flight > cap_usd:
                    stats.stopped_at_cap, stats.stopped_by = True, "total cap"
                    break
                if batch_usd is not None and stats.usd + in_flight > batch_usd:
                    stats.stopped_at_cap, stats.stopped_by = True, "batch stop"
                    break
                future = pool.submit(client.complete, PROMPTS[request.mode], request.text)
                pending[future] = (request, reserve, time.perf_counter())
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                request, _, submitted = pending.pop(future)
                try:
                    completion = future.result()
                except ApiError as error:
                    stats.failed += 1
                    stats.failures.append(f"{request.repo}: {error}")
                    if error.fatal and fatal is None:
                        fatal = error
                    continue
                seconds = time.perf_counter() - submitted
                usd = store.put(request, completion, name, seconds, replicate)
                spent += usd
                stats.generated += 1
                stats.truncated += completion.finish_reason == "length"
                stats.prompt_tokens += completion.usage.prompt_tokens
                stats.cached_tokens += completion.usage.cached_tokens
                stats.completion_tokens += completion.usage.completion_tokens
                stats.usd += usd
                if log and stats.generated % 500 == 0:
                    log(
                        f"[{time.perf_counter() - started:6.0f}s] {stats.generated}/{len(todo)} "
                        f"generated, ${stats.usd:.2f} this build, ${spent:.2f} in total"
                    )
    stats.spent_after = store.spent()
    stats.seconds = round(time.perf_counter() - started, 1)
    stats.failed_attempts = dict(client.failed_attempts)
    if fatal is not None:
        raise fatal
    return stats


def _packet_request(job: tuple[str, str, str]) -> CardRequest:
    mode, repo, packet = job
    return request_for(mode, read_evidence(repo, Path(packet)))


def universe_requests(
    cases_path: Path,
    split: str | None,
    universes: Path,
    mode: str,
    workers: int = os.cpu_count() or 1,
) -> list[CardRequest]:
    """One request per distinct snapshot in the selected cases' universes."""
    snapshots: dict[tuple[str, str], tuple[str, str, str]] = {}
    for case in select_cases(read_cases(cases_path), split=split):
        for candidate in Universe.load(resolve_universe(universes, case.case_id)).view().candidates:
            key = (repo_key(candidate.repo), candidate.evidence_dir.name)
            snapshots.setdefault(key, (mode, candidate.repo, str(candidate.evidence_dir)))
    jobs = [snapshots[k] for k in sorted(snapshots)]
    if mode == "names":
        return [CardRequest(mode, repo, names_digest(repo)) for _, repo, _ in jobs]
    with Pool(workers) as pool:
        return pool.map(_packet_request, jobs, chunksize=64)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m repo_discovery.llm_cards")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("build", help="generate the cards of every case universe's snapshots")
    cmd.add_argument("--cases", type=Path, required=True)
    cmd.add_argument("--split", choices=[*SPLITS, "all"], default="dev")
    cmd.add_argument("--universes", type=Path, required=True)
    cmd.add_argument("--mode", choices=MODES, required=True)
    cmd.add_argument("--build", required=True, help="a name for this build in the ledger")
    cmd.add_argument(
        "--cap-usd",
        type=float,
        default=DEFAULT_CAP_USD,
        help="stop before the whole ledger would pass this",
    )
    cmd.add_argument("--batch-usd", type=float, help="stop before this build would spend more")
    cmd.add_argument(
        "--replicate",
        type=int,
        default=0,
        help="0 for the cards rankers read by default; N > 0 repeats the build under its own keys",
    )
    cmd.add_argument("--workers", type=int, default=32)
    cmd.add_argument("--log", type=Path, help="append the build's stats here as JSON")
    cmd.add_argument("--dry-run", action="store_true", help="count and estimate, call nothing")
    commands.add_parser("spend", help="tokens and dollars in the ledger, by build")
    args = parser.parse_args(argv)

    store = CardStore(store_path())
    if args.command == "spend":
        for row in store.by_build():
            print(json.dumps(row))
        print(json.dumps({"total_usd": round(store.spent(), 4)}))
        return 0

    def log(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    split = None if args.split == "all" else args.split
    requests = universe_requests(args.cases, split, args.universes, args.mode)
    if args.replicate < 0:
        parser.error("--replicate must be 0 or more")
    if args.dry_run:
        have = store.get_many(r.key(MODEL, args.replicate) for r in requests)
        todo = {
            r.key(MODEL, args.replicate): r
            for r in requests
            if r.key(MODEL, args.replicate) not in have
        }
        chars = sum(len(PROMPTS[r.mode]) + len(r.text) for r in todo.values())
        print(
            json.dumps(
                {
                    "snapshots": len(requests),
                    "distinct": len({r.key() for r in requests}),
                    "to_generate": len(todo),
                    "input_chars": chars,
                    "worst_case_usd": round(sum(worst_case_usd(r) for r in todo.values()), 2),
                    "spent_usd": round(store.spent(), 4),
                }
            )
        )
        return 0
    try:
        stats = build(
            requests,
            store,
            OpenAIChat(),
            args.build,
            args.cap_usd,
            args.workers,
            log,
            replicate=args.replicate,
            batch_usd=args.batch_usd,
        )
    except ApiError as error:
        log(f"stopped: {error}")
        return 2
    record = {**asdict(stats), "universes": str(args.universes), "snapshots": len(requests)}
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        with args.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k != "failures"}))
    if stats.failures:
        log(f"{stats.failed} failed, e.g. {stats.failures[:3]}")
    if stats.stopped_by == "batch stop":
        log(f"stopped at the ${args.batch_usd:.2f} batch stop with ${stats.usd:.2f} spent by it")
    elif stats.stopped_at_cap:
        log(f"stopped at the ${args.cap_usd:.2f} cap with ${stats.spent_after:.2f} spent")
    if stats.stopped_at_cap:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
