"""Dollars spent so far by agent runs, from their trace directories, against a cap.

The agent half of the doc-health test (store ``internal/doc-health/agent-degradation-plan.md``)
runs under a fixed budget. Before each batch the plan's runner calls this, and it exits 1 once
the traces' total reaches ``--cap`` or the next batch could cross it at ``--per-case``.

A case's cost is ``v5_dev_report.case_cost`` on its trace: priced usage for finished runs
and context tokens at ``--rate`` for stopped ones, as in every V5 report.

    python3 scripts/doc_health/agent_spend.py --cap 50 --next 10 --per-case 0.35 \\
        ~/rd-dh/agent/traces/*
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v5_dev_report import PRICES, case_cost

# The per-token rate the agent-runs manifest records for every composer-2.5 cell.
RATE_USD_PER_MTOK = 0.257


def spend(trace_dir: Path, prices: dict, rate: float) -> tuple[int, float]:
    traces = sorted(trace_dir.glob("*.json")) if trace_dir.is_dir() else []
    return len(traces), sum(case_cost(json.loads(t.read_text()), prices, rate)[0] for t in traces)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace_dirs", type=Path, nargs="*")
    parser.add_argument("--cap", type=float, required=True, help="dollars for every listed run")
    parser.add_argument("--next", type=int, default=0, help="cases the next batch will run")
    parser.add_argument("--per-case", type=float, default=0.0, help="dollars allowed per case")
    parser.add_argument("--model", default="composer-2.5", choices=sorted(PRICES))
    parser.add_argument("--rate", type=float, default=RATE_USD_PER_MTOK)
    args = parser.parse_args(argv)

    total = 0.0
    for trace_dir in args.trace_dirs:
        cases, usd = spend(trace_dir, PRICES[args.model], args.rate)
        total += usd
        print(f"{trace_dir}\t{cases} cases\t${usd:.2f}")
    projected = total + args.next * args.per_case
    print(f"total\t${total:.2f}\tafter next batch at most ${projected:.2f}\tcap ${args.cap:.2f}")
    return 0 if projected < args.cap else 1


if __name__ == "__main__":
    raise SystemExit(main())
