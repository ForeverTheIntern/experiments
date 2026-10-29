"""Degrade the dev cases' target packets and re-score the static rankers on them.

The doc-health test (store ``docs/packet-construction.md``): how much do ``bm25f``,
``dense-cards`` and ``hybrid-rrf`` lose when the targets' README or docs are taken away?
A variant changes only the dev cases' target snapshots (each gold repository at its base
commit); every other packet is the base corpus's own.

Variants, fixed before the first run:

- ``no-readme``: every root README candidate is removed, so the reader finds none
  (:func:`repo_discovery.evidence.pick_readme`, repeated until it returns nothing).
- ``readme-cap``: the README the reader picks is cut to its first ``CAP_CHARS``
  characters, back to the last whitespace. Other files stay.
- ``no-docs``: every file the reader reads as docs is removed (``evidence_kind`` is
  ``docs``): docs directories, root guides, nested READMEs and unpicked README variants.
  The README stays. L3 holds no docs, so this variant leaves an L3 packet as it was.
- ``none``: nothing changes; the control that checks the overlay reproduces the base.

The path list stays as it was: a degraded packet still lists the removed files, as the
repository's tree does. Only their text is gone.

    build   write an overlay corpus over --base (a corpus at L3, an L3+ level or L4)
    score   run bm25f, dense-cards and hybrid-rrf on an overlay's dev universes
    verify  recompute an overlay's degraded packets' digests and compare them

The overlay is a directory of links. ``repos/<owner>__<name>`` links to the base corpus,
except for target repositories: those are real directories whose target commit is a real
directory of hard links, with a capped README written as a new file. ``meta`` links to the
base's, and ``fetch-level.json`` is copied, so caches and the agent harness read the base's
level. Each dev case's universe listing is copied with a ``repos`` link, as
``fetch_levels.restrict`` writes them. Only dev cases' universes are mirrored.

Dense vectors are keyed by level, repository and commit but not by text, so ``score``
reads the base store for every other snapshot and embeds the degraded targets into a
store of its own (``--store``).

    python3 scripts/doc_health/degrade.py build --base ~/rd-levels-u10000/L3 \\
        --cases ~/rd-cases.jsonl --variant no-readme --sizes 250 1000 5000 \\
        --out ~/rd-dh/real/no-readme/L3
    python3 scripts/doc_health/degrade.py score --overlay ~/rd-dh/real/no-readme/L3 \\
        --cases ~/rd-cases.jsonl --sizes 250 1000 5000 --base-store STORE.sqlite \\
        --store ~/rd-dh/real/no-readme/L3.vectors.sqlite --out ~/rd-dh/preds/real/no-readme/L3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

import numpy as np

from repo_discovery.cases import Case, Query, read_cases, repo_key, select_cases
from repo_discovery.contract import UNIVERSE_FILE, Universe, evidence_dir, resolve_universe
from repo_discovery.embeddings import VectorStore
from repo_discovery.evidence import (
    PATH_LIST_FILES,
    evidence_kind,
    pick_readme,
    read_text,
    sidecar_labels,
)
from repo_discovery.fetch_levels import FULL, MARKER
from repo_discovery.harness import DEFAULT_DEPTH
from repo_discovery.predictions import Prediction, write_predictions
from repo_discovery.rankers.bm25 import BM25Ranker
from repo_discovery.rankers.dense import DenseCardRanker, HybridRanker

VARIANTS = ("none", "no-readme", "readme-cap", "no-docs")
CAP_CHARS = 1_000
DEV_CASES = 61
RECORD = "degraded.json"
CONTEXT_REPOS = "repos-context"


def dev_cases(path: Path, expect: int = DEV_CASES) -> list[Case]:
    """The dev split of ``path``; refuses a file whose dev split is not ``expect`` cases."""
    cases = select_cases(read_cases(path), split="dev")
    if len(cases) != expect:
        raise SystemExit(f"{path}: {len(cases)} dev cases, expected {expect}")
    return cases


def files_of(packet: Path) -> list[str]:
    return sorted(p.relative_to(packet).as_posix() for p in packet.rglob("*") if p.is_file())


def packet_digest(packet: Path) -> str:
    lines = []
    for rel in files_of(packet):
        lines.append(f"{rel}\0{hashlib.sha256((packet / rel).read_bytes()).hexdigest()}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def cap_text(text: str, cap: int = CAP_CHARS) -> str:
    """The first ``cap`` characters of ``text``, cut back to the last whitespace when one
    falls in the second half."""
    if len(text) <= cap:
        return text
    head = text[:cap]
    cut = max(head.rfind(c) for c in " \t\r\n")
    return head[:cut] if cut > cap // 2 else head


def plan(packet: Path, variant: str) -> tuple[list[str], str | None]:
    """The files ``variant`` removes from ``packet`` and the README it caps, if any."""
    files = files_of(packet)
    labels = sidecar_labels(packet)
    readme = pick_readme(files, labels)
    if variant == "none":
        return [], None
    if variant == "readme-cap":
        return [], readme
    if variant == "no-readme":
        left, removed = list(files), []
        while (rel := pick_readme(left, labels)) is not None:
            removed.append(rel)
            left.remove(rel)
        return removed, None
    if variant == "no-docs":
        removed = [
            rel
            for rel in files
            if rel != readme
            and rel not in PATH_LIST_FILES
            and evidence_kind(packet, rel, labels) == "docs"
        ]
        return removed, None
    raise ValueError(f"unknown variant {variant!r}")


def _link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def degrade_packet(source: Path, dest: Path, variant: str, cap: int = CAP_CHARS) -> dict:
    """Write ``variant``'s copy of ``source`` at ``dest``; returns what changed."""
    removed, capped = plan(source, variant)
    files = files_of(source)
    dest.mkdir(parents=True)
    readme_before = readme_after = None
    for rel in files:
        if rel in removed:
            continue
        if rel == capped:
            text = read_text(source / rel, whole=True) or ""
            short = cap_text(text, cap)
            readme_before, readme_after = len(text), len(short)
            if short != text:
                (dest / rel).write_text(short, encoding="utf-8")
                continue
        _link(source / rel, dest / rel)
    kept = files_of(dest)
    return {
        "removed": removed,
        "capped": capped if readme_after is not None and readme_after < readme_before else None,
        "readme_chars": [readme_before, readme_after] if capped else None,
        "files": [len(files), len(kept)],
        "bytes": [_bytes(source, files), _bytes(dest, kept)],
        "digest": packet_digest(dest),
    }


