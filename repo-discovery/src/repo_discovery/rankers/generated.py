"""V1: rankers over LLM-written cards (``llm_cards.py``), and their length-matched controls.

- ``dense-llm-cards`` and ``bm25-llm-cards``: ``dense-cards`` and ``bm25-cards`` over the
  card the model wrote from the snapshot's packet, after the extractive card's title line.
- ``hybrid-llm-rrf``: ``bm25f``'s matches and ``dense-llm-cards`` fused by reciprocal rank,
  as ``hybrid-rrf`` fuses them with ``dense-cards``.
- ``dense-llm-names``, ``bm25-llm-names`` and ``hybrid-llm-names-rrf``: the same over the
  closed-book card the model wrote from ``owner/name`` alone, which measures what it
  already knows about a repository.
- ``dense-cards-matched`` and ``bm25-cards-matched``: extractive cards cut to the word
  count of the snapshot's generated card, so a gain is not just a shorter card.

Cards come from the cache only. A snapshot without one raises :class:`MissingCardError`;
fill the cache with ``python -m repo_discovery.llm_cards build`` first.
``python -m repo_discovery.rankers.generated embed`` stores the dense rankers' vectors.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from repo_discovery.cards import build_card, word_count
from repo_discovery.cases import SPLITS, repo_key
from repo_discovery.contract import Candidate
from repo_discovery.embeddings import Encoder
from repo_discovery.evidence import Evidence
from repo_discovery.llm_cards import (
    CARD_WORDS,
    MODEL,
    PROMPT_VERSION,
    CardStore,
    indexed_text,
    request_for,
    store_path,
)
from repo_discovery.rankers.bm25 import BM25Ranker, CardBM25Ranker, analyze
from repo_discovery.rankers.dense import DenseCardRanker, HybridRanker, embed


class MissingCardError(LookupError):
    pass


class GeneratedCards:
    """Looks up a snapshot's generated card and returns the text the rankers index."""

    def __init__(self, mode: str, model: str = MODEL, store: Path | None = None) -> None:
        self.mode = mode
        self.model = model
        self._store_path = store
        self._store: CardStore | None = None

    @property
    def store(self) -> CardStore:
        if self._store is None:
            self._store = CardStore(self._store_path or store_path())
        return self._store

    @property
    def variant(self) -> str:
        return f"llm-{self.mode}-{self.model}-{PROMPT_VERSION}"

    def text(self, evidence: Evidence) -> str:
        key = request_for(self.mode, evidence).key(self.model)
        card = self.store.get(key)
        if card is None:
            raise MissingCardError(
                f"no {self.mode} card for {evidence.repo} ({key}); "
                "run python -m repo_discovery.llm_cards build first"
            )
        return indexed_text(self.mode, evidence, card.text)


class GeneratedDenseRanker(DenseCardRanker):
    """``dense-cards`` over generated packet cards."""

    name = "dense-llm-cards"
    mode = "packet"

    def __init__(
        self,
        seed: int = 0,
        encoder: Encoder | None = None,
        store: Path | None = None,
        cards: GeneratedCards | None = None,
    ) -> None:
        super().__init__(seed=seed, budget=CARD_WORDS, encoder=encoder, store=store)
        self.cards = cards or GeneratedCards(self.mode)
        self.variant = self.cards.variant

    def card_text(self, evidence: Evidence) -> str:
        return self.cards.text(evidence)


class GeneratedNamesDenseRanker(GeneratedDenseRanker):
    """``dense-cards`` over closed-book cards; one vector per repository at every level."""

    name = "dense-llm-names"
    mode = "names"

    def _key(self, candidate: Candidate) -> str:
        return f"{self.variant}/{self.budget}/{repo_key(candidate.repo)}"


class GeneratedBM25Ranker(CardBM25Ranker):
    """``bm25-cards`` over generated packet cards."""

    name = "bm25-llm-cards"
    mode = "packet"

    def __init__(self, seed: int = 0, cards: GeneratedCards | None = None) -> None:
        super().__init__(seed, budget=CARD_WORDS)
        self.cards = cards or GeneratedCards(self.mode)

    def document(self, evidence: Evidence) -> dict[str, str]:
        return {"card": " ".join(analyze(self.cards.text(evidence)))}


class GeneratedNamesBM25Ranker(GeneratedBM25Ranker):
    """``bm25-cards`` over closed-book cards."""

    name = "bm25-llm-names"
    mode = "names"


class GeneratedHybridRanker(HybridRanker):
    """``bm25f`` and ``dense-llm-cards`` fused by reciprocal rank."""

    name = "hybrid-llm-rrf"
    dense_ranker: type[GeneratedDenseRanker] = GeneratedDenseRanker

    def __init__(
        self,
        seed: int = 0,
        keyword: BM25Ranker | None = None,
        dense: GeneratedDenseRanker | None = None,
    ) -> None:
        super().__init__(seed=seed, keyword=keyword, dense=dense or self.dense_ranker(seed=seed))


class GeneratedNamesHybridRanker(GeneratedHybridRanker):
    """``bm25f`` and ``dense-llm-names`` fused by reciprocal rank."""

    name = "hybrid-llm-names-rrf"
    dense_ranker = GeneratedNamesDenseRanker


def matched_card(evidence: Evidence, cards: GeneratedCards) -> str:
    """The extractive card with the word budget of the snapshot's generated card."""
    return build_card(evidence, word_count(cards.text(evidence))).text


class MatchedDenseRanker(DenseCardRanker):
    """``dense-cards`` over extractive cards as long as the generated ones."""

    name = "dense-cards-matched"

    def __init__(
        self,
        seed: int = 0,
        encoder: Encoder | None = None,
        store: Path | None = None,
        cards: GeneratedCards | None = None,
    ) -> None:
        super().__init__(seed=seed, budget=CARD_WORDS, encoder=encoder, store=store)
        self.cards = cards or GeneratedCards("packet")
        self.variant = f"card-matched-{self.cards.model}-{PROMPT_VERSION}"

    def card_text(self, evidence: Evidence) -> str:
        return matched_card(evidence, self.cards)


class MatchedBM25Ranker(CardBM25Ranker):
    """``bm25-cards`` over extractive cards as long as the generated ones."""

    name = "bm25-cards-matched"

    def __init__(self, seed: int = 0, cards: GeneratedCards | None = None) -> None:
        super().__init__(seed, budget=CARD_WORDS)
        self.cards = cards or GeneratedCards("packet")

    def document(self, evidence: Evidence) -> dict[str, str]:
        return {"card": " ".join(analyze(matched_card(evidence, self.cards)))}


DENSE: dict[str, type[DenseCardRanker]] = {
    r.name: r for r in (GeneratedDenseRanker, GeneratedNamesDenseRanker, MatchedDenseRanker)
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m repo_discovery.rankers.generated")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("embed", help="embed and store the cards of every case universe")
    cmd.add_argument("--ranker", choices=sorted(DENSE), required=True)
    cmd.add_argument("--cases", type=Path, required=True)
    cmd.add_argument("--split", choices=[*SPLITS, "all"], default="dev")
    cmd.add_argument("--universes", type=Path, required=True)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    ranker = DENSE[args.ranker]()
    stats = embed(ranker, args.cases, None if args.split == "all" else args.split, args.universes)
    record = {
        "ranker": args.ranker,
        **asdict(stats),
        "seconds": round(time.perf_counter() - started, 3),
        "store": str(ranker.store.path),
    }
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
