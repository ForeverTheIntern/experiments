"""The preregistered test analysis (PREREGISTRATION.md §4): per-cell metrics on the test
split, pooled over passes, and paired comparisons with an ecosystem-cluster bootstrap.

    python test_report.py CELLS.json --comparisons COMPARISONS.json --registered COMMIT

COMMIT is the registration commit, which the checkout must descend from. The tables go to
stdout, and `--out FILE` also writes them as JSON. CELLS.json maps a cell name to
{"names": "real"|"hidden", "size": N, "cases": PATH, "predictions": [PATH, ...]} (one file
per pass). Comparisons are listed in COMPARISONS.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from repo_discovery.cases import read_cases, select_cases
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import read_predictions
from repo_discovery.split import check_registered

KS = (5, 10, 20, 50, 100)
METRICS = (
    "Hit@10",
    "CompleteRecall@10",
    "CompleteRecall@20",
    "CompleteRecall@50",
    "RequiredRecall@10",
    "LastRequiredRank",
)
N_BOOT, SEED = 2000, 0


def per_case(cell: dict) -> tuple[dict[str, dict[str, float]], dict[str, str], int]:
    cases = {c.case_id: c for c in select_cases(read_cases(Path(cell["cases"])), split="test")}
    passes = []
    for path in cell["predictions"]:
        ranked = {p.case_id: p.ranking for p in read_predictions(Path(path))}
        missing = sorted(set(cases) - set(ranked))
        if missing:
            raise SystemExit(f"{path}: {len(missing)} test cases have no prediction")
        passes.append(ranked)
    values = {}
    for case_id, case in cases.items():
        runs = [case_metrics(case.gold_repos, p[case_id], KS) for p in passes]
        values[case_id] = {m: statistics.fmean(r[m] for r in runs) for m in METRICS}
    return values, {i: c.ecosystem for i, c in cases.items()}, len(passes)


def bootstrap(diffs: np.ndarray, clusters: list[str]) -> tuple[float, float, float, float]:
    _, index = np.unique(np.asarray(clusters), return_inverse=True)
    sums, counts = np.bincount(index, weights=diffs), np.bincount(index)
    draws = np.random.default_rng(SEED).integers(0, len(sums), size=(N_BOOT, len(sums)))
    reps = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    low, high = np.quantile(reps, [0.025, 0.975])
    return float(diffs.mean()), float(low), float(high), float((reps <= 0).mean())


def holm(pvalues: dict[str, float], alpha: float = 0.05) -> dict[str, tuple[float, bool]]:
    order = sorted(pvalues, key=pvalues.get)
    out, running, rejecting = {}, 0.0, True
    m = len(order)
    for i, name in enumerate(order):
        adjusted = min(1.0, max(running, (m - i) * pvalues[name]))
        running = adjusted
        rejecting = rejecting and adjusted <= alpha
        out[name] = (adjusted, rejecting)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cells", type=Path)
    p.add_argument("--comparisons", type=Path, required=True)
    p.add_argument(
        "--registered",
        metavar="COMMIT",
        help="the commit that registered the preregistration; needed to read test cases",
    )
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    check_registered(args.registered)
    spec = json.loads(args.cells.read_text())
    comparisons = json.loads(args.comparisons.read_text())
    results, eco = {}, {}
    for name, cell in spec.items():
        values, ecosystems, passes = per_case(cell)
        eco[name] = ecosystems
        ids = sorted(values)
        clusters = [ecosystems[i] for i in ids]
        summary = {}
        for m in METRICS:
            arr = np.array([values[i][m] for i in ids])
            mean, low, high, _ = bootstrap(arr, clusters)
            summary[m] = [round(mean, 4), round(low, 4), round(high, 4)]
        results[name] = {
            **cell,
            "passes": passes,
            "cases": len(ids),
            "metrics": summary,
            "per_case": values,
        }
    rows, confirmatory = [], {}
    for comp in comparisons:
        a, b, metric = comp["a"], comp["b"], comp.get("metric", "CompleteRecall@10")
        ids = sorted(results[a]["per_case"])
        if ids != sorted(results[b]["per_case"]):
            raise SystemExit(f"{a} and {b} cover different cases")
        diffs = np.array(
            [results[a]["per_case"][i][metric] - results[b]["per_case"][i][metric] for i in ids]
        )
        mean, low, high, p_one = bootstrap(diffs, [eco[a][i] for i in ids])
        row = {
            **comp,
            "metric": metric,
            "diff": round(mean, 4),
            "ci": [round(low, 4), round(high, 4)],
            "p_one_sided": round(p_one, 4),
            "won": int((diffs > 0).sum()),
            "lost": int((diffs < 0).sum()),
        }
        rows.append(row)
        if comp.get("family") == "confirmatory":
            confirmatory[comp["id"]] = p_one
    adjusted = holm(confirmatory)
    for row in rows:
        if row.get("id") in adjusted:
            row["p_holm"], row["reject"] = round(adjusted[row["id"]][0], 4), adjusted[row["id"]][1]

    print("# Test results (59 cases, 30 ecosystems; generated by test_report.py)\n")
    print("| Cell | Names | Size | Passes | " + " | ".join(METRICS) + " |")
    print("|---|---|--:|--:|" + "--:|" * len(METRICS))
    for name, r in results.items():
        cells = " | ".join(f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]" for v in r["metrics"].values())
        print(f"| {name} | {r['names']} | {r['size']} | {r['passes']} | {cells} |")
    print(
        "\n| ID | Comparison | Metric | Diff [95% CI] | Won / lost | p (one-sided) "
        "| Holm p | Reject |"
    )
    print("|---|---|---|---|---|--:|--:|---|")
    for row in rows:
        print(
            f"| {row.get('id', '')} | {row['a']} vs {row['b']} | {row['metric']} | "
            f"{row['diff']:+.3f} [{row['ci'][0]:+.3f}, {row['ci'][1]:+.3f}] | "
            f"{row['won']} / {row['lost']} | {row['p_one_sided']:.4f} | "
            f"{row.get('p_holm', '')} | {row.get('reject', '')} |"
        )
    if args.out:
        slim = {k: {kk: vv for kk, vv in v.items() if kk != "per_case"} for k, v in results.items()}
        args.out.write_text(json.dumps({"cells": slim, "comparisons": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
