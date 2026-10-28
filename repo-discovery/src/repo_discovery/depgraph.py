"""Declared-dependency graph over one universe, and its degree-matched random control.

There is an edge ``u -> v`` when ``u`` depends on a package that ``v`` declares
(``packages.py``). A dependency resolves as follows:

- ``internal``: ``u`` declares the package itself (a monorepo depending on its own
  workspace package). No edge.
- ``resolved``: one to ``MAX_PROVIDERS`` other repositories in the universe declare it.
  An edge to each.
- ``ambiguous``: more than ``MAX_PROVIDERS`` repositories declare it, usually a generic
  name such as ``docs`` or ``frontend``, or several forks. No edge.
- ``unresolved``: nothing in the universe declares it, so it is a package from outside
  the universe. No edge.

``rewired`` is the control: the same out-degree and in-degree for every repository, with
targets reassigned at random by shuffling edge endpoints (the directed configuration
model). Self-loops and repeated edges the shuffle creates are dropped, so a few edges
are lost.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from repo_discovery.packages import Packages, ecosystem_of

MAX_PROVIDERS = 3
OUTCOMES: tuple[str, ...] = ("internal", "resolved", "ambiguous", "unresolved")


@dataclass
class Resolution:
    """Counts of distinct (repository, dependency) pairs by outcome, overall and by ecosystem."""

    outcomes: Counter = field(default_factory=Counter)
    by_ecosystem: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    def add(self, dependency: str, outcome: str) -> None:
        self.outcomes[outcome] += 1
        self.by_ecosystem[ecosystem_of(dependency)][outcome] += 1

    @staticmethod
    def rate(counts: Mapping[str, int]) -> float:
        """Share of external dependencies (all but ``internal``) that resolve to an edge."""
        external = sum(counts.get(o, 0) for o in OUTCOMES if o != "internal")
        return counts.get("resolved", 0) / external if external else 0.0


@dataclass(frozen=True)
class DependencyGraph:
    """Directed edges ``dependent -> provider`` between repositories of one universe."""

    repos: tuple[str, ...]
    edges: frozenset[tuple[str, str]]

    def __post_init__(self) -> None:
        out: dict[str, set[str]] = defaultdict(set)
        inbound: dict[str, set[str]] = defaultdict(set)
        for u, v in self.edges:
            out[u].add(v)
            inbound[v].add(u)
        object.__setattr__(self, "_out", out)
        object.__setattr__(self, "_in", inbound)

    def outbound(self, repo: str) -> set[str]:
        """Repositories ``repo`` depends on."""
        return self._out.get(repo, set())

    def inbound(self, repo: str) -> set[str]:
        """Repositories that depend on ``repo``."""
        return self._in.get(repo, set())

    def neighbours(self, repo: str) -> set[str]:
        return self.outbound(repo) | self.inbound(repo)

    def degree(self, repo: str) -> int:
        return len(self.neighbours(repo))

    def rewired(self, rng: random.Random) -> DependencyGraph:
        """Random edges with every repository's in- and out-degree kept (up to dropped loops)."""
        ordered = sorted(self.edges)
        sources = [u for u, _ in ordered]
        targets = [v for _, v in ordered]
        rng.shuffle(targets)
        edges = frozenset((u, v) for u, v in zip(sources, targets, strict=True) if u != v)
        return DependencyGraph(self.repos, edges)


def build_graph(
    packages: Mapping[str, Packages], max_providers: int = MAX_PROVIDERS
) -> tuple[DependencyGraph, Resolution]:
    """The graph over ``packages`` (repository -> declarations) and how dependencies resolved."""
    providers: dict[str, set[str]] = defaultdict(set)
    for repo, declared in packages.items():
        for name in declared.provides:
            providers[name].add(repo)
    edges: set[tuple[str, str]] = set()
    resolution = Resolution()
    for repo, declared in packages.items():
        for dependency in declared.depends:
            found = providers.get(dependency, set())
            if dependency in declared.provides:
                resolution.add(dependency, "internal")
            elif not found:
                resolution.add(dependency, "unresolved")
            elif len(found) > max_providers:
                resolution.add(dependency, "ambiguous")
            else:
                resolution.add(dependency, "resolved")
                edges.update((repo, provider) for provider in found)
    return DependencyGraph(tuple(packages), frozenset(edges)), resolution


def neighbour_list(
    graph: DependencyGraph, seeds: Iterable[str], base_rank: Mapping[str, int]
) -> list[str]:
    """Repositories one hop from any seed, either direction, seeds included when adjacent.

    Ordered by how many seeds they touch (most first), then by ``base_rank`` (best first).
    """
    touches: Counter = Counter()
    for seed in seeds:
        touches.update(graph.neighbours(seed))
    last = len(base_rank) + 1
    return sorted(touches, key=lambda r: (-touches[r], base_rank.get(r, last), r))
