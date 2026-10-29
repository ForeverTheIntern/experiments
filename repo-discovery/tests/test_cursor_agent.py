from __future__ import annotations

import json
import os
import shutil
import site
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from repo_discovery import harness
from repo_discovery.cases import Case, Query
from repo_discovery.contract import Universe
from repo_discovery.fetch_levels import restrict
from repo_discovery.rankers import sandbox, search_repos
from repo_discovery.rankers.bm25 import BM25Ranker, candidate_digest
from repo_discovery.rankers.cursor_agent import (
    NO_PLUGIN_FETCH,
    CursorAgentRanker,
    build_workspace,
    find_binary,
    parse_ranking,
    tool_calls,
    warm_home,
)

REPOS = ["acme/billing", "acme/ledger", "acme/portal", "noise/one", "noise/two"]
EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"

FAKE_AGENT = """\
#!{python}
import json, os, sys, time
from pathlib import Path

args = sys.argv[1:]
workspace = Path(args[args.index("--workspace") + 1])
resumed = "--resume" in args
scenario = os.environ["FAKE_SCENARIO"]
log = Path(os.environ["FAKE_LOG"])
home = Path(os.environ["HOME"])
if scenario == "warm":
    for name in ("plugins/cache", "chats/h", "projects/p"):
        (home / ".cursor" / name).mkdir(parents=True)
    print("OK")
    sys.exit(0)
try:
    (workspace / "scribble.txt").write_text("x")
    wrote = True
except OSError:
    wrote = False
with log.open("a") as handle:
    handle.write(json.dumps({{
        "prompt": args[-1],
        "workspace": str(workspace),
        "resumed": resumed,
        "home": os.environ["HOME"],
        "home_files": sorted(str(p.relative_to(home)) for p in home.rglob("*")),
        "files": sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*")),
        "wrote": wrote,
    }}) + "\\n")

blobs = json.loads(os.environ.get("FAKE_BLOBS", "{{}}")).get("resumed" if resumed else "main")
if blobs:
    import sqlite3
    db = Path(os.environ["HOME"]) / ".cursor" / "chats" / "h" / "s1" / "store.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute("create table if not exists blobs (id text primary key, data blob)")
        rows = [(f"{{resumed}}-{{i}}", bytes.fromhex(b)) for i, b in enumerate(blobs)]
        conn.executemany("insert into blobs values (?, ?)", rows)

if scenario.startswith("offline") and len(log.read_text().splitlines()) <= int(scenario[-1]):
    sys.stderr.write("Failed to reach the Cursor API.\\n")
    sys.exit(1)

def emit(event):
    print(json.dumps({{**event, "session_id": "s1"}}), flush=True)

def answer(text):
    emit({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": text}}]}}}})
    emit({{"type": "result", "subtype": "success", "is_error": False, "duration_ms": 1200,
          "result": text, "usage": {{"inputTokens": 100, "outputTokens": 10,
          "cacheReadTokens": 50, "cacheWriteTokens": 0}}}})
    time.sleep(30)

def tool(path, turn="m1"):
    emit({{"type": "tool_call", "subtype": "started", "model_call_id": turn,
          "tool_call": {{"readToolCall": {{"args": {{"path": path}}}}}}}})

if scenario == "stall" and resumed and len(log.read_text().splitlines()) == 2:
    time.sleep(30)

emit({{"type": "system", "subtype": "init", "model": "Fake 1"}})
if resumed:
    answer('```json\\n["acme/portal"]\\n```' if scenario != "prose" else "no idea")
elif scenario == "answer" or scenario.startswith("offline"):
    tool(str(workspace / "candidates.txt"))
    tool("/home/someone/.cache/cases.jsonl")
    answer('Picks:\\n```json\\n["ACME/ledger", "made/up", "acme__billing", "acme/ledger"]\\n```')
elif scenario in ("loop", "stall"):
    for turn in range(10):
        tool(str(workspace / "candidates.txt"), f"m{{turn}}")
        tool(str(workspace / "repos"), f"m{{turn}}")
    time.sleep(30)
elif scenario in ("open", "search", "search-open"):
    import subprocess
    server = json.loads((home / ".cursor" / "mcp.json").read_text())
    server = server["mcpServers"]["repo-discovery"]
    proc = subprocess.Popen([server["command"], *server["args"]], text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            env={{**os.environ, **server["env"]}})
    def rpc(i, method, params):
        message = {{"jsonrpc": "2.0", "id": i, "method": method, "params": params}}
        proc.stdin.write(json.dumps(message) + "\\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())
    rpc(0, "initialize", {{}})
    tool(str(workspace / "candidates.txt"), "m0")
    if scenario == "open":
        asks = [("m1", "open_repo", "repo", "acme/ledger"), ("m1", "open_repo", "repo", "made/up"),
                ("m2", "open_repo", "repo", "ACME/Billing")]
    elif scenario == "search":
        asks = [("m1", "search_repos", "query", "billing"), ("m2", "search_repos", "query", "")]
    else:
        asks = [("m1", "search_repos", "query", "billing"),
                ("m2", "open_repo", "repo", "acme/billing"),
                ("m2", "open_repo", "repo", "acme/ledger")]
    listed = rpc(len(asks) + 1, "tools/list", {{}})["result"]["tools"]
    replies = []
    for i, (turn, name, key, value) in enumerate(asks):
        emit({{"type": "tool_call", "subtype": "started", "model_call_id": turn,
              "tool_call": {{"mcpToolCall": {{"args": {{"name": "repo-discovery-" + name,
              "args": {{key: value}}, "toolName": name}}}}}}}})
        reply = rpc(i + 1, "tools/call", {{"name": name, "arguments": {{key: value}}}})
        replies.append(reply["result"]["content"][0]["text"])
    proc.stdin.close()
    proc.wait()
    after = sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*"))
    with log.open("a") as handle:
        handle.write(json.dumps({{"after": after, "replies": replies,
                                 "listed": [t["name"] for t in listed]}}) + "\\n")
    answer('```json\\n["acme/ledger", "acme/billing"]\\n```')
elif scenario == "snoop":
    probe = json.loads(os.environ["FAKE_PROBE"])
    def read(path):
        try:
            return Path(path).read_text()
        except OSError:
            return None
    seen = {{
        "uid": os.getuid(),
        "tmp": sorted(os.listdir("/tmp")),
        "read": {{path: read(path) for path in probe["read"]}},
        "pids": sorted(int(p) for p in os.listdir("/proc") if p.isdigit()),
    }}
    with log.open("a") as handle:
        handle.write(json.dumps({{"seen": seen}}) + "\\n")
    answer('```json\\n["acme/ledger"]\\n```')
elif scenario in ("hang", "prose"):
    if scenario == "prose":
        answer("I could not decide.")
    time.sleep(30)
"""


