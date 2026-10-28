"""Ranker interface and registry."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Protocol

from repo_discovery.cases import Query
from repo_discovery.contract import UniverseView


class Ranker(Protocol):
    """Orders a universe's repositories for a change request, most likely first.

    ``rank`` returns ``owner/name`` strings drawn from ``universe.candidates``,
    without duplicates. It may stop early; unranked repositories count as misses.
    """

    name: str

    def rank(self, query: Query, universe: UniverseView) -> list[str]: ...


def _registry() -> dict[str, Callable[..., Ranker]]:
    from repo_discovery.rankers.baselines import NameMatchRanker, RandomRanker
    from repo_discovery.rankers.bm25 import BM25Ranker, CardBM25Ranker
    from repo_discovery.rankers.contract_joins import ContractJoinRanker, ContractTextRanker
    from repo_discovery.rankers.cursor_agent import CursorAgentRanker
    from repo_discovery.rankers.dense import (
        DenseCardRanker,
        HybridRanker,
        NamedCardBM25Ranker,
        NamedDenseCardRanker,
    )
    from repo_discovery.rankers.deps import DependencyExpansionRanker, RandomEdgeExpansionRanker
    from repo_discovery.rankers.open_top import BUDGETS, OpenTopRanker

    return {
        RandomRanker.name: RandomRanker,
        NameMatchRanker.name: NameMatchRanker,
        BM25Ranker.name: BM25Ranker,
        CardBM25Ranker.name: CardBM25Ranker,
        CursorAgentRanker.name: CursorAgentRanker,
        DependencyExpansionRanker.name: DependencyExpansionRanker,
        RandomEdgeExpansionRanker.name: RandomEdgeExpansionRanker,
        DenseCardRanker.name: DenseCardRanker,
        NamedDenseCardRanker.name: NamedDenseCardRanker,
        NamedCardBM25Ranker.name: NamedCardBM25Ranker,
        HybridRanker.name: HybridRanker,
        ContractJoinRanker.name: ContractJoinRanker,
        ContractTextRanker.name: ContractTextRanker,
        **{f"bm25f-open{n}": partial(OpenTopRanker, budget=n) for n in BUDGETS},
    }


def available() -> list[str]:
    return sorted(_registry())


def make_ranker(name: str, **options: object) -> Ranker:
    registry = _registry()
    if name not in registry:
        raise KeyError(f"unknown ranker {name!r}; choose from {sorted(registry)}")
    return registry[name](**options)
