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
says ``usage_complete: false``.

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
import stat
import subprocess
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, fields
from pathlib import Path

from repo_discovery.cases import Query, repo_key
from repo_discovery.contract import UniverseView

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
owner/name, one per line. Each repository's extracted evidence is under \
`repos/<owner>__<name>/`: README and docs, manifest files, API contract files, and a \
list of every file path in the repository.

Explore with the read, grep and glob tools. Shell commands and web access are disabled, \
so don't try them. You have at most {max_turns} rounds of tool calls (parallel calls in \
one round count once); answer before you run out.

A change like this often spans more than one repository, and finding all of them matters \
more than finding the most obvious one. Consider which repositories define, consume, \
configure, document or test the behavior the request touches.

Finish with the {k} repositories most likely to need edits, most likely first, as a JSON \
array of owner/name strings in a ```json code block, with nothing after it.
"""
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


def build_workspace(dest: Path, universe: UniverseView) -> None:
    repos_dir = dest / "repos"
    repos_dir.mkdir(parents=True)
    for candidate in universe.candidates:
        owner, name = candidate.repo.split("/")
        target = repos_dir / f"{owner}__{name}"
        if candidate.evidence_dir.is_dir():
            shutil.copytree(candidate.evidence_dir, target, symlinks=False)
        else:
            target.mkdir()
    (dest / "candidates.txt").write_text("\n".join(c.repo for c in universe.candidates) + "\n")
    for path in [dest, *dest.rglob("*")]:
        mode = path.stat().st_mode
        path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _remove(path: Path) -> None:
    for item in [path, *path.rglob("*")]:
        if item.is_dir() and not item.is_symlink():
            item.chmod(item.stat().st_mode | stat.S_IWUSR)
    shutil.rmtree(path, ignore_errors=True)


def _prepare_home(home: Path) -> None:
    config_path = home / ".cursor" / "cli-config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError):
        config = {"version": 1}
    config["permissions"] = {"allow": [], "deny": [*DENY, f"Read({home.resolve()}/**)"]}
    config_path.write_text(json.dumps(config, indent=2) + "\n")


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
    ) -> None:
        if not os.environ.get("CURSOR_API_KEY"):
            raise RuntimeError("CURSOR_API_KEY is not set")
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

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        repos = [c.repo for c in universe.candidates]
        k = min(self.top_k, len(repos))
        prompt = PROMPT.format(
            request=query.prompt.strip(), n=len(repos), k=k, max_turns=self.max_turns
        )
        scratch = Path(tempfile.mkdtemp(prefix="v5-"))
        workspace = scratch / "workspace"
        home = self.agent_home or scratch / "home"
        _prepare_home(home)
        env = {**os.environ, "HOME": str(home), "NO_COLOR": "1"}
        events: list[dict] = []
        try:
            build_workspace(workspace, universe)
            run = run_agent(
                self._command(workspace, prompt),
                workspace,
                env,
                self.timeout_s,
                self.max_turns,
                events,
            )
            parsed = parse_ranking(run.text, repos)
            follow_up = None
            if parsed.status != "ok" and run.session_id and run.status != "error":
                follow_up = run_agent(
                    self._command(workspace, FOLLOW_UP, resume=run.session_id),
                    workspace,
                    env,
                    self.follow_up_timeout_s,
                    1,
                    events,
                )
                parsed = parse_ranking(follow_up.text, repos)
        finally:
            _remove(scratch)

        chosen = {repo_key(r) for r in parsed.ranking}
        rest = [r for r in repos if repo_key(r) not in chosen]
        random.Random(f"{self.seed}:{query.case_id}").shuffle(rest)
        self._trace(query.case_id, run, follow_up, parsed, len(repos), k, events)
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
            "universe_size": universe_size,
            "k_requested": k,
            "status": run.status,
            "follow_up_status": follow_up.status if follow_up else None,
            "parse": parsed.status,
            "returned": len(parsed.ranking),
            "unknown": parsed.unknown,
            "seconds": round(sum(r.seconds for r in runs), 2),
            "agent_ms": sum(r.duration_ms for r in runs),
            "usage": dict(usage),
            "usage_complete": all(r.status in ("ok", "error") for r in runs),
            "tool_calls": sum(r.tool_calls for r in runs),
            "model_turns": sum(r.model_turns for r in runs),
            "tool_turns": sum(r.tool_turns for r in runs),
            "tools": dict(sum((r.tools for r in runs), Counter())),
            "outside_paths": [p for r in runs for p in r.outside_paths],
            "errors": [r.error for r in runs if r.error],
            "runs": [
                {f.name: getattr(r, f.name) for f in fields(r) if f.name != "text"} for r in runs
            ],
            "final_text": (follow_up or run).text[-4000:],
        }
        (self.trace_dir / f"{case_id}.json").write_text(json.dumps(summary, indent=2) + "\n")
