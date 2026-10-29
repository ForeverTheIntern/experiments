from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from repo_discovery.cards import build_card
from repo_discovery.cases import Case, Query, RepoAtCommit
from repo_discovery.contract import Universe, UniverseView
from repo_discovery.evidence import Evidence, read_evidence
from repo_discovery.fetch_levels import (
    FULL,
    L3_PARTS,
    LEVELS,
    MARKER,
    case_costs,
    fetch_level,
    holds,
    kept_files,
    main,
    packet_cost,
    restrict,
)
from repo_discovery.packages import read_packages
from repo_discovery.rankers import make_ranker
from repo_discovery.rankers.bm25 import BM25Ranker, analyze
from repo_discovery.rankers.dense import (
    DenseCardRanker,
    DenseL2HybridRanker,
    HybridRanker,
    NamesDenseL2HybridRanker,
    reciprocal_rank_fusion,
)
from repo_discovery.rankers.open_top import HybridOpenTopRanker, NameBM25Ranker, OpenTopRanker

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"
BILLING = Path("repos") / "acme__billing" / ("a1" * 20)
NUCLEUS = Path("repos") / "hooli__nucleus" / ("2" * 40)
CASE_UNIVERSE = Path("universes") / "monthly" / "u9" / "case-1"
CUTOFF = "2026-03-01T00:00:00+00:00"
METADATA = {
    ("acme/billing", BILLING.name): {
        "repo": "acme/billing",
        "commit": BILLING.name,
        "description": "Invoices and refunds for Acme",
        "topics": ["payments", "ledger"],
        "language": "TypeScript",
        "source": {"description": "archive", "topics": "archive", "language": "live"},
    }
}


def restrict_level(corpus: Path, level: str, out: Path):
    return restrict(corpus, level, out, METADATA if level == "L1" else None)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """The evidence fixture laid out as a builder corpus with one per-case universe."""
    root = tmp_path / "corpus"
    shutil.copytree(EVIDENCE_UNIVERSE / "repos", root / "repos")
    sidecar = root / "meta" / "acme__billing" / f"{BILLING.name}.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(json.dumps({"evidence": [{"path": "docs/rounding.md", "kind": "manifest"}]}))
    universe = root / CASE_UNIVERSE
    universe.mkdir(parents=True)
    shutil.copyfile(EVIDENCE_UNIVERSE / "universe.jsonl", universe / "universe.jsonl")
    (universe / "repos").symlink_to("../../../../repos", target_is_directory=True)
    return root


def view(root: Path) -> UniverseView:
    return Universe.load(root / CASE_UNIVERSE).view()


def packet_in(universe: UniverseView, repo: str) -> Path:
    return next(c.evidence_dir for c in universe.candidates if c.repo == repo)


def test_levels_keep_nested_subsets(corpus: Path) -> None:
    billing = corpus / BILLING
    kept = {level: kept_files(billing, level) for level in LEVELS}
    assert kept["L0"] == kept["L1"] == []
    assert kept["L2"] == ["README.md"]
    assert kept["L3"] == ["README.md", "docs/rounding.md", "package.json"]
    assert set(kept["L4"]) == {
        "LICENSE",
        "README.md",
        "api/openapi.yaml",
        "docs/rounding.md",
        "package.json",
        "paths.txt",
    }
    assert kept["L3+paths"] == [*kept["L3"], "paths.txt"]
    assert kept["L3+docs"] == kept["L3"]
    assert kept["L3+contracts"] == sorted([*kept["L3"], "api/openapi.yaml"])
    portal = corpus / "repos" / "acme__portal" / ("c3" * 20)
    assert kept_files(portal, "L3+docs") == [
        "README.md",
        "docs/subscription-schedule.rst",
        "package.json",
    ]
    assert kept_files(corpus / NUCLEUS, "L2") == ["README.rst"]
    with pytest.raises(ValueError, match="fetch level"):
        kept_files(billing, "L5")


def test_holds_follows_what_each_level_keeps() -> None:
    assert all(holds(level, "L0") and holds(level, level) for level in LEVELS)
    assert holds("L4", "L3+docs") and holds("L3+docs", "L3") and holds("L3+paths", "L2")
    assert not holds("L3+docs", "L3+paths") and not holds("L3", "L3+contracts")
    assert not holds("L4", "L1") and not holds("L1", "L2") and not holds("L2", "L3")


