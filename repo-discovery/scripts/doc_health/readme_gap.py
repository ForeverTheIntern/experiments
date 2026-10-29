"""Reproduce the fetch-level report's README gap (1,612 against 432 analyzed tokens) and
put it beside the same cases' whole universes, counted two ways.

The report took the dev cases that ``bm25f`` gets at L0 and misses at L2, and compared the
READMEs of the decoys in L2's top 10 with the targets'. ``raw`` runs the BM25 analyzer on
the README as written; ``indexed`` strips markup first, as ``bm25f`` does before indexing
(its 4,000-token field cap binds on none of these READMEs' medians).

    python3 scripts/doc_health/readme_gap.py --corpus ~/rd-corpus --cases ~/rd-cases.jsonl \\
        --preds ~/rd-fl/preds --size 250
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Sequence
from pathlib import Path

from degrade import dev_cases
from profile_packets import universe_rows

from repo_discovery.cards import strip_markup
from repo_discovery.cases import repo_key
from repo_discovery.contract import evidence_dir
from repo_discovery.evidence import read_evidence
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import read_predictions
from repo_discovery.rankers.bm25 import analyze

METRIC = "CompleteRecall@10"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--preds", type=Path, required=True, help="bm25f--L0/L2--u<N>.jsonl")
    parser.add_argument("--size", type=int, default=250)
    args = parser.parse_args(argv)

    cases = dev_cases(args.cases)
    gold = {c.case_id: [g.repo for g in c.gold] for c in cases}

    def rankings(level: str) -> dict[str, list[str]]:
        path = args.preds / f"bm25f--{level}--u{args.size}.jsonl"
        found = {p.case_id: list(p.ranking) for p in read_predictions(path) if p.case_id in gold}
        if found.keys() != gold.keys():
            raise SystemExit(f"{path}: {len(found)} of {len(gold)} dev cases")
        return found

    l0, l2 = rankings("L0"), rankings("L2")

    def complete(ranking: dict[str, list[str]], cid: str) -> bool:
        return case_metrics(gold[cid], ranking[cid], (10,))[METRIC] == 1

    broke = [cid for cid in gold if complete(l0, cid) and not complete(l2, cid)]
    tokens: dict[tuple[str, str], tuple[int, int] | None] = {}

    def count(entry: dict) -> tuple[int, int] | None:
        key = (repo_key(entry["repo"]), entry["commit"])
        if key not in tokens:
            readme = read_evidence(
                entry["repo"], evidence_dir(args.corpus, entry["repo"], entry["commit"])
            ).readme
            tokens[key] = (
                (len(analyze(readme)), len(analyze(strip_markup(readme)))) if readme else None
            )
        return tokens[key]

    groups: dict[str, list[tuple[int, int] | None]] = {
        "top-10 decoys": [],
        "targets": [],
        "all decoys": [],
    }
    for cid in broke:
        entries = {repo_key(e["repo"]): e for e in universe_rows(args.corpus, args.size, cid)}
        targets = {repo_key(g) for g in gold[cid]}
        groups["top-10 decoys"] += [
            count(entries[repo_key(r)]) for r in l2[cid][:10] if repo_key(r) not in targets
        ]
        for key, entry in entries.items():
            groups["targets" if key in targets else "all decoys"].append(count(entry))

    out = {"size": args.size, "cases": len(broke), "groups": {}}
    for name, values in groups.items():
        present = [v for v in values if v is not None]
        out["groups"][name] = {
            "rows": len(values),
            "with_readme": len(present),
            "raw_median": statistics.median(v[0] for v in present),
            "indexed_median": statistics.median(v[1] for v in present),
        }
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
