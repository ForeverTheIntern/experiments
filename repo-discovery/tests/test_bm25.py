from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from repo_discovery import harness
from repo_discovery.cases import Case, Query
from repo_discovery.cli import main
from repo_discovery.contract import Universe
from repo_discovery.evidence import read_evidence
from repo_discovery.rankers import bm25, make_ranker
from repo_discovery.rankers.bm25 import BM25Ranker, CardBM25Ranker, KeywordIndex, analyze

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"
CUTOFF = "2026-03-01T00:00:00+00:00"


def test_analyze() -> None:
    text = "The GlobexCore --dry-run flag, v2 and 42 x"
    assert analyze(text) == ["globex", "core", "dry", "run", "flag", "v2"]


def test_evidence_fields() -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    candidate = next(c for c in view.candidates if c.repo == "acme/billing")
    fields = bm25.evidence_fields(read_evidence(candidate.repo, candidate.evidence_dir))
    assert set(fields) == set(bm25.FIELDS)
    assert fields["name"] == "acme billing"
    assert "shields" not in fields["readme"] and "invoices" in fields["readme"]
    assert "github" in fields["paths"]
    assert {"list", "invoices", "create", "refund"} <= set(fields["identifiers"].split())


@pytest.mark.parametrize("ranker", ["bm25f", "bm25-cards"])
def test_gold_ranks_first_on_the_fixture(ranker: str, mini_cases: list[Case]) -> None:
    repos = sorted(c.repo for c in Universe.load(EVIDENCE_UNIVERSE).view().candidates)
    for prediction in harness.run(make_ranker(ranker), mini_cases, EVIDENCE_UNIVERSE):
        case = next(c for c in mini_cases if c.case_id == prediction.case_id)
        top = prediction.ranking[: len(case.gold_repos)]
        assert set(top) == set(case.gold_repos), (ranker, case.case_id, prediction.ranking)
        assert sorted(prediction.ranking) == repos


def test_unmatched_repositories_follow_in_seeded_order() -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    query = Query("c1", "Vaccine lots expire.", CUTOFF)
    first = BM25Ranker(seed=3).rank(query, view)
    assert first[0] == "umbrella/vaccine-tracker"
    assert sorted(first) == sorted(c.repo for c in view.candidates)
    assert BM25Ranker(seed=3).rank(query, view) == first
    assert BM25Ranker(seed=4).rank(query, view)[1:] != first[1:]
    nothing = Query("c1", "the and of 42", CUTOFF)
    assert BM25Ranker().search(nothing, view) == []
    assert sorted(BM25Ranker().rank(nothing, view)) == sorted(first)


def test_fts5_syntax_in_prompts_is_literal() -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    prompt = 'Fix "ledger" (NEAR refund*) AND col:value OR NOT ^tax - {x} [y]'
    for ranker in (BM25Ranker(), CardBM25Ranker()):
        assert ranker.rank(Query("c1", prompt, CUTOFF), view)[0] == "acme/ledger"


def filler(n: int) -> list[tuple[str, dict[str, str]]]:
    return [(f"filler/{i}", {"a": "gamma", "b": "delta"}) for i in range(n)]


def test_field_weights_decide_the_order() -> None:
    rows = [("r/1", {"a": "alpha", "b": "beta beta"}), ("r/2", {"a": "beta", "b": "alpha alpha"})]
    index = KeywordIndex(["a", "b"], rows + filler(3))
    assert [r for r, _ in index.search(["alpha"], {"a": 10, "b": 1})] == ["r/1", "r/2"]
    assert [r for r, _ in index.search(["alpha"], {"a": 1, "b": 10})] == ["r/2", "r/1"]
    scores = [s for _, s in index.search(["alpha", "alpha", "beta"], {"a": 1, "b": 1})]
    assert len(scores) == 2 and all(s > 0 for s in scores)


