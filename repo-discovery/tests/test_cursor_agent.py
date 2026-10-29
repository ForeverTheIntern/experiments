from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from repo_discovery import harness
from repo_discovery.cases import Case, Query
from repo_discovery.contract import Universe
from repo_discovery.rankers.cursor_agent import (
    CursorAgentRanker,
    find_binary,
    parse_ranking,
    warm_home,
)

REPOS = ["acme/billing", "acme/ledger", "acme/portal", "noise/one", "noise/two"]

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
elif scenario == "open":
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
    asks = [("m1", "acme/ledger"), ("m1", "made/up"), ("m2", "ACME/Billing")]
    for i, (turn, repo) in enumerate(asks):
        emit({{"type": "tool_call", "subtype": "started", "model_call_id": turn,
              "tool_call": {{"mcpToolCall": {{"args": {{"name": "repo-discovery-open_repo",
              "args": {{"repo": repo}}, "toolName": "open_repo"}}}}}}}})
        rpc(i + 1, "tools/call", {{"name": "open_repo", "arguments": {{"repo": repo}}}})
    proc.stdin.close()
    proc.wait()
    after = sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*"))
    with log.open("a") as handle:
        handle.write(json.dumps({{"after": after}}) + "\\n")
    answer('```json\\n["acme/ledger", "acme/billing"]\\n```')
elif scenario in ("hang", "prose"):
    if scenario == "prose":
        answer("I could not decide.")
    time.sleep(30)
"""


@pytest.fixture
def fake_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    binary = tmp_path / "fake-agent"
    binary.write_text(FAKE_AGENT.format(python=sys.executable))
    binary.chmod(0o755)
    log = tmp_path / "fake.log"
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
    assert "universe.jsonl" not in call["files"]
    assert not call["wrote"]
    assert not Path(call["workspace"]).exists()

    config = json.loads((Path(call["home"]) / ".cursor" / "cli-config.json").read_text())
    deny = config["permissions"]["deny"]
    assert {"Read(/home/**)", "Shell(*)", f"Read({Path(call['home']).resolve()}/**)"} <= set(deny)

    trace = json.loads((ranker.trace_dir / f"{case.case_id}.json").read_text())
    assert trace["status"] == "ok" and trace["parse"] == "ok"
    assert trace["unknown"] == ["made/up"]
    assert trace["outside_paths"] == ["/home/someone/.cache/cases.jsonl"]
    assert trace["usage"]["inputTokens"] == 100
    assert trace["seconds"] < 10


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
    config = json.loads((template / ".cursor" / "cli-config.json").read_text())
    assert "Shell(*)" in config["permissions"]["deny"]


def test_needs_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CURSOR_API_KEY"):
        CursorAgentRanker(binary="unused")


def test_binary_lookup_reports_missing() -> None:
    with pytest.raises(FileNotFoundError, match="install"):
        find_binary("definitely-not-a-cursor-agent")