def test_l3_parts_read_as_l4_does(corpus: Path, tmp_path: Path) -> None:
    spec = corpus / "repos" / "acme__ledger" / ("b2" * 20) / "api" / "service.yaml"
    spec.parent.mkdir()
    spec.write_text("openapi: 3.0.0\ninfo:\n  title: Ledger\n")
    path_list = corpus / BILLING / "paths.txt"
    listed = path_list.read_text()
    full = view(corpus)
    cut = {}
    for level in ("L3", *L3_PARTS):
        restrict(corpus, level, tmp_path / level)
        cut[level] = view(tmp_path / level)
    assert path_list.read_text() == listed
    for candidate in full.candidates:
        whole = read_evidence(candidate.repo, candidate.evidence_dir)
        at = {
            level: read_evidence(candidate.repo, packet_in(universe, candidate.repo))
            for level, universe in cut.items()
        }
        assert at["L3"] == replace(whole, docs=(), paths=(), contracts=(), other=())
        assert at["L3+paths"] == replace(at["L3"], paths=whole.paths)
        assert at["L3+docs"] == replace(at["L3"], docs=whole.docs)
        assert at["L3+contracts"] == replace(at["L3"], contracts=whole.contracts)
    ledger = read_evidence("acme/ledger", packet_in(cut["L3+contracts"], "acme/ledger"))
    assert {c.path for c in ledger.contracts} == {"api/service.yaml", "proto/ledger.proto"}


def test_a_restricted_universe_reads_only_its_level(corpus: Path, tmp_path: Path) -> None:
    full = packet_in(view(corpus), "acme/billing")
    assert fetch_level(full) == FULL
    evidence = {}
    for level in LEVELS:
        out = tmp_path / level
        stats = restrict_level(corpus, level, out)
        assert stats.packets == 10 and stats.universes == 1
        packet = packet_in(view(out), "acme/billing")
        assert fetch_level(packet) == level
        assert (out / "meta" / "acme__billing" / f"{BILLING.name}.json").is_file()
        evidence[level] = read_evidence("acme/billing", packet)
        portal = read_packages(packet_in(view(out), "acme/portal"))
        assert (portal.manifests > 0) == (level not in ("L0", "L1", "L2"))

    assert evidence["L0"] == Evidence("acme/billing")
    l1 = evidence["L1"]
    assert not (l1.readme or l1.docs or l1.paths or l1.contracts or l1.package_names)
    assert l1.descriptions == ("Invoices and refunds for Acme",)
    assert l1.keywords == ("payments", "ledger", "TypeScript")
    assert read_evidence("acme/portal", packet_in(view(tmp_path / "L1"), "acme/portal")) == (
        Evidence("acme/portal")
    )
    assert evidence["L2"].readme and not evidence["L2"].manifests and not evidence["L2"].paths
    assert evidence["L3"].readme == evidence["L4"].readme
    assert evidence["L3"].package_names == evidence["L4"].package_names
    assert not (evidence["L3"].docs or evidence["L3"].contracts or evidence["L3"].paths)
    assert evidence["L4"] == read_evidence("acme/billing", full)


def test_restrict_refuses_to_mix_levels(corpus: Path, tmp_path: Path) -> None:
    out = tmp_path / "L2"
    first = restrict(corpus, "L2", out)
    assert restrict(corpus, "L2", out) == first
    assert json.loads((out / MARKER).read_text())["level"] == "L2"
    with pytest.raises(ValueError, match="already holds"):
        restrict(corpus, "L3", out)
    with pytest.raises(ValueError, match="overwrite"):
        restrict(corpus, "L2", corpus)
    with pytest.raises(ValueError, match="metadata"):
        restrict(corpus, "L1", tmp_path / "L1")


def test_l1_metadata_reaches_bm25f_and_cards(corpus: Path, tmp_path: Path) -> None:
    restrict_level(corpus, "L1", tmp_path / "L1")
    packet = packet_in(view(tmp_path / "L1"), "acme/billing")
    stored = json.loads((packet / "github-metadata.json").read_text())
    assert stored == {k: METADATA["acme/billing", BILLING.name][k] for k in stored}
    assert set(stored) == {"description", "topics", "language"}
    evidence = read_evidence("acme/billing", packet)
    fields = BM25Ranker().document(evidence)
    assert {"invoices", "refunds", "payments", "type", "script"} <= set(fields["manifests"].split())
    card = build_card(evidence).text
    assert "Invoices and refunds for Acme" in card and "payments" in card


