from __future__ import annotations

import pytest

from repo_discovery.cases import Case, RepoAtCommit
from repo_discovery.metrics import case_metrics, evaluate, format_reports, metric_names
from repo_discovery.predictions import Prediction


def ranking_with(golds: dict[int, str], length: int = 150) -> list[str]:
    """A ranking of ``length`` filler repos with gold repos placed at the given 1-based ranks."""
    ranking = [f"fill/r{i}" for i in range(length)]
    for rank, repo in golds.items():
        ranking[rank - 1] = repo
    return ranking


def test_metric_names() -> None:
    assert metric_names((5,)) == [
        "Hit@5",
        "CompleteRecall@5",
        "RequiredRecall@5",
        "LastRequiredRank",
    ]


def test_case_metrics_by_k() -> None:
    m = case_metrics(["a/x", "a/y", "a/z"], ranking_with({3: "a/x", 15: "A/Y", 60: "a/z"}))
    assert [m[f"Hit@{k}"] for k in (10, 20, 50, 100)] == [1, 1, 1, 1]
    assert [m[f"CompleteRecall@{k}"] for k in (10, 20, 50, 100)] == [0, 0, 0, 1]
    assert [m[f"RequiredRecall@{k}"] for k in (10, 20, 50, 100)] == pytest.approx(
        [1 / 3, 2 / 3, 2 / 3, 1]
    )
    assert m["LastRequiredRank"] == 60


def test_last_required_rank_is_censored() -> None:
    assert (
        case_metrics(["a/x", "a/y"], ranking_with({1: "a/x", 120: "a/y"}))["LastRequiredRank"]
        == 101
    )
    missing = case_metrics(["a/x", "a/y"], ["a/x"])
    assert missing["LastRequiredRank"] == 101
    assert missing["Hit@10"] == 1 and missing["CompleteRecall@100"] == 0
    assert case_metrics(["a/x"], [])["Hit@100"] == 0


def make_case(case_id: str, ecosystem: str, gold: list[str]) -> Case:
    return Case(
        case_id=case_id,
        source="test",
        ecosystem=ecosystem,
        prompt="p",
        gold=tuple(RepoAtCommit(g, "0" * 40) for g in gold),
        cutoff="2026-01-01T00:00:00+00:00",
    )


def prediction(case_id: str, ranking: list[str], ranker: str = "r") -> Prediction:
    return Prediction(case_id, ranker, tuple(ranking), "u", len(ranking), 0.0)


def test_evaluate_aggregates_per_ranker() -> None:
    cases = [
        make_case("c1", "e1", ["a/x", "a/y"]),
        make_case("c2", "e1", ["a/x"]),
        make_case("c3", "e2", ["b/x", "b/y"]),
    ]
    preds = [
        prediction("c1", ranking_with({1: "a/x", 2: "a/y"})),
        prediction("c2", ranking_with({30: "a/x"})),
        prediction("c3", ranking_with({5: "b/x"})),
        *(prediction(c.case_id, [], ranker="empty") for c in cases),
    ]
    reports = {r.ranker: r for r in evaluate(cases, preds, n_boot=200)}
    r = reports["r"]
    assert (r.n_cases, r.n_ecosystems) == (3, 2)
    assert r.metrics["Hit@10"].mean == pytest.approx(2 / 3)
    assert r.metrics["CompleteRecall@10"].mean == pytest.approx(1 / 3)
    assert r.metrics["RequiredRecall@10"].mean == pytest.approx((1 + 0 + 0.5) / 3)
    assert r.metrics["LastRequiredRank"].mean == pytest.approx((2 + 30 + 101) / 3)
    assert r.median_last_required_rank == 30
    for e in r.metrics.values():
        assert e.low <= e.mean <= e.high
    assert reports["empty"].metrics["Hit@100"].mean == 0
    assert "CompleteRecall@20" in format_reports(list(reports.values()))
    assert set(r.to_dict()["metrics"]["Hit@10"]) == {"mean", "ci_low", "ci_high"}


def test_evaluate_requires_every_case() -> None:
    cases = [make_case("c1", "e1", ["a/x"]), make_case("c2", "e1", ["a/x"])]
    with pytest.raises(ValueError, match="no prediction"):
        evaluate(cases, [prediction("c1", ["a/x"])])
