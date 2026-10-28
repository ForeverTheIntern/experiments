"""The no-agent control for on-demand fetching (L5): open the top N of a names-only ranking.

``bm25f-open<N>`` ranks a universe with ``bm25f`` over repository names alone (fetch
level L0), opens the top N repositories' full packets (L4), reranks just those N with
``bm25f`` over their packets, and keeps the rest in names-only order. It opens what an
agent with budget N may open, chosen by a name ranker instead of the agent. Reranking
only reorders the opened set, so CompleteRecall@K for K >= N equals names-only
``bm25f``'s; the reranking shows below N.
"""

from __future__ import annotations

from repo_discovery.cases import Query
from repo_discovery.contract import Candidate, UniverseView
from repo_discovery.evidence import Evidence
from repo_discovery.fetch_levels import FULL, fetch_level
from repo_discovery.rankers.bm25 import BM25Ranker

BUDGETS: tuple[int, ...] = (0, 5, 10, 20, 50)


class NameBM25Ranker(BM25Ranker):
    """``bm25f`` over names only: owner, owner stems and repository name."""

    name = "bm25f-names"

    def _document(self, candidate: Candidate) -> dict[str, str]:
        return self.document(Evidence(candidate.repo))


class OpenTopRanker:
    """Names-only ``bm25f``, with its top ``budget`` reranked by ``bm25f`` over full packets."""

    def __init__(self, seed: int = 0, budget: int = 10) -> None:
        if budget < 0:
            raise ValueError("budget must be at least 0")
        self.budget = budget
        self.name = f"bm25f-open{budget}"
        self.names = NameBM25Ranker(seed=seed)
        self.full = BM25Ranker(seed=seed)

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        ranking = self.names.rank(query, universe)
        opened = set(ranking[: self.budget])
        if not opened:
            return ranking
        view = UniverseView(
            universe.root, tuple(c for c in universe.candidates if c.repo in opened)
        )
        if any(fetch_level(c.evidence_dir) != FULL for c in view.candidates):
            raise ValueError(f"{self.name} opens full packets; run it on an {FULL} universe")
        return self.full.rank(query, view) + ranking[self.budget :]
