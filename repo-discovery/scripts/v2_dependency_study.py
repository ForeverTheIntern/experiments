"""V2 study: one-hop dependency expansion of bm25f, development split only.

    python scripts/v2_dependency_study.py --cases CASES --universes UNIVERSES \
        --predictions PREDICTIONS --out v2-results.json

``UNIVERSES/u{N}`` holds the materialized universes for N in 50, 250, 1000 and 5000.
``PREDICTIONS/bm25f-full-u{N}.jsonl`` holds ``bm25f`` rankings of every repository in each
universe (``repo-discovery run --ranker bm25f --depth 5000``), so the expansion here is
the ``bm25f-deps`` ranker's, computed once per case for all variants. Prints the report
tables and writes every number to ``--out``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics
import time
from collections import Counter
from pathlib import Path

from repo_discovery.cases import Case, read_cases, repo_key
from repo_discovery.contract import Universe, resolve_universe
from repo_discovery.depgraph import OUTCOMES, DependencyGraph, Resolution, build_graph
from repo_discovery.metrics import case_metrics
from repo_discovery.packages import Packages, read_packages
from repo_discovery.predictions import read_predictions
from repo_discovery.rankers.deps import expand
from repo_discovery.stats import cluster_bootstrap

SIZES = (50, 250, 1000, 5000)
KS = (10, 20, 50, 100)
DRAWS = 5
SEEDS = 10
VARIANTS = {"V2": (10, 1), "S=5": (5, 1), "S=20": (20, 1), "keep top 5": (10, 5)}
METRICS = ("CompleteRecall@10", "CompleteRecall@20", "CompleteRecall@50", "Hit@10", "Hit@50")
SLICES = {"all": None, "none+partial": {"none", "partial"}}


def control_rng(draw: int, case_id: str) -> random.Random:
    """The rewiring stream ``bm25f-deps-random`` uses with ``seed=draw``."""
    return random.Random(f"rewire:{draw}:{case_id}")


class PackageCache:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], Packages] = {}
        self.seconds = 0.0

    def get(self, repo: str, packet: Path) -> Packages:
        key = (repo_key(repo), packet.name)
        if key not in self.entries:
            started = time.perf_counter()
            self.entries[key] = read_packages(packet)
            self.seconds += time.perf_counter() - started
        return self.entries[key]


def reachable(graph: DependencyGraph, seeds: list[str], targets: list[str]) -> dict[str, int]:
    out = set().union(*(graph.outbound(s) for s in seeds))
    inbound = set().union(*(graph.inbound(s) for s in seeds))
    return {
        "either": sum(t in out or t in inbound for t in targets),
        "outbound": sum(t in out for t in targets),
        "inbound": sum(t in inbound for t in targets),
    }


def study_case(case: Case, universe: Universe, base: list[str], cache: PackageCache) -> dict:
    view = universe.view()
    by_key = {repo_key(c.repo): c.repo for c in view.candidates}
    gold = [by_key[repo_key(g)] for g in case.gold_repos if repo_key(g) in by_key]
    if len(base) != len(view):
        raise ValueError(f"{case.case_id}: bm25f ranked {len(base)} of {len(view)} repositories")
    packages = {c.repo: cache.get(c.repo, c.evidence_dir) for c in view.candidates}
    started = time.perf_counter()
    graph, resolution = build_graph(packages)
    rankings = {"bm25f": base}
    for name, (seeds, head) in VARIANTS.items():
        rankings[name] = expand(base, graph, seeds, head)
    seconds = time.perf_counter() - started
    controls = [graph.rewired(control_rng(d, case.case_id)) for d in range(DRAWS)]
    metrics = {name: case_metrics(case.gold_repos, r, KS) for name, r in rankings.items()}
    draws = [case_metrics(case.gold_repos, expand(base, g, SEEDS, 1), KS) for g in controls]
    metrics["control"] = {m: statistics.fmean(d[m] for d in draws) for m in draws[0]}

    top = base[:SEEDS]
    missed = [g for g in gold if g not in top]
    pairs = list(itertools.combinations(gold, 2))
    rank = {r: i for i, r in enumerate(base, start=1)}
    touched = Counter(n for s in top for n in graph.neighbours(s))
    return {
        "case_id": case.case_id,
        "ecosystem": case.ecosystem,
        "visibility": case.scope_visibility,
        "metrics": metrics,
        "resolution": resolution,
        "repos": len(view),
        "repos_with_edges": sum(graph.degree(r) > 0 for r in graph.repos),
        "repos_declaring": sum(bool(p.provides) for p in packages.values()),
        "edges": len(graph.edges),
        "control_edges": statistics.fmean(len(g.edges) for g in controls),
        "neighbours": len(touched),
        "neighbours_outside_top": sum(r not in top for r in touched),
        "gold_pairs": len(pairs),
        "gold_pairs_linked": sum((a, b) in graph.edges or (b, a) in graph.edges for a, b in pairs),
        "control_gold_pairs_linked": statistics.fmean(
            sum((a, b) in g.edges or (b, a) in g.edges for a, b in pairs) for g in controls
        ),
        "gold_degrees": [graph.degree(g) for g in gold],
        "missed": len(missed),
        "missed_bm25f_ranks": [rank[g] for g in missed],
        "missed_reachable": reachable(graph, top, missed),
        "control_missed_reachable": statistics.fmean(
            reachable(g, top, missed)["either"] for g in controls
        ),
        "all_missed_reachable": bool(missed)
        and reachable(graph, top, missed)["either"] == len(missed),
        "v2_gold_ranks": {g: rankings["V2"].index(g) + 1 for g in gold},
        "bm25f_gold_ranks": {g: rank[g] for g in gold},
        "seconds": seconds,
    }


def estimate(rows: list[dict], value) -> list[float]:
    e = cluster_bootstrap([value(r) for r in rows], [r["ecosystem"] for r in rows])
    return [round(e.mean, 4), round(e.low, 4), round(e.high, 4)]


def summarize(rows: list[dict]) -> dict:
    names = ["bm25f", *VARIANTS, "control"]
    out: dict = {"cases": len(rows), "ecosystems": len({r["ecosystem"] for r in rows})}
    out["metrics"] = {
        n: {m: estimate(rows, lambda r, n=n, m=m: r["metrics"][n][m]) for m in METRICS}
        for n in names
    }
    out["median_last_rank"] = {
        n: statistics.median(r["metrics"][n]["LastRequiredRank"] for r in rows) for n in names
    }
    pairs = [("V2", "bm25f"), ("V2", "control"), ("control", "bm25f")]
    pairs += [(v, "bm25f") for v in VARIANTS if v != "V2"]
    out["differences"] = {
        f"{a} - {b}": {
            m: estimate(rows, lambda r, a=a, b=b, m=m: r["metrics"][a][m] - r["metrics"][b][m])
            for m in ("CompleteRecall@10", "CompleteRecall@50", "Hit@10")
        }
        for a, b in pairs
    }
    out["transitions"] = {}
    for k in (10, 50):
        m = f"CompleteRecall@{k}"
        rescued = [r["case_id"] for r in rows if r["metrics"]["V2"][m] > r["metrics"]["bm25f"][m]]
        broken = [r["case_id"] for r in rows if r["metrics"]["V2"][m] < r["metrics"]["bm25f"][m]]
        out["transitions"][m] = {"rescued": rescued, "broken": broken}
    return out


def graph_summary(rows: list[dict]) -> dict:
    pooled = Resolution()
    for r in rows:
        pooled.outcomes.update(r["resolution"].outcomes)
        for eco, counts in r["resolution"].by_ecosystem.items():
            pooled.by_ecosystem[eco].update(counts)
    incomplete = [r for r in rows if r["missed"]]
    missed = sum(r["missed"] for r in incomplete)
    return {
        "resolution_rate": round(pooled.rate(pooled.outcomes), 4),
        "outcomes": {o: pooled.outcomes[o] for o in OUTCOMES},
        "by_ecosystem": {
            eco: {"rate": round(pooled.rate(c), 4), **{o: c[o] for o in OUTCOMES}}
            for eco, c in sorted(pooled.by_ecosystem.items(), key=lambda kv: -kv[1].total())
        },
        "share_repos_with_edges": round(
            sum(r["repos_with_edges"] for r in rows) / sum(r["repos"] for r in rows), 4
        ),
        "share_repos_declaring": round(
            sum(r["repos_declaring"] for r in rows) / sum(r["repos"] for r in rows), 4
        ),
        "mean_edges": round(statistics.fmean(r["edges"] for r in rows), 1),
        "mean_control_edges": round(statistics.fmean(r["control_edges"] for r in rows), 1),
        "mean_neighbours": round(statistics.fmean(r["neighbours"] for r in rows), 1),
        "median_neighbours": statistics.median(r["neighbours"] for r in rows),
        "share_gold_with_edges": round(
            sum(d > 0 for r in rows for d in r["gold_degrees"])
            / sum(len(r["gold_degrees"]) for r in rows),
            4,
        ),
        "gold_pairs": sum(r["gold_pairs"] for r in rows),
        "gold_pairs_linked": sum(r["gold_pairs_linked"] for r in rows),
        "control_gold_pairs_linked": round(sum(r["control_gold_pairs_linked"] for r in rows), 2),
        "cases_with_linked_gold": sum(r["gold_pairs_linked"] > 0 for r in rows),
        "incomplete_at_10": len(incomplete),
        "missed_at_10": missed,
        "missed_reachable": {
            d: sum(r["missed_reachable"][d] for r in incomplete)
            for d in ("either", "outbound", "inbound")
        },
        "control_missed_reachable": round(
            sum(r["control_missed_reachable"] for r in incomplete), 2
        ),
        "cases_all_missed_reachable": sum(r["all_missed_reachable"] for r in incomplete),
        "seconds_per_case": round(statistics.fmean(r["seconds"] for r in rows), 4),
    }


def fmt(e: list[float]) -> str:
    return f"{e[0]:.2f} [{e[1]:.2f}, {e[2]:.2f}]"


def print_tables(results: dict) -> None:
    sizes = list(results["sizes"])
    head = "| " + " | ".join(f"U{s}" for s in sizes) + " |"
    for slice_name in SLICES:
        for metric in METRICS:
            print(f"\n{slice_name} {metric}\n| Ranker {head}\n|---" + "|---" * len(sizes) + "|")
            for name in ["bm25f", *VARIANTS, "control"]:
                cells = [
                    fmt(results["sizes"][s]["slices"][slice_name]["metrics"][name][metric])
                    for s in sizes
                ]
                print(f"| {name} | " + " | ".join(cells) + " |")
        print(f"\n{slice_name} differences\n| Pair | Metric {head}\n|---|---" + "|---" * len(sizes))
        first = results["sizes"][sizes[0]]["slices"][slice_name]["differences"]
        for pair, by_metric in first.items():
            for metric in by_metric:
                cells = [
                    fmt(results["sizes"][s]["slices"][slice_name]["differences"][pair][metric])
                    for s in sizes
                ]
                print(f"| {pair} | {metric} | " + " | ".join(cells) + " |")
    print("\ngraph")
    for s in sizes:
        print(f"U{s}", json.dumps(results["sizes"][s]["graph"]))
        for slice_name in SLICES:
            print(
                f"U{s} {slice_name} transitions",
                json.dumps(results["sizes"][s]["slices"][slice_name]["transitions"]),
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--universes", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    args = parser.parse_args()

    cases = sorted((c for c in read_cases(args.cases) if c.split == "dev"), key=lambda c: c.case_id)
    assert cases and all(c.split == "dev" for c in cases)
    cache = PackageCache()
    results: dict = {"cases": len(cases), "sizes": {}}
    for size in args.sizes:
        base = {
            p.case_id: list(p.ranking)
            for p in read_predictions(args.predictions / f"bm25f-full-u{size}.jsonl")
        }
        rows = []
        for case in cases:
            universe = Universe.load(resolve_universe(args.universes / f"u{size}", case.case_id))
            rows.append(study_case(case, universe, base[case.case_id], cache))
        results["sizes"][size] = {
            "graph": graph_summary(rows),
            "slices": {
                name: summarize([r for r in rows if keep is None or r["visibility"] in keep])
                for name, keep in SLICES.items()
            },
            "cases": [
                {k: v for k, v in r.items() if k != "resolution"}
                | {"resolution_rate": round(Resolution.rate(r["resolution"].outcomes), 4)}
                for r in rows
            ],
        }
        results["sizes"][size]["graph"]["package_seconds_cumulative"] = round(cache.seconds, 1)
        results["sizes"][size]["graph"]["snapshots_read_cumulative"] = len(cache.entries)
        print(f"U{size}: done, {len(cache.entries)} snapshots read in {cache.seconds:.0f} s")
    args.out.write_text(json.dumps(results, indent=1, default=list), encoding="utf-8")
    print_tables(results)


if __name__ == "__main__":
    main()
