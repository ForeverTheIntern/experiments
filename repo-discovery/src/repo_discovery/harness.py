"""Run a ranker over cases and their universes, producing predictions."""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from repo_discovery.cases import Case, Query, repo_key
from repo_discovery.contract import Universe, resolve_universe
from repo_discovery.predictions import Prediction
from repo_discovery.rankers import Ranker

DEFAULT_DEPTH = 100


@dataclass
class RunLog:
    """Universe problems that do not stop a run but can make its numbers wrong.

    ``gold_missing``: gold repositories absent from the case's universe can never be found.
    ``after_cutoff``: repositories snapshotted after the case cutoff may leak the change.
    """

    gold_missing: dict[str, list[str]] = field(default_factory=dict)
    after_cutoff: dict[str, list[str]] = field(default_factory=dict)


def _checked(ranking: Sequence[str], universe: Universe, ranker: str) -> tuple[str, ...]:
    known = universe.keys()
    keys = [repo_key(r) for r in ranking]
    unknown = sorted({r for r, k in zip(ranking, keys, strict=True) if k not in known})
    if unknown:
        raise ValueError(f"{ranker} ranked repositories outside the universe: {unknown[:5]}")
    if len(set(keys)) != len(keys):
        raise ValueError(f"{ranker} ranked a repository more than once")
    return tuple(ranking)


def run(
    ranker: Ranker,
    cases: Sequence[Case],
    universe_root: Path,
    depth: int = DEFAULT_DEPTH,
    log: RunLog | None = None,
) -> Iterator[Prediction]:
    """Yield one prediction per case, keeping the top ``depth`` repositories."""
    loaded: dict[Path, Universe] = {}
    for case in cases:
        root = resolve_universe(universe_root, case.case_id)
        if root not in loaded:
            loaded[root] = Universe.load(root)
        universe = loaded[root]
        if log is not None:
            keys = universe.keys()
            missing = [g for g in case.gold_repos if repo_key(g) not in keys]
            if missing:
                log.gold_missing[case.case_id] = missing
            cutoff = datetime.fromisoformat(case.cutoff).date()
            late = [
                e.repo for e in universe.entries if date.fromisoformat(e.snapshot_date) > cutoff
            ]
            if late:
                log.after_cutoff[case.case_id] = late
        started = time.perf_counter()
        ranking = ranker.rank(Query.from_case(case), universe.view())
        seconds = time.perf_counter() - started
        yield Prediction(
            case_id=case.case_id,
            ranker=ranker.name,
            ranking=_checked(ranking, universe, ranker.name)[:depth],
            universe=str(root),
            universe_size=len(universe),
            seconds=round(seconds, 6),
        )
