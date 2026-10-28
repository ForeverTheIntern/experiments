"""Universe build steps: frame, hard negatives, build, extract, materialize, measure."""

from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from repo_discovery import wideswe
from repo_discovery.cases import Case, repo_key

from . import build, evidence, frame, hardneg, sampling, snapshots
from .build import HARDNEG_QUOTA, cutoff_until
from .github import GitHub
from .grid import GRIDS, parse_time, snapshot_date, until_iso

SEED = 20261028
POOL_TARGET = 8000
PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFESTS = PACKAGE_ROOT / "scripts" / "universe" / "manifests"


def log(message: str) -> None:
    print(f"[{datetime.now(UTC):%H:%M:%S}] {message}", file=sys.stderr, flush=True)


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def work_dir(cache: Path | None) -> Path:
    path = (cache or wideswe.cache_root()) / "universe"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_cases(cache: Path | None) -> list[Case]:
    return wideswe.load(wideswe.fetch(cache))


def _read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n")
    tmp.replace(path)


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def case_repos(cases: list[Case]) -> list[str]:
    seen = {}
    for case in cases:
        for r in (*case.gold, *case.context):
            seen.setdefault(repo_key(r.repo), r.repo)
    return sorted(seen.values(), key=repo_key)


def ecosystem_owners(cases: list[Case]) -> dict[str, list[str]]:
    owners: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        owners[case.ecosystem].update(r.repo.split("/")[0] for r in (*case.gold, *case.context))
    return {eco: sorted(names) for eco, names in sorted(owners.items())}


def repo_profile(gh: GitHub, repos: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(repos), 50):
        chunk = repos[i : i + 50]
        fields = " ".join(
            f'r{j}: repository(owner: "{r.split("/")[0]}", name: "{r.split("/")[1]}") '
            "{ primaryLanguage { name } diskUsage }"
            for j, r in enumerate(chunk)
        )
        data, _ = gh.graphql("query { " + fields + " }")
        for j, repo in enumerate(chunk):
            node = data.get(f"r{j}") or {}
            out[repo] = {
                "language": (node.get("primaryLanguage") or {}).get("name"),
                "size_kb": node.get("diskUsage") or 0,
            }
    return out


def cmd_frame(
    manifests: Path, cache: Path | None, seed: int = SEED, pool_target: int = POOL_TARGET
):
    gh = GitHub()
    cases = load_cases(cache)
    work = work_dir(cache)
    targets = case_repos(cases)
    profile = repo_profile(gh, targets)
    shares, lang_share, size_share = frame.allocation(list(profile.values()))
    steps = frame.frame_step_counts(gh)
    log(f"frame: {steps[-1]['count']} repositories after all filters")

    rng = random.Random(seed)
    cell_dir = work / "frame-cells"
    cells, summaries = {}, []
    for lang in frame.LANGUAGE_STRATA:
        for size, _, _ in frame.SIZE_BUCKETS:
            cache_file = cell_dir / f"{lang.replace('#', 'sharp').replace('+', 'p')}_{size}.json"
            target = math.ceil(shares[(lang, size)] * pool_target)
            cell_rng = random.Random(f"{seed}:{lang}:{size}")
            cached = _read_json(cache_file, None)
            if cached and cached["target"] == target:
                cell = frame.CellSample(**cached)
            else:
                cell = frame.sample_cell(gh, lang, size, target, cell_rng, log=log)
                _write_json(cache_file, cell.__dict__)
            cells[(lang, size)] = cell.items
            summaries.append(cell.summary())

    wideswe_keys = {repo_key(r) for r in targets}
    owner_keys = {o.lower() for owners in ecosystem_owners(cases).values() for o in owners}
    excluded: Counter[str] = Counter()
    kept_cells: dict[tuple[str, str], list[dict]] = {}
    seen: set[str] = set()
    for key, items in cells.items():
        kept = []
        for item in items:
            name = repo_key(item["repo"])
            if name in seen:
                excluded["duplicate_across_cells"] += 1
            elif name in wideswe_keys:
                excluded["wideswe_repository"] += 1
            elif name.split("/")[0] in owner_keys:
                excluded["owned_by_wideswe_ecosystem_owner"] += 1
            else:
                seen.add(name)
                kept.append(
                    {
                        **item,
                        "cell": f"{key[0]}/{key[1]}",
                        "topic_group": sampling.topic_group(item["topics"]),
                    }
                )
        kept_cells[key] = kept
    ordered = sampling.order_pool(kept_cells, shares, rng)
    for item in ordered:
        cell = next(s for s in summaries if f"{s['language']}/{s['size']}" == item["cell"])
        item["inclusion_prob"] = cell["inclusion_prob"]

    record = {
        "created_at": now(),
        "seed": seed,
        "source": "GitHub repository search API (api.github.com/search/repositories)",
        "query": frame.base_query(),
        "filter_steps": steps,
        "strata": {
            "language": list(frame.LANGUAGE_STRATA),
            "size_kb": [{"name": n, "min": lo, "max": hi} for n, lo, hi in frame.SIZE_BUCKETS],
            "topic_group": list(sampling.TOPIC_GROUP_NAMES),
        },
        "allocation": {
            "basis": f"language and size marginals of the {len(targets)} WideSWE repositories",
            "language_share": lang_share,
            "size_share": size_share,
            "topic": "proportional within each language x size cell (prefix balancing only)",
        },
        "pool_target": pool_target,
        "cells": summaries,
        "pool_drawn": sum(len(v) for v in cells.values()),
        "pool_excluded": dict(excluded),
        "pool_size": len(ordered),
        "pool_topic_groups": dict(Counter(i["topic_group"] for i in ordered).most_common()),
        "pool_languages": dict(Counter(i["cell"].split("/")[0] for i in ordered).most_common()),
        "search_requests": gh.calls["search"],
    }
    _write_json(work / "wideswe-profile.json", profile)
    _write_json(manifests / "frame" / "frame.json", record)
    _write_jsonl(manifests / "frame" / "pool.jsonl", ordered)
    log(f"pool: {len(ordered)} repositories ({gh.calls['search']} search requests)")
    return record


