from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo_discovery.rankers.open_repo import OpenRepo, handle

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture
def tool(tmp_path: Path) -> OpenRepo:
    candidates = {}
    for repo in ("acme/billing", "acme/ledger", "noise/one"):
        packet = tmp_path / "corpus" / repo.replace("/", "__")
        (packet / "docs").mkdir(parents=True)
        (packet / "README.md").write_text(f"# {repo}\n")
        (packet / "docs" / "guide.md").write_text("guide\n")
        candidates[repo] = packet
    workspace = tmp_path / "workspace"
    (workspace / "packets").mkdir(parents=True)
    (workspace / "packets").chmod(0o555)
    return OpenRepo(workspace, 2, candidates, tmp_path / "log.jsonl")


def outcomes(log: Path) -> list[str]:
    return [json.loads(line)["outcome"] for line in log.read_text().splitlines()]


def test_opens_within_budget_and_charges_only_first_opens(tool: OpenRepo) -> None:
    text, error = tool.call("ACME/Ledger")
    assert not error
    assert text.splitlines() == [
        "Opened. Budget left: 1 of 2.",
        "2 files:",
        "packets/acme__ledger/README.md",
        "packets/acme__ledger/docs/guide.md",
    ]
    packet = tool.workspace / "packets" / "acme__ledger"
    assert (packet / "README.md").read_text() == "# acme/ledger\n"
    assert not os.access(packet / "README.md", os.W_OK) and not os.access(packet, os.W_OK)
    assert not os.access(tool.workspace / "packets", os.W_OK)

    assert tool.call("acme/ledger")[0].startswith("Already open, at no cost.")
    assert tool.call("made/up") == ("'made/up' is not in candidates.txt.", True)
    assert tool.call(None)[1]
    assert tool.call("`acme__billing`")[0].startswith("Opened. Budget left: 0 of 2.")
    text, error = tool.call("noise/one")
    assert error and text.startswith("Budget exhausted: 2 of 2")
    assert not (tool.workspace / "packets" / "noise__one").exists()
    assert tool.call("acme/billing")[0].startswith("Already open")
    assert tool.opened == ["acme/ledger", "acme/billing"]
    assert outcomes(tool.log) == [
        "opened",
        "reopened",
        "not_candidate",
        "not_candidate",
        "opened",
        "exhausted",
        "reopened",
    ]


def test_zero_budget_opens_nothing(tool: OpenRepo) -> None:
    tool.budget = 0
    text, error = tool.call("acme/ledger")
    assert error and text.startswith("Budget exhausted: 0 of 0")
    assert list((tool.workspace / "packets").iterdir()) == []


def test_restart_replays_the_log(tool: OpenRepo) -> None:
    tool.call("acme/ledger")
    tool.call("acme/billing")
    again = OpenRepo(tool.workspace, tool.budget, tool.candidates, tool.log)
    assert again.opened == ["acme/ledger", "acme/billing"]
    assert again.call("noise/one")[0].startswith("Budget exhausted")
    assert again.call("acme/ledger")[0].startswith("Already open")


def test_json_rpc(tool: OpenRepo) -> None:
    init = handle(
        tool,
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
    )
    assert init["result"]["protocolVersion"] == "2024-11-05"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert handle(tool, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    [listed] = handle(tool, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert listed["name"] == "open_repo" and listed["inputSchema"]["required"] == ["repo"]
    call = {"name": "open_repo", "arguments": {"repo": "acme/ledger"}}
    result = handle(tool, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": call})[
        "result"
    ]
    assert (
        result["isError"] is False
        and "packets/acme__ledger/README.md" in result["content"][0]["text"]
    )
    error = handle(tool, {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert error["error"]["code"] == -32601


def test_serves_over_stdio(tool: OpenRepo, tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "workspace": str(tool.workspace),
                "budget": 1,
                "log": str(tool.log),
                "candidates": {r: str(p) for r, p in tool.candidates.items()},
            }
        )
    )
    messages = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        "not json",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "open_repo", "arguments": {"repo": "noise/one"}},
        },
    ]
    stdin = "\n".join(m if isinstance(m, str) else json.dumps(m) for m in messages) + "\n"
    done = subprocess.run(
        [sys.executable, "-m", "repo_discovery.rankers.open_repo", str(config)],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        timeout=30,
        check=True,
    )
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [r["id"] for r in replies] == [0, 1]
    assert replies[1]["result"]["content"][0]["text"].startswith("Opened. Budget left: 0 of 1.")
    assert (tool.workspace / "packets" / "noise__one" / "README.md").is_file()
