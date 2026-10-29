"""V4: dense retrieval over extractive cards, and its fusion with keyword search.

- ``dense-cards``: embed each repository's extractive card and the prompt with the pinned
  encoder (``embeddings.py``), rank by exact cosine similarity. Its same-text control is
  ``bm25-cards``.
- ``dense-cards-name`` and ``bm25-cards-name``: the same two rankers over a card that
  leads with the repository's name tokens (``bm25f``'s ``name`` field) repeated
  ``NAME_REPEATS`` times. FTS5's BM25 sums field weights into term frequency, so the
  repeats give the name the weight ``bm25f`` gives it.
- ``hybrid-rrf``: reciprocal rank fusion of ``bm25f``'s matches and ``dense-cards``.
- ``hybrid-rrf-L0-L2`` and ``hybrid-rrf-dense-L2``: the fusion with its two halves at
  different fetch levels. The dense half reads cards at L2 (the README) in both; the
  keyword half is ``bm25f`` over names alone (L0) in the first and over the universe's
  own level in the second, so the second needs a universe at L2 or above.

Card vectors are stored per snapshot, keyed by card variant, budget, repository and
commit, in one SQLite file per encoder revision under the cache root, so reruns only
embed prompts. Cards from a restricted fetch level (``fetch_levels.py``) get the level in
their variant, such as ``card@L2``. A ranker held at L0 or L2 cuts each packet's
evidence to that level in memory, which reads as the restricted packet does, and shares
its stored vectors.

``python -m repo_discovery.rankers.dense embed`` fills the store for a set of cases and
reports how long building cards and embedding them took.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from repo_discovery.aliases import owner_stems
from repo_discovery.cards import DEFAULT_BUDGET, build_card
from repo_discovery.cases import SPLITS, Query, read_cases, repo_key, select_cases
from repo_discovery.contract import Candidate, Universe, UniverseView, resolve_universe
from repo_discovery.embeddings import ENCODER_ID, BgeSmallEncoder, Encoder, VectorStore, store_path
from repo_discovery.evidence import Evidence, read_evidence
from repo_discovery.fetch_levels import FULL, fetch_level, holds
from repo_discovery.rankers.bm25 import BM25Ranker, CardBM25Ranker, NameBM25Ranker, analyze

NAME_REPEATS = 4
RRF_K = 60
EMBED_CHUNK = 2_048
HELD_LEVELS = ("L0", "L2")


def evidence_at(evidence: Evidence, level: str) -> Evidence:
    """``evidence`` cut to what a packet restricted to ``level`` (L0 or L2) reads as."""
    if level == "L0":
        return Evidence(evidence.repo)
    if level == "L2":
        return Evidence(evidence.repo, readme=evidence.readme)
    raise ValueError(f"evidence is cut in memory only to {HELD_LEVELS}, not {level!r}")


def name_tokens(evidence: Evidence) -> str:
    """``bm25f``'s ``name`` field: owner, owner stems, name and package names, each token once."""
    owner, _, name = evidence.repo.partition("/")
    names = [owner, *sorted(owner_stems(owner)), name, *evidence.package_names]
    return " ".join(dict.fromkeys(analyze(" ".join(names))))


def name_led_card(evidence: Evidence, budget: int = DEFAULT_BUDGET) -> str:
    lead = " ".join([name_tokens(evidence)] * NAME_REPEATS)
    return f"{lead}\n{build_card(evidence, budget).text}"


@dataclass
class EmbedStats:
    snapshots: int = 0
    cached: int = 0
    embedded: int = 0
    card_seconds: float = 0.0
    embed_seconds: float = 0.0


