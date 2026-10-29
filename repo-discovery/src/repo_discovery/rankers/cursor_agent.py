"""V5: Cursor's command-line agent as a repository explorer.

Each case runs ``cursor-agent`` headless (``--print``, ``--mode ask``) in a fresh
temporary workspace holding ``candidates.txt`` and a copy of each candidate's evidence
under ``repos/<owner>__<name>/``. The workspace has no ``universe.jsonl``, commits,
roles, snapshot dates, cutoff or case ID (case IDs embed gold repository names).

The agent runs with a fresh ``HOME`` per case whose CLI config denies shell, writes, web
fetch, MCP tools, and reads under that ``HOME`` (where the CLI keeps transcripts) and
under ``/home``, ``/workspace``, ``/cursor`` and ``/root``, where cases, universes and
traces live. A run is stopped at ``timeout_s`` seconds or when it
starts a tool call in more than ``max_turns`` model turns (parallel calls in one turn
count once); then one short follow-up turn asks for the answer. The CLI reports token
usage only when a run finishes, so a stopped run's usage is missing and its summary
says ``usage_complete: false``. Each run also records ``context``, the context size after
each of its model calls as read from the CLI's chat store; these sum to the run's input,
cache and output tokens, including for stopped runs. An invocation that exits or times
out before starting a session, as when the CLI can't reach the Cursor API, is retried
after each of ``retry_pauses``.

With ``evidence="names"`` (closed book) the workspace holds only ``candidates.txt`` and
the prompt says nothing else about the candidates is available.

With ``evidence="on-demand"`` (fetch level L5) the workspace starts with
``candidates.txt`` and an empty ``packets/``, and the case's ``HOME`` configures one MCP
server, :mod:`repo_discovery.rankers.open_repo`, whose ``open_repo`` tool places up to
``open_budget`` candidates' packets there. Its one tool is the only MCP tool allowed;
the CLI rejects any other in ``--print`` mode. The trace adds each call's outcome and the
tool turn it was made in.

With ``search`` set to ``bm25f`` or ``hybrid-rrf`` (method c2) the full-packet workspace
comes with one MCP tool, :mod:`repo_discovery.rankers.search_repos`, which ranks the
candidates against the agent's own query with that ranker and returns the top results
with snippets. The trace adds each search, its results and its tool turn.

Packets come from the universe as they are, so a universe written by
:func:`repo_discovery.fetch_levels.restrict` gives the agent that level's cut (method c1):
the README at L2, the README and manifests at L3. The prompt then names only what the
packets hold, the cut's empty path list is left out of the workspace, and the trace
records the level.

The CLI loads team plugins and skills into a fresh ``HOME`` on start, which can take
minutes. ``home_template`` names a ``HOME`` warmed once by :func:`warm_home`; each case
then starts from a copy of its caches, without its chats or projects.

The agent's JSON list is matched to the universe, and repositories it left out are
appended in a seeded random order. Each case writes its raw event stream and a summary
(status, parse outcome, timings, token usage, turns, out-of-workspace paths) to
``trace_dir``.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import random
import re
import shutil
import signal
import site
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, fields
from pathlib import Path

from repo_discovery.cases import Query, repo_key
from repo_discovery.contract import UniverseView
from repo_discovery.evidence import PATH_LIST_FILES
from repo_discovery.fetch_levels import FULL, fetch_level
from repo_discovery.rankers import open_repo, search_repos
from repo_discovery.rankers.mcp_tool import SERVER

DEFAULT_MODEL = "composer-2.5"
DENY = [
    "Shell(*)",
    "Write(**)",
    "WebFetch(*)",
    "Mcp(*:*)",
    "Read(/home/**)",
    "Read(/workspace/**)",
    "Read(/cursor/**)",
    "Read(/root/**)",
]
FOLLOW_UP = (
    "Stop exploring now. Using only what you have already seen, reply with the JSON array "
    "of owner/name strings, most likely first, in a ```json code block and nothing else. "
    "Do not call any tools."
)
PROMPT = """\
Find every repository that this change request needs edits in.

<change_request>
{request}
</change_request>

This workspace holds {n} candidate repositories. `candidates.txt` lists them as \
owner/name, one per line. {workspace}

