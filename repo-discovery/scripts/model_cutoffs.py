"""When each case's change went public, against the dates of the models that rank it.

Usage::

    python scripts/model_cutoffs.py --cases CASES.jsonl --out merged.jsonl \\
        [--date composer-2.5-cutoff=2026-03-01 --date composer-2.5-release=2026-05-18]

A case's change is public once its last target pull request merges, so a model trained
after that date may have read the change itself, not only the repositories' names. For
every target in WideSWE's ``repos.yaml`` this dates ``merge_commit`` (``gold_commit``
when there is no merge commit) with a shallow ``git fetch``, needing no GitHub token, and
takes the latest per case. It prints, per split, how many cases merged before each
``--date``. ``--out`` keeps one line per case (case ID, split, ecosystem, merge dates),
which carries case IDs and stays out of git. Dates are cached next to the WideSWE fetch.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

import yaml

from repo_discovery import wideswe
from repo_discovery.cases import read_cases
from repo_discovery.universe.evidence import commit_dates_git

DEFAULT_DATES = {
    # Cursor publishes no knowledge cutoff for composer-2.5; this is a third-party listing.
    "composer-2.5-reported-cutoff": "2026-03-01",
    "composer-2.5-release": "2026-05-18",
    "gpt-5.4-nano-release": "2026-03-17",
}


def target_merges(root: Path, ecosystem: str, case_id: str) -> list[tuple[str, str]]:
    doc = yaml.safe_load((root / "cases" / ecosystem / case_id / "repos.yaml").read_text())
    out = []
    for spec in doc["repos"].values():
        if spec.get("included_in_task"):
            commit = spec.get("merge_commit") or spec.get("gold_commit")
            out.append((wideswe.github_repo(spec["url"]), str(commit)))
    return out


def merge_dates(pairs: set[tuple[str, str]], cache: Path) -> dict[tuple[str, str], str | None]:
    known = json.loads(cache.read_text()) if cache.exists() else {}
    todo = sorted(p for p in pairs if f"{p[0]}@{p[1]}" not in known)
    if todo:
        for (repo, commit), when in commit_dates_git(todo, cache.parent / "git").items():
            known[f"{repo}@{commit}"] = when
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(known, indent=1, sort_keys=True))
    return {tuple(k.split("@", 1)): v for k, v in known.items()}  # type: ignore[misc]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--date", action="append", default=[], help="LABEL=YYYY-MM-DD")
    args = parser.parse_args(argv)
    dates = dict(DEFAULT_DATES) | dict(d.split("=", 1) for d in args.date)

    root = wideswe.fetch()
    cases = read_cases(args.cases)
    merges = {c.case_id: target_merges(root, c.ecosystem, c.case_id) for c in cases}
    cache = wideswe.cache_root() / "model-cutoffs" / "merge-dates.json"
    found = merge_dates({p for ps in merges.values() for p in ps}, cache)

    rows, missing = [], 0
    for case in cases:
        when = [found.get(p) for p in merges[case.case_id]]
        missing += sum(w is None for w in when)
        known = sorted(w for w in when if w)
        rows.append(
            {
                "case_id": case.case_id,
                "split": case.split,
                "ecosystem": case.ecosystem,
                "cutoff": case.cutoff,
                "first_merge": known[0] if known else None,
                "last_merge": known[-1] if known else None,
            }
        )
    if args.out:
        args.out.write_text("".join(json.dumps(r) + "\n" for r in rows))

    print(f"{len(rows)} cases; {missing} target merge commits could not be dated")
    labels = list(dates)
    print("split  cases  " + "  ".join(f"merged before {k} ({v})" for k, v in dates.items()))
    for split in ("dev", "test", None):
        chosen = [r for r in rows if split is None or r["split"] == split]
        counts = Counter()
        for r in chosen:
            if r["last_merge"]:
                merged = date.fromisoformat(r["last_merge"][:10])
                for label in labels:
                    counts[label] += merged < date.fromisoformat(dates[label])
        cells = "  ".join(f"{counts[label]:>3}" for label in labels)
        print(f"{split or 'all':<6} {len(chosen):>5}  {cells}")
    merged = sorted(r["last_merge"][:10] for r in rows if r["last_merge"])
    if merged:
        print(
            f"last merges range from {merged[0]} to {merged[-1]}; median {merged[len(merged) // 2]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
