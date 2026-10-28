from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from repo_discovery import harness
from repo_discovery.cases import Case, Query
from repo_discovery.cli import main as cli
from repo_discovery.contract import Universe
from repo_discovery.embeddings import (
    ENCODER_DIR_ENV,
    ENCODER_ID,
    WEIGHTS_FILE,
    BgeSmallEncoder,
    VectorStore,
    store_path,
)
from repo_discovery.evidence import read_evidence
from repo_discovery.rankers import dense, make_ranker
from repo_discovery.rankers.bm25 import BM25Ranker, analyze
from repo_discovery.rankers.dense import (
    NAME_REPEATS,
    DenseCardRanker,
    HybridRanker,
    NamedCardBM25Ranker,
    NamedDenseCardRanker,
    name_led_card,
    reciprocal_rank_fusion,
)

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"
CUTOFF = "2026-03-01T00:00:00+00:00"


class HashingEncoder:
    """Bag of hashed word stems, so tests need no model: cosine tracks shared vocabulary."""

    id = "test/hashing@0"

    def __init__(self, dims: int = 512) -> None:
        self.dims = dims
        self.encoded: list[str] = []

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dims, dtype=np.float32)
        for token in analyze(text):
            vector[int(hashlib.md5(token.encode()).hexdigest(), 16) % self.dims] += 1.0
        return vector / max(float(np.linalg.norm(vector)), 1e-9)

    def documents(self, texts: Sequence[str]) -> np.ndarray:
        self.encoded.extend(texts)
        return np.stack([self._vector(t) for t in texts])

    def query(self, text: str) -> np.ndarray:
        return self._vector(text)


def ranker(cls: type[DenseCardRanker], tmp_path: Path) -> DenseCardRanker:
    return cls(encoder=HashingEncoder(), store=tmp_path / "v.sqlite")


@pytest.mark.parametrize("cls", [DenseCardRanker, NamedDenseCardRanker])
def test_dense_ranks_every_candidate(cls, tmp_path: Path, mini_cases: list[Case]) -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    repos = sorted(c.repo for c in view.candidates)
    dense_ranker = ranker(cls, tmp_path)
    for prediction in harness.run(dense_ranker, mini_cases, EVIDENCE_UNIVERSE):
        case = next(c for c in mini_cases if c.case_id == prediction.case_id)
        assert sorted(prediction.ranking) == repos
        assert prediction.ranking[0] in case.gold_repos, (cls.name, prediction.ranking)
    query = Query("c1", "ledger journal", CUTOFF)
    scores = [score for _, score in dense_ranker.search(query, view)]
    assert scores == sorted(scores, reverse=True) and -1.0 <= scores[-1] <= scores[0] <= 1.0


def test_vectors_are_stored_per_snapshot(tmp_path: Path) -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    query = Query("c1", "refund invoices", CUTOFF)
    first = ranker(DenseCardRanker, tmp_path)
    ranking = first.rank(query, view)
    assert len(first.encoder.encoded) == first.stats.embedded == first.stats.snapshots == len(view)
    assert first.stats.cached == 0
    first.rank(query, view)
    assert len(first.encoder.encoded) == len(view)

    copy = Universe.load(shutil.copytree(EVIDENCE_UNIVERSE, tmp_path / "copy")).view()
    again = ranker(DenseCardRanker, tmp_path)
    assert again.rank(query, copy) == ranking
    assert again.encoder.encoded == [] and again.stats.cached == len(view)

    named = ranker(NamedDenseCardRanker, tmp_path)
    named.rank(query, view)
    assert len(named.encoder.encoded) == len(view)
    assert len(VectorStore(tmp_path / "v.sqlite")) == 2 * len(view)


