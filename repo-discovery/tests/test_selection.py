from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from repo_discovery import selection
from repo_discovery.cases import Case, RepoAtCommit, write_cases
from repo_discovery.selection import AuditError, Cell, Costs, Row, Value

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "selection_table.py"
MANIFEST = {
    "ecosystems": {
        "alpha": {"split": "dev", "cases": 3},
        "beta": {"split": "dev", "cases": 1},
        "gamma": {"split": "test", "cases": 2},
    }
}


def case(case_id: str, ecosystem: str, split: str, gold: tuple[str, ...]) -> Case:
    return Case(
        case_id=case_id,
        source="synthetic",
        ecosystem=ecosystem,
        prompt="change something",
        gold=tuple(RepoAtCommit(g, "0" * 40) for g in gold),
        cutoff="2026-01-01T00:00:00+00:00",
        split=split,  # type: ignore[arg-type]
    )


CASES = [
    case("a1", "alpha", "dev", ("x/one",)),
    case("a2", "alpha", "dev", ("x/one", "x/two")),
    case("a3", "alpha", "dev", ("x/three",)),
    case("b1", "beta", "dev", ("y/one",)),
    case("g1", "gamma", "test", ("z/one",)),
    case("g2", "gamma", "test", ("z/two",)),
]
DEV = ["a1", "a2", "a3", "b1"]
TABLES = selection.CostTables(
    fetch={"L0": {100: [0, 0]}, "L3": {100: [700, 1.5]}, "L4": {100: [1800, 20.0]}},
    index={
        "bm25f": {"L0": {"build_ms": 0.2, "kb": 1.0}, "L4": {"build_ms": 10.0, "kb": 45.0}},
        "dense-cards": {"L0": {"build_ms": 1.0, "kb": 1.5}, "L4": {"build_ms": 40.0, "kb": 1.5}},
    },
)


@pytest.fixture
def split() -> selection.DevSplit:
    return selection.dev_split(CASES, MANIFEST)


def per_case(cr10: list[float], **extra: list[float]) -> list[dict]:
    rows = []
    for i, case_id in enumerate(DEV):
        rows.append(
            {"case_id": case_id, "CompleteRecall@10": cr10[i]} | {k: v[i] for k, v in extra.items()}
        )
    return rows


def entry(**kwargs: object) -> dict:
    base = {"method": "static", "shape": "bm25f", "level": "L4", "size": 100, "names": "real"}
    return base | kwargs


def test_dev_split_matches_manifest(split: selection.DevSplit) -> None:
    assert sorted(split.ids) == DEV
    assert "g1" in split.all_ids


def test_dev_split_refuses_a_changed_manifest() -> None:
    manifest = {"ecosystems": {**MANIFEST["ecosystems"], "beta": {"split": "test", "cases": 1}}}
    with pytest.raises(AuditError, match="split manifest"):
        selection.dev_split(CASES, manifest)


def test_dev_split_refuses_unlabelled_cases() -> None:
    unlabelled = [*CASES[:-1], case("g2", "gamma", None, ("z/two",))]  # type: ignore[arg-type]
    with pytest.raises(AuditError, match="no split"):
        selection.dev_split(unlabelled, MANIFEST)


@pytest.mark.parametrize(
    ("ids", "message"),
    [
        ([*DEV, "g1"], "test cases"),
        ([*DEV, "nope"], "unknown"),
        ([*DEV, "a1"], "repeats"),
        (DEV[:-1], "misses 1"),
    ],
)
def test_audit_refuses_anything_but_the_dev_cases(
    split: selection.DevSplit, ids: list[str], message: str
) -> None:
    with pytest.raises(AuditError, match=message):
        selection.audit_ids(ids, split, "file")


def test_audit_accepts_exactly_the_dev_cases(split: selection.DevSplit) -> None:
    selection.audit_ids(list(reversed(DEV)), split, "file")


def test_per_case_file_with_a_test_case_is_refused(split: selection.DevSplit) -> None:
    rows = [*per_case([1, 1, 0, 1]), {"case_id": "g1", "CompleteRecall@10": 1.0}]
    with pytest.raises(AuditError, match="test cases"):
        selection.build_row(entry(), split, TABLES, rows, "file")


