from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from repo_discovery import harness, llm_cards
from repo_discovery.cards import build_card, word_count
from repo_discovery.cases import Case, Query
from repo_discovery.cli import main as cli
from repo_discovery.contract import Universe
from repo_discovery.evidence import read_evidence
from repo_discovery.llm_cards import (
    KEY_ENV,
    MODEL,
    PROMPTS,
    ApiError,
    CardRequest,
    CardStore,
    Completion,
    OpenAIChat,
    Usage,
    build,
    indexed_text,
    packet_digest,
    parse_names_card,
    request_for,
)
from repo_discovery.rankers import make_ranker
from repo_discovery.rankers.bm25 import BM25Ranker, analyze
from repo_discovery.rankers.dense import reciprocal_rank_fusion
from repo_discovery.rankers.generated import (
    GeneratedBM25Ranker,
    GeneratedCards,
    GeneratedDenseRanker,
    GeneratedHybridRanker,
    GeneratedNamesBM25Ranker,
    GeneratedNamesDenseRanker,
    MatchedBM25Ranker,
    MatchedDenseRanker,
    MissingCardError,
)

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"
CUTOFF = "2026-03-01T00:00:00+00:00"
FAKE_KEY = "sk-test-not-a-real-key"


class HashingEncoder:
    """Bag of hashed word stems, so tests need no model: cosine tracks shared vocabulary."""

    id = "test/hashing@0"

    def __init__(self, dims: int = 512) -> None:
        self.dims = dims
        self.encoded: list[str] = []

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dims, dtype=np.float32)
        for token in analyze(text):
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dims] += 1.0
        return vector / max(float(np.linalg.norm(vector)), 1e-9)

    def documents(self, texts: Sequence[str]) -> np.ndarray:
        self.encoded.extend(texts)
        return np.stack([self._vector(t) for t in texts])

    def query(self, text: str) -> np.ndarray:
        return self._vector(text)


def evidence(repo: str):
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    candidate = next(c for c in view.candidates if c.repo == repo)
    return read_evidence(candidate.repo, candidate.evidence_dir)


def requests(mode: str) -> list[CardRequest]:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    return [request_for(mode, read_evidence(c.repo, c.evidence_dir)) for c in view.candidates]


class FakeClient:
    """Writes a card from the digest's own words, as a model told to stay on the digest would."""

    model = MODEL

    def __init__(self, fail: dict[str, ApiError] | None = None, output_tokens: int = 80) -> None:
        self.calls: list[str] = []
        self.fail = fail or {}
        self.output_tokens = output_tokens

    def complete(self, system: str, user: str) -> Completion:
        self.calls.append(user)
        repo = user.splitlines()[0].removeprefix("Repository: ").split(" ")[0]
        if repo in self.fail:
            raise self.fail[repo]
        if system == PROMPTS["names"]:
            text = f"Recognised: no\nThe name {repo} suggests a tool."
        else:
            text = " ".join(" ".join(user.splitlines()[1:]).split()[:60])
        return Completion(text, "stop", MODEL, Usage(len(user) // 4, 0, self.output_tokens))


def response(text: str = "A card.", model: str = MODEL, finish: str = "stop") -> io.BytesIO:
    payload = {
        "model": model,
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": 1_000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 400},
        },
    }
    return io.BytesIO(json.dumps(payload).encode())


def http_error(status: int, code: str = "", message: str = "") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    return urllib.error.HTTPError(llm_cards.API_URL, status, "error", {}, io.BytesIO(body))


class Opener:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request, timeout: float):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client(opener: Opener, sleeps: list[float] | None = None) -> OpenAIChat:
    record = sleeps if sleeps is not None else []
    return OpenAIChat(key=FAKE_KEY, opener=opener, sleep=record.append)


