"""Store card vectors for a new fetch level by copying those of cards the encoder reads alike.

bge-small reads only a card's first 512 tokens, so a card at one level often gives the
encoder exactly the input another level's card did: L3+contracts adds nothing to a
repository without contracts, and a card that already fills the window at L3 or L4 reads
the same at L3+paths. For every snapshot in the dev cases' universes under
``--universes``, this builds the card as ``dense embed`` would, tokenizes it as the
encoder does, and when the tokens equal those of the same snapshot's card under a
``--from`` universe whose vector is stored, stores that vector under the new card's key.
``python -m repo_discovery.rankers.dense embed`` then encodes only the rest. ``--check N``
re-encodes N copied cards and reports how far the copies are from fresh vectors.

``--from-store`` reads the source vectors from another vector store, such as another
anonymized build's cache: two builds under one salt give a snapshot the same key, but
tier-dependent scrubbing can change its text, so the tokens still decide.

    python3 scripts/reuse_card_vectors.py --cases ~/rd-cases.jsonl \
        --universes ~/rd-levels/L3+docs/universes/monthly/u5000 \
        --from ~/rd-levels/L3/universes/monthly/u5000 --from ~/rd-corpus/universes/monthly/u5000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

from repo_discovery.cases import read_cases, repo_key, select_cases
from repo_discovery.contract import Candidate, Universe, resolve_universe
from repo_discovery.embeddings import VectorStore
from repo_discovery.rankers.dense import DenseCardRanker

CHUNK = 500


def snapshots(cases: list, universes: Path) -> dict[tuple[str, str], Candidate]:
    found: dict[tuple[str, str], Candidate] = {}
    for case in cases:
        for c in Universe.load(resolve_universe(universes, case.case_id)).view().candidates:
            found.setdefault((repo_key(c.repo), c.evidence_dir.name), c)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--universes", type=Path, required=True, help="the new level's tier")
    parser.add_argument("--from", dest="sources", type=Path, action="append", required=True)
    parser.add_argument("--from-store", type=Path, help="vector store of the --from universes")
    parser.add_argument("--check", type=int, default=0, metavar="N")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    cases = select_cases(read_cases(args.cases), split=args.split)
    ranker = DenseCardRanker()
    source_store = VectorStore(args.from_store) if args.from_store else ranker.store
    model = ranker.encoder._model
    targets = snapshots(cases, args.universes)
    sources = [snapshots(cases, s) for s in args.sources]
    keys = {snap: ranker._key(c) for snap, c in targets.items()}
    stored = set(ranker.store.get_many(keys.values()))

    def tokens(candidate: Candidate) -> tuple[int, ...]:
        text = ranker.card_text(ranker._evidence(candidate))
        return tuple(model.tokenize([text])["input_ids"][0].tolist())

    stats: Counter[str] = Counter(snapshots=len(targets), already_stored=len(stored))
    copied: dict[str, np.ndarray] = {}
    checks: list[tuple[str, Candidate]] = []
    for snap, candidate in targets.items():
        if keys[snap] in stored:
            continue
        mine = None
        for i, source in enumerate(sources):
            other = source.get(snap)
            if other is None:
                continue
            vector = source_store.get_many([ranker._key(other)])
            if not vector:
                continue
            mine = tokens(candidate) if mine is None else mine
            if tokens(other) == mine:
                copied[keys[snap]] = next(iter(vector.values()))
                stats[f"copied_from_{i}"] += 1
                if len(checks) < args.check:
                    checks.append((keys[snap], candidate))
                break
        else:
            stats["left_to_embed"] += 1
        if len(copied) >= CHUNK:
            ranker.store.put_many(copied)
            copied.clear()
    ranker.store.put_many(copied)

    report: dict = {**stats, "sources": [str(s) for s in args.sources]}
    report["source_store"] = str(source_store.path)
    if checks:
        texts = [ranker.card_text(ranker._evidence(c)) for _, c in checks]
        fresh = ranker.encoder.documents(texts)
        reused = ranker.store.get_many(k for k, _ in checks)
        old = np.stack([reused[k] for k, _ in checks])
        report["check"] = {
            "cards": len(checks),
            "max_abs_diff": float(np.abs(fresh - old).max()),
            "min_cosine": float((fresh * old).sum(axis=1).min()),
        }
    report["seconds"] = round(time.perf_counter() - started, 1)
    report["store"] = str(ranker.store.path)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