def cmd_hardneg(manifests: Path, cache: Path | None, seed: int = SEED):
    gh = GitHub()
    cases = load_cases(cache)
    current = {n for n in wideswe_names(gh, cases, work_dir(cache)).values() if n}
    order, steps = hardneg.candidates(
        gh, ecosystem_owners(cases), set(case_repos(cases)), seed, log, current
    )
    record = {
        "created_at": now(),
        "seed": seed,
        "min_size_kb": hardneg.MIN_SIZE_KB,
        "steps": steps,
        "order": order,
    }
    _write_json(manifests / "frame" / "same-owner.json", record)
    log(f"same-owner candidates: {sum(len(v) for v in order.values())}")
    return record


class SnapshotCache:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict] = _read_json(path, {})

    def missing(self, repo: str, untils: set[str]) -> set[str]:
        snap = self.data.get(repo)
        if snap is None:
            return untils
        if snap.get("missing") and not snap.get("error"):
            return set()
        return untils - set(snap["commits"])

    def resolve(self, gh: GitHub, wants: dict[str, set[str]]) -> int:
        todo = {}
        for repo, untils in wants.items():
            need = self.missing(repo, untils)
            if need:
                todo[repo] = need
        if not todo:
            return 0
        log(f"resolving {sum(map(len, todo.values()))} snapshots for {len(todo)} repositories")
        for repo, snap in snapshots.resolve_history(gh, todo, log=log).items():
            old = self.data.get(repo)
            if old and not old.get("missing") and not snap.get("missing"):
                old["commits"].update(snap["commits"])
            else:
                self.data[repo] = snap
        _write_json(self.path, self.data)
        return len(todo)


def _grid_days(cases: list[Case], grids: tuple[str, ...]) -> list[str]:
    return sorted(
        {snapshot_date(parse_time(c.cutoff), g).isoformat() for c in cases for g in grids}
    )


def _pool_wants(pool: list[dict], untils: list[str]) -> dict[str, set[str]]:
    wants = {}
    for item in pool:
        created = item.get("created_at") or "1970-01-01T00:00:00Z"
        wants[item["repo"]] = {u for u in untils if u >= created}
    return wants