def test_packet_digest_is_labelled_and_deterministic() -> None:
    ledger = evidence("acme/ledger")
    digest = packet_digest(ledger)
    lines = digest.splitlines()
    assert lines[0].startswith("Repository: acme/ledger")
    assert "README:" in lines and any(line.startswith("Layout: ") for line in lines)
    assert digest == packet_digest(evidence("acme/ledger"))
    request = request_for("packet", ledger)
    assert request.key() == f"{MODEL}/v1/packet/acme/ledger/{request.sha256[:24]}"
    assert request_for("names", ledger).text == "Repository: acme/ledger"
    with pytest.raises(ValueError, match="mode must be one of"):
        request_for("closed", ledger)


def test_names_answers_are_parsed() -> None:
    assert parse_names_card("Recognised: yes  \nA web framework.") == (True, "A web framework.")
    assert parse_names_card("**Recognized: No**\nMaybe a tool.") == (False, "Maybe a tool.")
    assert parse_names_card("A card without the line.") == (None, "A card without the line.")
    ledger = evidence("acme/ledger")
    assert indexed_text("names", ledger, "Recognised: no\nA ledger.") == "acme/ledger\nA ledger."
    assert indexed_text("packet", ledger, " A ledger. ").endswith("\nA ledger.")


def test_usage_is_priced() -> None:
    usage = Usage(prompt_tokens=1_000_000, cached_tokens=500_000, completion_tokens=100_000)
    assert usage.usd() == pytest.approx(0.5 * 0.20 + 0.5 * 0.02 + 0.1 * 1.25)


def test_client_sends_a_pinned_deterministic_request() -> None:
    opener = Opener(response("A card. "))
    completion = client(opener).complete("system", "user")
    assert completion == Completion("A card.", "stop", MODEL, Usage(1_000, 400, 200))
    body = json.loads(opener.requests[0].data)
    assert body["model"] == MODEL and body["temperature"] == 0
    assert body["reasoning_effort"] == "none"
    assert body["max_completion_tokens"] == llm_cards.MAX_OUTPUT_TOKENS
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_client_retries_rate_limits_and_server_errors() -> None:
    sleeps: list[float] = []
    opener = Opener(http_error(429), http_error(503), urllib.error.URLError("reset"), response())
    assert client(opener, sleeps).complete("s", "u").text == "A card."
    assert len(opener.requests) == 4 and len(sleeps) == 3
    with pytest.raises(ApiError, match="gave up after 7 attempts") as error:
        client(Opener(*[http_error(500)] * 7)).complete("s", "u")
    assert not error.value.fatal


@pytest.mark.parametrize(
    ("failure", "fatal"),
    [
        (http_error(401, "invalid_api_key", f"Incorrect API key provided: {FAKE_KEY}"), True),
        (http_error(429, "insufficient_quota", "You exceeded your quota"), True),
        (http_error(400, "invalid_request_error", "bad"), False),
    ],
)
def test_client_errors_never_carry_the_key(failure, fatal: bool) -> None:
    opener = Opener(failure)
    with pytest.raises(ApiError) as error:
        client(opener).complete("s", "u")
    assert error.value.fatal is fatal and len(opener.requests) == 1
    assert FAKE_KEY not in str(error.value)


def test_client_refuses_another_model(monkeypatch) -> None:
    with pytest.raises(ApiError, match="got gpt-other") as error:
        client(Opener(response(model="gpt-other"))).complete("s", "u")
    assert error.value.fatal
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(ApiError, match=f"{KEY_ENV} is not set"):
        OpenAIChat()
    with pytest.raises(ValueError, match="no price"):
        OpenAIChat(model="gpt-unpriced", key=FAKE_KEY)


