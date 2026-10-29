"""The ``search_repos`` tool (method c2): ranked search over one case's universe, served
as a stdio MCP server, one per case.

The agent keeps the full packets in its workspace, as in V5, and gets this one extra tool.
A call ranks the candidates against the agent's own query with ``bm25f`` or
``hybrid-rrf`` and returns the best ``k`` (default 20, at most 50), best first, each with
a short snippet: its manifest descriptions, or else the start of its README's prose.
Calls are free. ``bm25f`` lists only candidates that match a query term, so it can return
fewer than ``k``; the hybrid's dense half ranks every candidate.

The server reads a JSON config written by the ranker: ``universe`` (the case's universe
directory), ``backend``, ``case_id``, ``cutoff`` and ``log``. One worker thread builds
the index as the server starts, so the handshake isn't held up, and then runs every search
(SQLite connections stay in the thread that made them). Each call appends one JSON line to
``log``. Run it as ``python -m repo_discovery.rankers.search_repos CONFIG``.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar

from repo_discovery.cards import prose_paragraphs
from repo_discovery.cases import Query
from repo_discovery.contract import Universe, UniverseView
from repo_discovery.evidence import read_evidence
from repo_discovery.rankers.bm25 import BM25Ranker
from repo_discovery.rankers.mcp_tool import serve

TOOL = "search_repos"
BACKENDS = ("bm25f", "hybrid-rrf")
DEFAULT_K = 20
MAX_K = 50
SNIPPET_WORDS = 30
SNIPPET_CHARS = 240
DESCRIPTION = (
    "Search the candidate repositories. Ranks every repository in candidates.txt against "
    "the query with a search index over their evidence packets and returns the best k "
    f"(default {DEFAULT_K}, at most {MAX_K}), best first, each with a short snippet from "
    "its manifests or README. Calls are free; search as often as you like."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "What to look for, in words or identifiers."},
        "k": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_K,
            "description": f"How many results to return (default {DEFAULT_K}).",
        },
    },
    "required": ["query"],
}


def snippet(repo: str, evidence_dir: Path) -> str:
    evidence = read_evidence(repo, evidence_dir)
    text = " ".join(evidence.descriptions)
    if not text:
        words: list[str] = []
        for paragraph in prose_paragraphs(evidence.readme):
            words.extend(paragraph.split())
            if len(words) >= SNIPPET_WORDS:
                break
        text = " ".join(words[:SNIPPET_WORDS])
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1].rstrip() + "…"


def _k(value: object) -> int:
    try:
        k = int(value) if value is not None else DEFAULT_K
    except (TypeError, ValueError):
        k = DEFAULT_K
    return max(1, min(MAX_K, k))


class SearchRepos:
    name: ClassVar[str] = TOOL
    description: ClassVar[str] = DESCRIPTION
    schema: ClassVar[dict] = SCHEMA

    def __init__(
        self, universe: Path, backend: str, case_id: str, cutoff: str, log: Path | None = None
    ) -> None:
        if backend not in BACKENDS:
            raise ValueError(f"backend must be one of {BACKENDS}, got {backend!r}")
        self.universe, self.backend, self.log = universe, backend, log
        self.case_id, self.cutoff = case_id, cutoff
        self._snippets: dict[str, str] = {}
        self._worker = ThreadPoolExecutor(max_workers=1)
        self._built = self._worker.submit(self._build)

    @classmethod
    def from_config(cls, path: Path) -> SearchRepos:
        config = json.loads(path.read_text())
        return cls(
            Path(config["universe"]),
            config["backend"],
            config["case_id"],
            config["cutoff"],
            Path(config["log"]) if config.get("log") else None,
        )

    def wait(self) -> None:
        """Block until the index is built; raises what building it raised."""
        self._built.result()

    def _build(self) -> None:
        self.view: UniverseView = Universe.load(self.universe).view()
        self.dirs = {c.repo: c.evidence_dir for c in self.view.candidates}
        if self.backend == "bm25f":
            self.ranker = BM25Ranker()
            self.ranker.index(self.view)
        else:
            from repo_discovery.rankers.dense import HybridRanker

            self.ranker = HybridRanker()
            self.ranker.keyword.index(self.view)
            self.ranker.dense.vectors(self.view)
            self.ranker.dense.encoder.query("warm up")

    def _search(self, text: str) -> list[str]:
        query = Query(self.case_id, text, self.cutoff)
        if self.backend == "bm25f":
            return [repo for repo, _ in self.ranker.search(query, self.view)]
        return self.ranker.rank(query, self.view)

    def search(self, text: str) -> list[str]:
        self.wait()
        return self._worker.submit(self._search, text).result()

    def respond(self, arguments: dict) -> tuple[str, bool]:
        return self.call(arguments.get("query"), arguments.get("k"))

    def call(self, query: object, k: object = None) -> tuple[str, bool]:
        """Search for ``query``; returns the tool's text and whether it is an error."""
        size = _k(k)
        results: list[str] = []
        if not isinstance(query, str) or not query.strip():
            outcome, text = "bad_query", "Give a non-empty `query` string."
        else:
            try:
                results = self.search(query)[:size]
            except Exception as error:  # the agent sees it rather than a dead server
                outcome, text = "error", f"Search failed: {type(error).__name__}: {error}"
            else:
                outcome = "ok" if results else "empty"
                text = self._format(query, results, size)
        self._log(query, size, outcome, results)
        return text, outcome in ("bad_query", "error")

    def _format(self, query: str, results: list[str], k: int) -> str:
        n = len(self.view.candidates)
        if not results:
            return f"No candidate matches any term of {query!r}."
        header = f"Top {len(results)} of {n} candidates for {query!r}"
        if len(results) < min(k, n):
            verb = "matches" if len(results) == 1 else "match"
            header += f" (only {len(results)} {verb} a query term)"
        lines = [header + ":"]
        for rank, repo in enumerate(results, start=1):
            if repo not in self._snippets:
                self._snippets[repo] = snippet(repo, self.dirs[repo])
            lines.append(
                f"{rank}. {repo}" + (f": {self._snippets[repo]}" if self._snippets[repo] else "")
            )
        return "\n".join(lines)

    def _log(self, query: object, k: int, outcome: str, results: list[str]) -> None:
        if self.log is None:
            return
        entry = {"t": time.time(), "query": query, "k": k, "outcome": outcome, "results": results}
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    serve(SearchRepos.from_config(Path(sys.argv[1])))
