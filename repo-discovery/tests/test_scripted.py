from __future__ import annotations

from pathlib import Path

import pytest

from repo_discovery import harness
from repo_discovery.cases import Case, Query
from repo_discovery.contract import Universe
from repo_discovery.rankers import make_ranker
from repo_discovery.rankers.scripted import ScriptedOpenExplorer, facets, fuse

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"
CUTOFF = "2026-03-01T00:00:00+00:00"
PROMPT = """Monthly invoices round tax twice when a refund lands on the last day.

- Totals must be computed once from the stored `line_items`.
- The `RefundEntry` must carry the original rounding mode.
ok then
"""


def test_facets_are_the_request_its_parts_and_its_code_tokens() -> None:
    found = facets(PROMPT)
    assert found[0] == PROMPT.strip()
    assert "Monthly invoices round tax twice when a refund lands on the last day." in found
    assert "Totals must be computed once from the stored `line_items`." in found
    assert found[-1] == "line_items RefundEntry"
    assert not any(f == "ok then" for f in found)
    assert len(facets(PROMPT, max_facets=2)) == 2
    assert facets("refund refund. Refund!") == ["refund refund. Refund!"]


def test_fuse_rewards_agreement_and_breaks_ties_by_first_facet() -> None:
    assert fuse([["a", "b", "c"], ["b", "d"]]) == ["b", "a", "d", "c"]
    assert fuse([["x"], ["y"]]) == ["x", "y"]


@pytest.mark.parametrize("ranker", ["scripted-search", "scripted-open5"])
def test_rankings_are_permutations_and_search_finds_gold(
    ranker: str, mini_cases: list[Case]
) -> None:
    repos = sorted(c.repo for c in Universe.load(EVIDENCE_UNIVERSE).view().candidates)
    for prediction in harness.run(make_ranker(ranker), mini_cases, EVIDENCE_UNIVERSE):
        assert sorted(prediction.ranking) == repos
        case = next(c for c in mini_cases if c.case_id == prediction.case_id)
        if ranker == "scripted-search":
            assert set(case.gold_repos) <= set(prediction.ranking[:5])


def test_open_explorer_reorders_only_what_it_opened() -> None:
    universe = Universe.load(EVIDENCE_UNIVERSE).view()
    query = Query("c1", "billing ledger rounding for refunds", CUTOFF)
    names = make_ranker("scripted-open5")
    ranking = names.rank(query, universe)
    assert isinstance(names, ScriptedOpenExplorer) and len(names.opened) == 5
    assert set(ranking[:5]) == set(names.opened)
    assert ranking[5:] == make_ranker("scripted-open5").rank(query, universe)[5:]
    with pytest.raises(ValueError, match="budget"):
        ScriptedOpenExplorer(budget=0)
