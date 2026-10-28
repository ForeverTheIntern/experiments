from __future__ import annotations

from pathlib import Path

import pytest

from repo_discovery import harness
from repo_discovery.cases import Case, Query
from repo_discovery.contract import Candidate, UniverseView
from repo_discovery.rankers import available, make_ranker


def view(*repos: str) -> UniverseView:
    return UniverseView(Path("."), tuple(Candidate(r, Path(r)) for r in repos))


REPOS = ("acme/billing", "acme/ledger", "acme/portal", "other/billing-ui", "noise/one", "noise/two")


def test_registry() -> None:
    assert available() == [
        "bm25-cards",
        "bm25f",
        "bm25f-deps",
        "bm25f-deps-random",
        "cursor-agent",
        "name-match",
        "random",
    ]
    with pytest.raises(KeyError):
        make_ranker("bm25")


def test_random_is_a_seeded_permutation() -> None:
    ranker = make_ranker("random", seed=7)
    query = Query("c1", "anything", "2026-01-01T00:00:00+00:00")
    first = ranker.rank(query, view(*REPOS))
    assert sorted(first) == sorted(REPOS)
    assert ranker.rank(query, view(*REPOS)) == first
    assert ranker.rank(Query("c2", "anything", query.cutoff), view(*REPOS)) != first


def test_name_match_orders_by_name_then_owner_then_overlap() -> None:
    ranker = make_ranker("name-match")
    query = Query("c1", "The acme Ledger drops billing rows.", "2026-01-01T00:00:00+00:00")
    ranking = ranker.rank(query, view(*REPOS))
    assert set(ranking[:2]) == {"acme/ledger", "acme/billing"}
    assert ranking[2] == "acme/portal"
    assert ranking[3] == "other/billing-ui"
    assert sorted(ranking) == sorted(REPOS)


def test_harness_rejects_rankings_outside_the_universe(
    make_universe, mini_cases: list[Case]
) -> None:
    class Cheater:
        name = "cheater"

        def rank(self, query: Query, universe: UniverseView) -> list[str]:
            return ["acme/billing", "made/up"]

    root = make_universe(
        [
            {
                "repo": "acme/billing",
                "commit": "1" * 40,
                "snapshot_date": "2026-01-31",
                "role": "gold",
            }
        ]
    )
    with pytest.raises(ValueError, match="outside the universe"):
        list(harness.run(Cheater(), mini_cases[:1], root))


def test_harness_rejects_duplicates(make_universe, mini_cases: list[Case]) -> None:
    class Stutter:
        name = "stutter"

        def rank(self, query: Query, universe: UniverseView) -> list[str]:
            return ["acme/billing", "ACME/billing"]

    root = make_universe(
        [
            {
                "repo": "acme/billing",
                "commit": "1" * 40,
                "snapshot_date": "2026-01-31",
                "role": "gold",
            }
        ]
    )
    with pytest.raises(ValueError, match="more than once"):
        list(harness.run(Stutter(), mini_cases[:1], root))


def test_harness_truncates_and_logs_missing_gold(make_universe, mini_cases: list[Case]) -> None:
    entries = [
        {"repo": f"n/r{i}", "commit": "0" * 40, "snapshot_date": "2026-01-31", "role": "distractor"}
        for i in range(30)
    ]
    root = make_universe(entries)
    log = harness.RunLog()
    [prediction] = harness.run(make_ranker("random"), mini_cases[:1], root, depth=10, log=log)
    assert len(prediction.ranking) == 10
    assert prediction.universe_size == 30
    assert log.gold_missing == {mini_cases[0].case_id: ["acme/billing", "acme/ledger"]}
    assert log.after_cutoff == {}


def test_harness_logs_snapshots_after_cutoff(make_universe, mini_cases: list[Case]) -> None:
    case = mini_cases[0]
    assert case.cutoff.startswith("2026-03-10")
    on_the_day = {"repo": "acme/billing", "commit": "a" * 40, "snapshot_date": "2026-03-10"}
    day_after = {"repo": "acme/ledger", "commit": "b" * 40, "snapshot_date": "2026-03-11"}
    root = make_universe([{**on_the_day, "role": "gold"}, {**day_after, "role": "gold"}])
    log = harness.RunLog()
    list(harness.run(make_ranker("random"), [case], root, log=log))
    assert log.after_cutoff == {case.case_id: ["acme/ledger"]}