def commit_date_map(gh: GitHub | None, cases: list[Case], work: Path) -> dict:
    """Committer dates of every gold and context commit, cached in the work directory."""
    path = work / "commit-dates.json"
    raw = _read_json(path, {})
    pairs = {(r.repo, r.commit) for c in cases for r in (*c.gold, *c.context)}
    todo = sorted(p for p in pairs if f"{p[0]}@{p[1]}" not in raw)
    if todo:
        for (repo, oid), when in snapshots.commit_dates(gh or GitHub(), todo).items():
            raw[f"{repo}@{oid}"] = when
        _write_json(path, raw)
    undated = sorted(k for k, v in raw.items() if not v)
    if undated:
        raise RuntimeError(f"no commit date for {undated[:5]}")
    return {tuple(k.split("@", 1)): v for k, v in raw.items()}


def _cutoffs_by_ecosystem(cases: list[Case]) -> dict[str, list[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        out[case.ecosystem].add(cutoff_until(case))
    return {eco: sorted(v) for eco, v in sorted(out.items())}


def wideswe_names(gh: GitHub | None, cases: list[Case], work: Path) -> dict[str, str | None]:
    """Current names of every gold and context repository, cached in the work directory."""
    path = work / "wideswe-names.json"
    raw = _read_json(path, {})
    todo = sorted({r.repo for c in cases for r in (*c.gold, *c.context)} - raw.keys())
    if todo:
        raw.update(snapshots.current_names(gh or GitHub(), todo))
        _write_json(path, raw)
    return raw


def same_owner_layers(cases, same_owner, snaps, length, exclude):
    layers, stats = {}, {}
    for eco, cutoffs in _cutoffs_by_ecosystem(cases).items():
        cands = same_owner["order"].get(eco, [])
        for cutoff in cutoffs:
            rows, st = build.same_owner_layer(cands, snaps.data, cutoff, length, exclude)
            layers[(eco, cutoff)] = rows
            stats[(eco, cutoff)] = st
    return layers, stats


def frame_layers(days, pool, snaps, length, exclude):
    layers, stats = {}, {}
    for day in days:
        rows, st = build.frame_layer(pool, snaps.data, date.fromisoformat(day), length, exclude)
        layers[day], stats[day] = rows, st
    return layers, stats


def compose_all(cases, sizes, grid, frames, owners, dates):
    universes, summaries = {}, []
    for case in cases:
        u, s = build.compose_case(case, sizes, grid, frames, owners, dates)
        build.check_nested(u)
        universes[case.case_id] = u
        summaries.append(s)
    return universes, summaries


def layers_root(manifests: Path) -> Path:
    return manifests / "layers"


def write_layers(manifests: Path, grid: str, frames: dict, owners: dict) -> None:
    root = layers_root(manifests)
    frame_dir = root / grid
    if frame_dir.exists():
        shutil.rmtree(frame_dir)
    for day, rows in frames.items():
        build.write_frame_layer(frame_dir / f"{day}.jsonl", rows)
    owner_dir = root / "same-owner"
    if owner_dir.exists():
        shutil.rmtree(owner_dir)
    by_eco: dict[str, dict[str, list]] = defaultdict(dict)
    for (eco, cutoff), rows in owners.items():
        by_eco[eco][cutoff] = rows
    for eco, layers in by_eco.items():
        build.write_same_owner_layers(owner_dir / f"{build.layer_name(eco)}.jsonl", layers)


def read_frame_layers(manifests: Path, grid: str) -> dict[str, list]:
    paths = sorted((layers_root(manifests) / grid).glob("*.jsonl"))
    return {p.stem: build.read_frame_layer(p) for p in paths}


def read_owner_layers(manifests: Path, cases: list[Case]) -> dict[tuple[str, str], list]:
    owners = {}
    for eco in sorted({c.ecosystem for c in cases}):
        path = layers_root(manifests) / "same-owner" / f"{build.layer_name(eco)}.jsonl"
        if path.exists():
            for cutoff, rows in build.read_same_owner_layers(path).items():
                owners[(eco, cutoff)] = rows
    return owners


def local_universes(manifests: Path, cache: Path | None, grid: str, sizes: tuple[int, ...]):
    """Per-case universes composed from committed layers and the local WideSWE checkout."""
    cases = load_cases(cache)
    dates = commit_date_map(None, cases, work_dir(cache))
    frames, owners = read_frame_layers(manifests, grid), read_owner_layers(manifests, cases)
    return compose_all(cases, sizes, grid, frames, owners, dates)


def _pairs_by_role(universes: dict[str, dict[int, list]], size: int) -> dict[str, set]:
    out: dict[str, set] = defaultdict(set)
    for by_size in universes.values():
        for e in by_size[size]:
            out["wideswe" if e.source == "wideswe" else e.source].add((e.repo, e.commit))
    return out


def pair_counts(universes: dict[str, dict[int, list]], sizes: tuple[int, ...]) -> dict:
    out = {}
    for size in sizes:
        by_role = _pairs_by_role(universes, size)
        frame_pairs = by_role.get("frame", set())
        frame_repos = len({r for r, _ in frame_pairs})
        out[str(size)] = {
            "total": len(set().union(*by_role.values())),
            **{role: len(p) for role, p in sorted(by_role.items())},
            "frame_repos": frame_repos,
            "commits_per_frame_repo": round(len(frame_pairs) / max(1, frame_repos), 2),
        }
    return out


def cmd_build(
    manifests: Path,
    cache: Path | None,
    sizes: tuple[int, ...],
    grid: str = "monthly",
    resolve_grids: tuple[str, ...] = GRIDS,
):
    gh = GitHub()
    cases = load_cases(cache)
    work = work_dir(cache)
    pool = _read_jsonl(manifests / "frame" / "pool.jsonl")
    same_owner = _read_json(manifests / "frame" / "same-owner.json", None)
    snaps = SnapshotCache(work / "snapshots.json")
    max_size = max(sizes)
    quota = HARDNEG_QUOTA[max_size]
    dates = commit_date_map(gh, cases, work)
    names = wideswe_names(gh, cases, work)
    started = time.monotonic()
    graphql_before = gh.calls["graphql"]

    cutoffs = _cutoffs_by_ecosystem(cases)
    depth, growing = 2 * quota, set(cutoffs)
    while True:
        wants: dict[str, set[str]] = defaultdict(set)
        for eco in growing:
            for cand in same_owner["order"].get(eco, [])[:depth]:
                wants[cand["repo"]].update(cutoffs[eco])
        snaps.resolve(gh, wants)
        copies = build.wideswe_copies(cases, names, snaps.data)
        owners, owner_stats = same_owner_layers(cases, same_owner, snaps, quota, copies)
        growing = {
            eco
            for (eco, _), rows in owners.items()
            if len(rows) < quota and len(same_owner["order"].get(eco, [])) > depth
        }
        if not growing:
            break
        depth *= 2

    days = _grid_days(cases, resolve_grids)
    untils = [until_iso(date.fromisoformat(d)) for d in days]
    depth = min(len(pool), math.ceil(1.4 * max_size))
    while True:
        snaps.resolve(gh, _pool_wants(pool[:depth], untils))
        copies = build.wideswe_copies(cases, names, snaps.data)
        frames, frame_stats = frame_layers(days, pool[:depth], snaps, max_size, copies)
        short = [d for d, rows in frames.items() if len(rows) < max_size]
        if not short or depth >= len(pool):
            break
        log(f"{len(short)} grid dates short of {max_size} distractors at depth {depth}; extending")
        depth = min(len(pool), math.ceil(depth * 1.25))
    owners, owner_stats = same_owner_layers(cases, same_owner, snaps, quota, copies)
    resolve_s = time.monotonic() - started

    grid_days = set(_grid_days(cases, (grid,)))
    write_layers(manifests, grid, {d: frames[d] for d in sorted(grid_days)}, owners)
    frames_read, owners_read = (
        read_frame_layers(manifests, grid),
        read_owner_layers(manifests, cases),
    )
    universes, summaries = compose_all(cases, sizes, grid, frames_read, owners_read, dates)
    local = work / "universes" / grid
    if local.exists():
        shutil.rmtree(local)
    for case_id, by_size in universes.items():
        for size in sizes:
            build.write_universe(local / f"u{size}" / case_id, by_size[size])
    _write_json(local / "cases.json", summaries)

    index = {
        "created_at": now(),
        "grid": grid,
        "sizes": list(sizes),
        "hardneg_quota": {str(k): v for k, v in HARDNEG_QUOTA.items() if k in sizes},
        "pool_depth_resolved": depth,
        "summary": build.summarize(summaries),
        "pairs": pair_counts(universes, sizes),
        "frame_layers": {d: dict(sorted(frame_stats[d].items())) for d in sorted(grid_days)},
        "same_owner_layers": {
            eco: {
                "cutoffs": len(eco_cutoffs),
                "candidates": len(same_owner["order"].get(eco, [])),
                "kept_min": min(owner_stats[(eco, c)]["kept"] for c in eco_cutoffs),
                "skipped_created_after_cutoff": sum(
                    owner_stats[(eco, c)]["skipped_created_after_snapshot"] for c in eco_cutoffs
                ),
                "skipped_wideswe_copy": max(
                    owner_stats[(eco, c)]["skipped_wideswe_copy"] for c in eco_cutoffs
                ),
            }
            for eco, eco_cutoffs in cutoffs.items()
        },
        "cost": {
            "resolve_seconds": round(resolve_s, 1),
            "graphql_requests": gh.calls["graphql"] - graphql_before,
            "snapshot_cache_repos": len(snaps.data),
        },
    }
    _write_json(manifests / f"index-{grid}.json", index)
    log(f"built {sizes} for {len(cases)} cases; pairs {index['pairs']}")
    return index


def _du(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"disk_bytes": 0, "apparent_bytes": 0, "files": 0}

    def run(*args: str) -> str:
        return subprocess.run(args, capture_output=True, text=True, check=True).stdout

    return {
        "disk_bytes": int(run("du", "-s", "-B1", str(path)).split()[0]),
        "apparent_bytes": int(run("du", "-s", "-B1", "--apparent-size", str(path)).split()[0]),
        "files": run("find", str(path), "-type", "f").count("\n"),
    }


def cmd_extract(
    manifests: Path,
    cache: Path | None,
    corpus: Path,
    size: int,
    grid: str = "monthly",
    workers: int = 8,
    sample: int | None = None,
    sources: tuple[str, ...] | None = None,
    label: str = "",
    seed: int = SEED,
):
    work = work_dir(cache)
    universes, _ = local_universes(manifests, cache, grid, (size,))
    source_of: dict[tuple[str, str], str] = {}
    for by_size in universes.values():
        for e in by_size[size]:
            source_of[(e.repo, e.commit)] = e.source
    chosen = sorted(p for p, s in source_of.items() if sources is None or s in sources)
    if sample is not None:
        by_repo: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for pair in chosen:
            by_repo[pair[0]].append(pair)
        rng = random.Random(f"{seed}:{label}")
        repos = rng.sample(sorted(by_repo), min(sample, len(by_repo)))
        chosen = sorted(rng.choice(by_repo[r]) for r in repos)
    before = _du(corpus)
    started = time.monotonic()
    results = evidence.extract_many(
        chosen, corpus, work / "git", work / "extract-log.jsonl", workers=workers, log=log
    )
    elapsed = time.monotonic() - started
    rows = [r for r in results if r.get("commit")]
    run = {
        "label": label or f"u{size}-{grid}",
        "started_at": now(),
        "size": size,
        "grid": grid,
        "sources": list(sources) if sources else "all",
        "workers": workers,
        "pairs": len(chosen),
        "repos": len({p[0] for p in chosen}),
        "ok": sum(r["status"] == "ok" for r in rows),
        "cached": sum(r["status"] == "cached" for r in rows),
        "errors": [r for r in rows if r["status"] == "error"],
        "elapsed_s": round(elapsed, 1),
        "corpus_before": before,
        "corpus_after": _du(corpus),
        "git_cache_peak_bytes": max(
            (r["bytes"] for r in results if r["status"] == "git_cache"), default=0
        ),
        "network_pack_bytes": sum(r.get("network_pack_bytes", 0) for r in rows),
        "source_counts": dict(Counter(source_of[p] for p in chosen)),
    }
    with open(work / "extract-runs.jsonl", "a") as fh:
        fh.write(json.dumps(run) + "\n")
    log(f"extract {run['label']}: {run['ok']} ok, {len(run['errors'])} errors in {elapsed:.0f}s")
    return run


def grid_comparison(cache: Path | None, manifests: Path, sizes: tuple[int, ...]) -> dict:
    """Distinct (repository, commit) pairs per size under each grid, from cached snapshots."""
    cases = load_cases(cache)
    work = work_dir(cache)
    pool = _read_jsonl(manifests / "frame" / "pool.jsonl")
    snaps = SnapshotCache(work / "snapshots.json")
    dates = commit_date_map(None, cases, work)
    copies = build.wideswe_copies(cases, wideswe_names(None, cases, work), snaps.data)
    owners = read_owner_layers(manifests, cases)
    out = {}
    for grid in GRIDS:
        days = _grid_days(cases, (grid,))
        frames, _ = frame_layers(days, pool, snaps, max(sizes), copies)
        universes, summaries = compose_all(cases, sizes, grid, frames, owners, dates)
        summary = build.summarize(summaries)
        out[grid] = {
            "snapshot_dates": summary["snapshot_dates"],
            "skew_days": summary["skew_days"],
            "shortfall_cases": summary["shortfall_cases"],
            "pairs": pair_counts(universes, sizes),
        }
    return out


def cmd_measure(manifests: Path, cache: Path | None, sizes: tuple[int, ...]) -> dict:
    """Summarize extraction runs as time and disk per 100 snapshots, and compare grids."""
    work = work_dir(cache)
    runs_path, log_path = work / "extract-runs.jsonl", work / "extract-log.jsonl"
    runs = _read_jsonl(runs_path) if runs_path.exists() else []
    rows = _read_jsonl(log_path) if log_path.exists() else []
    measured = []
    for run in runs:
        done = run["ok"]
        if not done:
            continue
        disk = run["corpus_after"]["disk_bytes"] - run["corpus_before"]["disk_bytes"]
        files = run["corpus_after"]["files"] - run["corpus_before"]["files"]
        measured.append(
            {
                "label": run["label"],
                "sources": run["source_counts"],
                "snapshots": done,
                "repos": run["repos"],
                "errors": len(run["errors"]),
                "workers": run["workers"],
                "elapsed_s": run["elapsed_s"],
                "wall_s_per_100": round(run["elapsed_s"] / done * 100, 1),
                "disk_mb_per_100": round(disk / done * 100 / 1e6, 2),
                "files_per_100": round(files / done * 100),
                "network_mb_per_100": round(run["network_pack_bytes"] / done * 100 / 1e6, 2),
                "git_cache_peak_mb": round(run["git_cache_peak_bytes"] / 1e6, 1),
            }
        )
    serial = sorted(r["wall_s"] for r in rows if r.get("status") == "ok")
    report = {
        "created_at": now(),
        "extraction_runs": measured,
        "serial_seconds_per_snapshot": {
            "n": len(serial),
            "median": serial[len(serial) // 2] if serial else None,
            "p90": serial[int(len(serial) * 0.9)] if serial else None,
            "mean": round(sum(serial) / len(serial), 2) if serial else None,
        },
        "grids": grid_comparison(cache, manifests, sizes),
    }
    _write_json(manifests / "costs.json", report)
    return report


def cmd_materialize(
    manifests: Path,
    cache: Path | None,
    corpus: Path,
    size: int,
    grid: str = "monthly",
    out: Path | None = None,
) -> dict:
    """Write contract universes for U<size> next to the corpus, symlinking evidence."""
    out = out or corpus / "universes" / grid / f"u{size}"
    universes, _ = local_universes(manifests, cache, grid, (size,))
    linked, missing = 0, []
    for case_id, by_size in sorted(universes.items()):
        dest = out / case_id
        if dest.exists():
            shutil.rmtree(dest)
        build.write_universe(dest, by_size[size])
        n, miss = evidence.materialize(dest, corpus)
        linked += n
        missing.extend(miss)
    log(f"materialized u{size} at {out}: {linked} links, {len(missing)} without evidence")
    return {"root": str(out), "cases": len(universes), "linked": linked, "missing": len(missing)}
