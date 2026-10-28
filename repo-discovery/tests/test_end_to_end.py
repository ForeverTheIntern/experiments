"""WideSWE-shaped cases -> tiny fake universes -> baselines -> predictions -> metrics."""

from __future__ import annotations

import json
from pathlib import Path

from repo_discovery import harness, metrics
from repo_discovery.cases import Case
from repo_discovery.cli import main
from repo_discovery.predictions import read_predictions, write_predictions
from repo_discovery.rankers import make_ranker


def test_baselines_end_to_end(tmp_path: Path, mini_cases: list[Case], tiny_universes: Path) -> None:
    for name in ("random", "name-match"):
        write_predictions(
            tmp_path / f"{name}.jsonl",
            harness.run(make_ranker(name, seed=1), mini_cases, tiny_universes),
        )
    predictions = [
        p for name in ("random", "name-match") for p in read_predictions(tmp_path / f"{name}.jsonl")
    ]
    assert len(predictions) == 6
    assert all(len(p.ranking) == harness.DEFAULT_DEPTH for p in predictions)

    reports = {r.ranker: r for r in metrics.evaluate(mini_cases, predictions, n_boot=200)}
    name_match = reports["name-match"].metrics
    assert (
        name_match["CompleteRecall@10"].mean >= reports["random"].metrics["CompleteRecall@10"].mean
    )

    by_case = {p.case_id: p for p in predictions if p.ranker == "name-match"}
    complete = next(c for c in mini_cases if c.scope_visibility == "complete")
    assert set(by_case[complete.case_id].ranking[:3]) == set(complete.gold_repos)
    partial = next(c for c in mini_cases if c.scope_visibility == "partial")
    assert by_case[partial.case_id].ranking[0] == "acme/portal"


def test_cli_end_to_end(tmp_path: Path, wideswe_mini: Path, tiny_universes: Path, capsys) -> None:
    cases = tmp_path / "cases.jsonl"
    manifest = tmp_path / "split.json"
    assert (
        main(
            [
                "build-cases",
                "--wideswe-dir",
                str(wideswe_mini),
                "--out",
                str(cases),
                "--manifest",
                str(manifest),
            ]
        )
        == 0
    )
    assert "complete" in capsys.readouterr().out
    assert set(json.loads(manifest.read_text())["ecosystems"]) == {"acme", "globex"}

    outputs = []
    for ranker in ("random", "name-match"):
        out = tmp_path / f"{ranker}.jsonl"
        args = ["run", "--cases", str(cases), "--split", "all", "--universes", str(tiny_universes)]
        assert main([*args, "--ranker", ranker, "--out", str(out)]) == 0
        outputs.append(str(out))

    report = tmp_path / "metrics.json"
    args = ["evaluate", "--cases", str(cases), "--split", "all", "--n-boot", "100"]
    assert main([*args, "--predictions", *outputs, "--out", str(report)]) == 0
    assert "CompleteRecall@20" in capsys.readouterr().out
    data = json.loads(report.read_text())
    assert [r["ranker"] for r in data] == ["name-match", "random"]
    assert all(r["n_cases"] == 3 for r in data)

    assert main(["describe", "--cases", str(cases)]) == 0
    assert "total" in capsys.readouterr().out
