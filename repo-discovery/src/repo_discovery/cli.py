"""Command line: ``repo-discovery <command>``."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from repo_discovery import cards, harness, metrics, split, wideswe
from repo_discovery.cases import SPLITS, VISIBILITIES, Case, read_cases, select_cases, write_cases
from repo_discovery.contract import Universe
from repo_discovery.predictions import read_predictions, write_predictions
from repo_discovery.rankers import available, make_ranker


def _default_cases_path(cache_dir: Path | None) -> Path:
    return (cache_dir or wideswe.cache_root()) / "cases" / f"wideswe-{wideswe.COMMIT[:12]}.jsonl"


def _selected(args: argparse.Namespace) -> list[Case]:
    chosen = None if args.split == "all" else args.split
    return select_cases(read_cases(args.cases), split=chosen, visibility=args.visibility)


def describe(cases: Sequence[Case]) -> str:
    table = Counter((c.split or "unsplit", c.scope_visibility or "unknown") for c in cases)
    splits = sorted({s for s, _ in table})
    header = f"{'split':<8}" + "".join(f"{v:>10}" for v in VISIBILITIES) + f"{'total':>10}"
    lines = [header]
    for s in splits:
        row = [table[(s, v)] for v in VISIBILITIES]
        ecosystems = len({c.ecosystem for c in cases if (c.split or "unsplit") == s})
        lines.append(
            f"{s:<8}" + "".join(f"{n:>10}" for n in row) + f"{sum(row):>10}"
            f"   ({ecosystems} ecosystems)"
        )
    totals = [sum(table[(s, v)] for s in splits) for v in VISIBILITIES]
    lines.append(f"{'all':<8}" + "".join(f"{n:>10}" for n in totals) + f"{sum(totals):>10}")
    return "\n".join(lines)


def cmd_fetch(args: argparse.Namespace) -> int:
    print(wideswe.fetch(args.cache_dir))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    root = args.wideswe_dir or wideswe.fetch(args.cache_dir)
    cases = split.assign_splits(wideswe.load(root))
    out = args.out or _default_cases_path(args.cache_dir)
    write_cases(out, cases)
    if args.manifest:
        split.write_manifest(args.manifest, split.manifest(cases))
    print(f"wrote {len(cases)} cases to {out}")
    print(describe(cases))
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    print(describe(read_cases(args.cases)))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cases = _selected(args)
    ranker = make_ranker(args.ranker, seed=args.seed)
    log = harness.RunLog()
    count = write_predictions(
        args.out, harness.run(ranker, cases, args.universes, depth=args.depth, log=log)
    )
    print(f"wrote {count} predictions from {ranker.name} to {args.out}")
    if log.gold_missing:
        print(
            f"warning: {len(log.gold_missing)} cases have gold repositories outside their universe",
            file=sys.stderr,
        )
    if log.after_cutoff:
        print(
            f"warning: {len(log.after_cutoff)} cases have universe snapshots after their cutoff",
            file=sys.stderr,
        )
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    cases = _selected(args)
    predictions = [p for path in args.predictions for p in read_predictions(path)]
    reports = metrics.evaluate(cases, predictions, n_boot=args.n_boot, seed=args.seed)
    print(metrics.format_reports(reports))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps([r.to_dict() for r in reports], indent=2) + "\n")
    return 0


def cmd_cards(args: argparse.Namespace) -> int:
    view = Universe.load(args.universe).view()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for card in cards.cards_for(view, budget=args.budget):
            handle.write(json.dumps(card.to_dict(), ensure_ascii=False) + "\n")
    print(f"wrote {len(view)} cards ({args.budget}-word budget) to {args.out}")
    return 0


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument(
        "--split",
        choices=[*SPLITS, "all"],
        default="dev",
        help="cases to use (default: dev, so the test split is only scored on purpose)",
    )
    parser.add_argument(
        "--visibility",
        nargs="+",
        choices=VISIBILITIES,
        help="keep only these scope_visibility classes",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-discovery", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser(
        "fetch-wideswe", help="fetch the pinned WideSWE files into the cache"
    )
    fetch.add_argument("--cache-dir", type=Path)
    fetch.set_defaults(func=cmd_fetch)

    build = commands.add_parser(
        "build-cases", help="WideSWE -> cases JSONL with split and visibility"
    )
    build.add_argument("--cache-dir", type=Path)
    build.add_argument(
        "--wideswe-dir", type=Path, help="existing WideSWE checkout; skips the fetch"
    )
    build.add_argument("--out", type=Path, help="default: <cache>/cases/wideswe-<commit>.jsonl")
    build.add_argument("--manifest", type=Path, help="also write the ecosystem split manifest")
    build.set_defaults(func=cmd_build)

    desc = commands.add_parser("describe", help="count cases by split and visibility")
    desc.add_argument("--cases", type=Path, required=True)
    desc.set_defaults(func=cmd_describe)

    run = commands.add_parser("run", help="rank each case's universe and write predictions JSONL")
    _add_selection(run)
    run.add_argument("--universes", type=Path, required=True, help="universe dir, or one per case")
    run.add_argument("--ranker", choices=available(), required=True)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--depth", type=int, default=harness.DEFAULT_DEPTH)
    run.add_argument("--out", type=Path, required=True)
    run.set_defaults(func=cmd_run)

    evaluate = commands.add_parser("evaluate", help="metrics with ecosystem-clustered CIs")
    _add_selection(evaluate)
    evaluate.add_argument("--predictions", type=Path, nargs="+", required=True)
    evaluate.add_argument("--n-boot", type=int, default=2000)
    evaluate.add_argument("--seed", type=int, default=0)
    evaluate.add_argument("--out", type=Path, help="write the reports as JSON")
    evaluate.set_defaults(func=cmd_evaluate)

    card = commands.add_parser("cards", help="extractive cards for one universe, as JSONL")
    card.add_argument("--universe", type=Path, required=True, help="directory with universe.jsonl")
    card.add_argument("--budget", type=int, default=cards.DEFAULT_BUDGET, help="words per card")
    card.add_argument("--out", type=Path, required=True)
    card.set_defaults(func=cmd_cards)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
