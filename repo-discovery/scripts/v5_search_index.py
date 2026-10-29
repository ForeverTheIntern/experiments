"""Prebuild each dev case's ``search_repos`` keyword index, so no agent waits on a build.

One SQLite file per case, ``OUT/<case_id>.sqlite``: the ``bm25f`` index over that case's
universe (the keyword half of ``hybrid-rrf`` too), tagged with its candidates' digest, so
a server refuses a file built over other candidates. A file already there is checked
against its case and kept if it matches. Each build appends a line to
``OUT/manifest.jsonl`` with the case's candidates, build seconds and bytes.

Cases are sorted by cutoff and split into one contiguous run per worker, so each worker's
document cache holds the packets its neighbouring universes share.

    python3 scripts/v5_search_index.py --cases ~/rd-cases.jsonl \\
        --universes ~/rd-corpus-u10000/universes/monthly/u10000 \\
        --out ~/.cache/rd-search-index/real-u10000 --workers 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from repo_discovery.cases import read_cases, select_cases
from repo_discovery.contract import Universe, resolve_universe
from repo_discovery.rankers.bm25 import BM25Ranker, KeywordIndex, candidate_digest


def matches(path: Path, fields: tuple[str, ...], digest: str) -> bool:
    if not path.is_file():
        return False
    try:
        KeywordIndex.open(path, fields, digest).close()
    except ValueError:
        return False
    return True


def build_run(universes: Path, case_ids: list[str], out: Path) -> list[str]:
    """Build the indexes of ``case_ids`` in order; returns the ones it built."""
    ranker = BM25Ranker()
    built = []
    for case_id in case_ids:
        started = time.monotonic()
        view = Universe.load(resolve_universe(universes, case_id)).view()
        digest = candidate_digest(view)
        target = out / f"{case_id}.sqlite"
        if matches(target, ranker.fields, digest):
            continue
        ranker.index(view).save(target, digest)
        row = {
            "case_id": case_id,
            "candidates": len(view.candidates),
            "build_s": round(time.monotonic() - started, 1),
            "bytes": target.stat().st_size,
        }
        with (out / "manifest.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        print(f"{case_id}: {row['build_s']}s, {row['bytes'] / 1e6:.0f} MB", flush=True)
        built.append(case_id)
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--universes", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    cases = sorted(select_cases(read_cases(args.cases), split="dev"), key=lambda c: c.cutoff)
    ids = [c.case_id for c in cases]
    args.out.mkdir(parents=True, exist_ok=True)
    size = -(-len(ids) // args.workers)
    runs = [ids[i : i + size] for i in range(0, len(ids), size)]
    started = time.monotonic()
    with ProcessPoolExecutor(len(runs)) as pool:
        futures = [pool.submit(build_run, args.universes, run, args.out) for run in runs]
        built = [case_id for future in futures for case_id in future.result()]
    missing = [i for i in ids if not (args.out / f"{i}.sqlite").is_file()]
    print(
        f"{len(built)} built, {len(ids) - len(built)} kept, {len(missing)} missing, "
        f"{time.monotonic() - started:.0f}s",
        flush=True,
    )
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