def _bytes(root: Path, names: Iterable[str]) -> int:
    return sum((root / n).stat().st_size for n in names)


def targets(cases: Iterable[Case]) -> dict[tuple[str, str], list[str]]:
    """``(repo key, commit)`` of every dev target, with the cases that need it."""
    found: dict[tuple[str, str], list[str]] = {}
    for case in cases:
        for gold in case.gold:
            found.setdefault((repo_key(gold.repo), gold.commit), []).append(case.case_id)
    return found


def universe_dirs(root: Path, cases: Sequence[Case], sizes: Sequence[int]) -> Iterable[tuple]:
    for size in sizes:
        tier = root / "universes" / "monthly" / f"u{size}"
        for case in cases:
            yield size, case, resolve_universe(tier, case.case_id)


def build(
    base: Path,
    out: Path,
    cases: Sequence[Case],
    variant: str,
    sizes: Sequence[int],
    cap: int = CAP_CHARS,
) -> dict:
    base, out = base.resolve(), out.resolve()
    if out.exists():
        raise SystemExit(f"{out} exists; remove it first")
    wanted = targets(cases)
    entries: dict[tuple[str, str], str] = {}
    overlaps = []
    for size, case, udir in universe_dirs(base, cases, sizes):
        gold = {(repo_key(g.repo), g.commit) for g in case.gold}
        for entry in Universe.load(udir).entries:
            key = (repo_key(entry.repo), entry.commit)
            if key in gold:
                entries.setdefault(key, entry.repo)
            elif key in wanted:
                overlaps.append(
                    {
                        "size": size,
                        "case_id": case.case_id,
                        "repo": entry.repo,
                        "commit": entry.commit,
                        "role": entry.role,
                    }
                )
    by_dir: dict[str, dict[str, tuple[str, str]]] = {}
    for key, repo in entries.items():
        packet = evidence_dir(base, repo, key[1])
        by_dir.setdefault(packet.parent.name, {})[packet.name] = key

    (out / "repos").mkdir(parents=True)
    records = []
    for repo_dir in sorted((base / "repos").iterdir()):
        link = out / "repos" / repo_dir.name
        commits = by_dir.get(repo_dir.name)
        if commits is None:
            link.symlink_to(repo_dir, target_is_directory=True)
            continue
        link.mkdir()
        for child in sorted(repo_dir.iterdir()):
            if child.name not in commits:
                (link / child.name).symlink_to(child, target_is_directory=child.is_dir())
                continue
            key = commits[child.name]
            record = degrade_packet(child, link / child.name, variant, cap)
            records.append({"repo": entries[key], "commit": key[1], "cases": wanted[key], **record})
    present = {(repo_key(r["repo"]), r["commit"]) for r in records}
    missing = [{"key": list(k), "cases": v} for k, v in sorted(wanted.items()) if k not in present]

    # A target snapshot that is another case's distractor keeps its base packet there:
    # that case's universes read a view restoring every such snapshot but its own targets.
    shared = {(repo_key(o["repo"]), o["commit"]) for o in overlaps}
    affected = {o["case_id"] for o in overlaps}
    restores = {
        c.case_id: frozenset(shared - {(repo_key(g.repo), g.commit) for g in c.gold})
        for c in cases
        if c.case_id in affected
    }
    views = {
        r: f"{CONTEXT_REPOS}-{i}" for i, r in enumerate(sorted(set(restores.values()), key=sorted))
    }
    for restore, name in views.items():
        _context_repos(
            base, out / name, out, {evidence_dir(base, entries[k], k[1]) for k in restore}
        )
    case_views = {case_id: views[r] for case_id, r in sorted(restores.items())}

    (out / "meta").symlink_to(base / "meta", target_is_directory=True)
    if (base / MARKER).is_file():
        shutil.copyfile(base / MARKER, out / MARKER)
    for _size, case, udir in universe_dirs(base, cases, sizes):
        target = out / udir.relative_to(base)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(udir / UNIVERSE_FILE, target / UNIVERSE_FILE)
        repos = case_views.get(case.case_id, "repos")
        (target / "repos").symlink_to(os.path.relpath(out / repos, target), True)

    summary = {
        "variant": variant,
        "cap_chars": cap if variant == "readme-cap" else None,
        "base": str(base),
        "level": corpus_level(out),
        "sizes": list(sizes),
        "cases": len(cases),
        "targets": len(records),
        "changed": sum(r["files"][0] != r["files"][1] or r["capped"] is not None for r in records),
        "missing": missing,
        "overlaps": overlaps,
        "views": {name: sorted(list(k) for k in r) for r, name in views.items()},
        "case_views": case_views,
        "packets": records,
    }
    (out / RECORD).write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def _context_repos(base: Path, view: Path, out: Path, restore: set[Path]) -> None:
    """``view``: ``out/repos`` with the ``restore`` packets read from ``base``."""
    view.mkdir()
    dirs = {p.parent.name for p in restore}
    for link in sorted((out / "repos").iterdir()):
        if link.name not in dirs:
            (view / link.name).symlink_to(link, target_is_directory=True)
            continue
        (view / link.name).mkdir()
        for child in sorted(link.iterdir()):
            source = base / "repos" / link.name / child.name
            (view / link.name / child.name).symlink_to(
                source if source in restore else child, target_is_directory=child.is_dir()
            )