def hidden_split(split: selection.DevSplit, tmp_path: Path, salt: str = "published"):
    anon = {real: f"h{i}" for i, real in enumerate(DEV)}
    gold = {"a1": ("o1/r1",), "a2": ("o1/r1", "o1/r2"), "a3": ("o1/r3",), "b1": ("o2/r1",)}
    by_id = {c.case_id: c for c in CASES}
    scrubbed = [case(anon[r], by_id[r].ecosystem, "dev", gold[r]) for r in DEV]
    write_cases(tmp_path / "scrubbed.jsonl", scrubbed)
    (tmp_path / "mapping.json").write_text(json.dumps({"cases": anon}), encoding="utf-8")
    loaded = selection.load_salt(split, tmp_path / "scrubbed.jsonl", tmp_path / "mapping.json")
    return selection.DevSplit(split.cases, split.all_ids, {salt: loaded}), anon


def test_hidden_ids_map_back_to_dev_ids(split: selection.DevSplit, tmp_path: Path) -> None:
    hidden, anon = hidden_split(split, tmp_path)
    rows = [{"case_id": anon[c], "CompleteRecall@10": 1.0} for c in DEV]
    row = selection.build_row(entry(names="hidden", salt="published"), hidden, TABLES, rows, "file")
    assert row.backing == "per-case"
    assert sorted(row.per_case) == DEV  # type: ignore[arg-type]


def test_hidden_predictions_score_against_the_scrubbed_gold(
    split: selection.DevSplit, tmp_path: Path
) -> None:
    hidden, anon = hidden_split(split, tmp_path)
    rankings = {"a1": ["o1/r1"], "a2": ["o1/r1"], "a3": ["o1/r3"], "b1": ["o9/r9"]}
    rows = [
        {
            "case_id": anon[c],
            "ranker": "bm25f",
            "ranking": rankings[c],
            "universe": "u",
            "universe_size": 100,
            "seconds": 0.5,
        }
        for c in DEV
    ]
    row = selection.build_row(
        entry(names="hidden", salt="published", cr10=0.5), hidden, TABLES, rows, "file"
    )
    assert row.backing == "predictions"
    assert row.quality["CompleteRecall@10"].mean == pytest.approx(0.5)
    assert row.checks == ("CompleteRecall@10 matches report",)
    assert row.costs.latency_s == pytest.approx(0.5)


def test_prediction_universe_size_is_checked(split: selection.DevSplit) -> None:
    rows = [
        {
            "case_id": c,
            "ranker": "r",
            "ranking": [],
            "universe": "u",
            "universe_size": 50,
            "seconds": 0,
        }
        for c in DEV
    ]
    with pytest.raises(AuditError, match="universe size"):
        selection.build_row(entry(), split, TABLES, rows, "file")


def test_hidden_build_must_map_onto_the_dev_cases(
    split: selection.DevSplit, tmp_path: Path
) -> None:
    anon = {real: f"h{i}" for i, real in enumerate(DEV[:-1])} | {"g1": "h9"}
    by_id = {c.case_id: c for c in CASES}
    write_cases(
        tmp_path / "s.jsonl",
        [case(a, by_id[r].ecosystem, "dev", ("o/r",)) for r, a in anon.items()],
    )
    (tmp_path / "m.json").write_text(json.dumps({"cases": anon}), encoding="utf-8")
    with pytest.raises(AuditError, match="test cases"):
        selection.load_salt(split, tmp_path / "s.jsonl", tmp_path / "m.json")


def test_mismatch_with_the_report_is_flagged(split: selection.DevSplit) -> None:
    row = selection.build_row(entry(cr10=[0.9, 0.8, 1.0]), split, TABLES, per_case([1, 1, 0, 1]))
    assert row.quality["CompleteRecall@10"].mean == pytest.approx(0.75)
    assert row.checks[0].startswith("MISMATCH CompleteRecall@10")


def test_report_backed_row_keeps_its_interval(split: selection.DevSplit) -> None:
    row = selection.build_row(entry(cr10=[0.51, 0.31, 0.74], cr50=0.82), split, TABLES)
    assert row.backing == "report"
    assert row.quality["CompleteRecall@10"] == Value(0.51, 0.31, 0.74)
    assert row.quality["CompleteRecall@50"] == Value(0.82)


def test_static_costs_come_from_the_tables(split: selection.DevSplit) -> None:
    row = selection.build_row(entry(shape="hybrid-rrf", cr10=0.5), split, TABLES)
    assert row.costs.fetch_requests == 1800
    assert row.costs.build_s == pytest.approx((10.0 + 40.0) * 100 / 1000)
    assert row.costs.index_mb == pytest.approx((45.0 + 1.5) * 100 / 1000)
    assert (row.costs.usd, row.costs.query_requests) == (0.0, 0.0)