Explore with the read, grep and glob tools. Shell commands and web access are disabled, \
so don't try them. You have at most {max_turns} rounds of tool calls (parallel calls in \
one round count once); answer before you run out.

A change like this often spans more than one repository, and finding all of them matters \
more than finding the most obvious one. Consider which repositories define, consume, \
configure, document or test the behavior the request touches.

Finish with the {k} repositories most likely to need edits, most likely first, as a JSON \
array of owner/name strings in a ```json code block, with nothing after it.
"""
PACKET_CONTENTS = {
    "L2": "its README only",
    "L3": "its README and package manifest files only",
    FULL: "README and docs, manifest files, API contract files, and a list of every file "
    "path in the repository",
}
WORKSPACE = {
    "packets": "Each repository's extracted evidence is under `repos/<owner>__<name>/`: "
    "{contents}.",
    "names": "Nothing else about them is available here, so rely on what you already know "
    "about these repositories.",
    "on-demand": "No evidence about them is here yet. The `open_repo` tool, called with "
    '`{{"repo": "owner/name"}}`, places one candidate\'s extracted evidence under '
    "`packets/<owner>__<name>/`: README and docs, manifest files, API contract files, and a "
    "list of every file path in the repository. It returns the packet's file list. You can "
    "open at most {budget} distinct repositories{zero}; reopening one is free, and so is a "
    "name that isn't a candidate. Your answer may include repositories you haven't opened.",
}
ZERO_BUDGET = ", so rely on the names and what you already know about these repositories"
OPEN_TOOL_ALLOW = f"Mcp({SERVER}:{open_repo.TOOL})"
SEARCH = (
    ' The `search_repos` tool, called with `{"query": "...", "k": 20}`, ranks every '
    "candidate against your query with a search index over these packets and returns the "
    f"best k (at most {search_repos.MAX_K}), best first, each with a short snippet. It is "
    "free, so search as often as you like."
)
SEARCH_TOOL_ALLOW = f"Mcp({SERVER}:{search_repos.TOOL})"
ANONYMIZED = {
    "evidence": " Owner and repository names have been replaced with random IDs, in "
    "`candidates.txt` and inside the evidence alike; the request still uses the real names.",
    "everywhere": " Owner and repository names have been replaced with random IDs "
    "everywhere, including the request, `candidates.txt` and the evidence.",
}
HOME_SKIP = {"chats", "projects", "cli-config.json"}
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class AgentRun:
    status: str = "ok"
    text: str = ""
    seconds: float = 0.0
    duration_ms: int = 0
    usage: Counter = field(default_factory=Counter)
    tool_calls: int = 0
    model_turns: int = 0
    tool_turns: int = 0
    tools: Counter = field(default_factory=Counter)
    outside_paths: list[str] = field(default_factory=list)
    session_id: str | None = None
    model: str | None = None
    error: str | None = None
    context: list[int] = field(default_factory=list)
    attempts: int = 1


@dataclass
class Parsed:
    status: str
    ranking: list[str]
    unknown: list[str]


_PATH_ARGS = {"path", "paths", "file", "files", "filePath", "directory", "targetDirectory", "cwd"}


def _path_args(args: dict) -> Iterator[str]:
    for key, value in args.items():
        if key not in _PATH_ARGS:
            continue
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str):
                yield item


def _arrays(text: str) -> list[list]:
    """JSON arrays in ``text``: fenced blocks first, then bare arrays, in order."""
    found = []
    decoder = json.JSONDecoder()
    for source in [m.group(1) for m in _FENCE.finditer(text)] or [text]:
        for start in (i for i, ch in enumerate(source) if ch == "["):
            try:
                value, _ = decoder.raw_decode(source[start:])
            except ValueError:
                continue
            if isinstance(value, list) and value:
                found.append(value)
    return found


def _normalize(item: object) -> str | None:
    if isinstance(item, dict):
        item = item.get("repo") or item.get("repository") or item.get("name")
    if not isinstance(item, str):
        return None
    item = item.strip().strip("`").removeprefix("https://github.com/").strip("/")
    if "/" not in item and "__" in item:
        item = item.replace("__", "/", 1)
    return item


