from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from repo_discovery.cases import Query
from repo_discovery.contract import Universe, UniverseView
from repo_discovery.evidence import Evidence, read_evidence
from repo_discovery.fetch_levels import (
    FULL,
    LEVELS,
    MARKER,
    fetch_level,
    kept_files,
    main,
    packet_cost,
    restrict,
)
from repo_discovery.packages import read_packages
from repo_discovery.rankers import make_ranker
from repo_discovery.rankers.bm25 import BM25Ranker, analyze
from repo_discovery.rankers.dense import DenseCardRanker
from repo_discovery.rankers.open_top import NameBM25Ranker, OpenTopRanker

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"
BILLING = Path("repos") / "acme__billing" / ("a1" * 20)
NUCLEUS = Path("repos") / "hooli__nucleus" / ("2" * 40)
CASE_UNIVERSE = Path("universes") / "monthly" / "u9" / "case-1"
CUTOFF = "2026-03-01T00:00:00+00:00"


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
    assert kept["L0"] == []
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
    assert kept_files(corpus / NUCLEUS, "L2") == ["README.rst"]
    with pytest.raises(ValueError, match="fetch level"):
        kept_files(billing, "L1")


def test_a_restricted_universe_reads_only_its_level(corpus: Path, tmp_path: Path) -> None:
    full = packet_in(view(corpus), "acme/billing")
    assert fetch_level(full) == FULL
    evidence = {}
    for level in LEVELS:
        out = tmp_path / level
        stats = restrict(corpus, level, out)
        assert stats.packets == 10 and stats.universes == 1
        packet = packet_in(view(out), "acme/billing")
        assert fetch_level(packet) == level
        assert (out / "meta" / "acme__billing" / f"{BILLING.name}.json").is_file()
        evidence[level] = read_evidence("acme/billing", packet)
        portal = read_packages(packet_in(view(out), "acme/portal"))
        assert (portal.manifests > 0) == (level in ("L3", "L4"))

    assert evidence["L0"] == Evidence("acme/billing")
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


def test_packet_cost_counts_what_each_level_fetches(corpus: Path, tmp_path: Path) -> None:
    billing = corpus / BILLING
    size = {rel: (billing / rel).stat().st_size for rel in kept_files(billing, FULL)}
    costs = {}
    for level in ("L0", "L2", "L3"):
        restrict(corpus, level, tmp_path / level)
        costs[level] = packet_cost(packet_in(view(tmp_path / level), "acme/billing"))
    costs[FULL] = packet_cost(corpus / BILLING)
    assert (costs["L0"].files, costs["L0"].bytes, costs["L0"].calls) == (0, 0, 0)
    assert (costs["L2"].files, costs["L2"].bytes, costs["L2"].calls) == (1, size["README.md"], 1)
    l3 = size["README.md"] + size["docs/rounding.md"] + size["package.json"]
    assert (costs["L3"].files, costs["L3"].bytes, costs["L3"].calls) == (3, l3, 4)
    assert (costs[FULL].files, costs[FULL].bytes, costs[FULL].calls) == (
        len(size),
        sum(size.values()),
        len(size),
    )


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


def test_restrict_command(corpus: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "L3"
    assert main(["restrict", "--corpus", str(corpus), "--level", "L3", "--out", str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "level": "L3",
        "packets": 10,
        "files": sum(len(kept_files(p, "L3")) for p in (corpus / "repos").glob("*/*")),
        "universes": 1,
    }