def write_packets(root: Path, packets: dict[str, dict[str, str]]) -> Universe:
    lines = []
    for i, (repo, files) in enumerate(packets.items()):
        commit = f"{i:040x}"
        entry = {"repo": repo, "commit": commit, "snapshot_date": "2026-01-31", "role": "ecosystem"}
        lines.append(json.dumps(entry))
        for rel, text in files.items():
            path = root / "repos" / repo.replace("/", "__") / commit / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    (root / "universe.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Universe.load(root)


def test_ranker_weights_option(tmp_path: Path) -> None:
    packets = {
        "a/readme-side": {"README.md": "Collects telemetry from devices.", "paths.txt": "x.py"},
        "b/paths-side": {"README.md": "Device agent.", "paths.txt": "telemetry/x.py\ny.py"},
        **{f"c/filler{i}": {"README.md": "Unrelated words."} for i in range(4)},
    }
    view = write_packets(tmp_path, packets).view()
    query = Query("c1", "telemetry", CUTOFF)
    readme_first = BM25Ranker(weights={"readme": 10, "paths": 0.1}).rank(query, view)
    paths_first = BM25Ranker(weights={"readme": 0.1, "paths": 10}).rank(query, view)
    assert readme_first[:2] == ["a/readme-side", "b/paths-side"]
    assert paths_first[:2] == ["b/paths-side", "a/readme-side"]


def test_bad_fields_and_weights_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        BM25Ranker(weights={"title": 1.0})
    for fields in (["rank"], ["a b"], ["repository"], []):
        with pytest.raises(ValueError, match="plain identifiers"):
            KeywordIndex(fields, [])


def test_index_and_documents_are_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []

    def counting(repo: str, packet: Path):
        reads.append(repo)
        return read_evidence(repo, packet)

    monkeypatch.setattr(bm25, "read_evidence", counting)
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    ranker = CardBM25Ranker()
    query = Query("c1", "ledger", CUTOFF)
    first = ranker.rank(query, view)
    index = ranker.index(view)
    assert ranker.rank(query, view) == first
    assert ranker.index(view) is index
    assert len(reads) == len(view)

    copy = Universe.load(shutil.copytree(EVIDENCE_UNIVERSE, tmp_path / "copy")).view()
    assert ranker.rank(query, copy) == first
    assert ranker.index(copy) is not index
    assert len(reads) == len(view)


def test_saved_index_ranks_alike_and_refuses_other_candidates(tmp_path: Path) -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    built = BM25Ranker()
    digest = bm25.candidate_digest(view)
    path = tmp_path / "c1.sqlite"
    built.index(view).save(path, digest)
    assert path.is_file() and not (tmp_path / "c1.sqlite.partial").exists()

    loaded = BM25Ranker()
    loaded.adopt(view, KeywordIndex.open(path, loaded.fields, digest))
    for prompt in ("ledger journal", "billing invoices refunds", "Vaccine lots expire."):
        query = Query("c1", prompt, CUTOFF)
        assert loaded.search(query, view) == built.search(query, view)
        assert loaded.rank(query, view) == built.rank(query, view)

    copy = Universe.load(shutil.copytree(EVIDENCE_UNIVERSE, tmp_path / "copy")).view()
    assert bm25.candidate_digest(copy) != digest
    with pytest.raises(ValueError, match="other fields or candidates"):
        KeywordIndex.open(path, loaded.fields, bm25.candidate_digest(copy))
    with pytest.raises(ValueError, match="other fields or candidates"):
        KeywordIndex.open(path, ("card",), digest)
    with pytest.raises(ValueError, match="fields"):
        CardBM25Ranker().adopt(view, KeywordIndex.open(path, loaded.fields, digest))


def test_cli_runs_both_variants(tmp_path: Path, wideswe_mini: Path, capsys) -> None:
    cases = tmp_path / "cases.jsonl"
    build = ["build-cases", "--wideswe-dir", str(wideswe_mini), "--out", str(cases)]
    assert main([*build, "--manifest", str(tmp_path / "split.json")]) == 0

    outputs = []
    for ranker in ("bm25f", "bm25-cards"):
        out = tmp_path / f"{ranker}.jsonl"
        args = ["run", "--cases", str(cases), "--split", "all"]
        args += ["--universes", str(EVIDENCE_UNIVERSE), "--ranker", ranker, "--out", str(out)]
        assert main(args) == 0
        outputs.append(str(out))

    report = tmp_path / "metrics.json"
    args = ["evaluate", "--cases", str(cases), "--split", "all", "--n-boot", "100"]
    assert main([*args, "--predictions", *outputs, "--out", str(report)]) == 0
    data = json.loads(report.read_text())
    assert [r["ranker"] for r in data] == ["bm25-cards", "bm25f"]
    assert all(r["median_last_required_rank"] == 2.0 for r in data)

    out = tmp_path / "cards.jsonl"
    args = ["cards", "--universe", str(EVIDENCE_UNIVERSE), "--budget", "40", "--out", str(out)]
    assert main(args) == 0
    assert "wrote 10 cards (40-word budget)" in capsys.readouterr().out
    written = [json.loads(line) for line in out.read_text().splitlines()]
    assert [c["repo"] for c in written][:2] == ["acme/billing", "acme/ledger"]
    assert all(0 < c["words"] <= 40 for c in written)