def test_unknown_index_leaves_build_unknown(split: selection.DevSplit) -> None:
    row = selection.build_row(entry(level="L3", cr10=0.5), split, TABLES)
    assert row.costs.build_s is None
    assert "build_s" in row.costs.unknown()


def test_l5_pays_opens_per_query_without_the_listing(split: selection.DevSplit) -> None:
    row = selection.build_row(
        entry(
            method="l5",
            shape="none",
            level="L0",
            budget=20,
            opens="L4",
            cr10=0.95,
            usd=0.19,
            query_fetch=[983, 12.0],
            query_fetch_includes_listing=True,
        ),
        split,
        TABLES,
    )
    assert (row.costs.fetch_requests, row.costs.build_s) == (0, 0.0)
    assert row.costs.query_requests == 983 - 1
    assert row.costs.query_mb == 12.0


def test_per_case_costs_override_reported_ones(split: selection.DevSplit) -> None:
    rows = per_case([1, 1, 1, 1], usd=[0.1, 0.2, 0.3, 0.4], seconds=[10, 20, 30, 40])
    row = selection.build_row(entry(method="c1", shape="none", usd=0.9), split, TABLES, rows)
    assert row.costs.usd == pytest.approx(0.25)
    assert row.costs.latency_s == pytest.approx(25)
    assert row.costs.build_s == 0.0


def test_index_parts() -> None:
    def cell(**kw: object) -> Cell:
        return Cell.from_dict(entry(**kw))

    assert selection.index_parts(cell(method="c1", shape="none")) == ()
    assert selection.index_parts(cell(method="c2", shape="hybrid-rrf")) == (
        ("bm25f", "L4"),
        ("dense-cards", "L4"),
    )
    assert selection.index_parts(
        cell(method="l5+search", shape="none", budget=20, tool="search_repos:bm25f@L4")
    ) == (("bm25f", "L4"),)
    assert selection.index_parts(
        cell(method="static-open", shape="bm25f", level="L0", budget=20)
    ) == (("bm25f", "L0"),)
    assert selection.index_parts(cell(shape="hybrid-rrf-dense-L2", level="L3+docs")) == (
        ("bm25f", "L3+docs"),
        ("dense-cards", "L2"),
    )


def test_cell_rules() -> None:
    with pytest.raises(ValueError, match="salt"):
        Cell.from_dict(entry(names="hidden"))
    with pytest.raises(ValueError, match="no salt"):
        Cell.from_dict(entry(salt="published"))
    with pytest.raises(ValueError, match="budget"):
        Cell.from_dict(entry(method="l5"))


def row(name: str, q: float, usd: float | None = 0.0, mb: float = 1.0, **cell: object) -> Row:
    c = Cell.from_dict(entry(shape=name) | cell)
    costs = Costs(
        fetch_requests=1.0,
        fetch_mb=mb,
        build_s=1.0,
        build_usd=0.0,
        index_mb=1.0,
        query_requests=0.0,
        query_mb=0.0,
        usd=usd,
    )
    return Row(c, {"CompleteRecall@10": Value(q)}, "report", costs, n_cases=61)


def test_dominance() -> None:
    cheap_good = row("a", 0.8, mb=1.0)
    dear_worse = row("b", 0.7, mb=2.0)
    same = row("c", 0.8, mb=1.0)
    assert selection.dominates(cheap_good, dear_worse)
    assert not selection.dominates(dear_worse, cheap_good)
    assert not selection.dominates(cheap_good, same)
    assert not selection.dominates(row("d", 0.9, usd=None), dear_worse)
    assert not selection.dominates(cheap_good, row("e", 0.5, usd=None))


def test_small_cost_differences_dont_decide_dominance() -> None:
    weak = row("a", 0.5, mb=1.0)
    strong = row("b", 0.8, mb=1.05)
    assert not selection.dominates(strong, weak)
    assert selection.dominates(strong, weak, tolerance=0.1)
    assert not selection.dominates(weak, row("c", 0.5, mb=1.05), tolerance=0.1)
    standings = selection.frontier([weak, strong])[(100, "real", None)]
    assert [s.row.cell.shape for s in standings if s.on_frontier] == ["b"]


