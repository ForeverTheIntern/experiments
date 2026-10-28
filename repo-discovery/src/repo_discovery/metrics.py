"""Per-case ranking metrics and their clustered summaries.

For each K:

- ``Hit@K``: at least one gold repository is in the top K.
- ``CompleteRecall@K``: every gold repository is in the top K.
- ``RequiredRecall@K``: the fraction of gold repositories in the top K.

``LastRequiredRank`` is the rank of the last gold repository found, capped at
``max(K) + 1`` (101 by default) when any gold repository is ranked lower or not
at all.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from repo_discovery.cases import Case, repo_key
from repo_discovery.predictions import Prediction
from repo_discovery.stats import Estimate, cluster_bootstrap

KS: tuple[int, ...] = (10, 20, 50, 100)


def metric_names(ks: Sequence[int] = KS) -> list[str]:
    names = [f"{m}@{k}" for m in ("Hit", "CompleteRecall", "RequiredRecall") for k in ks]
    return [*names, "LastRequiredRank"]


def case_metrics(
    gold: Sequence[str], ranking: Sequence[str], ks: Sequence[int] = KS
) -> dict[str, float]:
    positions: dict[str, int] = {}
    for rank, repo in enumerate(ranking, start=1):
        positions.setdefault(repo_key(repo), rank)
    cap = max(ks) + 1
    gold_ranks = [positions.get(repo_key(g), cap) for g in gold]
    values: dict[str, float] = {}
    for k in ks:
        found = sum(rank <= k for rank in gold_ranks)
        values[f"Hit@{k}"] = float(found > 0)
        values[f"CompleteRecall@{k}"] = float(found == len(gold_ranks))
        values[f"RequiredRecall@{k}"] = found / len(gold_ranks)
    values["LastRequiredRank"] = float(min(max(gold_ranks), cap))
    return values


@dataclass(frozen=True)
class Report:
    ranker: str
    n_cases: int
    n_ecosystems: int
    metrics: dict[str, Estimate]
    median_last_required_rank: float

    def to_dict(self) -> dict:
        return {
            "ranker": self.ranker,
            "n_cases": self.n_cases,
            "n_ecosystems": self.n_ecosystems,
            "metrics": {
                name: {"mean": e.mean, "ci_low": e.low, "ci_high": e.high}
                for name, e in self.metrics.items()
            },
            "median_last_required_rank": self.median_last_required_rank,
        }


def evaluate(
    cases: Sequence[Case],
    predictions: Sequence[Prediction],
    ks: Sequence[int] = KS,
    n_boot: int = 2000,
    seed: int = 0,
) -> list[Report]:
    """One report per ranker over ``cases``; every case needs a prediction from every ranker."""
    by_ranker: dict[str, dict[str, Prediction]] = defaultdict(dict)
    for prediction in predictions:
        by_ranker[prediction.ranker][prediction.case_id] = prediction
    reports = []
    for ranker, by_case in sorted(by_ranker.items()):
        missing = [c.case_id for c in cases if c.case_id not in by_case]
        if missing:
            raise ValueError(f"{ranker}: no prediction for {len(missing)} cases, e.g. {missing[0]}")
        rows = [case_metrics(c.gold_repos, by_case[c.case_id].ranking, ks) for c in cases]
        clusters = [c.ecosystem for c in cases]
        metrics = {
            name: cluster_bootstrap([r[name] for r in rows], clusters, n_boot=n_boot, seed=seed)
            for name in metric_names(ks)
        }
        reports.append(
            Report(
                ranker=ranker,
                n_cases=len(cases),
                n_ecosystems=len(set(clusters)),
                metrics=metrics,
                median_last_required_rank=statistics.median(r["LastRequiredRank"] for r in rows),
            )
        )
    return reports


def format_reports(reports: Sequence[Report], ks: Sequence[int] = KS) -> str:
    lines = []
    for report in reports:
        lines.append(
            f"{report.ranker}: {report.n_cases} cases, {report.n_ecosystems} ecosystems "
            f"(95% CI, ecosystem cluster bootstrap)"
        )
        for name in metric_names(ks):
            e = report.metrics[name]
            lines.append(f"  {name:<20} {e.mean:8.3f}  [{e.low:.3f}, {e.high:.3f}]")
        lines.append(f"  {'median LastReqRank':<20} {report.median_last_required_rank:8.1f}")
    return "\n".join(lines)