@pytest.fixture
def fake_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    binary = tmp_path / "bin" / "fake-agent"
    binary.parent.mkdir()
    binary.write_text(FAKE_AGENT.format(python=sys.executable))
    binary.chmod(0o755)
    log = binary.parent / "fake.log"
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.setenv("FAKE_LOG", str(log))

    def make(scenario: str, **options) -> tuple[CursorAgentRanker, Path]:
        monkeypatch.setenv("FAKE_SCENARIO", scenario)
        ranker = CursorAgentRanker(
            binary=str(binary),
            trace_dir=tmp_path / "traces",
            **{
                "agent_home": tmp_path / "home",
                "timeout_s": 10,
                "follow_up_timeout_s": 10,
                **options,
            },
        )
        return ranker, log

    return make


def universe_entries() -> list[dict]:
    return [
        {"repo": r, "commit": f"{i:040x}", "snapshot_date": "2026-02-01", "role": role}
        for i, (r, role) in enumerate(
            zip(REPOS, ["gold", "gold", "ecosystem", "distractor", "distractor"], strict=True)
        )
    ]


def calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_parse_takes_last_array_naming_a_candidate() -> None:
    text = 'First ["acme/portal"] then\n```json\n["noise/one", "nope/x"]\n```'
    parsed = parse_ranking(text, REPOS)
    assert parsed.status == "ok"
    assert parsed.ranking == ["noise/one"]
    assert parsed.unknown == ["nope/x"]
    assert parse_ranking("```json\n[1, 2]\n```", REPOS).status == "no_candidates"
    assert parse_ranking("nothing here", REPOS).status == "no_json"
    objects = '[{"repo": "https://github.com/acme/ledger"}, {"repository": "acme__portal"}]'
    assert parse_ranking(objects, REPOS).ranking == ["acme/ledger", "acme/portal"]