def parse_ranking(text: str, repos: list[str]) -> Parsed:
    """Match the last JSON array in ``text`` that names a candidate to ``repos``."""
    by_key = {repo_key(r): r for r in repos}
    arrays = _arrays(text)
    for array in reversed(arrays):
        ranking, unknown, seen = [], [], set()
        for item in array:
            name = _normalize(item)
            key = repo_key(name) if name else None
            if key in by_key and key not in seen:
                ranking.append(by_key[key])
                seen.add(key)
            elif key not in seen:
                unknown.append(str(item))
        if ranking:
            return Parsed("ok", ranking, unknown)
    return Parsed("no_candidates" if arrays else "no_json", [], [])


def universe_level(universe: UniverseView) -> str:
    """The fetch level of ``universe``'s packets (all of a universe's share one corpus)."""
    return fetch_level(universe.candidates[0].evidence_dir) if universe.candidates else FULL


def build_workspace(dest: Path, universe: UniverseView, packets: bool = True) -> None:
    dest.mkdir(parents=True)
    if packets:
        repos_dir = dest / "repos"
        repos_dir.mkdir()
        # Below L4 a packet's path list is an empty stub, not the repository's file list.
        ignore = (
            None if universe_level(universe) == FULL else shutil.ignore_patterns(*PATH_LIST_FILES)
        )
        for candidate in universe.candidates:
            owner, name = candidate.repo.split("/")
            target = repos_dir / f"{owner}__{name}"
            if candidate.evidence_dir.is_dir():
                shutil.copytree(
                    candidate.evidence_dir,
                    target,
                    symlinks=False,
                    ignore=ignore,
                    copy_function=_link_or_copy,
                )
            else:
                target.mkdir()
    (dest / "candidates.txt").write_text("\n".join(c.repo for c in universe.candidates) + "\n")
    for path in [dest, *dest.rglob("*")]:
        mode = path.stat().st_mode
        path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _link_or_copy(src: str, dst: str) -> None:
    # A hard link shares the corpus file's inode, so build_workspace's read-only pass also
    # makes the corpus file read-only, and nothing in a case can write through to it.
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _remove(path: Path) -> None:
    for item in [path, *path.rglob("*")]:
        if item.is_dir() and not item.is_symlink():
            item.chmod(item.stat().st_mode | stat.S_IWUSR)
    shutil.rmtree(path, ignore_errors=True)


def _prepare_home(home: Path, allow: tuple[str, ...] = ()) -> None:
    """Write the CLI's permissions; an allowed MCP tool lifts the blanket MCP deny,
    which would otherwise override it."""
    config_path = home / ".cursor" / "cli-config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError):
        config = {"version": 1}
    deny = [d for d in DENY if not (allow and d == "Mcp(*:*)")]
    config["permissions"] = {"allow": list(allow), "deny": [*deny, f"Read({home.resolve()}/**)"]}
    config_path.write_text(json.dumps(config, indent=2) + "\n")


def _register_tool(home: Path, module, config: dict, env: dict | None = None) -> Path:
    """Register ``module``'s MCP server in ``home`` with a fresh log; returns the log."""
    tool_dir = home / module.TOOL.replace("_", "-")
    tool_dir.mkdir(parents=True, exist_ok=True)
    log = tool_dir / "log.jsonl"
    log.unlink(missing_ok=True)
    (tool_dir / "config.json").write_text(json.dumps({**config, "log": str(log)}) + "\n")
    server = {
        "command": sys.executable,
        "args": ["-m", module.__name__, str(tool_dir / "config.json")],
        "env": {"PYTHONPATH": str(Path(module.__file__).resolve().parents[2]), **(env or {})},
    }
    mcp = home / ".cursor" / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {SERVER: server}}, indent=2) + "\n")
    return log


