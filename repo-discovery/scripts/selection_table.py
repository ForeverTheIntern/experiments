"""Gather every dev-split result into one table and draft each size's cost frontier.

Usage::

    python scripts/selection_table.py --registry cells.yaml \\
        --cases ~/rd-cases.jsonl --manifest splits/wideswe-release120.json \\
        --inbox inbox/agent-runs --inbox inbox/fetch-level --out out/

The registry (YAML) holds the shared cost tables (``fetch``: requests and MB per case
universe by level and size; ``index``: build milliseconds, KB and dollars per repository
by index and level), the hidden-name builds (``salts``: each one's scrubbed cases and
mapping), and ``cells``: one entry per combination, with its reported values, its source
and commit, and optionally a ``predictions`` or ``per_case`` file. Each inbox file is one
cell (see ``repo_discovery.selection.inbox_entries``) and replaces that cell's reported
values; where the registry or another inbox file already has per-case data for the cell,
the two are compared case for case. Every file is audited against the dev split first:
exactly the 61 dev cases, no test case, none twice.

Writes ``cells.csv`` and ``cells.json`` (every row), ``frontier.md`` (one table per size,
names and salt, with frontier standing), ``frontier.json``, ``audit.txt`` (the files read
and the report cross-checks), and ``charts/<group>.svg``.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from repo_discovery import selection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--inbox", type=Path, action="append", default=[])
    parser.add_argument("--metric", default="CompleteRecall@10", choices=selection.METRICS)
    parser.add_argument("--cost-tolerance", type=float, default=selection.COST_TOLERANCE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    registry = selection.load_registry(args.registry)
    base = args.registry.parent
    split = selection.load_split(args.cases, args.manifest, registry.get("salts", {}), base)
    inbox = [p for p in args.inbox if p.exists()]
    rows = selection.collect(registry, split, base, inbox)
    waiting = selection.undescribed(inbox)
    pooled = selection.pool_runs(rows, split)
    standings = selection.frontier(pooled, args.metric, tolerance=args.cost_tolerance)

    args.out.mkdir(parents=True, exist_ok=True)
    records = [selection.record(r) for r in sorted(rows, key=_order)]
    (args.out / "cells.json").write_text(json.dumps(records, indent=1) + "\n", encoding="utf-8")
    with (args.out / "cells.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    (args.out / "frontier.md").write_text(
        selection.frontier_markdown(standings, args.metric), encoding="utf-8"
    )
    summary = {
        selection.group_title(group): [
            {
                **selection.record(s.row),
                "on_frontier": s.on_frontier,
                "dominated_by": [d.cell.label() for d in s.dominated_by],
                "one_case_from": [n.cell.label() for n in s.near],
            }
            for s in members
        ]
        for group, members in standings.items()
    }
    (args.out / "frontier.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    charts = args.out / "charts"
    charts.mkdir(exist_ok=True)
    for group, members in standings.items():
        size, names, salt = group
        name = f"u{size}-{names}" + (f"-{salt}" if salt else "")
        (charts / f"{name}.svg").write_text(
            selection.svg_chart(group, members, args.metric), encoding="utf-8"
        )

    audit = [
        f"dev cases: {len(split.cases)} in {len({c.ecosystem for c in split.cases})} ecosystems",
        f"hidden-name builds loaded: {', '.join(sorted(split.salts)) or 'none'}",
        f"rows: {len(rows)}; combinations after pooling repeats: {len(pooled)}",
    ]
    for backing in selection.BACKINGS:
        audit.append(f"  {backing}: {sum(r.backing == backing for r in rows)}")
    audit.append(f"inbox files left out, no cell line or manifest entry yet: {len(waiting)}")
    audit += [f"  {path}" for path in waiting]
    audit.append(
        f"cost tolerance: {args.cost_tolerance:.0%}, floors "
        + ", ".join(f"{k} {v:g}" for k, v in selection.COST_FLOOR.items())
    )
    audit.append("files read, each audited against the dev split:")
    for row in sorted(rows, key=_order):
        if row.data:
            label = f"{selection.group_title(row.cell.group)} | {row.cell.label()}"
            audit.append(f"  {label}: {row.data}")
    audit.append("cross-checks against reported values:")
    for row in sorted(rows, key=_order):
        for check in row.checks:
            audit.append(f"  {selection.group_title(row.cell.group)} | {row.cell.label()}: {check}")
    (args.out / "audit.txt").write_text("\n".join(audit) + "\n", encoding="utf-8")
    print("\n".join(audit[: 4 + len(selection.BACKINGS)]))
    print(f"inbox files left out, no cell yet: {len(waiting)}")
    mismatches = [c for r in rows for c in r.checks if c.startswith("MISMATCH")]
    print(f"cross-checks: {sum(len(r.checks) for r in rows)}, mismatches: {len(mismatches)}")
    return 0


def _order(row: selection.Row) -> tuple:
    size, names, salt = row.cell.group
    return (size, names, salt or "", -row.quality[next(iter(row.quality))].mean, row.cell.label())


if __name__ == "__main__":
    raise SystemExit(main())