def corpus_level(root: Path) -> str:
    marker = root / MARKER
    return json.loads(marker.read_text())["level"] if marker.is_file() else FULL


def _packets(out: Path, records: Iterable[dict]) -> Iterable[Path]:
    return (evidence_dir(out, r["repo"], r["commit"]) for r in records)


def verify(overlay: Path, expect: Path | None = None) -> list[str]:
    """Differences between the overlay's packets and its record (and ``expect``'s)."""
    record = json.loads((overlay / RECORD).read_text())
    problems = []
    for packet, r in zip(_packets(overlay, record["packets"]), record["packets"], strict=True):
        if packet_digest(packet) != r["digest"]:
            problems.append(f"{r['repo']}@{r['commit']}: digest differs from {RECORD}")
    if expect is not None:
        theirs = {
            (repo_key(r["repo"]), r["commit"]): r["digest"]
            for r in json.loads(expect.read_text())["packets"]
        }
        ours = {(repo_key(r["repo"]), r["commit"]): r["digest"] for r in record["packets"]}
        for key in sorted(theirs.keys() | ours.keys()):
            if theirs.get(key) != ours.get(key):
                problems.append(f"{key[0]}@{key[1]}: digest differs from {expect}")
    return problems


def _snapshot(key: str) -> tuple[str, str]:
    # <variant>/<budget>/<owner>/<name>/<commit>, as DenseCardRanker._key writes it
    parts = key.split("/")
    return "/".join(parts[-3:-1]), parts[-1]


