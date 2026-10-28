"""V3: typed contract joins from ``bm25f`` seeds, and the same contract text without joins.

``contract-joins`` reranks ``bm25f`` in three steps:

1. **Seeds.** The top ``SEEDS`` (20) repositories of ``bm25f``.
2. **Joins.** Two repositories join on a contract key (``api_contracts``) that both hold
   and at least one provides: a provider and a consumer (``provides-consumes``), or two
   providers (``co-defines``, except for ``.proto`` file paths, which join only through an
   import). Two consumers of the same key do not join. A key held by ``n`` repositories
   of the universe weighs ``1 / (n - 1)``; keys held by more than ``MAX_HOLDERS`` (50)
   are hubs and never join. A join's weight is the sum over its keys.
3. **Promotion.** Each repository joined to a seed ranked above it is anchored to the
   seed it joins most heavily (ties: the higher seed). Each seed lifts its ``PER_SEED``
   (1) heaviest anchored repository to just behind itself; everything else keeps its
   ``bm25f`` order. Without joins the ranking is exactly ``bm25f``'s.

Reciprocal rank fusion of ``bm25f`` with a join-expansion list was tried first and lost
to promotion on dev at every size: with ``k = 60``, anything on both lists outranks
everything on one, so the expansion list took over the top of the ranking.

``bm25f-contract-text`` is the control: ``bm25f`` with one more field, ``contracts``
(weight 1), holding the values of the same contract identifiers as text. It sees the
evidence ``contract-joins`` joins on, but never joins.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from repo_discovery.api_contracts import PROVIDES, Identifier, contract_text, identifiers
from repo_discovery.cases import Query, repo_key
from repo_discovery.contract import Candidate, UniverseView
from repo_discovery.evidence import Evidence, read_evidence
from repo_discovery.rankers.bm25 import (
    CACHE_SIZE,
    DEFAULT_WEIGHTS,
    FIELDS,
    BM25Ranker,
    analyze,
    evidence_fields,
)

SEEDS = 20
PER_SEED = 1
MAX_HOLDERS = 50
CONTRACT_TOKENS = 4_000
# Two repositories holding a .proto file under the same path suffix (``test.proto``,
# ``k8s.io/api/core/v1/generated.proto``) are usually vendoring it; only an import joins.
IMPORT_ONLY = frozenset({"proto.file"})


@dataclass(frozen=True)
class Join:
    key: tuple[str, str]
    kind: str
    weight: float


class ContractIndex:
    """One universe's contract keys, inverted: key -> repository -> roles."""

    def __init__(self, found: Iterable[tuple[str, Iterable[Identifier]]]):
        self.keys: dict[str, dict[tuple[str, str], set[str]]] = {}
        self.holders: dict[tuple[str, str], dict[str, set[str]]] = defaultdict(dict)
        for repo, ids in found:
            roles: dict[tuple[str, str], set[str]] = defaultdict(set)
            for identifier in ids:
                roles[identifier.key].add(identifier.role)
            if roles:
                self.keys[repo] = dict(roles)
                for key, held in roles.items():
                    self.holders[key][repo] = held

    def joins(self, repo: str) -> dict[str, list[Join]]:
        """Repositories joined to ``repo``, with the keys that join them."""
        found: dict[str, list[Join]] = defaultdict(list)
        for key, mine in self.keys.get(repo, {}).items():
            holders = self.holders[key]
            if len(holders) < 2 or len(holders) > MAX_HOLDERS:
                continue
            weight = 1.0 / (len(holders) - 1)
            for other, theirs in holders.items():
                if other == repo:
                    continue
                kind = join_kind(key[0], mine, theirs)
                if kind is not None:
                    found[other].append(Join(key, kind, weight))
        return dict(found)


def join_kind(key_type: str, mine: set[str], theirs: set[str]) -> str | None:
    if PROVIDES in mine and PROVIDES in theirs:
        return None if key_type in IMPORT_ONLY else "co-defines"
    if PROVIDES in mine or PROVIDES in theirs:
        return "provides-consumes"
    return None


def promote(
    base: Sequence[str], index: ContractIndex, seeds: int = SEEDS, per_seed: int = PER_SEED
) -> list[str]:
    """``base`` with each seed's heaviest contract-joined repositories moved behind it."""
    order = {repo: rank for rank, repo in enumerate(base)}
    anchors: dict[str, tuple[float, int]] = {}
    for rank, seed in enumerate(base[:seeds]):
        for other, joins in index.joins(seed).items():
            weight = sum(j.weight for j in joins)
            if order.get(other, -1) > rank and weight > anchors.get(other, (0.0, 0))[0]:
                anchors[other] = (weight, rank)
    attached: dict[int, list[tuple[float, int, str]]] = defaultdict(list)
    for other, (weight, rank) in anchors.items():
        attached[rank].append((-weight, order[other], other))
    position = {repo: (rank, 0) for repo, rank in order.items()}
    for rank, items in attached.items():
        for slot, (_, _, other) in enumerate(sorted(items)[:per_seed], start=1):
            position[other] = (rank, slot)
    return sorted(base, key=position.__getitem__)


class ContractJoinRanker:
    """``bm25f``, with each top seed's heaviest contract-joined repository lifted behind it."""

    name = "contract-joins"

    def __init__(self, seed: int = 0, seeds: int = SEEDS, per_seed: int = PER_SEED) -> None:
        self.keyword = BM25Ranker(seed=seed)
        self.seeds = seeds
        self.per_seed = per_seed
        self._identifiers: dict[tuple[str, str], frozenset[Identifier]] = {}
        self._index_key: tuple | None = None
        self._index: ContractIndex | None = None

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        base = self.keyword.rank(query, universe)
        return promote(base, self.index(universe), self.seeds, self.per_seed)

    def index(self, universe: UniverseView) -> ContractIndex:
        key = (universe.root, universe.candidates)
        if self._index is None or self._index_key != key:
            found = ((c.repo, self.identifiers(c)) for c in universe.candidates)
            self._index, self._index_key = ContractIndex(found), key
        return self._index

    def identifiers(self, candidate: Candidate) -> frozenset[Identifier]:
        key = (repo_key(candidate.repo), candidate.evidence_dir.name)
        if key not in self._identifiers:
            if len(self._identifiers) >= CACHE_SIZE:
                del self._identifiers[next(iter(self._identifiers))]
            evidence = read_evidence(candidate.repo, candidate.evidence_dir)
            self._identifiers[key] = identifiers(evidence)
        return self._identifiers[key]


class ContractTextRanker(BM25Ranker):
    """``bm25f`` plus a ``contracts`` field holding the contract identifiers as text."""

    name = "bm25f-contract-text"
    fields = (*FIELDS, "contracts")

    def __init__(self, seed: int = 0, weights: Mapping[str, float] | None = None) -> None:
        super().__init__(seed, weights={**DEFAULT_WEIGHTS, "contracts": 1.0, **(weights or {})})

    def document(self, evidence: Evidence) -> dict[str, str]:
        text = contract_text(identifiers(evidence))
        return {
            **evidence_fields(evidence),
            "contracts": " ".join(analyze(text)[:CONTRACT_TOKENS]),
        }
