"""Dev/test split by ecosystem, fixed by a committed seed.

Ecosystems with more than ``LARGE_ECOSYSTEM`` cases go to dev, so no single
ecosystem dominates the test set. The rest are shuffled with ``SEED`` and moved
to dev in that order until dev holds ``DEV_FRACTION`` of all cases.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from repo_discovery.cases import Case, Split

SEED = 20261028
DEV_FRACTION = 0.5
LARGE_ECOSYSTEM = 10


def assign_ecosystems(
    counts: Mapping[str, int],
    seed: int = SEED,
    dev_fraction: float = DEV_FRACTION,
    large: int = LARGE_ECOSYSTEM,
) -> dict[str, Split]:
    target = dev_fraction * sum(counts.values())
    splits: dict[str, Split] = {e: "dev" for e, n in counts.items() if n > large}
    dev_cases = sum(counts[e] for e in splits)
    rest = sorted(e for e in counts if e not in splits)
    random.Random(seed).shuffle(rest)
    for ecosystem in rest:
        if dev_cases < target:
            splits[ecosystem] = "dev"
            dev_cases += counts[ecosystem]
        else:
            splits[ecosystem] = "test"
    return dict(sorted(splits.items()))


def ecosystem_counts(cases: Iterable[Case]) -> dict[str, int]:
    return dict(sorted(Counter(c.ecosystem for c in cases).items()))


def assign_splits(cases: list[Case], seed: int = SEED) -> list[Case]:
    splits = assign_ecosystems(ecosystem_counts(cases), seed=seed)
    return [replace(c, split=splits[c.ecosystem]) for c in cases]


def manifest(cases: list[Case], seed: int = SEED) -> dict:
    counts = ecosystem_counts(cases)
    splits = assign_ecosystems(counts, seed=seed)
    return {
        "seed": seed,
        "dev_fraction": DEV_FRACTION,
        "large_ecosystem": LARGE_ECOSYSTEM,
        "ecosystems": {e: {"split": splits[e], "cases": n} for e, n in counts.items()},
    }


def write_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


REGISTRATION = "repo-discovery/PREREGISTRATION.md"
RUN_SPLITS = ("dev", "test", "all")


def add_split_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--split",
        choices=RUN_SPLITS,
        default="dev",
        help="cases to use (default: dev); test and all need --registered",
    )
    parser.add_argument(
        "--registered",
        metavar="COMMIT",
        help="the commit that registered the preregistration; needed to touch test cases",
    )


def check_registered(commit: str | None, repo: Path | None = None) -> str:
    """The full SHA of ``commit``, which must hold the preregistration and be an ancestor
    of the checked-out HEAD, so no test case is read before the plan is fixed."""
    if not commit:
        raise SystemExit(f"test cases need --registered COMMIT, the commit of {REGISTRATION}")
    cwd = repo or Path(__file__).resolve().parents[3]

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)

    sha = git("rev-parse", "--verify", f"{commit}^{{commit}}")
    if sha.returncode:
        raise SystemExit(f"--registered {commit}: not a commit in {cwd}")
    full = sha.stdout.strip()
    if git("cat-file", "-e", f"{full}:{REGISTRATION}").returncode:
        raise SystemExit(f"--registered {commit}: {REGISTRATION} is not in that commit")
    if git("merge-base", "--is-ancestor", full, "HEAD").returncode:
        raise SystemExit(f"--registered {commit}: not an ancestor of the checked-out HEAD")
    return full


def run_cases(cases: Iterable[Case], split: str, registered: str | None = None) -> list[Case]:
    """The cases a run script may touch: dev by default, test or all once registered."""
    if split not in RUN_SPLITS:
        raise ValueError(f"split must be one of {RUN_SPLITS}")
    if split != "dev":
        check_registered(registered)
    return [c for c in cases if split == "all" or c.split == split]