class DenseCardRanker:
    """Exact cosine search over embedded extractive cards.

    With ``level`` (L0 or L2), cards hold only that level's evidence whatever level the
    universe is at, as long as the universe's packets hold it.
    """

    name = "dense-cards"
    variant = "card"

    def __init__(
        self,
        seed: int = 0,
        budget: int = DEFAULT_BUDGET,
        encoder: Encoder | None = None,
        store: Path | None = None,
        level: str | None = None,
    ) -> None:
        if level is not None and level not in HELD_LEVELS:
            raise ValueError(f"cards can be held at {HELD_LEVELS}, not {level!r}")
        self.seed = seed
        self.budget = budget
        self.level = level
        self._encoder = encoder
        self._store_path = store or store_path(encoder.id if encoder else ENCODER_ID)
        self._store: VectorStore | None = None
        self._vectors: dict[str, np.ndarray] = {}
        self.stats = EmbedStats()

    @property
    def encoder(self) -> Encoder:
        if self._encoder is None:
            self._encoder = BgeSmallEncoder()
        return self._encoder

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = VectorStore(self._store_path)
        return self._store

    def card_text(self, evidence: Evidence) -> str:
        return build_card(evidence, self.budget).text

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        return [repo for repo, _ in self.search(query, universe)]

    def search(self, query: Query, universe: UniverseView) -> list[tuple[str, float]]:
        scores = self.vectors(universe) @ self.encoder.query(query.prompt)
        order = np.argsort(-scores, kind="stable")
        return [(universe.candidates[i].repo, float(scores[i])) for i in order]

    def vectors(self, universe: UniverseView) -> np.ndarray:
        """One row per candidate, in ``universe.candidates`` order."""
        keys = [self._key(c) for c in universe.candidates]
        unseen = [k for k in dict.fromkeys(keys) if k not in self._vectors]
        if unseen:
            found = self.store.get_many(unseen)
            self._vectors.update(found)
            self.stats.cached += len(found)
            todo = {k: c for k, c in zip(keys, universe.candidates, strict=True)}
            missing = [(k, todo[k]) for k in unseen if k not in found]
            for start in range(0, len(missing), EMBED_CHUNK):
                self._embed(missing[start : start + EMBED_CHUNK])
            self.stats.snapshots += len(unseen)
        return np.stack([self._vectors[k] for k in keys])

    def _key(self, candidate: Candidate) -> str:
        # The evidence directory is named for the commit. Full packets keep the key they
        # had before fetch levels existed, so stored vectors stay valid.
        commit = candidate.evidence_dir.name
        level = fetch_level(candidate.evidence_dir)
        if self.level is not None:
            if not holds(level, self.level):
                raise ValueError(f"cards held at {self.level} can't be read from {level} packets")
            level = self.level
        variant = self.variant if level == FULL else f"{self.variant}@{level}"
        return f"{variant}/{self.budget}/{repo_key(candidate.repo)}/{commit}"

    def _evidence(self, candidate: Candidate) -> Evidence:
        if self.level == "L0":
            return Evidence(candidate.repo)
        evidence = read_evidence(candidate.repo, candidate.evidence_dir)
        return evidence if self.level is None else evidence_at(evidence, self.level)

    def _embed(self, batch: Sequence[tuple[str, Candidate]]) -> None:
        started = time.perf_counter()
        texts = [self.card_text(self._evidence(c)) for _, c in batch]
        self.stats.card_seconds += time.perf_counter() - started
        started = time.perf_counter()
        vectors = self.encoder.documents(texts)
        self.stats.embed_seconds += time.perf_counter() - started
        fresh = dict(zip((key for key, _ in batch), vectors, strict=True))
        self.store.put_many(fresh)
        self._vectors.update(fresh)
        self.stats.embedded += len(batch)


class NamedDenseCardRanker(DenseCardRanker):
    """``dense-cards`` over the name-led card."""

    name = "dense-cards-name"
    variant = "name"

    def card_text(self, evidence: Evidence) -> str:
        return name_led_card(evidence, self.budget)