def test_frontier_marks_near_ties_and_gaps() -> None:
    a = row("a", 0.80, mb=1.0)
    b = row("b", 0.79, mb=2.0)
    c = row("c", 0.90, mb=3.0)
    d = row("d", 0.85, mb=0.5)
    standings = selection.frontier([a, b, c, d])[(100, "real", None)]
    by_name = {s.row.cell.shape: s for s in standings}
    assert [s.row.cell.shape for s in standings if s.on_frontier] == ["c", "d"]
    assert by_name["b"].closest() is a
    assert by_name["d"].near == ()
    assert by_name["c"].near == ()
    near = selection.frontier([row("e", 0.80, mb=2.0), row("f", 0.79, mb=1.0)])
    e = next(s for s in near[(100, "real", None)] if s.row.cell.shape == "e")
    assert e.on_frontier
    assert [n.cell.shape for n in e.near] == ["f"]


def test_frontier_never_compares_across_salts() -> None:
    published = row("a", 0.9, names="hidden", salt="published")
    memory = row("b", 0.5, mb=5.0, names="hidden", salt="memory-check")
    real = row("c", 0.4, mb=9.0)
    groups = selection.frontier([published, memory, real])
    assert set(groups) == {
        (100, "real", None),
        (100, "hidden", "published"),
        (100, "hidden", "memory-check"),
    }
    assert all(s.on_frontier for members in groups.values() for s in members)


def test_pool_runs_averages_repeats(split: selection.DevSplit) -> None:
    first = selection.build_row(
        entry(method="c1", shape="none", usd=0.2), split, TABLES, per_case([1, 1, 0, 1])
    )
    second = selection.build_row(
        entry(method="c1", shape="none", usd=0.1, run=2), split, TABLES, per_case([1, 1, 1, 1])
    )
    (pooled,) = selection.pool_runs([first, second], split)
    assert pooled.runs == 2
    assert pooled.quality["CompleteRecall@10"].mean == pytest.approx(0.875)
    assert pooled.costs.usd == pytest.approx(0.15)


def test_expand_entries_by_size() -> None:
    entries = selection.expand_entries(
        [{"method": "static", "source": "s", "sizes": {50: {"cr10": 0.7}, 250: {"cr10": 0.6}}}]
    )
    assert entries == [
        {"method": "static", "source": "s", "cr10": 0.7, "size": 50},
        {"method": "static", "source": "s", "cr10": 0.6, "size": 250},
    ]
    (one,) = selection.expand_entries([{"predictions": "p/u{size}.jsonl", "sizes": {50: None}}])
    assert one == {"predictions": "p/u50.jsonl", "size": 50}


def test_collect_refuses_a_cell_given_twice(split: selection.DevSplit, tmp_path: Path) -> None:
    registry = {"fetch": {}, "cells": [entry(cr10=0.5), entry(cr10=0.6, source="other")]}
    with pytest.raises(ValueError, match="two registry entries"):
        selection.collect(registry, split, tmp_path)


