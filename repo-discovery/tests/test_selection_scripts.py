"""The selection step's scripts (scripts/selection/) on a toy run layout: the hidden-seed
rewrite, the per-case cell export, and the hidden-seed case-cost driver."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import statistics
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from repo_discovery.cases import Case, RepoAtCommit, read_cases, select_cases, write_cases
from repo_discovery.contract import evidence_dir
from repo_discovery.fetch_levels import case_costs
from repo_discovery.predictions import Prediction, read_predictions, write_predictions

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "selection"
SIZES = (250, 1000, 10000)
SPLIT = {"d1": "dev", "d2": "dev", "t1": "test"}
GOLD = {"d1": ("alpha/one",), "d2": ("beta/two", "beta/three"), "t1": ("gamma/four",)}
NOISE = tuple(f"noise{i:02d}/repo{i:02d}" for i in range(12))
# d1's repository comes first; d2's two come first and thirteenth.
RANKING = {
    "d1": ("alpha/one", *NOISE),
    "d2": ("beta/two", *NOISE[:11], "beta/three", NOISE[11]),
    "t1": ("gamma/four", *NOISE),
}
ANON_REPO = {
    r: f"o{i:02d}/r{i:02d}"
    for i, r in enumerate(sorted({*NOISE, *(r for gold in GOLD.values() for r in gold)}))
}
ANON_CASE = {"d1": "c-0a", "d2": "c-0b", "t1": "c-0c"}
DEV = ("d1", "d2")


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"selection_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(repo: str) -> str:
    return hashlib.sha1(repo.encode()).hexdigest()


def case(case_id: str, anon: bool = False) -> Case:
    name = ANON_REPO.get if anon else str
    return Case(
        case_id=ANON_CASE[case_id] if anon else case_id,
        source="toy",
        ecosystem="toy",
        prompt=f"change {case_id}",
        gold=tuple(RepoAtCommit(name(r), sha(r)) for r in GOLD[case_id]),
        cutoff="2026-02-01T00:00:00+00:00",
        split=SPLIT[case_id],
    )


def write_universe(root: Path, case_id: str) -> None:
    repos = [*GOLD[case_id], *NOISE]
    root.mkdir(parents=True)
    with (root / "universe.jsonl").open("w") as handle:
        for repo in repos:
            role = "gold" if repo in GOLD[case_id] else "distractor"
            entry = {"repo": repo, "commit": sha(repo), "snapshot_date": "2026-01-31", "role": role}
            handle.write(json.dumps(entry) + "\n")
    for i, repo in enumerate(repos):
        packet = evidence_dir(root, repo, sha(repo))
        packet.mkdir(parents=True)
        (packet / "README.md").write_text("x" * 100 * (i + 1))
        (packet / "paths.txt").write_text("README.md\n")


def preds(ranker: str, size: int, ids=DEV, anon: bool = False) -> Iterator[Prediction]:
    for c in ids:
        yield Prediction(
            case_id=ANON_CASE[c] if anon else c,
            ranker=ranker,
            ranking=tuple(ANON_REPO[r] for r in RANKING[c]) if anon else RANKING[c],
            universe=f"u{size}/{c}",
            universe_size=len(RANKING[c]),
            seconds=0.1,
        )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    write_cases(root / "rd-cases.jsonl", [case(c) for c in SPLIT])
    for size in SIZES:
        for c in SPLIT:
            write_universe(root / f"rd-corpus/universes/monthly/u{size}/{c}", c)
    mapping = json.dumps({"repos": ANON_REPO, "owners": {}, "cases": ANON_CASE})
    for build in ("u1000", "u10000"):
        (root / f"rd-anon/{build}/private").mkdir(parents=True)
        (root / f"rd-anon/{build}/private/mapping.json").write_text(mapping)
    write_cases(root / "rd-anon/u1000/cases-scrubbed.jsonl", [case(c, anon=True) for c in SPLIT])

    first = root / "rd-fl/preds"
    for ranker in ("bm25f", "bm25f-open0", "bm25f-open2"):
        write_predictions(first / f"{ranker}--L4--u250.jsonl", preds(ranker, 250))
    write_predictions(root / "rd-fu/preds/real/bm25f--L4--u10000.jsonl", preds("bm25f", 10000))
    hidden = root / "rd-fu/preds/hidden"
    write_predictions(hidden / "bm25f--L4--u1000.jsonl", preds("bm25f", 1000, anon=True))
    for size in SIZES:
        for seed in ("bm25f", "hybrid"):
            ranker = f"{seed}-open0"
            write_predictions(
                hidden / f"{ranker}--L4--u{size}.jsonl", preds(ranker, size, anon=True)
            )
    write_predictions(
        hidden / "hybrid-open2--L4--u10000.jsonl", preds("hybrid-open2", 10000, anon=True)
    )
    return root


def dev_costs(root: Path, size: int, budget: int):
    dev = select_cases(read_cases(root / "rd-cases.jsonl"), "dev")
    opened = {c: RANKING[c][:budget] for c in DEV}
    return case_costs(dev, root / f"rd-corpus/universes/monthly/u{size}", opened)


def test_hidden_seeds_restore_real_ids(root: Path) -> None:
    assert load("hidden_seeds").main(["--root", str(root)]) == 0
    for size in SIZES:
        for seed in ("bm25f", "hybrid"):
            got = read_predictions(root / f"rd-fu/out/hidden-seeds/{seed}-open0--u{size}.jsonl")
            assert [p.case_id for p in got] == list(DEV)
            assert [p.ranking for p in got] == [RANKING[c] for c in DEV]
            assert got[0].universe == str(root / f"rd-corpus/universes/monthly/u{size}/d1")


def test_hidden_seeds_refuse_a_test_case(root: Path) -> None:
    write_predictions(
        root / "rd-fu/preds/hidden/bm25f-open0--L4--u250.jsonl",
        preds("bm25f-open0", 250, ids=(*DEV, "t1"), anon=True),
    )
    with pytest.raises(SystemExit, match="outside the dev split"):
        load("hidden_seeds").main(["--root", str(root)])
    assert not list((root / "rd-fu/out/hidden-seeds").iterdir())


@pytest.fixture
def export(monkeypatch: pytest.MonkeyPatch):
    module = load("export_selection")
    # Real follow-up runs started before the checkout switch, hidden ones after it.
    late = {"real": -1, "hidden": 1}
    monkeypatch.setattr(module, "birth_time", lambda path: module.SWITCH + late[path.parent.name])
    return module


def test_export_writes_one_file_per_cell(root: Path, export, tmp_path: Path) -> None:
    load("hidden_seeds").main(["--root", str(root)])
    out = tmp_path / "inbox"
    assert export.main([str(out), "--root", str(root)]) == 0

    files = {
        p.name: [json.loads(line) for line in p.read_text().splitlines()]
        for p in out.glob("*.jsonl")
    }
    assert len(files) == 12
    assert len((out / "cells.txt").read_text().splitlines()) == 12
    for lines in files.values():
        assert [row["case_id"] for row in lines[1:]] == list(DEV)
    scores = [
        {"case_id": "d1", "CompleteRecall@10": 1.0, "CompleteRecall@50": 1.0},
        {"case_id": "d2", "CompleteRecall@10": 0.0, "CompleteRecall@50": 1.0},
    ]

    static = files["real--bm25f--L4--u250.jsonl"]
    assert static[1:] == scores
    cell = static[0]["cell"]
    assert (cell["method"], cell["shape"], cell["level"], cell["budget"]) == (
        "static",
        "bm25f",
        "L4",
        None,
    )
    assert (cell["size"], cell["names"], cell["salt"], cell["commit"]) == (
        250,
        "real",
        None,
        "2bbc984",
    )
    assert "rd-fl/preds/bm25f--L4--u250.jsonl" in cell["source"]
    assert str(root) not in cell["source"]
    assert files["real--bm25f--L4--u10000.jsonl"][0]["cell"]["commit"] == "81bf192"

    hidden = files["hidden--bm25f--L4--u1000.jsonl"]
    assert hidden[1:] == scores
    assert (hidden[0]["cell"]["names"], hidden[0]["cell"]["salt"]) == ("hidden", "published")
    assert hidden[0]["cell"]["commit"] == "51d11ec"

    open0 = files["real--bm25f-open0--L4--u250.jsonl"]
    assert (open0[0]["cell"]["method"], open0[0]["cell"]["budget"]) == ("static-open", 0)
    assert open0[0]["cell"]["opens"] is None
    assert "fetch_bytes" not in open0[1]

    for name, size, shape in (
        ("real--bm25f-open2--L4--u250.jsonl", 250, "bm25f"),
        ("hidden--hybrid-open2--L4--u10000.jsonl", 10000, "hybrid-rrf"),
    ):
        cell, *rows = files[name]
        assert (cell["cell"]["shape"], cell["cell"]["level"]) == (shape, "L0")
        assert (cell["cell"]["budget"], cell["cell"]["opens"]) == (2, "L4")
        expected = dev_costs(root, size, 2)
        assert [(r["fetch_requests"], r["fetch_bytes"]) for r in rows] == [
            (c.calls, c.bytes) for c in expected
        ]
        assert expected[0].bytes != expected[1].bytes


@pytest.mark.parametrize(
    ("cell", "ids", "anon", "match"),
    [
        ("rd-fl/preds/bm25f--L4--u250.jsonl", (*DEV, "t1"), False, "outside the dev split"),
        ("rd-fu/preds/hidden/bm25f--L4--u1000.jsonl", (*DEV, "t1"), True, "outside the dev"),
        ("rd-fl/preds/bm25f--L4--u250.jsonl", ("d1",), False, "1 lines for 1 of 2 dev cases"),
    ],
)
def test_export_refuses_a_cell_outside_the_dev_cases(
    root: Path, export, tmp_path: Path, cell: str, ids, anon: bool, match: str
) -> None:
    load("hidden_seeds").main(["--root", str(root)])
    write_predictions(root / cell, preds("bm25f", 250, ids=ids, anon=anon))
    with pytest.raises(SystemExit, match=match):
        export.main([str(tmp_path / "inbox"), "--root", str(root)])


def test_case_cost_hidden_seed_prices_the_real_packets(root: Path) -> None:
    load("hidden_seeds").main(["--root", str(root)])
    env = {**os.environ, "ROOT": str(root), "PYTHON": sys.executable}
    run = subprocess.run(
        ["bash", str(SCRIPTS / "case-cost-hidden-seed.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    errors = "".join(p.read_text() for p in (root / "rd-fu/out").glob("*.err"))
    assert run.returncode == 0, run.stdout + run.stderr + errors
    assert run.stdout.splitlines()[-1] == "CASE-COST-HIDDEN-DONE"
    for seed in ("bm25f", "hybrid"):
        rows = json.loads((root / f"rd-fu/out/case-cost-hidden-{seed}-seed.json").read_text())
        for size in SIZES:
            for budget in (5, 10, 20, 50):
                costs = dev_costs(root, size, budget)
                assert rows["rows"][f"L0, open top {budget} at L4"][str(size)] == {
                    "calls": statistics.fmean(c.calls for c in costs),
                    "bytes": statistics.fmean(c.bytes for c in costs),
                }


@pytest.mark.parametrize("script", sorted(SCRIPTS.glob("*.sh")), ids=lambda p: p.name)
def test_shell_scripts_parse(script: Path) -> None:
    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True)
