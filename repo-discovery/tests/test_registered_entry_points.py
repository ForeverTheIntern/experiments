"""`repo-discovery run`, `evaluate` and scripts/test_report.py refuse test cases until they
get a registration commit the checkout descends from."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from repo_discovery import split
from repo_discovery.cases import Case, write_cases
from repo_discovery.cli import main
from repo_discovery.predictions import read_predictions

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import test_report as report


@pytest.fixture
def split_cases(tmp_path: Path, mini_cases: list[Case]) -> Path:
    """The mini cases, with the last one moved to the test split."""
    *dev, held_out = mini_cases
    path = tmp_path / "cases.jsonl"
    write_cases(path, [*(replace(c, split="dev") for c in dev), replace(held_out, split="test")])
    return path


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Stands in for the git check, which needs the registration in local history (CI clones
    one commit deep), and records each commit it is asked about."""
    commits: list[str | None] = []

    def record(commit: str | None, repo: Path | None = None) -> None:
        commits.append(commit)

    monkeypatch.setattr(split, "check_registered", record)
    monkeypatch.setattr(report, "check_registered", record)
    return commits


def run(cases: Path, universes: Path, out: Path, *extra: str) -> int:
    args = ["run", "--cases", str(cases), "--universes", str(universes), "--ranker", "random"]
    return main([*args, "--out", str(out), *extra])


def evaluate(cases: Path, predictions: Path, *extra: str) -> int:
    args = ["evaluate", "--cases", str(cases), "--predictions", str(predictions)]
    return main([*args, "--n-boot", "50", *extra])


def test_cli_keeps_test_cases_behind_the_registration(
    tmp_path: Path, split_cases: Path, tiny_universes: Path
) -> None:
    dev = tmp_path / "dev.jsonl"
    assert run(split_cases, tiny_universes, dev) == 0
    assert len(read_predictions(dev)) == 2
    assert evaluate(split_cases, dev) == 0
    for chosen in ("test", "all"):
        out = tmp_path / f"{chosen}.jsonl"
        with pytest.raises(SystemExit, match="--registered"):
            run(split_cases, tiny_universes, out, "--split", chosen)
        assert not out.exists()
        with pytest.raises(SystemExit, match="--registered"):
            evaluate(split_cases, dev, "--split", chosen)
        with pytest.raises(SystemExit, match="not a commit"):
            evaluate(split_cases, dev, "--split", chosen, "--registered", "f" * 40)


def test_cli_passes_the_registration_to_the_check(
    tmp_path: Path, split_cases: Path, tiny_universes: Path, seen: list[str | None]
) -> None:
    out = tmp_path / "all.jsonl"
    assert run(split_cases, tiny_universes, out, "--split", "all", "--registered", "abc1234") == 0
    assert len(read_predictions(out)) == 3
    assert evaluate(split_cases, out, "--split", "test", "--registered", "abc1234") == 0
    assert seen == ["abc1234", "abc1234"]


def test_test_report_checks_the_registration_before_reading_anything(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.json")
    with pytest.raises(SystemExit, match="--registered"):
        report.main([missing, "--comparisons", missing])
    with pytest.raises(SystemExit, match="not a commit"):
        report.main([missing, "--comparisons", missing, "--registered", "f" * 40])


def test_test_report_runs_once_registered(
    tmp_path: Path, split_cases: Path, tiny_universes: Path, seen: list[str | None], capsys
) -> None:
    out = tmp_path / "test.jsonl"
    assert run(split_cases, tiny_universes, out, "--split", "test", "--registered", "a1") == 0
    cell = {"names": "real", "size": 50, "cases": str(split_cases)}
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({name: {**cell, "predictions": [str(out)]} for name in "ab"}))
    comparisons = tmp_path / "comparisons.json"
    comparisons.write_text(json.dumps([{"id": "H1", "a": "a", "b": "b", "family": "confirmatory"}]))
    assert report.main([str(cells), "--comparisons", str(comparisons), "--registered", "a1"]) == 0
    assert "| a | real | 50 | 1 |" in capsys.readouterr().out
    assert seen == ["a1", "a1"]