def _setup_open_repo(home: Path, workspace: Path, universe: UniverseView, budget: int) -> Path:
    """Give ``workspace`` an empty ``packets/`` and ``home`` the ``open_repo`` MCP server;
    returns the tool's log."""
    packets = workspace / open_repo.PACKETS
    mode = workspace.stat().st_mode
    workspace.chmod(mode | stat.S_IWUSR)
    packets.mkdir()
    workspace.chmod(mode)
    packets.chmod(packets.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    config = {
        "workspace": str(workspace.resolve()),
        "budget": budget,
        "candidates": {c.repo: str(c.evidence_dir) for c in universe.candidates},
    }
    return _register_tool(home, open_repo, config)


def _setup_search(home: Path, universe: UniverseView, query: Query, backend: str) -> Path:
    """Give ``home`` the ``search_repos`` MCP server over ``universe``; returns its log.
    The server runs under the case's ``HOME``, so it is pointed at this process's caches
    (stored card vectors, the encoder's weights) and user site-packages (the encoder's
    libraries), and kept off the network. Each server gets one thread, since one runs per
    concurrent case and torch would otherwise take every core."""
    from repo_discovery.embeddings import ENCODER_DIR_ENV
    from repo_discovery.wideswe import cache_root

    config = {
        "universe": str(universe.root.resolve()),
        "backend": backend,
        "case_id": query.case_id,
        "cutoff": query.cutoff,
    }
    env = {
        "REPO_DISCOVERY_CACHE": str(cache_root()),
        "HF_HOME": os.environ.get("HF_HOME") or str(Path.home() / ".cache" / "huggingface"),
        "HF_HUB_OFFLINE": "1",
        "PYTHONUSERBASE": site.getuserbase(),
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }
    if os.environ.get(ENCODER_DIR_ENV):
        env[ENCODER_DIR_ENV] = os.environ[ENCODER_DIR_ENV]
    return _register_tool(home, search_repos, config, env)


def tool_calls(events: list[dict], log: Path, tool: str, key: str) -> list[dict]:
    """``tool``'s log, each call with the tool turn it was made in (1-based, counted as
    ``run_agent`` counts them, across the run and its follow-up). Log entries are matched
    to call events in order by the ``key`` argument, which the log keeps as ``asked``
    (``open_repo``) or under ``key`` itself."""
    turns: dict[str, int] = {}
    asked: list[tuple[int, object]] = []
    calls = 0
    for event in events:
        if event.get("type") != "tool_call" or event.get("subtype") != "started":
            continue
        calls += 1
        turn = turns.setdefault(event.get("model_call_id") or f"call-{calls}", len(turns) + 1)
        call = event.get("tool_call", {}).get("mcpToolCall", {}).get("args", {})
        if call.get("toolName") == tool:
            asked.append((turn, (call.get("args") or {}).get(key)))
    entries = [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []
    for entry in entries:
        value = entry.get("asked", entry.get(key))
        match = next((i for i, (_, v) in enumerate(asked) if v == value), None)
        entry["turn"] = asked.pop(match)[0] if match is not None else None
        entry.pop("t", None)
    return entries


def _seed_home(home: Path, template: Path) -> None:
    """Copy ``template``'s CLI caches into ``home``, leaving out its chats and projects."""
    source = template / ".cursor"
    target = home / ".cursor"
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir() if source.is_dir() else ():
        if item.name in HOME_SKIP:
            continue
        if item.is_dir():
            shutil.copytree(item, target / item.name, symlinks=True)
        else:
            shutil.copy2(item, target / item.name)


def warm_home(template: Path, binary: str | None = None, model: str = DEFAULT_MODEL) -> bool:
    """Start the CLI once with ``HOME=template`` so its caches fill; False if already warm."""
    if (template / ".cursor" / "plugins").is_dir():
        return False
    _prepare_home(template)
    with tempfile.TemporaryDirectory(prefix="v5-warm-") as workspace:
        subprocess.run(
            [
                binary or find_binary(),
                "--print",
                "--mode",
                "ask",
                "--trust",
                "--model",
                model,
                "--workspace",
                workspace,
                "Reply with the single word OK.",
            ],
            cwd=workspace,
            env={**os.environ, "HOME": str(template), "NO_COLOR": "1"},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=600,
            check=True,
        )
    for name in ("chats", "projects"):
        shutil.rmtree(template / ".cursor" / name, ignore_errors=True)
    return True


def _reader(stream, lines: queue.Queue) -> None:
    for line in stream:
        lines.put(line)
    lines.put(None)


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def run_agent(
    command: list[str],
    workspace: Path,
    env: dict[str, str],
    timeout_s: float,
    max_turns: int,
    events: list[dict],
) -> AgentRun:
    """Run one ``--print --output-format stream-json`` invocation under the budget."""
    run = AgentRun()
    root = str(workspace.resolve())
    turns: set[str] = set()
    tool_turns: set[str] = set()
    started = time.monotonic()
    with tempfile.TemporaryFile("w+") as stderr:
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            start_new_session=True,
        )
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=_reader, args=(process.stdout, lines), daemon=True).start()
        finished = False
        while not finished:
            remaining = timeout_s - (time.monotonic() - started)
            if remaining <= 0:
                run.status = "timeout"
                break
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                run.status = "timeout"
                break
            if line is None:
                stderr.seek(0)
                run.status = "error"
                run.error = f"exit {process.wait()}: {stderr.read()[-2000:]}"
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.append(event)
            kind = event.get("type")
            if event.get("model_call_id"):
                turns.add(event["model_call_id"])
            if kind == "system" and event.get("subtype") == "init":
                run.session_id = event.get("session_id")
                run.model = event.get("model")
            elif kind == "tool_call" and event.get("subtype") == "started":
                call = event.get("tool_call", {})
                tool = next((k for k in call if k.endswith("ToolCall")), "unknown")
                run.tools[tool] += 1
                run.tool_calls += 1
                for value in _path_args(call.get(tool, {}).get("args", {})):
                    if value.startswith("/") and not value.startswith(root):
                        run.outside_paths.append(value)
                tool_turns.add(event.get("model_call_id") or f"call-{run.tool_calls}")
                if len(tool_turns) > max_turns:
                    run.status = "turn_budget"
                    break
            elif kind == "result":
                run.text = event.get("result") or ""
                run.duration_ms = int(event.get("duration_ms") or 0)
                run.usage.update(event.get("usage") or {})
                if event.get("is_error"):
                    run.status, run.error = "error", run.text[-2000:]
                finished = True
        _stop(process)
    run.seconds = time.monotonic() - started
    run.model_turns = len(turns)
    run.tool_turns = len(tool_turns)
    return run