def test_build_generates_once_and_keeps_the_ledger(tmp_path: Path) -> None:
    store = CardStore(tmp_path / "cards.sqlite")
    fake = FakeClient()
    batch = requests("packet") + requests("packet")[:3]
    first = build(batch, store, fake, "first", workers=4)
    assert first.requests == first.generated == len(fake.calls) == 10
    assert first.cached == first.failed == 0 and not first.stopped_at_cap
    assert first.usd == pytest.approx(store.spent()) == pytest.approx(first.spent_after)

    again = build(batch + requests("names"), store, fake, "second", workers=4)
    assert again.cached == 10 and again.generated == 10 and len(fake.calls) == 20
    ledger = {row["build"]: row for row in CardStore(store.path).by_build()}
    assert set(ledger) == {"first", "second"}
    assert ledger["first"]["cards"] == ledger["second"]["cards"] == 10
    assert ledger["second"]["mode"] == "names"


def test_build_stops_before_the_cap(tmp_path: Path) -> None:
    store = CardStore(tmp_path / "cards.sqlite")
    fake = FakeClient(output_tokens=llm_cards.MAX_OUTPUT_TOKENS)
    batch = requests("packet")
    cap = 3.5 * max(llm_cards.worst_case_usd(r) for r in batch)
    stats = build(batch, store, fake, "capped", cap_usd=cap, workers=1)
    assert stats.stopped_at_cap and 3 <= stats.generated < len(batch)
    assert store.spent() <= cap
    rerun = build(batch, store, fake, "capped", cap_usd=0.0, workers=1)
    assert rerun.stopped_at_cap and rerun.generated == 0 and rerun.cached == stats.generated


def test_build_keeps_finished_cards_when_a_call_is_fatal(tmp_path: Path) -> None:
    store = CardStore(tmp_path / "cards.sqlite")
    batch = requests("packet")
    fail = {"acme/portal": ApiError(400, "bad"), "hooli/nucleus": ApiError(401, "no", fatal=True)}
    with pytest.raises(ApiError, match="HTTP 401"):
        build(batch, store, FakeClient(fail), "broken", workers=1)
    kept = store.get_many(r.key() for r in batch)
    assert 0 < len(kept) < len(batch)
    assert {r.repo for r in batch if r.key() in kept}.isdisjoint(fail)


@pytest.fixture
def filled(tmp_path: Path) -> GeneratedCards:
    store = CardStore(tmp_path / "cards.sqlite")
    build(requests("packet") + requests("names"), store, FakeClient(), "test", workers=2)
    return GeneratedCards("packet", store=store.path)


def names(cards: GeneratedCards) -> GeneratedCards:
    return GeneratedCards("names", store=cards.store.path)


def test_generated_rankers_rank_every_candidate(
    filled: GeneratedCards, tmp_path: Path, mini_cases: list[Case]
) -> None:
    repos = sorted(c.repo for c in Universe.load(EVIDENCE_UNIVERSE).view().candidates)
    rankers = [
        GeneratedDenseRanker(encoder=HashingEncoder(), store=tmp_path / "v.sqlite", cards=filled),
        GeneratedBM25Ranker(cards=filled),
        MatchedDenseRanker(encoder=HashingEncoder(), store=tmp_path / "v.sqlite", cards=filled),
        MatchedBM25Ranker(cards=filled),
    ]
    for ranker in rankers:
        for prediction in harness.run(ranker, mini_cases, EVIDENCE_UNIVERSE):
            case = next(c for c in mini_cases if c.case_id == prediction.case_id)
            assert sorted(prediction.ranking) == repos
            assert prediction.ranking[0] in case.gold_repos, (ranker.name, prediction.ranking)


def test_generated_cards_are_what_gets_indexed(filled: GeneratedCards, tmp_path: Path) -> None:
    ledger = evidence("acme/ledger")
    text = filled.text(ledger)
    card = filled.store.get(request_for("packet", ledger).key()).text
    assert text == indexed_text("packet", ledger, card)
    encoder = HashingEncoder()
    dense = GeneratedDenseRanker(encoder=encoder, store=tmp_path / "v.sqlite", cards=filled)
    dense.vectors(Universe.load(EVIDENCE_UNIVERSE).view())
    assert text in encoder.encoded and dense.variant == f"llm-packet-{MODEL}-v1"

    vectors = tmp_path / "m.sqlite"
    matched = MatchedDenseRanker(encoder=HashingEncoder(), store=vectors, cards=filled)
    assert matched.card_text(ledger) == build_card(ledger, word_count(text)).text
    assert word_count(matched.card_text(ledger)) <= word_count(text)


