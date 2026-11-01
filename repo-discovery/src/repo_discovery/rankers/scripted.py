"""Equal-budget scripted explorers: the agent's tools run by a fixed rule, with no model.

They answer the design pack's V5-E1/E2 question: does the agent beat simple extra queries
and deterministic traversal given the same budget? Each explorer splits the request into
deterministic facets, searches once per facet, fuses the searches by reciprocal rank, and
follows declared dependencies and contract joins one hop from its best candidates.

- ``scripted-search``: the control for the search-tool agent (c2), which ranks against a
  prebuilt full-packet index. It searches ``bm25f`` over full packets once per facet, at
  most ``max_facets`` searches, then applies V2's dependency expansion and V3's contract
  promotion over the universe.
- ``scripted-open<N>``: the control for the on-demand agent (L5), which starts from names
  and opens at most N packets. It searches names only per facet, opens the fused top N
  (full packets), reranks the opened set with per-facet ``bm25f`` over their packets, and
  expands and promotes among the opened set only, since unopened packets were never read.

Facets: the whole request, then each sentence or list item with at least
``MIN_FACET_TERMS`` analyzed terms, then one facet of code-like tokens (backticks,
CamelCase, snake_case, dotted or slashed names), in request order, deduplicated, capped
at ``max_facets``. Fusion is reciprocal rank fusion with ``k`` = 60 over each facet's
matches; repositories no facet matched follow in a seeded shuffle per case, as in
``bm25f``.
"""

from __future__ import annotations

import random
import re
from collections import defaultdict
from collections.abc import Sequence

from repo_discovery.cases import Query
from repo_discovery.contract import UniverseView
from repo_discovery.depgraph import build_graph
from repo_discovery.fetch_levels import FULL, fetch_level
from repo_discovery.rankers.bm25 import BM25Ranker, NameBM25Ranker, analyze
from repo_discovery.rankers.contract_joins import ContractJoinRanker, promote
from repo_discovery.rankers.deps import DependencyExpansionRanker, expand

MAX_FACETS = 8
MIN_FACET_TERMS = 3
RRF_K = 60
BUDGETS: tuple[int, ...] = (5, 10, 20, 50)
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_CODE = re.compile(
    r"`([^`]+)`|\b([A-Za-z]+[a-z0-9]+[A-Z][A-Za-z0-9]*|[a-z0-9]+(?:_[a-z0-9]+)+"
    r"|[A-Za-z0-9-]+(?:[./][A-Za-z0-9_-]+)+)\b"
)


def facets(prompt: str, max_facets: int = MAX_FACETS) -> list[str]:
    """The request's search facets, whole request first."""
    found = [prompt.strip()]
    for part in _SPLIT.split(prompt):
        part = _BULLET.sub("", part).strip()
        if part and len(analyze(part)) >= MIN_FACET_TERMS:
            found.append(part)
    code = [a or b for a, b in _CODE.findall(prompt)]
    if code:
        found.append(" ".join(dict.fromkeys(code)))
    seen: dict[tuple[str, ...], str] = {}
    for facet in found:
        terms = tuple(sorted(set(analyze(facet))))
        if terms and terms not in seen:
            seen[terms] = facet
    return list(seen.values())[:max_facets]


def fuse(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    """Reciprocal rank fusion; ties go to the repository first seen in an earlier facet."""
    score: dict[str, float] = defaultdict(float)
    first: dict[str, tuple[int, int]] = {}
    for f, ranking in enumerate(rankings):
        for rank, repo in enumerate(ranking, start=1):
            score[repo] += 1.0 / (k + rank)
            first.setdefault(repo, (f, rank))
    return sorted(score, key=lambda r: (-score[r], first[r], r))


def _searched(ranker: BM25Ranker, query: Query, universe: UniverseView, parts: list[str]):
    return [
        [repo for repo, _ in ranker.search(Query(query.case_id, part, query.cutoff), universe)]
        for part in parts
    ]


def _with_rest(ranked: list[str], universe: UniverseView, seed: int, case_id: str) -> list[str]:
    found = set(ranked)
    rest = [c.repo for c in universe.candidates if c.repo not in found]
    random.Random(f"{seed}:{case_id}").shuffle(rest)
    return ranked + rest


class ScriptedSearchExplorer:
    """Per-facet ``bm25f`` over full packets, fused, then dependency and contract hops."""

    name = "scripted-search"

    def __init__(self, seed: int = 0, max_facets: int = MAX_FACETS) -> None:
        self.seed = seed
        self.max_facets = max_facets
        self.keyword = BM25Ranker(seed=seed)
        self.deps = DependencyExpansionRanker(seed=seed)
        self.contracts = ContractJoinRanker(seed=seed)
        self.searches = 0

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        parts = facets(query.prompt, self.max_facets)
        self.searches = len(parts)
        fused = fuse(_searched(self.keyword, query, universe, parts))
        base = _with_rest(fused, universe, self.seed, query.case_id)
        expanded = expand(base, self.deps.graph(query, universe))
        return promote(expanded, self.contracts.index(universe))


class ScriptedOpenExplorer:
    """Per-facet names-only search, open the fused top ``budget``, rerank and hop among them."""

    def __init__(self, seed: int = 0, budget: int = 20, max_facets: int = MAX_FACETS) -> None:
        if budget < 1:
            raise ValueError("budget must be at least 1")
        self.seed = seed
        self.budget = budget
        self.max_facets = max_facets
        self.name = f"scripted-open{budget}"
        self.names = NameBM25Ranker(seed=seed)
        self.keyword = BM25Ranker(seed=seed)
        self.deps = DependencyExpansionRanker(seed=seed)
        self.contracts = ContractJoinRanker(seed=seed)
        self.opened: list[str] = []

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        parts = facets(query.prompt, self.max_facets)
        first = _with_rest(
            fuse(_searched(self.names, query, universe, parts)), universe, self.seed, query.case_id
        )
        self.opened = first[: self.budget]
        chosen = set(self.opened)
        view = UniverseView(
            universe.root, tuple(c for c in universe.candidates if c.repo in chosen)
        )
        if any(fetch_level(c.evidence_dir) != FULL for c in view.candidates):
            raise ValueError(f"{self.name} opens full packets; run it on an {FULL} universe")
        reranked = fuse(_searched(self.keyword, query, view, parts))
        reranked += [r for r in self.opened if r not in set(reranked)]
        graph, _ = build_graph({c.repo: self.deps.packages(c) for c in view.candidates})
        hopped = promote(expand(reranked, graph), self.contracts.index(view))
        return hopped + first[self.budget :]