def test_rank_parses_appends_and_hides_labels(
    fake_agent, make_universe, mini_cases: list[Case]
) -> None:
    ranker, log = fake_agent("answer")
    root = make_universe(universe_entries())
    case = mini_cases[0]
    [prediction] = harness.run(ranker, [case], root)

    assert prediction.ranking[:2] == ("acme/ledger", "acme/billing")
    assert sorted(prediction.ranking) == sorted(REPOS)

    [call] = calls(log)
    assert case.case_id not in call["prompt"] and case.prompt.strip() in call["prompt"]
    for leak in ("gold", "distractor", "2026-02-01", case.cutoff[:10], "0" * 40):
        assert leak not in call["prompt"]
        assert not any(leak in f for f in call["files"])
    assert "candidates.txt" in call["files"]
    assert "repos/acme__billing/README.md" in call["files"]
    assert "repos/acme__billing/paths.txt" in call["files"]
    assert "API contract files, and a list of every file path in the repository." in call["prompt"]
    assert "universe.jsonl" not in call["files"]
    assert not call["wrote"]
    assert not Path(call["workspace"]).exists()

    config = json.loads((Path(call["home"]) / ".cursor" / "cli-config.json").read_text())
    deny = config["permissions"]["deny"]
    assert {"Read(/home/**)", "Shell(*)", f"Read({Path(call['home']).resolve()}/**)"} <= set(deny)
    assert ".gitconfig" not in call["home_files"]

    trace = json.loads((ranker.trace_dir / f"{case.case_id}.json").read_text())
    assert trace["status"] == "ok" and trace["parse"] == "ok" and trace["fetch_level"] == "L4"
    assert trace["unknown"] == ["made/up"]
    assert trace["outside_paths"] == ["/home/someone/.cache/cases.jsonl"]
    assert trace["usage"]["inputTokens"] == 100
    assert trace["seconds"] < 10
    assert 0 <= trace["setup_seconds"] < 10
    events = (ranker.trace_dir / f"{case.case_id}.events.jsonl").read_text().splitlines()
    clock = [json.loads(line)["_run_s"] for line in events]
    assert clock and clock == sorted(clock) and clock[-1] <= trace["seconds"] + 0.01
    assert trace["first_event_s"] == clock[0]
    assert abs(trace["wall_seconds"] - trace["seconds"]) < 1


def test_workspace_links_packets_and_makes_them_read_only(make_universe, tmp_path) -> None:
    universe = Universe.load(make_universe(universe_entries())).view()
    build_workspace(tmp_path / "workspace", universe)
    candidate = universe.candidates[0]
    source = candidate.evidence_dir / "README.md"
    linked = tmp_path / "workspace" / "repos" / candidate.repo.replace("/", "__") / "README.md"
    assert linked.read_text() == source.read_text()
    assert linked.stat().st_ino == source.stat().st_ino
    assert not source.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def test_turn_budget_then_follow_up(fake_agent, make_universe) -> None:
    ranker, log = fake_agent("loop", max_turns=3)
    universe = Universe.load(make_universe(universe_entries())).view()
    ranking = ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    assert ranking[0] == "acme/portal"
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert (trace["status"], trace["follow_up_status"], trace["parse"]) == (
        "turn_budget",
        "ok",
        "ok",
    )
    assert trace["runs"][0]["tool_calls"] == 7 and trace["runs"][0]["tool_turns"] == 4
    assert not trace["usage_complete"]
    assert [c["resumed"] for c in calls(log)] == [False, True]


def _pb(*items: tuple[int, int | bytes]) -> bytes:
    def varint(n: int) -> bytes:
        out = bytearray()
        while True:
            out.append(n & 0x7F | (0x80 if n > 0x7F else 0))
            n >>= 7
            if not n:
                return bytes(out)

    return b"".join(
        varint(num << 3) + varint(v)
        if isinstance(v, int)
        else varint(num << 3 | 2) + varint(len(v)) + v
        for num, v in items
    )


def _root(used: int) -> str:
    return _pb((1, b"\x01" * 32), (5, _pb((1, used), (2, 200_000))), (9, b"file:///ws")).hex()