def test_names_rankers_read_closed_book_cards(filled: GeneratedCards, tmp_path: Path) -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    closed = names(filled)
    assert closed.text(evidence("acme/ledger")) == (
        "acme/ledger\nThe name acme/ledger suggests a tool."
    )
    query = Query("c1", "ledger", CUTOFF)
    assert GeneratedNamesBM25Ranker(cards=closed).rank(query, view)[0] == "acme/ledger"
    dense = GeneratedNamesDenseRanker(
        encoder=HashingEncoder(), store=tmp_path / "v.sqlite", cards=closed
    )
    assert dense.rank(query, view)[0] == "acme/ledger"
    first = view.candidates[0]
    assert dense._key(first) == f"llm-names-{MODEL}-v1/250/{first.repo}"


def test_hybrid_fuses_bm25f_with_generated_cards(filled: GeneratedCards, tmp_path: Path) -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    query = Query("c1", "Vaccine lots expire.", CUTOFF)
    vectors = tmp_path / "v.sqlite"
    dense = GeneratedDenseRanker(encoder=HashingEncoder(), store=vectors, cards=filled)
    hybrid = GeneratedHybridRanker(dense=dense)
    keyword = [repo for repo, _ in BM25Ranker().search(query, view)]
    expected = reciprocal_rank_fusion([keyword, dense.rank(query, view)])
    assert hybrid.rank(query, view) == expected and expected[0] == "umbrella/vaccine-tracker"


def test_a_missing_card_is_an_error(tmp_path: Path) -> None:
    empty = GeneratedCards("packet", store=tmp_path / "empty.sqlite")
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    with pytest.raises(MissingCardError, match=r"run python -m repo_discovery\.llm_cards build"):
        GeneratedBM25Ranker(cards=empty).rank(Query("c1", "ledger", CUTOFF), view)


def test_registry_builds_v1_rankers() -> None:
    for name in (
        "dense-llm-cards",
        "bm25-llm-cards",
        "hybrid-llm-rrf",
        "dense-llm-names",
        "bm25-llm-names",
        "hybrid-llm-names-rrf",
        "dense-cards-matched",
        "bm25-cards-matched",
    ):
        assert make_ranker(name, seed=0).name == name


def test_build_command(tmp_path: Path, wideswe_mini: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("REPO_DISCOVERY_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(llm_cards, "OpenAIChat", FakeClient)
    cases = tmp_path / "cases.jsonl"
    split = ["--manifest", str(tmp_path / "split.json")]
    build_cases = ["build-cases", "--wideswe-dir", str(wideswe_mini), "--out", str(cases)]
    assert cli([*build_cases, *split]) == 0
    capsys.readouterr()
    args = ["build", "--cases", str(cases), "--split", "all", "--universes", str(EVIDENCE_UNIVERSE)]
    args += ["--mode", "packet", "--build", "mini"]

    assert llm_cards.main([*args, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["to_generate"] == dry["distinct"] == 10 and dry["spent_usd"] == 0

    log = tmp_path / "builds.jsonl"
    assert llm_cards.main([*args, "--log", str(log)]) == 0
    built = json.loads(capsys.readouterr().out)
    assert built["generated"] == 10 and json.loads(log.read_text())["build"] == "mini"
    assert llm_cards.main([*args, "--cap-usd", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["cached"] == 10

    assert llm_cards.main(["spend"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["build"] == "mini" and rows[0]["cards"] == 10
    assert rows[-1]["total_usd"] == pytest.approx(built["usd"], abs=1e-4)
