"""Doc-health scores for the dev cases, CR@10 by doc-health tier, the target-versus-decoy
confound check, and the READMEs of the decoys that take targets' places in the top 10.

Reads the per-snapshot rows that ``profile_packets.py features`` wrote, the existing static
predictions (``<ranker>--<level>--u<N>.jsonl``, real and hidden names) and the agent
runs' per-case exports. Nothing is run or re-ranked here.

The doc-health score of a snapshot is the mean of seven components in [0, 1], fixed before
any result was looked at (see ``components``). A case's score is its weakest target's,
since CompleteRecall@10 needs every target in the top 10; the mean over targets breaks ties.

Only dev cases are read: the cases file's dev split is checked, hidden predictions are
mapped back through the build's ``private/mapping.json`` (which holds the 61 dev cases),
and a prediction or agent file must hold exactly the dev cases.

    python3 scripts/doc_health/stratify.py --corpus ~/rd-corpus --cases ~/rd-cases.jsonl \\
        --features ~/rd-dh/profile/snapshots.jsonl --static-real ~/rd-fu/preds/real \\
        --static-hidden ~/rd-fu/preds/hidden --anon ~/rd-anon \\
        --agents /cursor/stores/self/internal/selection/inbox/agent-runs \\
        --out ~/rd-dh/stratify.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
from degrade import DEV_CASES, dev_cases
from profile_packets import group_of, universe_rows

from repo_discovery.cases import Case, repo_key
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import read_predictions

METRIC = "CompleteRecall@10"
RANKERS = ("bm25f", "dense-cards", "hybrid-rrf")
LEVELS = ("L0", "L2", "L3", "L3+docs", "L4")
SIZES = (250, 1000, 5000, 10000)
LENGTH_FULL = 300
DOCS_FULL = 10_000
TIERS = ("low", "mid", "high")
PERMUTATIONS = 10_000
LONG_README = 1_000


def components(r: dict) -> dict[str, float]:
    """Seven doc-health components in [0, 1]; a snapshot without a README scores 0 on five."""
    has = bool(r.get("readme"))
    tokens = r.get("readme_tokens", 0) if has else 0
    return {
        "readme": float(has),
        "readme_length": min(1.0, math.log1p(tokens) / math.log1p(LENGTH_FULL)),
        "readme_structure": 0.5 * (r.get("headings", 0) >= 2)
        + 0.5 * (r.get("code_blocks", 0) >= 1),
        "readme_prose": float(has and not r.get("thin")),
        "english": float(has and not r.get("non_english")),
        "docs": 0.0 if not r.get("docs_files") else 0.5 + 0.5 * (r["docs_bytes"] >= DOCS_FULL),
        "manifests": min(1.0, r.get("manifests_files", 0) / 2),
    }


def health(r: dict) -> float:
    c = components(r)
    return sum(c.values()) / len(c)


FLAGS: dict[str, Callable[[dict], bool]] = {
    "no README": lambda r: not r.get("readme"),
    "thin or badge/link-only README": lambda r: bool(r.get("readme")) and r["thin"],
    "non-English README": lambda r: bool(r.get("non_english")),
    "README under 100 tokens": lambda r: bool(r.get("readme")) and r["readme_tokens"] < 100,
    "README over 1,000 tokens": lambda r: r.get("readme_tokens", 0) > LONG_README,
    "README over 512 bge tokens": lambda r: r.get("readme_bge", 0) > 512,
    "README without a code block": lambda r: bool(r.get("readme")) and r["code_blocks"] == 0,
    "no docs": lambda r: not r.get("docs_files"),
    "docs under 10 KB": lambda r: r.get("docs_bytes", 0) < DOCS_FULL,
    "no manifest": lambda r: not r.get("manifests_files"),
    "one manifest or none": lambda r: r.get("manifests_files", 0) <= 1,
}


def load_rows(path: Path) -> dict[tuple[str, str], dict]:
    with path.open() as f:
        return {(repo_key(r["repo"]), r["commit"]): r for r in map(json.loads, f)}


def gold_rows(corpus: Path, case: Case, rows: dict) -> list[dict]:
    entries = universe_rows(corpus, max(SIZES), case.case_id)
    found = [rows[(repo_key(e["repo"]), e["commit"])] for e in entries if e["role"] == "gold"]
    assert len(found) == len(case.gold), case.case_id
    return found


def case_table(corpus: Path, cases: Sequence[Case], rows: dict) -> dict[str, dict]:
    table = {}
    for case in cases:
        targets = gold_rows(corpus, case, rows)
        scores = [health(r) for r in targets]
        weakest = targets[scores.index(min(scores))]
        table[case.case_id] = {
            "ecosystem": case.ecosystem,
            "targets": len(targets),
            "score": round(min(scores), 4),
            "score_mean": round(statistics.fmean(scores), 4),
            "components": components(weakest),
            "flags": {name: any(test(r) for r in targets) for name, test in FLAGS.items()},
        }
    order = sorted(table, key=lambda c: (table[c]["score"], table[c]["score_mean"], c))
    third = len(order) / 3
    for i, cid in enumerate(order):
        table[cid]["tier"] = TIERS[min(2, int(i // third))]
    return table


# Cells ------------------------------------------------------------------------------------


def score_file(
    path: Path, gold: dict[str, list[str]], to_real: dict[str, str] | None
) -> dict[str, float]:
    preds = {}
    for p in read_predictions(path):
        cid = to_real.get(p.case_id) if to_real is not None else p.case_id
        if cid in gold:
            preds[cid] = p
    if preds.keys() != gold.keys():
        raise SystemExit(f"{path}: {len(preds)} of {len(gold)} dev cases")
    return {
        cid: case_metrics(gold[cid], list(p.ranking), (10,))[METRIC] for cid, p in preds.items()
    }


def find(dirs: Sequence[Path], name: str) -> Path | None:
    return next((d / name for d in dirs if (d / name).is_file()), None)


def static_cells(
    cases: Sequence[Case], real: Sequence[Path], hidden: Path | None, anon: Path | None
) -> dict:
    gold = {c.case_id: [g.repo for g in c.gold] for c in cases}
    cells = {}
    for ranker in RANKERS:
        for size in SIZES:
            base = None
            for level in LEVELS:
                path = find(real, f"{ranker}--{level}--u{size}.jsonl")
                if path is None:
                    continue
                scores = score_file(path, gold, None)
                family = f"static {ranker}" if level != "L0" else f"static {ranker} names only (L0)"
                cells[f"real {ranker} {level} U{size}"] = {
                    "family": family,
                    "names": "real",
                    "level": level,
                    "size": size,
                    "cases": scores,
                }
                if level == "L0":
                    base = scores
                elif base is not None:
                    cells[f"real {ranker} {level} U{size} minus L0"] = {
                        "family": f"static {ranker} gain over L0",
                        "names": "real",
                        "level": level,
                        "size": size,
                        "cases": {c: scores[c] - base[c] for c in scores},
                    }
    if hidden is None or anon is None:
        return cells
    for size in SIZES:
        build = anon / ("u1000" if size <= 1000 else f"u{size}")
        mapping = json.loads((build / "private" / "mapping.json").read_text())["cases"]
        if set(mapping) != set(gold):
            raise SystemExit(f"{build}: mapping does not hold exactly the dev cases")
        hidden_cases = dev_cases(build / "cases-scrubbed.jsonl")
        to_real = {h: r for r, h in mapping.items()}
        hidden_gold = {to_real[c.case_id]: [g.repo for g in c.gold] for c in hidden_cases}
        for ranker in RANKERS:
            for level in LEVELS:
                path = hidden / f"{ranker}--{level}--u{size}.jsonl"
                if path.is_file():
                    cells[f"hidden {ranker} {level} U{size}"] = {
                        "family": f"static {ranker}",
                        "names": "hidden",
                        "level": level,
                        "size": size,
                        "cases": score_file(path, hidden_gold, to_real),
                    }
    return cells


AGENT_FAMILIES = {
    "c1 L2": "reading agent, L2",
    "c1 L3": "reading agent, L3",
    "L4": "reading agent, L4",
    "c2 bm25f": "search-tool agent",
    "c2 hybrid-rrf": "search-tool agent",
    "L5": "on-demand agent (open_repo, N=20)",
    "L5+search": "on-demand agent with search (N=20)",
    "closed-book": "closed-book agent (names only)",
}


def agent_cells(cases: Sequence[Case], runs: Path) -> dict:
    """Canonical agent cells at U250 and up: neither contaminated nor superseded."""
    ids = {c.case_id for c in cases}
    cells = {}
    for line in (runs / "manifest.jsonl").read_text().splitlines():
        m = json.loads(line)
        family = AGENT_FAMILIES.get(m["method"])
        if (
            m["kind"] != "agent"
            or family is None
            or m["size"] < 250
            or m.get("superseded_by")
            or m.get("quality") not in (None, "canonical")
            or m["request_text"] == "verbatim"
            or (m["method"].startswith("L5") and m["budget_n"] != 20)
        ):
            continue
        per_case = {}
        for row in map(json.loads, (runs / m["file"]).read_text().splitlines()):
            if "case_id" in row:
                per_case[row["case_id"]] = float(row[METRIC])
        if per_case.keys() != ids:
            raise SystemExit(f"{m['file']}: {len(per_case)} cases, not the {len(ids)} dev cases")
        cells[m["cell"]] = {
            "family": family,
            "names": m["names"],
            "level": m["packet_shape"],
            "size": m["size"],
            "cases": per_case,
        }
    return cells


def tier_means(cell: dict, tiers: dict[str, str]) -> dict[str, float]:
    by = defaultdict(list)
    for cid, v in cell["cases"].items():
        by[tiers[cid]].append(v)
    return {t: round(statistics.fmean(by[t]), 4) for t in TIERS}


def gap_test(
    cells: Sequence[dict], tiers: dict[str, str], rng: random.Random, relative: bool = False
) -> dict:
    """Mean over cells of CR@10(high) - CR@10(low), with a permutation p-value.

    Each permutation shuffles the cases' tiers once and applies it to every cell, so cells
    that share cases stay correlated as they are in the data. ``relative`` cells hold a
    change in CR@10, so they count gains and losses instead of misses.
    """
    ids = sorted(tiers)
    matrix = np.array([[c["cases"][cid] for cid in ids] for c in cells])
    labels = np.array([tiers[cid] for cid in ids])
    perms = np.array([rng.sample(range(len(ids)), len(ids)) for _ in range(PERMUTATIONS)])
    shuffled = np.vstack([labels, labels[perms]])
    hi = (shuffled == "high").astype(float)
    lo = (shuffled == "low").astype(float)
    gaps = ((matrix @ hi.T) / hi.sum(1) - (matrix @ lo.T) / lo.sum(1)).mean(0)
    observed = float(gaps[0])
    extreme = int((np.abs(gaps[1:]) >= abs(observed) - 1e-12).sum())
    result = {
        "cells": len(cells),
        "gap_high_minus_low": round(observed, 4),
        "p": round((extreme + 1) / (PERMUTATIONS + 1), 4),
    }
    if relative:
        for name, test in (("gains", lambda v: v > 0), ("losses", lambda v: v < 0)):
            count = Counter(tiers[cid] for c in cells for cid, v in c["cases"].items() if test(v))
            result[name] = {t: count[t] for t in TIERS}
    else:
        count = Counter(tiers[cid] for c in cells for cid, v in c["cases"].items() if v < 1)
        result["misses"] = {t: count[t] for t in TIERS}
    return result


def flag_splits(cells: Sequence[dict], table: dict) -> dict:
    out = {}
    for name in FLAGS:
        flagged = {cid for cid, row in table.items() if row["flags"][name]}
        if not flagged or len(flagged) == len(table):
            out[name] = {"cases": len(flagged)}
            continue
        yes = [v for c in cells for cid, v in c["cases"].items() if cid in flagged]
        no = [v for c in cells for cid, v in c["cases"].items() if cid not in flagged]
        out[name] = {
            "cases": len(flagged),
            "with": round(statistics.fmean(yes), 4),
            "without": round(statistics.fmean(no), 4),
        }
    return out


# Confound check ---------------------------------------------------------------------------

CONFOUND_FEATURES: dict[str, Callable[[dict], float]] = {
    "doc-health score": health,
    "README analyzed tokens": lambda r: r.get("readme_tokens", 0),
    "README headings": lambda r: r.get("headings", 0),
    "README code blocks": lambda r: r.get("code_blocks", 0),
    "README badges": lambda r: r.get("badges", 0),
    "README is English": lambda r: float(bool(r.get("readme")) and not r.get("non_english")),
    "README not thin": lambda r: float(bool(r.get("readme")) and not r.get("thin")),
    "docs bytes": lambda r: r.get("docs_bytes", 0),
    "docs files": lambda r: r.get("docs_files", 0),
    "manifests": lambda r: r.get("manifests_files", 0),
    "contracts": lambda r: r.get("contracts_files", 0),
    "listed paths": lambda r: r.get("paths", 0),
    "packet bytes": lambda r: r.get("packet_bytes", 0),
}
SHORTCUTS = (
    ("packet bytes", True),
    ("docs bytes", True),
    ("manifests", True),
    ("listed paths", True),
    ("doc-health score", True),
    ("README analyzed tokens", True),
    ("README analyzed tokens", False),
)


def tiebreak(repo: str) -> str:
    return hashlib.sha1(repo_key(repo).encode()).hexdigest()


def random_complete(universe: int, k: int, depth: int = 10) -> float:
    return math.perm(depth, k) / math.perm(universe, k) if k <= depth else 0.0


def confound(corpus: Path, cases: Sequence[Case], rows: dict, sizes: Sequence[int]) -> dict:
    out: dict = {}
    for size in sizes:
        auc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        shortcut: dict[str, list[float]] = defaultdict(list)
        chance = []
        for case in cases:
            entries = universe_rows(corpus, size, case.case_id)
            snap = [(e, rows[(repo_key(e["repo"]), e["commit"])]) for e in entries]
            gold = [e["repo"] for e, _ in snap if e["role"] == "gold"]
            chance.append(random_complete(len(snap), len(gold)))
            for name, fn in CONFOUND_FEATURES.items():
                values = defaultdict(list)
                targets = []
                for e, r in snap:
                    v = fn(r)
                    if e["role"] == "gold":
                        targets.append(v)
                    else:
                        values[group_of(e["role"], e["source"])].append(v)
                        values["all decoys"].append(v)
                for group, vs in values.items():
                    vs.sort()
                    for t in targets:
                        lo = _bisect_left(vs, t)
                        hi = _bisect_right(vs, t)
                        auc[name][group].append((lo + 0.5 * (hi - lo)) / len(vs))
            for name, descending in SHORTCUTS:
                fn = CONFOUND_FEATURES[name]
                ranked = sorted(
                    snap,
                    key=lambda er: ((-1 if descending else 1) * fn(er[1]), tiebreak(er[0]["repo"])),
                )
                ranking = [e["repo"] for e, _ in ranked]
                label = f"{name}, {'largest' if descending else 'smallest'} first"
                shortcut[label].append(case_metrics(gold, ranking, (10,))[METRIC])
        out[size] = {
            "auc": {
                name: {g: round(statistics.fmean(v), 4) for g, v in groups.items()}
                for name, groups in auc.items()
            },
            "shortcut_cr10": {k: round(statistics.fmean(v), 4) for k, v in shortcut.items()},
            "random_cr10": round(statistics.fmean(chance), 6),
        }
    return out


def _bisect_left(a: Sequence[float], x: float) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _bisect_right(a: Sequence[float], x: float) -> int:
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


# Displacers -------------------------------------------------------------------------------


def displacers(corpus: Path, cases: Sequence[Case], rows: dict, real: Sequence[Path]) -> dict:
    """README length of the decoys in the top 10 of the cases a ranker misses.

    Compares them with those cases' targets and with every decoy in those cases' universes.
    ``L2 breaks`` repeats the original comparison: cases L0 gets and L2 misses.
    """
    gold = {c.case_id: [g.repo for g in c.gold] for c in cases}
    out: dict = {}
    for size in SIZES:
        snaps = {}
        for case in cases:
            entries = universe_rows(corpus, size, case.case_id)
            snaps[case.case_id] = {
                repo_key(e["repo"]): (e, rows[(repo_key(e["repo"]), e["commit"])]) for e in entries
            }
        for ranker in RANKERS:
            preds = {}
            for level in LEVELS:
                path = find(real, f"{ranker}--{level}--u{size}.jsonl")
                if path is not None:
                    preds[level] = {
                        p.case_id: list(p.ranking)
                        for p in read_predictions(path)
                        if p.case_id in gold
                    }
            for level, ranking in preds.items():
                if level == "L0":
                    continue
                missed = [c for c in gold if case_metrics(gold[c], ranking[c], (10,))[METRIC] < 1]
                out[f"{ranker} {level} U{size}"] = _displacer_stats(missed, gold, ranking, snaps)
            if "L0" in preds and "L2" in preds:
                broke = [
                    c
                    for c in gold
                    if case_metrics(gold[c], preds["L0"][c], (10,))[METRIC] == 1
                    and case_metrics(gold[c], preds["L2"][c], (10,))[METRIC] < 1
                ]
                out[f"{ranker} L2 breaks U{size}"] = _displacer_stats(
                    broke, gold, preds["L2"], snaps
                )
    return out


def _displacer_stats(missed: Sequence[str], gold: dict, ranking: dict, snaps: dict) -> dict:
    top, targets, pool = [], [], []
    for cid in missed:
        keys = {repo_key(g) for g in gold[cid]}
        for repo in ranking[cid][:10]:
            if repo_key(repo) not in keys:
                top.append(snaps[cid][repo_key(repo)][1])
        for key, (_, r) in snaps[cid].items():
            (targets if key in keys else pool).append(r)

    def med(rs: Sequence[dict]) -> float | None:
        v = [r["readme_tokens"] for r in rs if r.get("readme")]
        return statistics.median(v) if v else None

    def long(rs: Sequence[dict]) -> float | None:
        v = [r for r in rs if r.get("readme")]
        return round(sum(r["readme_tokens"] > LONG_README for r in v) / len(v), 4) if v else None

    return {
        "cases": len(missed),
        "top10_decoys": len(top),
        "readme_tokens_median": {
            "top10 decoys": med(top),
            "targets": med(targets),
            "all decoys": med(pool),
        },
        "readme_over_1000": {
            "top10 decoys": long(top),
            "targets": long(targets),
            "all decoys": long(pool),
        },
    }


MATCH_FILTERS: dict[str, Callable[[dict, Sequence[dict]], bool]] = {
    "documented decoys only (docs >= 10 KB, >= 2 manifests)": lambda r, targets: (
        r.get("docs_bytes", 0) >= DOCS_FULL and r.get("manifests_files", 0) >= 2
    ),
    "decoys at least as large as the case's smallest target": lambda r, targets: (
        r.get("packet_bytes", 0) >= min(t.get("packet_bytes", 0) for t in targets)
    ),
}
RANDOM_DRAWS = 20
SAVED_DEPTH = 100


def matched(
    corpus: Path,
    cases: Sequence[Case],
    rows: dict,
    real: Sequence[Path],
    sizes: Sequence[int],
    levels: Sequence[str],
) -> dict:
    """CR@10 of the saved rankings with the targets kept and only matching decoys left in.

    The rankings were scored over the whole universe, so this filters them after the fact;
    a random filter keeping as many decoys per case is the control for the smaller pool.
    ``short`` counts cases whose filtered list has fewer than 10 entries inside the saved
    top 100, where the filtered top 10 is not fully known.
    """
    gold = {c.case_id: [g.repo for g in c.gold] for c in cases}
    rng = random.Random(0)
    out: dict = {}
    for size in sizes:
        keep: dict[str, dict[str, set[str]]] = {}
        draws: dict[str, dict[str, list[set[str]]]] = {}
        share: dict[str, list[float]] = defaultdict(list)
        for case in cases:
            entries = universe_rows(corpus, size, case.case_id)
            snaps = [
                (repo_key(e["repo"]), rows[(repo_key(e["repo"]), e["commit"])], e["role"])
                for e in entries
            ]
            targets = [r for _, r, role in snaps if role == "gold"]
            decoys = [(k, r) for k, r, role in snaps if role != "gold"]
            for name, test in MATCH_FILTERS.items():
                kept = {k for k, r in decoys if test(r, targets)}
                keep.setdefault(name, {})[case.case_id] = kept
                share[name].append(len(kept) / len(decoys))
                keys = [k for k, _ in decoys]
                draws.setdefault(name, {})[case.case_id] = [
                    set(rng.sample(keys, len(kept))) for _ in range(RANDOM_DRAWS)
                ]
        for ranker in RANKERS:
            for level in levels:
                path = find(real, f"{ranker}--{level}--u{size}.jsonl")
                if path is None:
                    continue
                preds = {
                    p.case_id: list(p.ranking) for p in read_predictions(path) if p.case_id in gold
                }
                row: dict = {"all decoys": _cr10(gold, preds, None)}
                for name in MATCH_FILTERS:
                    filtered, short = _cr10(gold, preds, keep[name], count_short=True)
                    rand = statistics.fmean(
                        _cr10(gold, preds, {c: draws[name][c][i] for c in gold})
                        for i in range(RANDOM_DRAWS)
                    )
                    row[name] = {
                        "decoys_kept": round(statistics.fmean(share[name]), 4),
                        "cr10": filtered,
                        "random_same_count": round(rand, 4),
                        "short": short,
                    }
                out[f"{ranker} {level} U{size}"] = row
    return out


def _cr10(gold: dict, preds: dict, kept: dict[str, set[str]] | None, count_short: bool = False):
    scores, short = [], 0
    for cid, ranking in preds.items():
        keys = {repo_key(g) for g in gold[cid]}
        if kept is None:
            filtered = ranking
        else:
            filtered = [r for r in ranking if repo_key(r) in keys or repo_key(r) in kept[cid]]
            short += len(filtered) < 10 and len(ranking) >= SAVED_DEPTH
        scores.append(case_metrics(gold[cid], filtered, (10,))[METRIC])
    mean = round(statistics.fmean(scores), 4)
    return (mean, short) if count_short else mean


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--features", type=Path, required=True)
    parser.add_argument(
        "--static-real",
        type=Path,
        nargs="+",
        required=True,
        help="directories searched in order for each real-name prediction file",
    )
    parser.add_argument("--static-hidden", type=Path)
    parser.add_argument(
        "--anon", type=Path, help="holds the hidden-name builds u1000, u5000, u10000"
    )
    parser.add_argument("--agents", type=Path, required=True)
    parser.add_argument("--confound-sizes", type=int, nargs="+", default=list(SIZES))
    parser.add_argument("--expect-cases", type=int, default=DEV_CASES)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    cases = dev_cases(args.cases, args.expect_cases)
    rows = load_rows(args.features)
    table = case_table(args.corpus, cases, rows)
    tiers = {cid: row["tier"] for cid, row in table.items()}
    cells = static_cells(cases, args.static_real, args.static_hidden, args.anon)
    cells.update(agent_cells(cases, args.agents))

    rng = random.Random(0)
    by_family: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for cell in cells.values():
        by_family[(cell["family"], cell["names"])].append(cell)
        if cell["family"].startswith("static") and cell["level"] != "L0":
            by_family[(f"{cell['family']} {cell['level']}", cell["names"])].append(cell)
    families = {
        f"{family} | {names}": {
            **gap_test(cs, tiers, rng, relative="gain over L0" in family),
            "flags": flag_splits(cs, table),
        }
        for (family, names), cs in sorted(by_family.items())
    }
    summary = {
        "cases": table,
        "tier_sizes": Counter(tiers.values()),
        "tier_composition": {
            field: {
                t: Counter(getattr(c, field) for c in cases if tiers[c.case_id] == t) for t in TIERS
            }
            for field in ("ecosystem", "source", "category")
        },
        "tier_score_ranges": {
            t: [
                min(r["score"] for r in table.values() if r["tier"] == t),
                max(r["score"] for r in table.values() if r["tier"] == t),
            ]
            for t in TIERS
        },
        "cells": {
            name: {k: v for k, v in c.items() if k != "cases"}
            | {
                "all": round(statistics.fmean(c["cases"].values()), 4),
                "tiers": tier_means(c, tiers),
            }
            for name, c in cells.items()
        },
        "families": families,
        "confound": confound(args.corpus, cases, rows, args.confound_sizes),
        "displacers": displacers(args.corpus, cases, rows, args.static_real),
        "matched": matched(
            args.corpus,
            cases,
            rows,
            args.static_real,
            args.confound_sizes,
            ("L0", "L2", "L3", "L3+docs", "L4"),
        ),
    }
    args.out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({"tiers": summary["tier_sizes"], "ranges": summary["tier_score_ranges"]}))
    for name, f in families.items():
        counts = {k: f[k] for k in ("misses", "gains", "losses") if k in f}
        gap = f["gap_high_minus_low"]
        print(f"{name:<60} cells={f['cells']:>3} gap={gap:+.3f} p={f['p']:.3f} {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