def test_stopped_run_records_context_from_chat_store(
    fake_agent, make_universe, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_root = _pb((5, _pb((1, 7), (2, 9)))).hex()
    blobs = {
        "main": [_root(1000), _root(1000), b'{"role":"user"}'.hex(), not_root, _root(1500)],
        "resumed": [_root(2600)],
    }
    monkeypatch.setenv("FAKE_BLOBS", json.dumps(blobs))
    ranker, _ = fake_agent("loop", max_turns=3, agent_home=None)
    universe = Universe.load(make_universe(universe_entries())).view()
    ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert [r["context"] for r in trace["runs"]] == [[1000, 1500], [2600]]
    assert trace["context_tokens"] == 5100


def test_retries_when_the_cli_cannot_start_a_session(fake_agent, make_universe) -> None:
    universe = Universe.load(make_universe(universe_entries())).view()
    query = Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00")
    ranker, log = fake_agent("offline2", retry_pauses=(0, 0))
    assert ranker.rank(query, universe)[0] == "acme/ledger"
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert (trace["status"], trace["runs"][0]["attempts"]) == ("ok", 3)

    log.unlink()
    ranker, _ = fake_agent("offline3", retry_pauses=(0, 0))
    ranker.rank(query, universe)
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert (trace["status"], trace["runs"][0]["attempts"]) == ("error", 3)
    assert "Failed to reach" in trace["errors"][0]


def test_retries_a_follow_up_that_stalls_before_starting(fake_agent, make_universe) -> None:
    ranker, log = fake_agent("stall", max_turns=3, follow_up_timeout_s=1, retry_pauses=(0,))
    universe = Universe.load(make_universe(universe_entries())).view()
    ranking = ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    assert ranking[0] == "acme/portal"
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert (trace["follow_up_status"], trace["parse"]) == ("ok", "ok")
    assert trace["runs"][1]["attempts"] == 2
    assert [c["resumed"] for c in calls(log)] == [False, True, True]


def test_timeout_then_follow_up(fake_agent, make_universe) -> None:
    ranker, _ = fake_agent("hang", timeout_s=1)
    universe = Universe.load(make_universe(universe_entries())).view()
    ranking = ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    assert ranking[0] == "acme/portal"
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert trace["status"] == "timeout" and trace["parse"] == "ok"


def test_unparseable_answer_falls_back_to_seeded_order(fake_agent, make_universe) -> None:
    ranker, _ = fake_agent("prose")
    universe = Universe.load(make_universe(universe_entries())).view()
    query = Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00")
    first = ranker.rank(query, universe)
    assert sorted(first) == sorted(REPOS)
    assert ranker.rank(query, universe) == first
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert trace["parse"] == "no_json" and trace["returned"] == 0


def test_default_home_is_fresh_per_case(fake_agent, make_universe) -> None:
    ranker, log = fake_agent("answer", agent_home=None)
    universe = Universe.load(make_universe(universe_entries())).view()
    for case_id in ("c1", "c2"):
        ranker.rank(Query(case_id, "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    homes = [c["home"] for c in calls(log)]
    assert len(set(homes)) == 2
    assert not any(Path(h).exists() for h in homes)


def test_names_only_workspace_is_closed_book(fake_agent, make_universe) -> None:
    ranker, log = fake_agent("answer", evidence="names")
    universe = Universe.load(make_universe(universe_entries())).view()
    ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    [call] = calls(log)
    assert call["files"] == ["candidates.txt"]
    assert "rely on what you already know" in call["prompt"]
    assert "repos/" not in call["prompt"] and "random IDs" not in call["prompt"]
    assert json.loads((ranker.trace_dir / "c1.json").read_text())["evidence"] == "names"
    with pytest.raises(ValueError, match="evidence"):
        fake_agent("answer", evidence="everything")


@pytest.mark.parametrize(
    ("anonymized", "says"), [("evidence", "still uses the real names"), ("everywhere", "request")]
)
def test_anonymized_prompt_says_names_are_ids(
    fake_agent, make_universe, anonymized: str, says: str
) -> None:
    ranker, log = fake_agent("answer", anonymized=anonymized)
    universe = Universe.load(make_universe(universe_entries())).view()
    ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    [call] = calls(log)
    assert "repos/<owner>__<name>/" in call["prompt"] and "random IDs" in call["prompt"]
    assert says in call["prompt"]
    assert json.loads((ranker.trace_dir / "c1.json").read_text())["anonymized"] == anonymized
    with pytest.raises(ValueError, match="anonymized"):
        fake_agent("answer", anonymized="partly")


def test_on_demand_opens_packets_within_budget(fake_agent, make_universe) -> None:
    ranker, log = fake_agent("open", evidence="on-demand", open_budget=1)
    universe = Universe.load(make_universe(universe_entries())).view()
    ranking = ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    assert ranking[:2] == ["acme/ledger", "acme/billing"]
    start, end = calls(log)
    assert start["files"] == ["candidates.txt", "packets"]
    assert "open at most 1 distinct repositories;" in start["prompt"]
    assert '`{"repo": "owner/name"}`' in start["prompt"]
    assert end["after"] == [
        "candidates.txt",
        "packets",
        "packets/acme__ledger",
        "packets/acme__ledger/README.md",
        "packets/acme__ledger/paths.txt",
    ]

    config = json.loads((Path(start["home"]) / ".cursor" / "cli-config.json").read_text())
    assert config["permissions"]["allow"] == ["Mcp(repo-discovery:open_repo)"]
    assert "Mcp(*:*)" not in config["permissions"]["deny"]
    assert "Shell(*)" in config["permissions"]["deny"]
    assert ".cursor/mcp.json" in start["home_files"]

    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert trace["open_budget"] == 1 and trace["opened"] == ["acme/ledger"]
    assert [(o["asked"], o["outcome"], o["turn"]) for o in trace["opens"]] == [
        ("acme/ledger", "opened", 2),
        ("made/up", "not_candidate", 2),
        ("ACME/Billing", "exhausted", 3),
    ]


def test_on_demand_zero_budget_says_so_and_other_modes_keep_mcp_denied(
    fake_agent, make_universe
) -> None:
    ranker, log = fake_agent("answer", evidence="on-demand", open_budget=0)
    universe = Universe.load(make_universe(universe_entries())).view()
    ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    [call] = calls(log)
    assert "open at most 0 distinct repositories, so rely on the names" in call["prompt"]
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert (trace["open_budget"], trace["opened"], trace["opens"]) == (0, [], [])

    log.unlink()
    ranker, log = fake_agent("answer")
    ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    [call] = calls(log)
    config = json.loads((Path(call["home"]) / ".cursor" / "cli-config.json").read_text())
    assert config["permissions"]["allow"] == [] and "Mcp(*:*)" in config["permissions"]["deny"]
    with pytest.raises(ValueError, match="open_budget"):
        fake_agent("answer", evidence="on-demand")
    with pytest.raises(ValueError, match="open_budget"):
        fake_agent("answer", open_budget=5)


def test_search_tool_ranks_full_packets(
    fake_agent, make_universe, monkeypatch: pytest.MonkeyPatch
) -> None:
    ranker, log = fake_agent("search", search="bm25f")
    universe = Universe.load(make_universe(universe_entries())).view()
    ranking = ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    assert ranking[:2] == ["acme/ledger", "acme/billing"]
    start, end = calls(log)
    assert "repos/acme__billing/README.md" in start["files"]
    assert "The `search_repos` tool" in start["prompt"]
    assert end["replies"][0].startswith("Top 1 of 5 candidates for 'billing'")
    assert "1. acme/billing" in end["replies"][0]
    assert end["replies"][1] == "Give a non-empty `query` string."

    home = Path(start["home"])
    config = json.loads((home / ".cursor" / "cli-config.json").read_text())
    assert config["permissions"]["allow"] == ["Mcp(repo-discovery:search_repos)"]
    assert "Mcp(*:*)" not in config["permissions"]["deny"]
    server = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"]
    env = server["repo-discovery"]["env"]
    assert env["HF_HUB_OFFLINE"] == "1" and env["PYTHONUSERBASE"] == site.getuserbase()
    assert env["OMP_NUM_THREADS"] == "1"

    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert trace["isolation"] == ("namespaces" if NAMESPACES is None else "none")
    assert trace["search"] == "bm25f" and trace["opens"] == []
    assert [(s["query"], s["outcome"], s["results"], s["turn"]) for s in trace["searches"]] == [
        ("billing", "ok", ["acme/billing"], 2),
        ("", "bad_query", [], 3),
    ]
    assert trace["searches"][0]["index"] == "built"
    assert trace["index_warm_s"] is None

    index_dir = home.parent / "indexes"
    index_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="prebuilt search index"):
        fake_agent("search", search="bm25f", search_index=index_dir)[0].rank(
            Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe
        )
    BM25Ranker().index(universe).save(index_dir / "c1.sqlite", candidate_digest(universe))
    log.unlink()
    warmed = []
    warm = search_repos.warm

    def record_warm(index: Path) -> float:
        warmed.append((index, log.exists()))
        return warm(index)

    monkeypatch.setattr(search_repos, "warm", record_warm)
    ranker, log = fake_agent("search", search="bm25f", search_index=index_dir)
    ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    assert calls(log)[1]["replies"][0] == end["replies"][0]
    assert warmed == [((index_dir / "c1.sqlite").resolve(), False)]
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert trace["searches"][0]["index"] == "prebuilt"
    assert trace["index_warm_s"] >= 0
    with pytest.raises(ValueError, match="search_index"):
        fake_agent("answer", search_index=index_dir)
    with pytest.raises(ValueError, match="search"):
        fake_agent("answer", search="grep")
    with pytest.raises(ValueError, match="search"):
        fake_agent("answer", search="bm25f", evidence="names")


def mcp_event(subtype: str, call_id: str, turn: str, at: float, query: str, result=None) -> dict:
    call = {
        "args": {
            "name": "repo-discovery-search_repos",
            "args": {"query": query},
            "toolName": "search_repos",
        }
    }
    if result is not None:
        call["result"] = result
    return {
        "type": "tool_call",
        "subtype": subtype,
        "call_id": call_id,
        "model_call_id": turn,
        "_run_s": at,
        "tool_call": {"mcpToolCall": call},
    }


def test_tool_calls_say_what_the_agent_got(tmp_path: Path) -> None:
    timed_out = {"error": {"error": "MCP error -32001: Request timed out"}}
    answered = {"success": {"content": [], "isError": False}}
    events = [
        mcp_event("started", "a", "m1", 10.0, "billing"),
        mcp_event("started", "b", "m1", 10.1, "ledger"),
        mcp_event("started", "c", "m1", 10.2, "billing"),
        mcp_event("completed", "a", "m1", 70.0, "billing", timed_out),
        mcp_event("completed", "b", "m1", 10.5, "ledger", {"rejected": {"reason": "no"}}),
        mcp_event("completed", "c", "m1", 95.5, "billing", answered),
        mcp_event("started", "d", "m2", 96.0, "portal"),
    ]
    log = tmp_path / "search.jsonl"
    rows = [{"t": 1.0, "query": q, "outcome": "ok"} for q in ("billing", "billing", "portal")]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = tool_calls(events, log, "search_repos", "query")
    assert [(e["query"], e["turn"], e["cli"], e["cli_s"]) for e in got] == [
        ("billing", 1, "timeout", 60.0),
        ("billing", 1, "ok", 85.3),
        ("portal", 2, None, None),
    ]
    assert all("t" not in e for e in got)


def test_search_guides_l3_opens_on_demand(
    fake_agent, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPO_DISCOVERY_CACHE", str(tmp_path / "cache"))
    ranker, log = fake_agent(
        "search-open", evidence="on-demand", open_budget=1, search="bm25f", open_level="L3"
    )
    universe = Universe.load(EVIDENCE_UNIVERSE).view()
    ranking = ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    assert ranking[:2] == ["acme/ledger", "acme/billing"]
    start, end = calls(log)
    assert start["files"] == ["candidates.txt", "packets"]
    prompt = start["prompt"]
    assert "`packets/<owner>__<name>/`: its README and package manifest files only." in prompt
    assert "search index over every candidate's full evidence packet (README and docs" in prompt
    assert end["listed"] == ["open_repo", "search_repos"]
    assert "1. acme/billing" in end["replies"][0]
    assert end["replies"][2].startswith("Budget exhausted: 1 of 1")
    assert end["after"] == [
        "candidates.txt",
        "packets",
        "packets/acme__billing",
        "packets/acme__billing/README.md",
        "packets/acme__billing/package.json",
    ]

    home = Path(start["home"])
    config = json.loads((home / ".cursor" / "cli-config.json").read_text())
    assert config["permissions"]["allow"] == [
        "Mcp(repo-discovery:open_repo)",
        "Mcp(repo-discovery:search_repos)",
    ]
    [server] = json.loads((home / ".cursor" / "mcp.json").read_text())["mcpServers"].values()
    assert server["args"][:2] == ["-m", "repo_discovery.rankers.mcp_tool"]
    assert server["env"]["HF_HUB_OFFLINE"] == "1"

    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    assert (trace["open_level"], trace["search"]) == ("L3", "bm25f")
    assert trace["opened"] == ["acme/billing"]
    assert [(o["asked"], o["outcome"], o["turn"]) for o in trace["opens"]] == [
        ("acme/billing", "opened", 3),
        ("acme/ledger", "exhausted", 3),
    ]
    assert trace["opens"][0]["files"] == 2 and trace["opens"][0]["bytes"] > 0
    assert [(s["query"], s["turn"]) for s in trace["searches"]] == [("billing", 2)]

    with pytest.raises(ValueError, match="open_level"):
        fake_agent("answer", open_level="L3")
    with pytest.raises(ValueError, match="open_level"):
        fake_agent("answer", evidence="on-demand", open_budget=1, open_level="L1")


@pytest.mark.parametrize(
    ("level", "says", "billing"),
    [
        ("L2", "`repos/<owner>__<name>/`: its README only.", ["README.md"]),
        ("L3", "its README and package manifest files only.", ["README.md", "package.json"]),
    ],
)
def test_restricted_packets_say_what_they_hold(
    fake_agent, tmp_path: Path, level: str, says: str, billing: list[str]
) -> None:
    corpus = tmp_path / "corpus"
    shutil.copytree(EVIDENCE_UNIVERSE / "repos", corpus / "repos")
    listing = corpus / "universes" / "u" / "c1"
    listing.mkdir(parents=True)
    shutil.copyfile(EVIDENCE_UNIVERSE / "universe.jsonl", listing / "universe.jsonl")
    (listing / "repos").symlink_to("../../../repos", target_is_directory=True)
    restrict(corpus, level, tmp_path / level)
    universe = Universe.load(tmp_path / level / "universes" / "u" / "c1").view()
    query = Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00")

    ranker, log = fake_agent("answer")
    ranker.rank(query, universe)
    [call] = calls(log)
    assert says in call["prompt"] and "every file path" not in call["prompt"]
    prefix = "repos/acme__billing/"
    assert [f[len(prefix) :] for f in call["files"] if f.startswith(prefix)] == billing
    assert not any(f.endswith("paths.txt") for f in call["files"])
    assert json.loads((ranker.trace_dir / "c1.json").read_text())["fetch_level"] == level
    for options in ({"search": "bm25f"}, {"evidence": "on-demand", "open_budget": 1}):
        with pytest.raises(ValueError, match="full packets"):
            fake_agent("answer", **options)[0].rank(query, universe)

    restrict(corpus, "L0", tmp_path / "L0")
    names_only = Universe.load(tmp_path / "L0" / "universes" / "u" / "c1").view()
    with pytest.raises(ValueError, match="no packets"):
        fake_agent("answer")[0].rank(query, names_only)


def test_home_template_is_warmed_once_and_copied_without_chats(
    fake_agent, make_universe, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ranker, log = fake_agent("warm")
    template = tmp_path / "template"
    assert warm_home(template, binary=ranker.binary)
    assert sorted(p.name for p in (template / ".cursor").iterdir()) == [
        "cli-config.json",
        "plugins",
    ]
    assert not warm_home(template, binary=ranker.binary)

    ranker, log = fake_agent("answer", agent_home=None, home_template=template)
    universe = Universe.load(make_universe(universe_entries())).view()
    (template / ".cursor" / "chats" / "old").mkdir(parents=True)
    ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    [call] = calls(log)
    assert ".cursor/plugins/cache" in call["home_files"]
    assert not any(f.startswith(".cursor/chats/old") for f in call["home_files"])
    assert ".gitconfig" in call["home_files"]
    config = json.loads((template / ".cursor" / "cli-config.json").read_text())
    assert "Shell(*)" in config["permissions"]["deny"]


def test_scratch_home_fails_plugin_fetches_without_the_network(tmp_path: Path):
    (tmp_path / ".gitconfig").write_text(NO_PLUGIN_FETCH)
    started = time.monotonic()
    fetch = subprocess.run(
        ["git", "ls-remote", "git@github.com:cursor/plugins.git"],
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"], "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert fetch.returncode != 0 and "/dev/null/cursor/plugins.git" in fetch.stderr
    assert time.monotonic() - started < 5


NAMESPACES = sandbox.unavailable()
needs_namespaces = pytest.mark.skipif(
    NAMESPACES is not None and not os.environ.get("REQUIRE_NAMESPACES"),
    reason=f"no isolation here: {NAMESPACES}",
)


@pytest.fixture
def other_case():
    """What a concurrent case leaves where this one could look: its sandbox in /tmp with a
    file naming its gold, files in the other temp dirs, and one in the user's home."""
    sibling = Path(tempfile.mkdtemp(prefix="v5-"))
    (sibling / "workspace").mkdir()
    planted = [
        sibling / "workspace" / "gold.txt",
        Path("/dev/shm") / f"{sibling.name}.txt",
        Path("/var/tmp") / f"{sibling.name}.txt",
        Path.home() / f".{sibling.name}.txt",
    ]
    for path in planted:
        path.write_text("acme/ledger")
    yield sibling, planted
    shutil.rmtree(sibling)
    for path in planted[1:]:
        path.unlink(missing_ok=True)


@needs_namespaces
@pytest.mark.parametrize("isolation", ["required", "off"])
def test_agent_sees_no_other_case(
    isolation, fake_agent, make_universe, other_case, monkeypatch: pytest.MonkeyPatch
) -> None:
    sibling, planted = other_case
    root = make_universe(universe_entries())
    probe = {"read": [str(p) for p in planted] + [str(root / "universe.jsonl")]}
    monkeypatch.setenv("FAKE_PROBE", json.dumps(probe))
    ranker, log = fake_agent("snoop", search="bm25f", isolation=isolation, agent_home=None)
    query = Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00")
    assert ranker.rank(query, Universe.load(root).view())[0] == "acme/ledger"

    start, snoop = calls(log)
    seen = snoop["seen"]
    *others, universe = seen["read"].values()
    leaks = {
        "another case's sandbox": sibling.name in seen["tmp"],
        "another case's files": any(others),
        "the universe's roles": '"gold"' in (universe or ""),
        "other processes": os.getpid() in seen["pids"],
    }
    trace = json.loads((ranker.trace_dir / "c1.json").read_text())
    if isolation == "off":
        assert all(leaks.values()) and trace["isolation"] == "none"
        return
    assert not any(leaks.values()), leaks
    assert trace["isolation"] == "namespaces" and seen["uid"] == os.getuid()
    assert "candidates.txt" in start["files"]
    masked = [json.loads(line) for line in universe.splitlines()]
    assert [e["repo"] for e in masked] == REPOS
    assert {e["role"] for e in masked} == {"distractor"}
    assert '"gold"' in (root / "universe.jsonl").read_text()


def test_isolation_is_required_or_recorded(
    fake_agent, make_universe, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="isolation"):
        fake_agent("answer", isolation="maybe")
    monkeypatch.setattr(sandbox, "unavailable", lambda: "no user namespaces")
    with pytest.raises(RuntimeError, match="isolation is required, but no user namespaces"):
        fake_agent("answer", isolation="required")
    ranker, _ = fake_agent("answer")
    universe = Universe.load(make_universe(universe_entries())).view()
    ranker.rank(Query("c1", "Fix billing.", "2026-03-01T00:00:00+00:00"), universe)
    assert json.loads((ranker.trace_dir / "c1.json").read_text())["isolation"] == "none"


def test_needs_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CURSOR_API_KEY"):
        CursorAgentRanker(binary="unused")


def test_binary_lookup_reports_missing() -> None:
    with pytest.raises(FileNotFoundError, match="install"):
        find_binary("definitely-not-a-cursor-agent")
