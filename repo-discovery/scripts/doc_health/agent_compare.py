"""CR@10 of the agent on degraded packets against its baseline passes, paired by case.

The agent half of the doc-health test (store ``internal/doc-health/agent-degradation-plan.md``).
A ``--variant`` is a prediction file from ``v5_dev_run.py`` on a degraded overlay, or a
per-case export like the baselines'. A ``--baseline`` is a per-case export from the
selection inbox (``internal/selection/inbox/agent-runs/<cell>.jsonl``). Cases are keyed by
the ID the run saw: ``case_id_hidden`` in an export when it has one, else ``case_id``.

Per variant, on the cases every listed run finished: CR@10 of the variant and of each
baseline pass; cases ``lost`` (the variant misses, every pass got them) and ``gained``
(the reverse), with an exact two-sided sign test on the two; the same split by doc-health
tier (``--stratify``, keyed by real ID through ``--mapping``); and, with ``--degraded``,
the cases whose targets the variant left as they were, which serve as a within-run control.

    python3 scripts/doc_health/agent_compare.py \\
        --cases ~/rd-anon/u5000/cases-scrubbed.jsonl \\
        --mapping ~/rd-anon/u5000/private/mapping.json --stratify ~/rd-dh/stratify.json \\
        --baseline p1=INBOX/u5000-hidden-published-l4-p1.jsonl \\
        --baseline p2=INBOX/u5000-hidden-published-l4-p2.jsonl \\
        --variant no-readme=~/rd-dh/agent/pred/no-readme.jsonl \\
        --degraded no-readme=~/rd-dh/hidden/no-readme/L4/degraded.json --out OUT.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections.abc import Sequence
from pathlib import Path

from degrade import DEV_CASES, dev_cases

from repo_discovery.cases import Case, repo_key
from repo_discovery.metrics import case_metrics

METRIC = "CompleteRecall@10"
TIERS = ("low", "mid", "high")


def _pair(text: str) -> tuple[str, Path]:
    label, _, path = text.partition("=")
    if not path:
        raise argparse.ArgumentTypeError(f"expected LABEL=PATH, got {text!r}")
    return label, Path(path).expanduser()


def per_case(path: Path, cases: Sequence[Case]) -> dict[str, float]:
    """CR@10 by case ID from a prediction file or a per-case export."""
    gold = {c.case_id: [g.repo for g in c.gold] for c in cases}
    out: dict[str, float] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cid = row.get("case_id_hidden") or row.get("case_id")
        if cid is None:
            continue
        if cid not in gold:
            raise SystemExit(f"{path}: case {cid} is not a dev case of --cases")
        if cid in out:
            raise SystemExit(f"{path}: case {cid} appears twice")
        if "ranking" in row:
            out[cid] = case_metrics(gold[cid], row["ranking"], (10,))[METRIC]
        else:
            out[cid] = float(row[METRIC])
    return out


def sign_test(lost: int, gained: int) -> float:
    n = lost + gained
    if n == 0:
        return 1.0
    k = min(lost, gained)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def untouched(record: Path, cases: Sequence[Case]) -> set[str]:
    """Cases none of whose targets the variant changed."""
    packets = json.loads(record.read_text())["packets"]
    changed = {
        (repo_key(p["repo"]), p["commit"])
        for p in packets
        if p["files"][0] != p["files"][1] or p["capped"] is not None
    }
    return {
        c.case_id for c in cases if not any((repo_key(g.repo), g.commit) in changed for g in c.gold)
    }


def compare(
    variant: dict[str, float], baselines: dict[str, dict[str, float]], ids: Sequence[str]
) -> dict:
    lost = [c for c in ids if variant[c] < 1 and all(b[c] == 1 for b in baselines.values())]
    gained = [c for c in ids if variant[c] == 1 and all(b[c] < 1 for b in baselines.values())]
    mean = lambda xs: round(statistics.fmean(xs), 4) if xs else None  # noqa: E731
    return {
        "cases": len(ids),
        "variant": mean([variant[c] for c in ids]),
        **{name: mean([b[c] for c in ids]) for name, b in baselines.items()},
        "lost": len(lost),
        "gained": len(gained),
        "sign_p": round(sign_test(len(lost), len(gained)), 4),
        "lost_cases": sorted(lost),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--expect-cases", type=int, default=DEV_CASES)
    parser.add_argument("--baseline", type=_pair, action="append", required=True)
    parser.add_argument("--variant", type=_pair, action="append", required=True)
    parser.add_argument("--degraded", type=_pair, action="append", default=[])
    parser.add_argument("--mapping", type=Path, help="private/mapping.json of the hidden build")
    parser.add_argument("--stratify", type=Path, help="stratify.py output, for the tiers")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    cases = dev_cases(args.cases, args.expect_cases)
    baselines = {label: per_case(path, cases) for label, path in args.baseline}
    records = dict(args.degraded)
    tiers: dict[str, str] = {}
    if args.stratify:
        table = json.loads(args.stratify.read_text())["cases"]
        hidden = json.loads(args.mapping.read_text())["cases"] if args.mapping else {}
        for real, row in table.items():
            tiers[hidden.get(real, real)] = row["tier"]

    report = {}
    for label, path in args.variant:
        variant = per_case(path, cases)
        ids = sorted(set(variant).intersection(*(set(b) for b in baselines.values())))
        row = {"all": compare(variant, baselines, ids)}
        if tiers:
            row["tiers"] = {
                t: compare(variant, baselines, [c for c in ids if tiers.get(c) == t]) for t in TIERS
            }
        if label in records:
            same = untouched(records[label], cases)
            row["targets_unchanged"] = compare(variant, baselines, [c for c in ids if c in same])
            row["targets_changed"] = compare(variant, baselines, [c for c in ids if c not in same])
        report[label] = row
        a = row["all"]
        passes = " ".join(f"{n}={a[n]:.3f}" for n in baselines)
        print(
            f"{label:<12} cases={a['cases']:>2} variant={a['variant']:.3f} {passes} "
            f"lost={a['lost']} gained={a['gained']} p={a['sign_p']:.3f}"
        )
        for t, r in row.get("tiers", {}).items():
            if r["cases"]:
                print(
                    f"  {t:<5} cases={r['cases']:>2} variant={r['variant']:.3f} "
                    f"lost={r['lost']} gained={r['gained']}"
                )
    if args.out:
        args.out.write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