def test_packet_cost_counts_what_each_level_fetches(corpus: Path, tmp_path: Path) -> None:
    billing = corpus / BILLING
    size = {rel: (billing / rel).stat().st_size for rel in kept_files(billing, FULL)}
    costs = {}
    for level in ("L0", "L1", "L2", "L3", "L3+paths", "L3+contracts"):
        restrict_level(corpus, level, tmp_path / level)
        costs[level] = packet_cost(packet_in(view(tmp_path / level), "acme/billing"))
    costs[FULL] = packet_cost(corpus / BILLING)
    assert (costs["L0"].files, costs["L0"].bytes, costs["L0"].calls) == (0, 0, 0)
    metadata = packet_in(view(tmp_path / "L1"), "acme/billing") / "github-metadata.json"
    assert (costs["L1"].files, costs["L1"].bytes, costs["L1"].calls) == (
        1,
        metadata.stat().st_size,
        1,
    )
    portal = packet_cost(packet_in(view(tmp_path / "L1"), "acme/portal"))
    assert (portal.files, portal.calls) == (0, 1)
    assert (costs["L2"].files, costs["L2"].bytes, costs["L2"].calls) == (1, size["README.md"], 1)
    l3 = size["README.md"] + size["docs/rounding.md"] + size["package.json"]
    assert (costs["L3"].files, costs["L3"].bytes, costs["L3"].calls) == (3, l3, 4)
    paths = costs["L3+paths"]
    assert (paths.files, paths.bytes, paths.calls) == (4, l3 + size["paths.txt"], 4)
    contracts = costs["L3+contracts"]
    assert (contracts.files, contracts.bytes, contracts.calls) == (
        4,
        l3 + size["api/openapi.yaml"],
        5,
    )
    assert (costs[FULL].files, costs[FULL].bytes, costs[FULL].calls) == (
        len(size),
        sum(size.values()),
        len(size),
    )


def test_case_costs_sum_each_cases_packets(corpus: Path, tmp_path: Path) -> None:
    restrict_level(corpus, "L3", tmp_path / "L3")
    case = Case(
        case_id="case-1",
        source="test",
        ecosystem="acme",
        prompt="Round refunds the way invoices do",
        gold=(RepoAtCommit("acme/billing", BILLING.name),),
        cutoff=CUTOFF,
    )
    for root in (corpus, tmp_path / "L3"):
        universe = view(root)
        packets = [packet_cost(c.evidence_dir) for c in universe.candidates]
        [whole] = case_costs([case], root / CASE_UNIVERSE.parent)
        assert (whole.case_id, whole.calls, whole.bytes) == (
            "case-1",
            sum(p.calls for p in packets),
            sum(p.bytes for p in packets),
        )
        top = ["ACME/Billing", "hooli/nucleus"]
        [opened] = case_costs([case], root / CASE_UNIVERSE.parent, {"case-1": top})
        picked = [packet_cost(packet_in(universe, r)) for r in ("acme/billing", "hooli/nucleus")]
        assert (opened.calls, opened.bytes) == (
            sum(p.calls for p in picked),
            sum(p.bytes for p in picked),
        )
        assert 0 < opened.bytes < whole.bytes


class HashingEncoder:
    id = "test/hashing@0"

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(64, dtype=np.float32)
        for token in analyze(text):
            vector[sum(token.encode()) % 64] += 1.0
        return vector / max(float(np.linalg.norm(vector)), 1e-9)

    def documents(self, texts: Sequence[str]) -> np.ndarray:
        return np.stack([self._vector(t) for t in texts])

    def query(self, text: str) -> np.ndarray:
        return self._vector(text)


def test_dense_vectors_are_kept_per_level(corpus: Path, tmp_path: Path) -> None:
    restrict(corpus, "L0", tmp_path / "L0")
    dense = DenseCardRanker(encoder=HashingEncoder(), store=tmp_path / "v.sqlite")
    full, names = view(corpus), view(tmp_path / "L0")
    billing = next(c for c in full.candidates if c.repo == "acme/billing")
    assert dense._key(billing) == f"card/256/acme/billing/{BILLING.name}"
    vectors = {"L4": dense.vectors(full), "L0": dense.vectors(names)}
    assert dense.stats.embedded == 2 * len(full)
    assert not np.allclose(vectors["L4"], vectors["L0"])


@pytest.mark.parametrize("level", ["L0", "L2"])
def test_dense_held_at_a_level_matches_that_levels_corpus(
    corpus: Path, tmp_path: Path, level: str
) -> None:
    restrict(corpus, level, tmp_path / level)
    restrict(corpus, "L3+docs", tmp_path / "L3+docs")
    store = tmp_path / "v.sqlite"
    at_level = DenseCardRanker(encoder=HashingEncoder(), store=store)
    expected = at_level.vectors(view(tmp_path / level))
    for universe in (view(corpus), view(tmp_path / "L3+docs")):
        held = DenseCardRanker(encoder=HashingEncoder(), store=store, level=level)
        assert np.array_equal(held.vectors(universe), expected)
        assert held.stats.embedded == 0
    fresh = DenseCardRanker(encoder=HashingEncoder(), store=tmp_path / "w.sqlite", level=level)
    assert np.allclose(fresh.vectors(view(corpus)), expected)
    with pytest.raises(ValueError, match="held"):
        DenseCardRanker(level="L3")


