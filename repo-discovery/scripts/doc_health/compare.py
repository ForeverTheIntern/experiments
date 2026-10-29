"""CR@10 of degraded predictions against their base predictions, per ranker and size.

Reads ``<ranker>--u<N>.jsonl`` under each ``--degraded`` directory and the matching
``<ranker>--<level>--u<N>.jsonl`` in ``--baseline``, scores both on the dev cases only,
and prints one row per (variant, level, ranker, size): base CR@10, degraded CR@10, the
drop, the cases that lose and gain a full top 10, and how many rankings differ at all.

    python3 scripts/doc_health/compare.py --cases ~/rd-cases.jsonl \\
        --degraded ~/rd-dh/preds/real/no-readme/L3=~/rd-fl/preds ... --out drops.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from degrade import DEV_CASES, RECORD, dev_cases

from repo_discovery.cases import Case
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import read_predictions

RANKERS = ("bm25f", "dense-cards", "hybrid-rrf")
METRIC = "CompleteRecall@10"


def complete(cases: Sequence[Case], path: Path) -> tuple[dict[str, float], dict[str, tuple]]:
    gold = {c.case_id: [g.repo for g in c.gold] for c in cases}
    preds = {p.case_id: p for p in read_predictions(path) if p.case_id in gold}
    if preds.keys() != gold.keys():
        raise SystemExit(f"{path}: {len(preds)} of {len(gold)} dev cases")
    return {
        cid: case_metrics(gold[cid], list(preds[cid].ranking), (10,))[METRIC] for cid in gold
    }, {cid: preds[cid].ranking for cid in gold}


def compare(cases: Sequence[Case], degraded: Path, baseline: Path) -> list[dict]:
    score = json.loads((degraded / "score.json").read_text())
    record = json.loads((Path(score["overlay"]) / RECORD).read_text())
    rows = []
    for size in score["sizes"]:
        for ranker in RANKERS:
            mine, my_rankings = complete(cases, degraded / f"{ranker}--u{size}.jsonl")
            base, base_rankings = complete(
                cases, baseline / f"{ranker}--{record['level']}--u{size}.jsonl"
            )
            n = len(cases)
            rows.append(
                {
                    "variant": record["variant"],
                    "level": record["level"],
                    "ranker": ranker,
                    "size": size,
                    "cases": n,
                    "base": round(sum(base.values()) / n, 4),
                    "degraded": round(sum(mine.values()) / n, 4),
                    "drop": round((sum(base.values()) - sum(mine.values())) / n, 4),
                    "lost": sorted(c for c in base if base[c] > mine[c]),
                    "gained": sorted(c for c in base if base[c] < mine[c]),
                    "rankings_differ": sum(my_rankings[c] != base_rankings[c] for c in base),
                }
            )
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--expect-cases", type=int, default=DEV_CASES)
    parser.add_argument(
        "--degraded", nargs="+", required=True, help="DEGRADED_DIR=BASELINE_DIR pairs"
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    cases = dev_cases(args.cases, args.expect_cases)
    rows = []
    for pair in args.degraded:
        degraded, baseline = (Path(p).expanduser() for p in pair.split("=", 1))
        rows.extend(compare(cases, degraded, baseline))
    for r in rows:
        print(
            f"{r['variant']:<11} {r['level']:<8} {r['ranker']:<12} u{r['size']:<5} "
            f"{r['base']:.3f} -> {r['degraded']:.3f}  drop {r['drop']:+.3f}  "
            f"lost {len(r['lost'])} gained {len(r['gained'])} differ {r['rankings_differ']}"
        )
    if args.out:
        args.out.write_text(json.dumps(rows, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