def _varint(data: bytes, i: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return value, i


def _fields(data: bytes) -> dict[int, list]:
    """Top-level protobuf fields of ``data``; ValueError if it doesn't parse as one."""
    out: dict[int, list] = {}
    i = 0
    try:
        while i < len(data):
            key, i = _varint(data, i)
            number, wire = key >> 3, key & 7
            if wire == 0:
                value, i = _varint(data, i)
            elif wire in (1, 2, 5):
                size = {1: 8, 5: 4}.get(wire)
                if size is None:
                    size, i = _varint(data, i)
                value, i = data[i : i + size], i + size
            else:
                raise ValueError(f"wire type {wire}")
            if number == 0 or i > len(data):
                raise ValueError("truncated")
            out.setdefault(number, []).append(value)
    except IndexError as exc:
        raise ValueError("truncated") from exc
    return out


def _context_used(blob: bytes) -> int | None:
    """Tokens used, if ``blob`` is a conversation root: field 5 holds {1: used, 2: window}."""
    try:
        root = _fields(blob)
        if not any(isinstance(v, bytes) and v.startswith(b"file://") for v in root.get(9, [])):
            return None
        for value in root.get(5, []):
            usage = _fields(value) if isinstance(value, bytes) else {}
            used, window = usage.get(1, [None])[0], usage.get(2, [None])[0]
            if isinstance(used, int) and isinstance(window, int) and 0 < used <= window:
                return used
    except ValueError:
        return None
    return None


def context_tokens(home: Path, session_id: str | None) -> list[int]:
    """Context sizes after each model call of a chat, oldest first, from the CLI's store.

    The store keeps every version of the conversation root. The format is undocumented,
    so anything unreadable gives an empty list.
    """
    if not session_id:
        return []
    seen: list[int] = []
    for db in sorted(home.glob(f".cursor/chats/*/{session_id}/store.db")):
        try:
            with contextlib.closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                rows = conn.execute("select data from blobs order by rowid").fetchall()
        except sqlite3.Error:
            return []
        for (data,) in rows:
            used = _context_used(bytes(data)) if data else None
            if used and used not in seen:
                seen.append(used)
    return seen


def find_binary(name: str = "cursor-agent") -> str:
    found = shutil.which(name) or shutil.which(name, path=str(Path.home() / ".local" / "bin"))
    if not found:
        raise FileNotFoundError(
            f"{name} not found; install it with `curl https://cursor.com/install -fsS | bash`"
        )
    return found


class CursorAgentRanker:
    """Ranks by asking Cursor's command-line agent to explore the universe's evidence."""

    name = "cursor-agent"

    def __init__(
        self,
        seed: int = 0,
        model: str = DEFAULT_MODEL,
        top_k: int = 20,
        timeout_s: float = 300.0,
        max_turns: int = 20,
        follow_up_timeout_s: float = 90.0,
        trace_dir: Path | None = None,
        agent_home: Path | None = None,
        binary: str | None = None,
        retry_pauses: tuple[float, ...] = (15.0, 60.0, 180.0),
        evidence: str = "packets",
        anonymized: str | None = None,
        home_template: Path | None = None,
        open_budget: int | None = None,
        search: str | None = None,
    ) -> None:
        if not os.environ.get("CURSOR_API_KEY"):
            raise RuntimeError("CURSOR_API_KEY is not set")
        if evidence not in WORKSPACE:
            raise ValueError(f"evidence must be one of {sorted(WORKSPACE)}, got {evidence!r}")
        if anonymized is not None and anonymized not in ANONYMIZED:
            raise ValueError(f"anonymized must be one of {sorted(ANONYMIZED)}, got {anonymized!r}")
        if (evidence == "on-demand") != (open_budget is not None):
            raise ValueError("open_budget is required with evidence='on-demand' and only then")
        if open_budget is not None and open_budget < 0:
            raise ValueError("open_budget must be at least 0")
        if search is not None and search not in search_repos.BACKENDS:
            raise ValueError(f"search must be one of {search_repos.BACKENDS}, got {search!r}")
        if search is not None and evidence != "packets":
            raise ValueError("search needs evidence='packets'")
        self.evidence = evidence
        self.open_budget = open_budget
        self.search = search
        self.anonymized = anonymized
        self.home_template = home_template
        self.seed = seed
        self.model = model
        self.top_k = top_k
        self.timeout_s = timeout_s
        self.max_turns = max_turns
        self.follow_up_timeout_s = follow_up_timeout_s
        if trace_dir is None:
            from repo_discovery.wideswe import cache_root

            trace_dir = cache_root() / "agent-traces" / model
        self.trace_dir = trace_dir
        self.agent_home = agent_home
        self.binary = binary or find_binary()
        self.retry_pauses = retry_pauses

    def _command(self, workspace: Path, prompt: str, resume: str | None = None) -> list[str]:
        command = [
            self.binary,
            "--print",
            "--output-format",
            "stream-json",
            "--mode",
            "ask",
            "--trust",
            "--model",
            self.model,
            "--workspace",
            str(workspace),
        ]
        if resume:
            command += ["--resume", resume]
        return [*command, prompt]

    def _run(self, command: list[str], *args) -> AgentRun:
        """``run_agent``, retried after a pause when the CLI stops before starting a session."""
        for attempt, pause in enumerate([*self.retry_pauses, None], start=1):
            run = run_agent(command, *args)
            run.attempts = attempt
            if run.status not in ("error", "timeout") or run.session_id or pause is None:
                return run
            time.sleep(pause)
        raise AssertionError("unreachable")

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        repos = [c.repo for c in universe.candidates]
        k = min(self.top_k, len(repos))
        on_demand = self.evidence == "on-demand"
        level = universe_level(universe)
        if self.evidence != "names" and level not in PACKET_CONTENTS:
            raise ValueError(f"a {level} universe has no packets; use evidence='names'")
        if (on_demand or self.search) and level != FULL:
            raise ValueError(f"open_repo and search_repos describe full packets, not {level}")
        workspace_text = WORKSPACE[self.evidence].format(
            budget=self.open_budget,
            zero=ZERO_BUDGET if self.open_budget == 0 else "",
            contents=PACKET_CONTENTS.get(level),
        ) + (SEARCH if self.search else "")
        prompt = PROMPT.format(
            request=query.prompt.strip(),
            n=len(repos),
            k=k,
            max_turns=self.max_turns,
            workspace=workspace_text + ANONYMIZED.get(self.anonymized, ""),
        )
        scratch = Path(tempfile.mkdtemp(prefix="v5-"))
        workspace = scratch / "workspace"
        home = self.agent_home or scratch / "home"
        if self.home_template and not self.agent_home:
            _seed_home(home, self.home_template)
        allow = (OPEN_TOOL_ALLOW,) if on_demand else (SEARCH_TOOL_ALLOW,) if self.search else ()
        _prepare_home(home, allow=allow)
        env = {**os.environ, "HOME": str(home), "NO_COLOR": "1"}
        events: list[dict] = []
        opens: list[dict] = []
        searches: list[dict] = []
        try:
            setup_started = time.monotonic()
            build_workspace(workspace, universe, packets=self.evidence == "packets")
            if on_demand:
                log = _setup_open_repo(home, workspace, universe, self.open_budget)
            if self.search:
                log = _setup_search(home, universe, query, self.search)
            setup_seconds = time.monotonic() - setup_started
            run = self._run(
                self._command(workspace, prompt),
                workspace,
                env,
                self.timeout_s,
                self.max_turns,
                events,
            )
            run.context = context_tokens(home, run.session_id)
            parsed = parse_ranking(run.text, repos)
            follow_up = None
            if parsed.status != "ok" and run.session_id and run.status != "error":
                follow_up = self._run(
                    self._command(workspace, FOLLOW_UP, resume=run.session_id),
                    workspace,
                    env,
                    self.follow_up_timeout_s,
                    1,
                    events,
                )
                follow_up.context = context_tokens(home, run.session_id)[len(run.context) :]
                parsed = parse_ranking(follow_up.text, repos)
            if on_demand:
                opens = tool_calls(events, log, open_repo.TOOL, "repo")
            if self.search:
                searches = tool_calls(events, log, search_repos.TOOL, "query")
        finally:
            _remove(scratch)

        chosen = {repo_key(r) for r in parsed.ranking}
        rest = [r for r in repos if repo_key(r) not in chosen]
        random.Random(f"{self.seed}:{query.case_id}").shuffle(rest)
        self._trace(
            query.case_id,
            run,
            follow_up,
            parsed,
            len(repos),
            k,
            events,
            opens,
            searches,
            level,
            setup_seconds,
        )
        return parsed.ranking + rest

    def _trace(
        self,
        case_id: str,
        run: AgentRun,
        follow_up: AgentRun | None,
        parsed: Parsed,
        universe_size: int,
        k: int,
        events: list[dict],
        opens: list[dict] = (),
        searches: list[dict] = (),
        level: str = FULL,
        setup_seconds: float = 0.0,
    ) -> None:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        with (self.trace_dir / f"{case_id}.events.jsonl").open("w", encoding="utf-8") as handle:
            for event in events:
                if event.get("type") != "thinking" or event.get("subtype") != "delta":
                    handle.write(json.dumps(event) + "\n")
        runs = [r for r in (run, follow_up) if r is not None]
        usage = sum((r.usage for r in runs), Counter())
        summary = {
            "case_id": case_id,
            "model": self.model,
            "model_reported": run.model,
            "evidence": self.evidence,
            "fetch_level": level,
            "anonymized": self.anonymized,
            "universe_size": universe_size,
            "k_requested": k,
            "status": run.status,
            "follow_up_status": follow_up.status if follow_up else None,
            "parse": parsed.status,
            "returned": len(parsed.ranking),
            "unknown": parsed.unknown,
            "seconds": round(sum(r.seconds for r in runs), 2),
            "setup_seconds": round(setup_seconds, 2),
            "agent_ms": sum(r.duration_ms for r in runs),
            "usage": dict(usage),
            "usage_complete": all(r.status in ("ok", "error") for r in runs),
            "context_tokens": sum(sum(r.context) for r in runs),
            "tool_calls": sum(r.tool_calls for r in runs),
            "model_turns": sum(r.model_turns for r in runs),
            "tool_turns": sum(r.tool_turns for r in runs),
            "tools": dict(sum((r.tools for r in runs), Counter())),
            "outside_paths": [p for r in runs for p in r.outside_paths],
            "errors": [r.error for r in runs if r.error],
            "open_budget": self.open_budget,
            "opened": [o["repo"] for o in opens if o["outcome"] == "opened"],
            "opens": list(opens),
            "search": self.search,
            "searches": list(searches),
            "runs": [
                {f.name: getattr(r, f.name) for f in fields(r) if f.name != "text"} for r in runs
            ],
            "final_text": (follow_up or run).text[-4000:],
        }
        (self.trace_dir / f"{case_id}.json").write_text(json.dumps(summary, indent=2) + "\n")
