"""Fetch levels: how much of each repository is fetched before it is indexed.

Each level keeps part of a snapshot's evidence packet:

- ``L0``: nothing; the name comes from the listing that defines the universe.
- ``L2``: the README.
- ``L3``: the README and the package manifests (found through a tree listing).
- ``L4``: the whole packet: README, docs, manifests, contracts, other text, path list.

L1, GitHub metadata, is not in the corpus.

``restrict`` writes a level's corpus from the full one. Each packet holds hard links to
the files the level keeps, plus an empty ``paths.txt`` below L4 so readers don't fall back
to listing the packet's own files. ``meta/`` links the builder's sidecars, every universe
is mirrored with its ``repos`` link pointing at the restricted packets, and
``fetch-level.json`` at the root names the level. Rankers read a restricted universe
unchanged; ``fetch_level`` reads the marker so caches keyed by snapshot keep levels apart.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

from repo_discovery.cases import SPLITS, read_cases, select_cases
from repo_discovery.contract import UNIVERSE_FILE, Universe, resolve_universe
from repo_discovery.evidence import PATH_LIST_FILES, classify, pick_readme, sidecar_labels

LEVELS: tuple[str, ...] = ("L0", "L2", "L3", "L4")
FULL = "L4"
MARKER = "fetch-level.json"


def kept_files(packet: Path, level: str) -> list[str]:
    """Packet-relative paths of the files ``level`` keeps, sorted."""
    _check(level)
    files = sorted(p.relative_to(packet).as_posix() for p in packet.rglob("*") if p.is_file())
    if level == FULL:
        return files
    if level == "L0":
        return []
    labels = sidecar_labels(packet)
    readme = pick_readme(files, labels)
    kept = {readme} if readme else set()
    if level == "L3":
        kept.update(
            rel
            for rel in files
            if rel not in PATH_LIST_FILES and (labels.get(rel) or classify(rel)) == "manifests"
        )
    return sorted(kept)


def restrict_packet(source: Path, dest: Path, level: str) -> int:
    """Write ``level``'s copy of ``source`` at ``dest``; returns the number of files kept."""
    kept = kept_files(source, level)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for rel in kept:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        _link(source / rel, dest / rel)
    if level != FULL:
        (dest / PATH_LIST_FILES[0]).write_text("")
    return len(kept)


@dataclass
class RestrictStats:
    level: str
    packets: int = 0
    files: int = 0
    universes: int = 0


def restrict(corpus: Path, level: str, out: Path) -> RestrictStats:
    """Write ``level``'s corpus under ``out``: packets, sidecar links and mirrored universes."""
    _check(level)
    corpus, out = corpus.resolve(), out.resolve()
    if out == corpus:
        raise ValueError("the restricted corpus must not overwrite the full one")
    if (out / MARKER).is_file() and _corpus_level(out) != level:
        raise ValueError(f"{out} already holds level {_corpus_level(out)}")
    stats = RestrictStats(level)
    for packet in sorted((corpus / "repos").glob("*/*")):
        if not packet.is_dir():
            continue
        repo_dir, commit = packet.parent.name, packet.name
        stats.files += restrict_packet(packet, out / "repos" / repo_dir / commit, level)
        stats.packets += 1
        sidecar = corpus / "meta" / repo_dir / f"{commit}.json"
        if sidecar.is_file():
            target = out / "meta" / repo_dir / sidecar.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            _link(sidecar, target)
    for listing in sorted((corpus / "universes").glob(f"**/{UNIVERSE_FILE}")):
        target = out / listing.parent.relative_to(corpus)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(listing, target / UNIVERSE_FILE)
        link = target / "repos"
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(os.path.relpath(out / "repos", target), target_is_directory=True)
        stats.universes += 1
    (out / MARKER).write_text(json.dumps({"level": level, "source": str(corpus)}) + "\n")
    _corpus_level.cache_clear()
    return stats


def fetch_level(packet: Path) -> str:
    """The level of the corpus holding ``packet`` (``.../repos/<owner>__<name>/<commit>``)."""
    return _corpus_level(packet.parent.parent.resolve().parent)


@cache
def _corpus_level(root: Path) -> str:
    marker = root / MARKER
    if not marker.is_file():
        return FULL
    level = json.loads(marker.read_text())["level"]
    _check(level)
    return level


@dataclass(frozen=True)
class PacketCost:
    """What fetching one packet at its level takes.

    ``files``: evidence files stored (an empty path list doesn't count). ``calls``: REST
    requests to fetch them, one per file plus a tree listing at L3 and L4 unless the
    stored path list is that listing; L2 asks for the README even when there is none.
    """

    level: str
    files: int
    bytes: int
    calls: int


def packet_cost(packet: Path) -> PacketCost:
    level = fetch_level(packet)
    files = [p for p in packet.rglob("*") if p.is_file()] if packet.is_dir() else []
    sizes = {p.relative_to(packet).as_posix(): p.stat().st_size for p in files}
    listed = any(sizes.get(name, 0) > 0 for name in PATH_LIST_FILES)
    stored = {rel: size for rel, size in sizes.items() if size > 0 or rel not in PATH_LIST_FILES}
    if level == "L0":
        calls = 0
    elif level == "L2":
        calls = max(len(stored), 1)
    else:
        calls = len(stored) + (0 if listed else 1)
    return PacketCost(level, len(stored), sum(stored.values()), calls)


def cost_summary(costs: Sequence[PacketCost]) -> dict[str, dict[str, float]]:
    summary = {}
    for field in ("files", "bytes", "calls"):
        values = [getattr(c, field) for c in costs]
        summary[field] = {
            "mean": round(statistics.fmean(values), 3) if values else 0.0,
            "median": statistics.median(values) if values else 0.0,
        }
    return summary


def universe_costs(cases_path: Path, split: str | None, universes: Path) -> list[PacketCost]:
    """One cost per distinct snapshot in the selected cases' universes."""
    seen: dict[tuple[str, str], PacketCost] = {}
    for case in select_cases(read_cases(cases_path), split=split):
        universe = Universe.load(resolve_universe(universes, case.case_id))
        for entry in universe.entries:
            key = (entry.repo.lower(), entry.commit)
            if key not in seen:
                seen[key] = packet_cost(universe.evidence_dir(entry))
    return list(seen.values())


def _link(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def _check(level: str) -> None:
    if level not in LEVELS:
        raise ValueError(f"fetch level must be one of {LEVELS}, got {level!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m repo_discovery.fetch_levels")
    commands = parser.add_subparsers(dest="command", required=True)
    cmd = commands.add_parser("restrict", help="write a fetch level's corpus from the full one")
    cmd.add_argument("--corpus", type=Path, required=True, help="holds repos/, meta/, universes/")
    cmd.add_argument("--level", choices=LEVELS, required=True)
    cmd.add_argument("--out", type=Path, required=True)
    cost = commands.add_parser("cost", help="per-snapshot fetch cost of the cases' universes")
    cost.add_argument("--cases", type=Path, required=True)
    cost.add_argument("--split", choices=[*SPLITS, "all"], default="dev")
    cost.add_argument("--universes", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "restrict":
        stats = restrict(args.corpus, args.level, args.out)
        print(json.dumps(asdict(stats)))
        return 0
    costs = universe_costs(args.cases, None if args.split == "all" else args.split, args.universes)
    levels = sorted({c.level for c in costs})
    print(json.dumps({"levels": levels, "snapshots": len(costs), **cost_summary(costs)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