def test_dense_held_at_l2_needs_packets_that_hold_l2(corpus: Path, tmp_path: Path) -> None:
    restrict(corpus, "L0", tmp_path / "L0")
    held = DenseCardRanker(encoder=HashingEncoder(), store=tmp_path / "v.sqlite", level="L2")
    with pytest.raises(ValueError, match="can't be read"):
        held.vectors(view(tmp_path / "L0"))


def test_mixed_level_hybrids(corpus: Path, tmp_path: Path) -> None:
    restrict(corpus, "L2", tmp_path / "L2")
    restrict(corpus, "L3+paths", tmp_path / "L3+paths")
    query = Query("c1", "acme refund invoices rounding ledger", CUTOFF)
    store = tmp_path / "v.sqlite"

    def dense(level: str | None = None) -> DenseCardRanker:
        return DenseCardRanker(encoder=HashingEncoder(), store=store, level=level)

    readme_cards = dense().rank(query, view(tmp_path / "L2"))
    names = [repo for repo, _ in NameBM25Ranker().search(query, view(corpus))]
    mixed = NamesDenseL2HybridRanker(dense=dense("L2"))
    assert mixed.rank(query, view(tmp_path / "L2")) == reciprocal_rank_fusion([names, readme_cards])
    assert mixed.rank(query, view(corpus)) == mixed.rank(query, view(tmp_path / "L2"))
    universe = view(tmp_path / "L3+paths")
    keyword = [repo for repo, _ in BM25Ranker().search(query, universe)]
    assert DenseL2HybridRanker(dense=dense("L2")).rank(query, universe) == (
        reciprocal_rank_fusion([keyword, readme_cards])
    )
    assert make_ranker("hybrid-rrf-L0-L2").dense.level == "L2"
    with pytest.raises(ValueError, match="L2"):
        DenseL2HybridRanker(dense=dense())


def test_names_only_bm25f_is_bm25f_at_l0(corpus: Path, tmp_path: Path) -> None:
    restrict(corpus, "L0", tmp_path / "L0")
    query = Query("c1", "acme billing ledger invoices", CUTOFF)
    at_l0 = BM25Ranker().rank(query, view(tmp_path / "L0"))
    assert NameBM25Ranker().rank(query, view(corpus)) == at_l0
    assert OpenTopRanker(budget=0).rank(query, view(corpus)) == at_l0


def test_open_top_reranks_only_what_it_opens(corpus: Path, tmp_path: Path) -> None:
    query = Query("c1", "refund invoices rounding with the ledger", CUTOFF)
    universe = view(corpus)
    names = NameBM25Ranker().rank(query, universe)
    assert make_ranker("bm25f-open20").name == "bm25f-open20"
    control = OpenTopRanker(budget=3)
    ranking = control.rank(query, universe)
    assert set(ranking[:3]) == set(names[:3]) and ranking[3:] == names[3:]
    opened = UniverseView(
        universe.root, tuple(c for c in universe.candidates if c.repo in names[:3])
    )
    assert ranking[:3] == BM25Ranker().rank(query, opened)

    restrict(corpus, "L2", tmp_path / "L2")
    with pytest.raises(ValueError, match="full packets"):
        control.rank(query, view(tmp_path / "L2"))


def test_hybrid_open_top_opens_the_l0_hybrids_top(corpus: Path, tmp_path: Path) -> None:
    restrict(corpus, "L0", tmp_path / "L0")
    query = Query("c1", "refund invoices rounding with the ledger", CUTOFF)
    universe = view(corpus)
    store = tmp_path / "v.sqlite"

    def dense(level: str | None = None) -> DenseCardRanker:
        return DenseCardRanker(encoder=HashingEncoder(), store=store, level=level)

    at_l0 = HybridRanker(keyword=BM25Ranker(), dense=dense()).rank(query, view(tmp_path / "L0"))
    assert HybridOpenTopRanker(budget=0, dense=dense("L0")).rank(query, universe) == at_l0
    control = HybridOpenTopRanker(budget=3, dense=dense("L0"))
    assert control.name == "hybrid-open3"
    ranking = control.rank(query, universe)
    assert set(ranking[:3]) == set(at_l0[:3]) and ranking[3:] == at_l0[3:]
    opened = UniverseView(
        universe.root, tuple(c for c in universe.candidates if c.repo in at_l0[:3])
    )
    assert ranking[:3] == BM25Ranker().rank(query, opened)
    assert make_ranker("hybrid-open20").name == "hybrid-open20"
    with pytest.raises(ValueError, match="L0"):
        HybridOpenTopRanker(dense=dense())


def test_restrict_command(corpus: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "L3"
    assert main(["restrict", "--corpus", str(corpus), "--level", "L3", "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "level": "L3",
        "packets": 10,
        "files": sum(len(kept_files(p, "L3")) for p in (corpus / "repos").glob("*/*")),
        "universes": 1,
    }
