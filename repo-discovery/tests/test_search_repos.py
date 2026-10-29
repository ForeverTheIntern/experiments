from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from repo_discovery.contract import Universe
from repo_discovery.embeddings import ENCODER_ID, store_path
from repo_discovery.rankers import dense
from repo_discovery.rankers.bm25 import BM25Ranker, analyze, candidate_digest
from repo_discovery.rankers.mcp_tool import handle
from repo_discovery.rankers.search_repos import MAX_K, SNIPPET_WORDS, SearchRepos, snippet

SRC = Path(__file__).resolve().parents[1] / "src"
EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"
CUTOFF = "2026-03-01T00:00:00+00:00"


class BagEncoder:
    id = "test/bag@0"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(256, dtype=np.float32)
        for token in analyze(text):
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % 256] += 1.0
        return vector / max(float(np.linalg.norm(vector)), 1e-9)

    def documents(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([self._vector(t) for t in texts])

    def query(self, text: str) -> np.ndarray:
        self.queries.append(text)
        return self._vector(text)


def entries(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_bm25f_lists_matching_candidates_with_snippets(tmp_path: Path) -> None:
    tool = SearchRepos(EVIDENCE_UNIVERSE, "bm25f", "c1", CUTOFF, tmp_path / "log.jsonl")
    text, error = tool.call("ledger journal")
    assert not error
    assert text.splitlines() == [
        "Top 1 of 10 candidates for 'ledger journal' (only 1 matches a query term):",
        "1. acme/ledger: Double-entry ledger for Acme billing",
    ]
    text, error = tool.call("billing invoices", 2)
    assert not error and text.splitlines()[0] == "Top 2 of 10 candidates for 'billing invoices':"
    assert text.splitlines()[1].startswith("1. acme/billing: Invoices, tax and refunds")
    assert tool.call("zzzqqq") == ("No candidate matches any term of 'zzzqqq'.", False)
    assert tool.call("  ") == ("Give a non-empty `query` string.", True)
    assert tool.call(None)[1]
    tool.call("acme", 500)
    tool.call("acme", "lots")

    logged = entries(tool.log)
    assert [e["outcome"] for e in logged] == [
        "ok",
        "ok",
        "empty",
        "bad_query",
        "bad_query",
        "ok",
        "ok",
    ]
    assert logged[0]["results"] == ["acme/ledger"] and logged[0]["query"] == "ledger journal"
    assert [e["k"] for e in logged] == [20, 2, 20, 20, 20, MAX_K, 20]
    assert {e["index"] for e in logged} == {"built"}
    first = logged[0]
    assert first["index_ready_s"] >= 0 and first["wait_s"] >= 0 and first["latency_s"] >= 0
    assert first["received"] <= first["t"]


def test_prebuilt_index_answers_alike(tmp_path: Path) -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    index = tmp_path / "c1.sqlite"
    BM25Ranker().index(view).save(index, candidate_digest(view))
    built = SearchRepos(EVIDENCE_UNIVERSE, "bm25f", "c1", CUTOFF, tmp_path / "built.jsonl")
    prebuilt = SearchRepos(
        EVIDENCE_UNIVERSE, "bm25f", "c1", CUTOFF, tmp_path / "prebuilt.jsonl", index
    )
    for query in ("ledger journal", "billing invoices", "zzzqqq"):
        assert prebuilt.call(query) == built.call(query)
    assert {e["index"] for e in entries(prebuilt.log)} == {"prebuilt"}

    other = tmp_path / "other.sqlite"
    copy = Universe.load(shutil.copytree(EVIDENCE_UNIVERSE, tmp_path / "copy")).view()
    BM25Ranker().index(copy).save(other, candidate_digest(copy))
    stale = SearchRepos(EVIDENCE_UNIVERSE, "bm25f", "c1", CUTOFF, tmp_path / "stale.jsonl", other)
    text, error = stale.call("ledger")
    assert error and "other fields or candidates" in text
    assert entries(stale.log)[0]["outcome"] == "error"


def test_hybrid_ranks_every_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    encoder = BagEncoder()
    monkeypatch.setattr(dense, "BgeSmallEncoder", lambda: encoder)
    monkeypatch.setenv("REPO_DISCOVERY_CACHE", str(tmp_path / "cache"))
    tool = SearchRepos(EVIDENCE_UNIVERSE, "hybrid-rrf", "c1", CUTOFF)
    text, error = tool.call("ledger journal", 5)
    assert not error
    lines = text.splitlines()
    assert lines[0] == "Top 5 of 10 candidates for 'ledger journal':"
    assert lines[1].startswith("1. acme/ledger:") and len(lines) == 6
    assert len(tool.search("zzzqqq")) == 10
    assert store_path(ENCODER_ID).is_file()
    assert encoder.queries == ["warm up", "ledger journal", "zzzqqq"]


def test_hybrid_reads_a_prebuilt_keyword_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dense, "BgeSmallEncoder", BagEncoder)
    monkeypatch.setenv("REPO_DISCOVERY_CACHE", str(tmp_path / "cache"))
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    index = tmp_path / "c1.sqlite"
    BM25Ranker().index(view).save(index, candidate_digest(view))
    built = SearchRepos(EVIDENCE_UNIVERSE, "hybrid-rrf", "c1", CUTOFF)
    prebuilt = SearchRepos(EVIDENCE_UNIVERSE, "hybrid-rrf", "c1", CUTOFF, index=index)
    for query in ("ledger journal", "vaccine lots"):
        assert prebuilt.search(query) == built.search(query)


def test_snippet_falls_back_to_readme_prose(tmp_path: Path) -> None:
    packet = tmp_path / "packet"
    packet.mkdir()
    prose = " ".join(f"word{i}" for i in range(50))
    (packet / "README.md").write_text(f"# Title\n\n```\ncode\n```\n\n{prose}\n")
    (packet / "paths.txt").write_text("README.md\n")
    assert snippet("a/b", packet) == " ".join(f"word{i}" for i in range(SNIPPET_WORDS))
    (packet / "package.json").write_text(json.dumps({"description": "x" * 300}))
    (packet / "paths.txt").write_text("README.md\npackage.json\n")
    text = snippet("a/b", packet)
    assert len(text) == 240 and text.endswith("…")


def test_lists_one_tool_and_serves_over_stdio(tmp_path: Path) -> None:
    tool = SearchRepos(EVIDENCE_UNIVERSE, "bm25f", "c1", CUTOFF)
    [listed] = handle(tool, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    assert listed["name"] == "search_repos" and listed["inputSchema"]["required"] == ["query"]

    config = tmp_path / "config.json"
    log = tmp_path / "log.jsonl"
    config.write_text(
        json.dumps(
            {
                "universe": str(EVIDENCE_UNIVERSE),
                "backend": "bm25f",
                "case_id": "c1",
                "cutoff": CUTOFF,
                "log": str(log),
            }
        )
    )
    messages = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_repos", "arguments": {"query": "ledger", "k": 3}},
        },
    ]
    done = subprocess.run(
        [sys.executable, "-m", "repo_discovery.rankers.search_repos", str(config)],
        input="".join(json.dumps(m) + "\n" for m in messages),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)},
        timeout=30,
        check=True,
    )
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [r["id"] for r in replies] == [0, 1]
    result = replies[1]["result"]
    assert not result["isError"] and "1. acme/ledger" in result["content"][0]["text"]
    assert [e["outcome"] for e in entries(log)] == ["ok"]


def test_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="backend"):
        SearchRepos(EVIDENCE_UNIVERSE, "grep", "c1", CUTOFF)
