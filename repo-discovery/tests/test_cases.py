from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from repo_discovery.cases import (
    Case,
    Query,
    RepoAtCommit,
    read_cases,
    repo_key,
    select_cases,
    write_cases,
)


def make_case(case_id: str = "c1", **changes: object) -> Case:
    base = Case(
        case_id=case_id,
        source="test",
        ecosystem="acme",
        prompt="Fix the thing.",
        gold=(RepoAtCommit("acme/api", "a" * 40), RepoAtCommit("acme/web", "b" * 40)),
        cutoff="2026-01-02T03:04:05+00:00",
    )
    return replace(base, **changes)


def test_round_trip(tmp_path: Path) -> None:
    cases = [
        make_case("c1", scope_visibility="none", split="dev"),
        make_case(
            "c2",
            context=(RepoAtCommit("acme/docs", "c" * 40),),
            scope_visibility="partial",
            visible_gold=("acme/api",),
            split="test",
        ),
    ]
    path = tmp_path / "cases.jsonl"
    assert write_cases(path, cases) == 2
    assert read_cases(path) == cases


def test_rejects_bad_cases() -> None:
    with pytest.raises(ValueError, match="at least one gold"):
        make_case(gold=())
    with pytest.raises(ValueError, match="duplicate gold"):
        make_case(gold=(RepoAtCommit("acme/api", "a"), RepoAtCommit("ACME/api", "b")))
    with pytest.raises(ValueError):
        make_case(cutoff="yesterday")
    with pytest.raises(ValueError, match="scope_visibility"):
        make_case(scope_visibility="mostly")
    with pytest.raises(ValueError, match="split"):
        make_case(split="train")


def test_duplicate_case_ids_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    write_cases(path, [make_case("c1"), make_case("c1")])
    with pytest.raises(ValueError, match="duplicate case_id"):
        read_cases(path)


def test_select_cases() -> None:
    cases = [
        make_case("a", split="dev", scope_visibility="none"),
        make_case("b", split="dev", scope_visibility="complete"),
        make_case("c", split="test", scope_visibility="none"),
    ]
    assert [c.case_id for c in select_cases(cases, split="dev")] == ["a", "b"]
    assert [c.case_id for c in select_cases(cases, visibility=["none"])] == ["a", "c"]
    assert [c.case_id for c in select_cases(cases, "dev", ["none", "partial"])] == ["a"]


def test_query_hides_gold_and_ecosystem() -> None:
    query = Query.from_case(make_case())
    assert set(vars(query)) == {"case_id", "prompt", "cutoff"}


def test_repo_key_is_case_insensitive() -> None:
    assert repo_key(" PaddlePaddle/Paddle ") == repo_key("paddlepaddle/paddle")
