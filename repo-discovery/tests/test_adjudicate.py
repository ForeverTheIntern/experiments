from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "labels"))

from adjudicate import FIELDS, apply, kappa

from repo_discovery.cases import Case, RepoAtCommit, read_cases, write_cases


def test_kappa() -> None:
    assert kappa([("a", "a"), ("b", "b")]) == 1.0
    assert kappa([("a", "b"), ("b", "a")]) == pytest.approx(-1.0)
    assert kappa([("a", "a"), ("a", "b"), ("b", "b"), ("b", "a")]) == pytest.approx(0.0)


def test_apply_relabels_gold_from_verdicts(tmp_path: Path) -> None:
    case = Case(
        case_id="acme_x_1",
        source="wideswe",
        ecosystem="acme",
        prompt="Fix acme billing rounding.",
        gold=(RepoAtCommit("acme/billing", "a" * 40), RepoAtCommit("acme/bump", "b" * 40)),
        cutoff="2026-01-01T00:00:00+00:00",
        context=(RepoAtCommit("acme/ledger", "c" * 40),),
    )
    other = Case(
        case_id="acme_y_2",
        source="wideswe",
        ecosystem="acme",
        prompt="Unreviewed.",
        gold=(RepoAtCommit("acme/one", "d" * 40),),
        cutoff="2026-01-01T00:00:00+00:00",
    )
    cases = tmp_path / "cases.jsonl"
    write_cases(cases, [case, other])
    rows = [
        {"case_id": "acme_x_1", "repo": "acme/billing", "verdict": "required"},
        {"case_id": "acme_x_1", "repo": "acme/bump", "verdict": "affected"},
        {"case_id": "acme_x_1", "repo": "acme/ledger", "verdict": "not-needed"},
        {"case_id": "acme_x_1", "repo": "(missing)", "missing": "acme/ledger, acme/elsewhere"},
    ]
    final = tmp_path / "final.csv"
    with final.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({**dict.fromkeys(FIELDS, ""), **r} for r in rows)
    out = tmp_path / "adjudicated.jsonl"
    apply(cases, final, out)
    relabelled, untouched = read_cases(out)
    assert relabelled.gold_repos == ("acme/billing", "acme/ledger")
    assert {r.repo for r in relabelled.context} == {"acme/bump"}
    assert relabelled.scope_visibility == "partial"
    assert untouched.gold_repos == ("acme/one",)