def write_inbox(directory: Path, files: dict[str, list[dict]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, lines in files.items():
        text = "\n".join(json.dumps(x) for x in lines)
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def test_two_files_for_one_cell_are_compared_case_for_case(
    split: selection.DevSplit, tmp_path: Path
) -> None:
    registry = {"fetch": {}, "cells": [entry(cr10=0.5)]}
    same = write_inbox(
        tmp_path / "same",
        {f"{n}.jsonl": [{"cell": entry()}, *per_case([1, 1, 0, 1])] for n in ("one", "two")},
    )
    (row,) = selection.collect(registry, split, tmp_path, [same])
    assert row.data.endswith("one.jsonl")
    assert "one.jsonl and two.jsonl agree case for case" in row.checks

    other = write_inbox(tmp_path / "other", {"three.jsonl": [*per_case([1, 1, 1, 1])]})
    (other / "three.cell.json").write_text(json.dumps(entry()), encoding="utf-8")
    (row,) = selection.collect(registry, split, tmp_path, [same, other])
    assert any(c.startswith("MISMATCH per case: 1 of 4") for c in row.checks)


def test_agent_runs_manifest_describes_files_without_a_cell_line(
    split: selection.DevSplit, tmp_path: Path
) -> None:
    common = {"size": 100, "names": "real", "salt": None, "request_text": "real", "pass": 1}
    manifest = [
        {"file": "l4.jsonl", "method": "L4", "kind": "agent", "fetch_level": "L4", **common},
        {"file": "c2.jsonl", "method": "c2 bm25f", "kind": "agent", **common},
        {"file": "l5.jsonl", "method": "L5", "kind": "agent", "budget_n": 20, "open_level": "L4"}
        | common
        | {"pass": 2},
        {"file": "search.jsonl", "method": "L5+search", "kind": "agent", "budget_n": 20}
        | {"open_level": "L3", "index": "bm25f", **common},
        {"file": "open.jsonl", "method": "bm25f-open", "kind": "static", "budget_n": 5}
        | {"open_level": "L4", **common},
        {"file": "verbatim.jsonl", "method": "L4", "kind": "agent", "fetch_level": "L4"}
        | common
        | {"names": "hidden", "salt": "memory-check", "request_text": "verbatim"},
    ]
    files = {m["file"]: per_case([1, 1, 1, 1]) for m in manifest}
    inbox = write_inbox(tmp_path / "agent-runs", {**files, "waiting.jsonl": per_case([1] * 4)})
    (inbox / "manifest.jsonl").write_text(
        "\n".join(json.dumps(m) for m in manifest), encoding="utf-8"
    )
    cells = {p.name: Cell.from_dict(c) for c, _, p in selection.inbox_entries([inbox])}
    assert cells["l4.jsonl"] == Cell("c1", "none", "L4", 100, "real")
    assert cells["c2.jsonl"] == Cell("c2", "bm25f", "L4", 100, "real")
    assert cells["l5.jsonl"] == Cell(
        "l5", "none", "L0", 100, "real", budget=20, opens="L4", run="2"
    )
    assert cells["search.jsonl"].tool == "search_repos:bm25f@L4"
    assert cells["search.jsonl"].level == "L4"
    assert cells["open.jsonl"] == Cell("static-open", "bm25f", "L0", 100, "real", None, 5, "L4")
    assert cells["verbatim.jsonl"].variant == "verbatim request"
    assert cells["verbatim.jsonl"].label() == "agent · L4 packets [verbatim request]"
    assert selection.undescribed([inbox]) == [inbox / "waiting.jsonl"]


def test_opening_none_of_the_top_n_is_the_names_only_cell() -> None:
    cell = Cell.from_dict(entry(method="static-open", level="L0", budget=0, opens="L4"))
    assert cell == Cell("static", "bm25f", "L0", 100, "real")


def test_latency_is_run_time_less_startup_without_paused_runs(split: selection.DevSplit) -> None:
    rows = per_case([1, 1, 1, 1], wall_s=[50, 60, 70, 900], startup_s=[10] * 4)
    rows[3]["paused"] = True
    built = selection.build_row(entry(method="c1", shape="none"), split, TABLES, rows)
    assert built.costs.latency_s == 50


def test_script_end_to_end(tmp_path: Path) -> None:
    write_cases(tmp_path / "cases.jsonl", CASES)
    (tmp_path / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    registry = {
        "fetch": {"L0": {100: [0, 0]}, "L4": {100: [1800, 20.0]}},
        "index": {"bm25f": {"L4": {"build_ms": 10.0, "kb": 45.0}}},
        "cells": [
            entry(source="report A", sizes={100: {"cr10": [0.5, 0.2, 0.8], "cr50": 0.8}}),
            entry(method="c1", shape="none", source="report B", cr10=0.95, usd=0.19, tokens=7e5),
            entry(method="closed-book", shape="none", level="L0", source="C", cr10=0.9, usd=0.06),
        ],
    }
    (tmp_path / "cells.yaml").write_text(yaml.safe_dump(registry), encoding="utf-8")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    lines = [{"cell": entry(method="c1", shape="none", commit="abc1234", source="pass")}]
    lines += per_case([1, 1, 1, 0], usd=[0.2] * 4)
    (inbox / "c1.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("selection_table", SCRIPT)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    out = tmp_path / "out"
    argv = [
        "--registry", str(tmp_path / "cells.yaml"),
        "--cases", str(tmp_path / "cases.jsonl"),
        "--manifest", str(tmp_path / "manifest.json"),
        "--inbox", str(inbox),
        "--out", str(out),
    ]  # fmt: skip
    assert module.main(argv) == 0

    records = json.loads((out / "cells.json").read_text(encoding="utf-8"))
    by_label = {r["label"]: r for r in records}
    assert by_label["agent · L4 packets"]["backing"] == "per-case"
    assert by_label["agent · L4 packets"]["cr10"] == 0.75
    assert by_label["agent · L4 packets"]["checks"].startswith("MISMATCH")
    assert by_label["bm25f · L4"]["backing"] == "report"
    frontier = (out / "frontier.md").read_text(encoding="utf-8")
    assert "### U100, real names" in frontier
    assert (out / "charts" / "u100-real.svg").read_text(encoding="utf-8").startswith("<svg")
    assert "mismatches" not in (out / "audit.txt").read_text(encoding="utf-8")
