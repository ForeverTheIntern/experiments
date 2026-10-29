"""Fetch cost per case, as in the fetch-level report's cost table.

A case pays for its own universe. At a static level it fetches every candidate's packet
at that level; the open-N control fetches only the L4 packets of a seed ranking's top N.
Costs are ``fetch_levels.packet_cost``'s REST-equivalent requests and stored bytes,
averaged over the cases with no rounding before the last step. MB are 10^6 bytes.

``--level NAME=CORPUS`` names a level's corpus (``fetch_levels restrict`` output, or the
full corpus for L4). ``--seed`` is the seed ranking's predictions, with ``{size}`` standing
for the universe size; the opened packets come from the L4 corpus.

    python3 scripts/case_cost.py --cases CASES --split dev --sizes 250 1000 5000 \
        --level L1=LEVELS/L1 --level L2=LEVELS/L2 --level L3=LEVELS/L3 --level L4=CORPUS \
        --seed 'PREDS/bm25f-open0--L4--u{size}.jsonl' --budgets 20 50 --json OUT.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

from repo_discovery.cases import SPLITS, read_cases, select_cases
from repo_discovery.fetch_levels import case_costs
from repo_discovery.predictions import read_predictions

MB = 10**6


def _universes(corpus: Path, size: int) -> Path:
    return corpus / "universes" / "monthly" / f"u{size}"


def _mean(costs) -> dict[str, float]:
    return {
        "calls": statistics.fmean(c.calls for c in costs),
        "bytes": statistics.fmean(c.bytes for c in costs),
    }


def _mb(size: float) -> str:
    mb = size / MB
    return f"{mb:,.2f}" if mb < 1 else f"{mb:,.1f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--split", choices=[*SPLITS, "all"], default="dev")
    parser.add_argument("--sizes", type=int, nargs="+", required=True)
    parser.add_argument("--level", action="append", default=[], metavar="NAME=CORPUS")
    parser.add_argument("--seed", help="seed predictions path, with {size} for the size")
    parser.add_argument("--budgets", type=int, nargs="+", default=[])
    parser.add_argument("--json", type=Path, help="also write the unrounded means here")
    args = parser.parse_args(argv)

    levels = {}
    for spec in args.level:
        name, sep, path = spec.partition("=")
        if not sep:
            parser.error(f"--level wants NAME=CORPUS, got {spec!r}")
        levels[name] = Path(path).expanduser()
    if args.seed and "L4" not in levels:
        parser.error("--seed opens L4 packets: name the L4 corpus with --level L4=CORPUS")
    cases = select_cases(read_cases(args.cases), None if args.split == "all" else args.split)

    rows: dict[str, dict[int, dict[str, float]]] = {}
    for name, corpus in levels.items():
        rows[name] = {n: _mean(case_costs(cases, _universes(corpus, n))) for n in args.sizes}
        print(f"{name}: {rows[name]}", file=sys.stderr, flush=True)
    if args.seed:
        for n in args.sizes:
            path = Path(args.seed.format(size=n)).expanduser()
            seed = {p.case_id: p.ranking for p in read_predictions(path)}
            for budget in args.budgets:
                opened = {c.case_id: seed[c.case_id][:budget] for c in cases}
                costs = case_costs(cases, _universes(levels["L4"], n), opened)
                rows.setdefault(f"L0, open top {budget} at L4", {})[n] = _mean(costs)

    head = "".join(f" U{n} requests / case | U{n} MB / case |" for n in args.sizes)
    print(f"\n{len(cases)} cases; MB are 10^6 bytes.\n")
    print(f"| Fetch |{head}")
    print("|---|" + "---|---|" * len(args.sizes))
    for name, by_size in rows.items():
        cells = "".join(
            f" {by_size[n]['calls']:,.0f} | {_mb(by_size[n]['bytes'])} |" for n in args.sizes
        )
        print(f"| {name} |{cells}")
    if args.seed and "L4" in rows:
        print()
        for name, by_size in rows.items():
            if not name.startswith("L0, open"):
                continue
            for n in args.sizes:
                full = rows["L4"][n]
                print(
                    f"- {name}, U{n}: {by_size[n]['calls'] / full['calls']:.1%} of L4's requests, "
                    f"{by_size[n]['bytes'] / full['bytes']:.1%} of its bytes"
                )
    if args.json:
        args.json.write_text(
            json.dumps(
                {"cases": len(cases), "split": args.split, "mb_bytes": MB, "rows": rows}, indent=1
            )
            + "\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