def test_a_new_commit_is_a_new_snapshot(tmp_path: Path, make_universe) -> None:
    def universe(commit: int) -> Universe:
        entries = [
            {
                "repo": "acme/a",
                "commit": f"{commit:040x}",
                "snapshot_date": "2026-01-31",
                "role": "gold",
            },
            {
                "repo": "acme/b",
                "commit": f"{9:040x}",
                "snapshot_date": "2026-01-31",
                "role": "gold",
            },
        ]
        return Universe.load(make_universe(entries, name=f"u{commit}"))

    dense_ranker = ranker(DenseCardRanker, tmp_path)
    assert dense_ranker.vectors(universe(1).view()).shape == (2, 512)
    assert dense_ranker.vectors(universe(2).view()).shape == (2, 512)
    assert dense_ranker.stats.snapshots == dense_ranker.stats.embedded == 3


def test_vector_store_round_trip(tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "deep" / "v.sqlite")
    vectors = {f"k{i}": np.full(4, i, dtype=np.float32) for i in range(1_200)}
    store.put_many(vectors)
    found = VectorStore(tmp_path / "deep" / "v.sqlite").get_many([*vectors, "missing"])
    assert set(found) == set(vectors) and np.array_equal(found["k7"], vectors["k7"])
    path = store_path("org/model@abc", tmp_path)
    assert path == tmp_path / "embeddings" / "org__model--abc.sqlite"


def test_reciprocal_rank_fusion() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "b", "d"]], k=60)
    assert fused == ["c", "b", "a", "d"]
    assert reciprocal_rank_fusion([["b"], ["a"]]) == ["a", "b"]
    assert reciprocal_rank_fusion([[], []]) == []


def test_hybrid_fuses_keyword_matches_with_dense(tmp_path: Path) -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    query = Query("c1", "Vaccine lots expire.", CUTOFF)
    hybrid = HybridRanker(dense=ranker(DenseCardRanker, tmp_path))
    ranking = hybrid.rank(query, view)
    keyword = [repo for repo, _ in BM25Ranker().search(query, view)]
    expected = reciprocal_rank_fusion([keyword, hybrid.dense.rank(query, view)])
    assert ranking == expected and ranking[0] == "umbrella/vaccine-tracker"
    assert sorted(ranking) == sorted(c.repo for c in view.candidates)


def test_name_led_card() -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    candidate = next(c for c in view.candidates if c.repo == "acme/ledger")
    evidence = read_evidence(candidate.repo, candidate.evidence_dir)
    lead, _, card = name_led_card(evidence, budget=40).partition("\n")
    assert lead.split() == ["acme", "ledger"] * NAME_REPEATS
    assert card.startswith("acme/ledger")
    query = Query("c1", "ledger", CUTOFF)
    assert NamedCardBM25Ranker().rank(query, view)[0] == "acme/ledger"


def test_registry_builds_v4_rankers() -> None:
    for name in ("dense-cards", "dense-cards-name", "bm25-cards-name", "hybrid-rrf"):
        assert make_ranker(name, seed=0).name == name


def test_encoder_checks_local_weights_before_loading(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(ValueError, match=r"is not BAAI/bge-small-en-v1\.5"):
        BgeSmallEncoder(tmp_path)
    (tmp_path / WEIGHTS_FILE).write_bytes(b"not the weights")
    monkeypatch.setenv(ENCODER_DIR_ENV, str(tmp_path))
    with pytest.raises(ValueError, match="at revision 5c38ec7c"):
        BgeSmallEncoder()


def test_embed_command(tmp_path: Path, wideswe_mini: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("REPO_DISCOVERY_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(dense, "BgeSmallEncoder", HashingEncoder)
    cases = tmp_path / "cases.jsonl"
    build = ["build-cases", "--wideswe-dir", str(wideswe_mini), "--out", str(cases)]
    assert cli([*build, "--manifest", str(tmp_path / "split.json")]) == 0
    capsys.readouterr()
    args = ["embed", "--cases", str(cases), "--split", "all"]
    args += ["--universes", str(EVIDENCE_UNIVERSE), "--budget", "40"]

    assert dense.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["embedded"] == first["snapshots"] == 10
    assert Path(first["store"]) == store_path(ENCODER_ID, tmp_path / "cache")
    assert dense.main([*args, "--variant", "card"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["embedded"] == 0 and again["cached"] == 10
