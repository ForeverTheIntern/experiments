"""V2: keyword seeds plus their one-hop declared-dependency neighbours.

``bm25f-deps`` ranks a universe with ``bm25f``, takes the top ``seeds`` repositories as
seeds, and lists every repository one dependency edge from a seed, in either direction
(``depgraph.neighbour_list``: most seeds touched first, then ``bm25f`` rank). The two lists
are fused by balanced interleaving: ``bm25f``'s first ``head`` repositories, then one
from the neighbour list and one from ``bm25f`` in turn, skipping repositories already
placed, and the rest of ``bm25f`` once the neighbour list runs out. With no edges near
the seeds the ranking is ``bm25f``'s, and a repository at ``bm25f`` rank r ends up no
lower than about 2r.

``bm25f-deps-random`` is the control: the same seeds and fusion over
``DependencyGraph.rewired`` edges, which keep every repository's in- and out-degree but
connect it to random repositories. A gain over the control comes from which
repositories the real edges connect, not from promoting more ``bm25f`` candidates.
"""

from __future__ import annotations

import random
from collections.abc import Sequence

from repo_discovery.cases import Query, repo_key
from repo_discovery.contract import Candidate, UniverseView
from repo_discovery.depgraph import DependencyGraph, build_graph, neighbour_list
from repo_discovery.packages import Packages, read_packages
from repo_discovery.rankers.bm25 import BM25Ranker

DEFAULT_SEEDS = 10
DEFAULT_HEAD = 1


def interleave(first: Sequence[str], second: Sequence[str], head: int = DEFAULT_HEAD) -> list[str]:
    """``first[:head]``, then ``second`` and ``first`` in turn, each repository once."""
    placed: dict[str, None] = dict.fromkeys(first[:head])
    i, j = head, 0
    while j < len(second):
        while j < len(second) and second[j] in placed:
            j += 1
        if j < len(second):
            placed[second[j]] = None
        while i < len(first) and first[i] in placed:
            i += 1
        if i < len(first):
            placed[first[i]] = None
    placed.update(dict.fromkeys(first[i:]))
    return list(placed)


def expand(
    base: Sequence[str],
    graph: DependencyGraph,
    seeds: int = DEFAULT_SEEDS,
    head: int = DEFAULT_HEAD,
) -> list[str]:
    """``base`` fused with the one-hop neighbours of its top ``seeds`` repositories."""
    rank = {repo: i for i, repo in enumerate(base, start=1)}
    return interleave(base, neighbour_list(graph, base[:seeds], rank), head)


class DependencyExpansionRanker:
    """``bm25f`` seeds plus one-hop declared-dependency neighbours, interleaved."""

    name = "bm25f-deps"
    random_edges = False

    def __init__(self, seed: int = 0, seeds: int = DEFAULT_SEEDS, head: int = DEFAULT_HEAD) -> None:
        self.seed = seed
        self.seeds = seeds
        self.head = head
        self.base = BM25Ranker(seed=seed)
        self._packages: dict[tuple[str, str], Packages] = {}

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        base = self.base.rank(query, universe)
        return expand(base, self.graph(query, universe), self.seeds, self.head)

    def graph(self, query: Query, universe: UniverseView) -> DependencyGraph:
        graph, _ = build_graph({c.repo: self.packages(c) for c in universe.candidates})
        if self.random_edges:
            graph = graph.rewired(random.Random(f"rewire:{self.seed}:{query.case_id}"))
        return graph

    def packages(self, candidate: Candidate) -> Packages:
        key = (repo_key(candidate.repo), candidate.evidence_dir.name)
        if key not in self._packages:
            self._packages[key] = read_packages(candidate.evidence_dir)
        return self._packages[key]


class RandomEdgeExpansionRanker(DependencyExpansionRanker):
    """The control: the same expansion over degree-matched random edges."""

    name = "bm25f-deps-random"
    random_edges = True