class NamedCardBM25Ranker(CardBM25Ranker):
    """``bm25-cards`` over the name-led card."""

    name = "bm25-cards-name"

    def document(self, evidence: Evidence) -> dict[str, str]:
        return {"card": " ".join(analyze(name_led_card(evidence, self.budget)))}


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> list[str]:
    """Sum of ``1 / (k + rank)`` over the rankings that list a repository; ties by name."""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, repo in enumerate(ranking, start=1):
            scores[repo] += 1.0 / (k + rank)
    return sorted(scores, key=lambda repo: (-scores[repo], repo_key(repo)))


class HybridRanker:
    """``bm25f`` and ``dense-cards`` fused by reciprocal rank.

    Only repositories matching a prompt term get a keyword rank; ``bm25f``'s shuffled
    tail of non-matches is left out of the fusion.
    """

    name = "hybrid-rrf"

    def __init__(
        self,
        seed: int = 0,
        k: int = RRF_K,
        keyword: BM25Ranker | None = None,
        dense: DenseCardRanker | None = None,
    ) -> None:
        self.k = k
        self.keyword = keyword or BM25Ranker(seed=seed)
        self.dense = dense or DenseCardRanker(seed=seed)

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        keyword = [repo for repo, _ in self.keyword.search(query, universe)]
        return reciprocal_rank_fusion([keyword, self.dense.rank(query, universe)], self.k)


class DenseL2HybridRanker(HybridRanker):
    """``bm25f`` at the universe's level fused with ``dense-cards`` over README cards (L2)."""

    name = "hybrid-rrf-dense-L2"
    keyword_ranker: type[BM25Ranker] = BM25Ranker

    def __init__(self, seed: int = 0, k: int = RRF_K, dense: DenseCardRanker | None = None):
        dense = dense or DenseCardRanker(seed, level="L2")
        if dense.level != "L2":
            raise ValueError(f"{self.name} reads cards at L2")
        super().__init__(seed, k, self.keyword_ranker(seed=seed), dense)


class NamesDenseL2HybridRanker(DenseL2HybridRanker):
    """``bm25f`` over names (L0) fused with ``dense-cards`` over README cards (L2)."""

    name = "hybrid-rrf-L0-L2"
    keyword_ranker = NameBM25Ranker


VARIANTS: dict[str, type[DenseCardRanker]] = {
    DenseCardRanker.variant: DenseCardRanker,
    NamedDenseCardRanker.variant: NamedDenseCardRanker,
}


def embed(
    ranker: DenseCardRanker, cases_path: Path, split: str | None, universes: Path
) -> EmbedStats:
    """Store vectors for every snapshot in the selected cases' universes."""
    for case in select_cases(read_cases(cases_path), split=split):
        ranker.vectors(Universe.load(resolve_universe(universes, case.case_id)).view())
    return ranker.stats


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m repo_discovery.rankers.dense")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("embed", help="embed and store the cards of every case universe")
    cmd.add_argument("--cases", type=Path, required=True)
    cmd.add_argument("--split", choices=[*SPLITS, "all"], default="dev")
    cmd.add_argument("--universes", type=Path, required=True)
    cmd.add_argument("--variant", choices=sorted(VARIANTS), default=DenseCardRanker.variant)
    cmd.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    ranker = VARIANTS[args.variant](budget=args.budget)
    stats = embed(ranker, args.cases, None if args.split == "all" else args.split, args.universes)
    seconds = time.perf_counter() - started
    rate = stats.embedded / stats.embed_seconds if stats.embed_seconds else 0.0
    print(
        f"{args.variant} cards: {stats.snapshots} snapshots, {stats.cached} already stored, "
        f"{stats.embedded} embedded; cards {stats.card_seconds:.1f} s, "
        f"encoder {stats.embed_seconds:.1f} s ({rate:.1f} cards/s), "
        f"total {seconds:.1f} s -> {ranker.store.path}",
        file=sys.stderr,
    )
    print(
        json.dumps({**asdict(stats), "seconds": round(seconds, 3), "store": str(ranker.store.path)})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