class LayeredStore:
    """Vectors from a store of our own, then from the base store.

    ``degraded`` snapshots are never read from the base, whose vectors are of their base
    packets; ``restored`` ones are only read from it.
    """

    def __init__(self, base: VectorStore, own: VectorStore, degraded: set, restored: set):
        self.base, self.own = base, own
        self.degraded, self.restored = degraded, restored

    def get_many(self, keys: Iterable[str]) -> dict[str, np.ndarray]:
        keys = list(keys)
        restored = [k for k in keys if _snapshot(k) in self.restored]
        found = self.base.get_many(restored)
        if len(found) != len(restored):
            raise KeyError(f"{len(restored) - len(found)} restored snapshots not in the base")
        rest = [k for k in keys if k not in found]
        found.update(self.own.get_many(rest))
        found.update(
            self.base.get_many(
                k for k in rest if k not in found and _snapshot(k) not in self.degraded
            )
        )
        return found

    def put_many(self, vectors: Mapping[str, np.ndarray]) -> None:
        bad = [k for k in vectors if _snapshot(k) not in self.degraded]
        if bad:
            raise ValueError(f"would embed {len(bad)} snapshots the overlay leaves as they were")
        self.own.put_many(vectors)


class SplitDocuments(dict):
    """A ranker's document cache that keeps ``private`` snapshots to itself and shares the
    rest with ``shared``, the main group's cache."""

    def __init__(self, shared: dict, private: set[tuple[str, str]]):
        super().__init__()
        self.shared, self.private = shared, private

    def _home(self, key: tuple[str, str]) -> dict:
        return self if key in self.private else self.shared

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self._home(key), key)  # type: ignore[arg-type]

    def __getitem__(self, key: tuple[str, str]) -> dict[str, str]:
        return dict.__getitem__(self._home(key), key)

    def __setitem__(self, key: tuple[str, str], value: dict[str, str]) -> None:
        dict.__setitem__(self._home(key), key, value)


