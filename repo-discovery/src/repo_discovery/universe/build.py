"""Nested per-case universes, composed from shared, case-free layers.

A case's universe of size N holds, in order of precedence:

1. ``gold``: the case's target repositories at their base commits;
2. ``ecosystem`` / ``wideswe``: the case's WideSWE context repositories at their
   case commits;
3. ``ecosystem`` / ``same_owner``: the first ``HARDNEG_QUOTA[N]`` rows of the
   *same-owner layer* for the case's ecosystem and cutoff: other repositories of
   the ecosystem's GitHub owners at their latest default-branch commit at or
   before the cutoff (WideSWE's own policy for context repositories);
4. ``distractor`` / ``frame``: the first rows of the *frame layer* for the case's
   grid date: the stratified pool order, keeping repositories that existed and
   had a default-branch commit at the grid date, each at that commit.

Rows 1 and 2 come from the local WideSWE checkout. Layers 3 and 4 never depend on
which repositories a case targets (layers exclude every WideSWE repository, under
any name, and every candidate that sits at a WideSWE commit), so they can be
committed without WideSWE labels. Each component is a prefix of a fixed order, so universes nest.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path

from repo_discovery.cases import Case, repo_key
from repo_discovery.contract import UNIVERSE_FILE, Universe

from .grid import parse_time, skew_days, snapshot_date, until_iso

SIZES = (50, 250, 1000, 5000, 10000)
# U10000 keeps U5000's same-owner share (1.2%), so it differs from U5000 only in size.
HARDNEG_QUOTA = {50: 5, 250: 15, 1000: 30, 5000: 60, 10000: 120}


@dataclass(frozen=True)
class Entry:
    repo: str
    commit: str
    snapshot_date: str
    role: str
    source: str


def cutoff_until(case: Case) -> str:
    return parse_time(case.cutoff).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(timestamp: str | None) -> str:
    if not timestamp:
        raise ValueError("missing commit date")
    return parse_time(timestamp).date().isoformat()


def _sorted(entries: list[Entry]) -> list[Entry]:
    return sorted(entries, key=lambda e: (repo_key(e.repo), e.commit))


def fixed_entries(case: Case, commit_dates: dict[tuple[str, str], str | None]) -> list[Entry]:
    """Gold and WideSWE context rows; ``snapshot_date`` is each commit's committer date."""
    out, taken = [], set()
    for role, repos in (("gold", case.gold), ("ecosystem", case.context)):
        for r in repos:
            if repo_key(r.repo) not in taken:
                taken.add(repo_key(r.repo))
                day = _day(commit_dates.get((r.repo, r.commit)))
                out.append(Entry(r.repo, r.commit, day, role, "wideswe"))
    return out


def _commit_at(snap: dict | None, until: str, created_by: datetime, stats: Counter) -> dict | None:
    if snap is None:
        stats["unresolved"] += 1
        return None
    if snap.get("missing", True):
        stats["skipped_missing"] += 1
        return None
    if parse_time(snap["created_at"]) > created_by:
        stats["skipped_created_after_snapshot"] += 1
        return None
    commit = snap["commits"].get(until)
    if not commit:
        stats["skipped_no_commit_at_snapshot"] += 1
    return commit


def wideswe_copies(
    cases: list[Case], current_names: dict[str, str | None], snaps: dict[str, dict]
) -> set[str]:
    """Candidate keys that are a WideSWE repository renamed, or that sit at a WideSWE commit.

    GitHub's listings and search use current names, so a renamed gold repository can
    come back as a candidate; mirrors such as docs copies share its commits.
    """
    known = {repo_key(n) for n in current_names.values() if n}
    known |= {repo_key(r.repo) for c in cases for r in (*c.gold, *c.context)}
    commits = {r.commit for c in cases for r in (*c.gold, *c.context)}
    out = set()
    for repo, snap in snaps.items():
        shas = {c["oid"] for c in (snap.get("commits") or {}).values() if c}
        if repo_key(snap.get("name_with_owner") or repo) in known or shas & commits:
            out.add(repo_key(repo))
    return out


def frame_layer(
    pool: list[dict],
    snaps: dict[str, dict],
    day: date,
    length: int,
    exclude: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[Entry], Counter]:
    """The first ``length`` pool repositories that existed and had a commit at ``day``."""
    until = until_iso(day)
    created_by = parse_time(until)
    stats: Counter[str] = Counter()
    out: list[Entry] = []
    for cand in pool:
        if len(out) >= length:
            break
        if repo_key(cand["repo"]) in exclude:
            stats["skipped_wideswe_copy"] += 1
            continue
        commit = _commit_at(snaps.get(cand["repo"]), until, created_by, stats)
        if commit:
            out.append(Entry(cand["repo"], commit["oid"], day.isoformat(), "distractor", "frame"))
    stats["kept"] = len(out)
    return out, stats


