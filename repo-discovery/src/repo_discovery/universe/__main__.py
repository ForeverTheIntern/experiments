"""Command line for the universe build: ``python -m repo_discovery.universe <step>``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import pipeline
from .build import SIZES
from .grid import GRIDS


def _sizes(value: str) -> tuple[int, ...]:
    sizes = tuple(sorted(int(v) for v in value.split(",")))
    unknown = [s for s in sizes if s not in SIZES]
    if unknown:
        raise argparse.ArgumentTypeError(f"sizes must be among {SIZES}, got {unknown}")
    return sizes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m repo_discovery.universe")
    parser.add_argument("--cache", type=Path, help="cache root (default: repo-discovery cache)")
    parser.add_argument("--manifests", type=Path, default=pipeline.DEFAULT_MANIFESTS)
    parser.add_argument("--seed", type=int, default=pipeline.SEED)
    sub = parser.add_subparsers(dest="step", required=True)

    p = sub.add_parser("frame", help="count the frame and draw the stratified distractor pool")
    p.add_argument("--pool-target", type=int, default=pipeline.POOL_TARGET)
    p = sub.add_parser("extend-frame", help="grow the pool, keeping the committed pool as prefix")
    p.add_argument("--pool-target", type=int, required=True)
    sub.add_parser("hardneg", help="list same-owner hard negatives per ecosystem")

    p = sub.add_parser("extend", help="grow committed layers to U<size>, keeping them as prefixes")
    p.add_argument("--size", type=int, choices=SIZES, required=True)
    p.add_argument("--grid", choices=GRIDS, default="monthly")

    p = sub.add_parser("build", help="resolve snapshots and write nested per-case universes")
    p.add_argument("--sizes", type=_sizes, default=(50, 250, 1000))
    p.add_argument("--grid", choices=GRIDS, default="monthly")

    p = sub.add_parser("extract", help="extract evidence for every snapshot in U<size>")
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--size", type=int, default=1000)
    p.add_argument("--grid", choices=GRIDS, default="monthly")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--sample", type=int, help="extract this many random repositories only")
    p.add_argument("--sources", help="comma-separated: wideswe, same_owner, frame (default: all)")
    p.add_argument("--label", default="")

    p = sub.add_parser("materialize", help="write U<size> universes that link into the corpus")
    p.add_argument("--corpus", type=Path, required=True)
    p.add_argument("--size", type=int, default=1000)
    p.add_argument("--grid", choices=GRIDS, default="monthly")
    p.add_argument("--out", type=Path, help="default: <corpus>/universes/<grid>/u<size>")

    p = sub.add_parser("measure", help="write costs.json: extraction rates and grid comparison")
    p.add_argument("--sizes", type=_sizes, default=(50, 250, 1000))

    args = parser.parse_args(argv)
    manifests = args.manifests.resolve()
    if args.step == "frame":
        pipeline.cmd_frame(manifests, args.cache, args.seed, args.pool_target)
    elif args.step == "extend-frame":
        print(
            json.dumps(
                pipeline.cmd_extend_frame(manifests, args.cache, args.seed, args.pool_target)
            )
        )
    elif args.step == "hardneg":
        pipeline.cmd_hardneg(manifests, args.cache, args.seed)
    elif args.step == "extend":
        index = pipeline.cmd_extend(manifests, args.cache, args.size, args.grid)
        print(json.dumps(index["extensions"][-1]["cost"] | {"pairs": index["pairs"]}))
    elif args.step == "build":
        pipeline.cmd_build(manifests, args.cache, args.sizes, args.grid)
    elif args.step == "extract":
        sources = tuple(args.sources.split(",")) if args.sources else None
        run = pipeline.cmd_extract(
            manifests,
            args.cache,
            args.corpus,
            args.size,
            grid=args.grid,
            workers=args.workers,
            sample=args.sample,
            sources=sources,
            label=args.label,
            seed=args.seed,
        )
        print(json.dumps({k: v for k, v in run.items() if k != "errors"}, indent=1))
    elif args.step == "measure":
        report = pipeline.cmd_measure(manifests, args.cache, args.sizes)
        print(json.dumps(report, indent=1))
    elif args.step == "materialize":
        result = pipeline.cmd_materialize(
            manifests, args.cache, args.corpus, args.size, args.grid, args.out
        )
        print(json.dumps(result))


if __name__ == "__main__":
    main()