def score(
    overlay: Path,
    cases: Sequence[Case],
    sizes: Sequence[int],
    base_store: Path,
    store: Path,
    out: Path,
) -> dict:
    """``bm25f``, ``dense-cards`` and ``hybrid-rrf`` on the overlay's dev universes.

    Cases whose universes read the context view are ranked by rankers of their own, as
    both rankers cache documents and vectors by repository and commit.
    """
    record = json.loads((overlay / RECORD).read_text())
    degraded = {(repo_key(p["repo"]), p["commit"]) for p in record["packets"]}
    base, own = VectorStore(base_store), VectorStore(store)
    groups = {}
    for name, keys in (("repos", []), *record["views"].items()):
        restored = {tuple(k) for k in keys}
        dense = DenseCardRanker()
        dense._store = LayeredStore(base, own, degraded - restored, restored)
        keyword = BM25Ranker()
        if groups:
            keyword._documents = SplitDocuments(groups["repos"][0]._documents, degraded)
        groups[name] = (keyword, dense, HybridRanker(keyword=keyword, dense=dense))
    out.mkdir(parents=True, exist_ok=True)
    timing = {}
    for size in sizes:
        started = time.perf_counter()
        predictions: dict[str, list[Prediction]] = {}
        for _size, case, udir in universe_dirs(overlay, cases, [size]):
            universe = Universe.load(udir)
            view = universe.view()
            query = Query.from_case(case)
            for ranker in groups[record["case_views"].get(case.case_id, "repos")]:
                t0 = time.perf_counter()
                ranking = ranker.rank(query, view)
                predictions.setdefault(ranker.name, []).append(
                    Prediction(
                        case_id=case.case_id,
                        ranker=ranker.name,
                        ranking=tuple(ranking[:DEFAULT_DEPTH]),
                        universe=str(udir),
                        universe_size=len(universe),
                        seconds=round(time.perf_counter() - t0, 6),
                    )
                )
        for name, preds in predictions.items():
            write_predictions(out / f"{name}--u{size}.jsonl", preds)
        timing[size] = round(time.perf_counter() - started, 1)
        print(f"u{size}: {timing[size]} s", file=sys.stderr, flush=True)
    stats = {
        "overlay": str(overlay),
        "variant": record["variant"],
        "level": record["level"],
        "sizes": list(sizes),
        "seconds": timing,
        "embedded": sum(g[1].stats.embedded for g in groups.values()),
        "from_store": sum(g[1].stats.cached for g in groups.values()),
        "store": str(store),
        "base_store": str(base_store),
    }
    (out / "score.json").write_text(json.dumps(stats, indent=1) + "\n")
    return stats


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build")
    b.add_argument("--base", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--variant", choices=VARIANTS, required=True)
    b.add_argument("--cap", type=int, default=CAP_CHARS)
    s = sub.add_parser("score")
    s.add_argument("--overlay", type=Path, required=True)
    s.add_argument("--base-store", type=Path, required=True)
    s.add_argument("--store", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    for p in (b, s):
        p.add_argument("--cases", type=Path, required=True)
        p.add_argument("--sizes", type=int, nargs="+", required=True)
        p.add_argument("--expect-cases", type=int, default=DEV_CASES)
    v = sub.add_parser("verify")
    v.add_argument("--overlay", type=Path, required=True)
    v.add_argument("--expect", type=Path)
    args = parser.parse_args(argv)

    if args.command == "verify":
        problems = verify(args.overlay, args.expect)
        print("\n".join(problems) or "ok")
        return 1 if problems else 0
    cases = dev_cases(args.cases, args.expect_cases)
    if args.command == "build":
        summary = build(args.base, args.out, cases, args.variant, args.sizes, args.cap)
        keys = ("variant", "level", "targets", "changed", "missing", "overlaps")
        print(
            json.dumps(
                {
                    k: summary[k] if k not in ("missing", "overlaps") else len(summary[k])
                    for k in keys
                }
            )
        )
        return 0
    stats = score(args.overlay, cases, args.sizes, args.base_store, args.store, args.out)
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