def same_owner_layer(
    candidates: list[dict],
    snaps: dict[str, dict],
    cutoff: str,
    length: int,
    exclude: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[Entry], Counter]:
    """The first ``length`` same-owner candidates that existed by ``cutoff``, at that time."""
    created_by = parse_time(cutoff)
    stats: Counter[str] = Counter()
    out: list[Entry] = []
    for cand in candidates:
        if len(out) >= length:
            break
        if repo_key(cand["repo"]) in exclude:
            stats["skipped_wideswe_copy"] += 1
            continue
        commit = _commit_at(snaps.get(cand["repo"]), cutoff, created_by, stats)
        if commit:
            day = _day(commit["committed_date"])
            out.append(Entry(cand["repo"], commit["oid"], day, "ecosystem", "same_owner"))
    stats["kept"] = len(out)
    return out, stats


def compose(
    fixed: list[Entry], same_owner: list[Entry], frame: list[Entry], size: int
) -> list[Entry]:
    taken = {repo_key(e.repo) for e in fixed}
    hn = [e for e in same_owner if repo_key(e.repo) not in taken][: HARDNEG_QUOTA[size]]
    taken |= {repo_key(e.repo) for e in hn}
    rest = [e for e in frame if repo_key(e.repo) not in taken][
        : max(0, size - len(fixed) - len(hn))
    ]
    return _sorted(fixed + hn + rest)


def compose_case(
    case: Case,
    sizes: tuple[int, ...],
    grid: str,
    frame_layers: dict[str, list[Entry]],
    same_owner_layers: dict[tuple[str, str], list[Entry]],
    commit_dates: dict[tuple[str, str], str | None],
) -> tuple[dict[int, list[Entry]], dict]:
    cutoff = parse_time(case.cutoff)
    day = snapshot_date(cutoff, grid)
    fixed = fixed_entries(case, commit_dates)
    hn = same_owner_layers.get((case.ecosystem, cutoff_until(case)), [])
    frame = frame_layers.get(day.isoformat(), [])
    universes = {size: compose(fixed, hn, frame, size) for size in sorted(sizes)}
    late = [
        (date.fromisoformat(e.snapshot_date) - cutoff.date()).days
        for e in fixed
        if e.snapshot_date > cutoff.date().isoformat()
    ]
    summary = {
        "case_id": case.case_id,
        "ecosystem": case.ecosystem,
        "grid": grid,
        "snapshot_date": day.isoformat(),
        "skew_days": round(skew_days(cutoff, day), 2),
        "fixed": len(fixed),
        "fixed_after_cutoff": len(late),
        "fixed_after_cutoff_max_days": max(late, default=0),
        "shortfall": {str(s): s - len(u) for s, u in universes.items() if len(u) < s},
        "sizes": {
            str(size): dict(sorted(Counter(f"{e.role}:{e.source}" for e in u).items()))
            for size, u in universes.items()
        },
    }
    return universes, summary


def check_nested(universes: dict[int, list[Entry]]) -> None:
    for small, big in pairwise(sorted(universes)):
        a = {(e.repo, e.commit) for e in universes[small]}
        b = {(e.repo, e.commit) for e in universes[big]}
        if not a <= b:
            raise AssertionError(f"U{small} is not a subset of U{big}")


def write_universe(directory: Path, entries: list[Entry]) -> Universe:
    """Write ``universe.jsonl`` and read it back through the harness contract."""
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / UNIVERSE_FILE, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(asdict(e)) + "\n")
    universe = Universe.load(directory)
    if len(universe) != len(entries):
        raise AssertionError(f"{directory}: wrote {len(entries)} entries, read {len(universe)}")
    return universe


def layer_name(ecosystem: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", ecosystem)


def write_frame_layer(path: Path, entries: list[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps({"repo": e.repo, "commit": e.commit}) + "\n")


def read_frame_layer(path: Path) -> list[Entry]:
    day = path.stem
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return [Entry(r["repo"], r["commit"], day, "distractor", "frame") for r in rows]


def write_same_owner_layers(path: Path, layers: dict[str, list[Entry]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for cutoff in sorted(layers):
            for e in layers[cutoff]:
                row = {"cutoff": cutoff, "repo": e.repo, "commit": e.commit}
                fh.write(json.dumps(row | {"snapshot_date": e.snapshot_date}) + "\n")


def read_same_owner_layers(path: Path) -> dict[str, list[Entry]]:
    out: dict[str, list[Entry]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                entry = Entry(r["repo"], r["commit"], r["snapshot_date"], "ecosystem", "same_owner")
                out.setdefault(r["cutoff"], []).append(entry)
    return out


def summarize(summaries: list[dict]) -> dict:
    skews = [s["skew_days"] for s in summaries]
    late = [s["fixed_after_cutoff_max_days"] for s in summaries if s["fixed_after_cutoff"]]
    return {
        "cases": len(summaries),
        "snapshot_dates": len({s["snapshot_date"] for s in summaries}),
        "skew_days": {"min": min(skews), "median": statistics.median(skews), "max": max(skews)},
        "cases_with_fixed_repos_after_cutoff": len(late),
        "fixed_repos_after_cutoff": sum(s["fixed_after_cutoff"] for s in summaries),
        "fixed_after_cutoff_days": (
            {"median": statistics.median(late), "max": max(late)} if late else None
        ),
        "shortfall_cases": {
            size: sum(size in s["shortfall"] for s in summaries)
            for size in sorted({k for s in summaries for k in s["shortfall"]}, key=int)
        },
    }
