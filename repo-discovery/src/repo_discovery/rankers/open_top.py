"""The no-agent control for on-demand fetching (L5): open the top N of a cheap ranking.

``bm25f-open<N>`` ranks a universe with ``bm25f`` over repository names alone (fetch
level L0), opens the top N repositories' full packets (L4), reranks just those N with
``bm25f`` over their packets, and keeps the rest in names-only order. It opens what an
agent with budget N may open, chosen by a name ranker instead of the agent. Reranking
only reorders the opened set, so CompleteRecall@K for K >= N equals names-only
``bm25f``'s; the reranking shows below N.

``hybrid-open<N>`` chooses what to open with ``hybrid-rrf`` at L0 instead: names-only
``bm25f`` fused with ``dense-cards`` over name-only cards.
"""

from __future__ import annotations

from repo_discovery.cases import Query
from repo_discovery.contract import UniverseView
from repo_discovery.fetch_levels import FULL, fetch_level
from repo_discovery.rankers import Ranker
from repo_discovery.rankers.bm25 import BM25Ranker, NameBM25Ranker
from repo_discovery.rankers.dense import DenseCardRanker, HybridRanker

BUDGETS: tuple[int, ...] = (0, 5, 10, 20, 50)


class OpenTopRanker:
    """Names-only ``bm25f``, with its top ``budget`` reranked by ``bm25f`` over full packets."""

    first_name = "bm25f"

    def __init__(self, seed: int = 0, budget: int = 10, first: Ranker | None = None) -> None:
        if budget < 0:
            raise ValueError("budget must be at least 0")
        self.budget = budget
        self.name = f"{self.first_name}-open{budget}"
        self.first = first or NameBM25Ranker(seed=seed)
        self.full = BM25Ranker(seed=seed)

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        ranking = self.first.rank(query, universe)
        opened = set(ranking[: self.budget])
        if not opened:
            return ranking
        view = UniverseView(
            universe.root, tuple(c for c in universe.candidates if c.repo in opened)
        )
        if any(fetch_level(c.evidence_dir) != FULL for c in view.candidates):
            raise ValueError(f"{self.name} opens full packets; run it on an {FULL} universe")
        return self.full.rank(query, view) + ranking[self.budget :]


class HybridOpenTopRanker(OpenTopRanker):
    """``hybrid-rrf`` at L0, with its top ``budget`` reranked by ``bm25f`` over full packets."""

    first_name = "hybrid"

    def __init__(
        self, seed: int = 0, budget: int = 10, dense: DenseCardRanker | None = None
    ) -> None:
        dense = dense or DenseCardRanker(seed, level="L0")
        if dense.level != "L0":
            raise ValueError("the hybrid that picks what to open reads cards at L0")
        first = HybridRanker(seed, keyword=NameBM25Ranker(seed=seed), dense=dense)
        super().__init__(seed, budget, first)
