from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_discovery.api_contracts import CONSUMES, PROVIDES, Identifier
from repo_discovery.cases import Query
from repo_discovery.contract import Universe
from repo_discovery.rankers import contract_joins, make_ranker
from repo_discovery.rankers.contract_joins import (
    ContractIndex,
    ContractJoinRanker,
    ContractTextRanker,
    join_kind,
    promote,
)

CUTOFF = "2026-03-01T00:00:00+00:00"


def provides(type_: str, value: str) -> Identifier:
    return Identifier(type_, value, PROVIDES)


def consumes(type_: str, value: str) -> Identifier:
    return Identifier(type_, value, CONSUMES)


def test_join_kinds() -> None:
    assert join_kind("openapi.path", {PROVIDES}, {CONSUMES}) == "provides-consumes"
    assert join_kind("openapi.path", {CONSUMES}, {PROVIDES, CONSUMES}) == "provides-consumes"
    assert join_kind("proto.message", {PROVIDES}, {PROVIDES}) == "co-defines"
    assert join_kind("proto.file", {PROVIDES}, {PROVIDES}) is None
    assert join_kind("proto.file", {CONSUMES}, {PROVIDES}) == "provides-consumes"
    assert join_kind("k8s.kind", {CONSUMES}, {CONSUMES}) is None


def test_joins_are_typed_and_weighted() -> None:
    path = ("openapi.path", "/fleet/agents")
    index = ContractIndex(
        [
            ("a/server", [provides(*path), provides("proto.file", "x.proto")]),
            ("b/client", [consumes(*path)]),
            ("c/client", [consumes(*path), provides("proto.file", "x.proto")]),
            ("d/none", []),
        ]
    )
    assert set(index.joins("a/server")) == {"b/client", "c/client"}
    assert [(j.key, j.kind, j.weight) for j in index.joins("a/server")["b/client"]] == [
        (path, "provides-consumes", 0.5)
    ]
    assert set(index.joins("b/client")) == {"a/server"}
    assert index.joins("d/none") == {}


def test_hub_keys_never_join(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contract_joins, "MAX_HOLDERS", 3)
    hub = [(f"r/{i}", [provides("k8s.kind", "x.io/thing")]) for i in range(4)]
    assert ContractIndex(hub).joins("r/0") == {}
    assert set(ContractIndex(hub[:3]).joins("r/0")) == {"r/1", "r/2"}


def test_promotion_lifts_the_heaviest_join_behind_its_seed() -> None:
    base = ["s1", "s2", "x3", "x4", "j5", "j6", "j7"]
    index = ContractIndex(
        [
            ("s1", [provides("openapi.path", "/a/b"), provides("openapi.path", "/c/d")]),
            ("j6", [consumes("openapi.path", "/a/b"), consumes("openapi.path", "/c/d")]),
            ("j5", [consumes("openapi.path", "/a/b")]),
            ("s2", [provides("proto.message", "p.M"), provides("proto.message", "p.N")]),
            ("j7", [consumes("proto.message", "p.M"), consumes("proto.message", "p.N")]),
            ("x3", [consumes("proto.message", "p.M")]),
        ]
    )
    assert promote(base, index) == ["s1", "j6", "s2", "j7", "x3", "x4", "j5"]
    assert promote(base, index, per_seed=2) == ["s1", "j6", "j5", "s2", "j7", "x3", "x4"]
    assert promote(base, index, seeds=1) == ["s1", "j6", "s2", "x3", "x4", "j5", "j7"]
    assert promote(base, ContractIndex([])) == base


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


SPEC = """\
openapi: 3.0.0
paths:
  /api/telemetry/batches:
    post:
      operationId: uploadBatch
"""


def test_rankers_end_to_end(tmp_path: Path) -> None:
    packets = {
        "acme/collector": {"README.md": "Telemetry collector.", "contracts/openapi.yml": SPEC},
        **{f"acme/filler{i}": {"README.md": f"Unrelated words {i}."} for i in range(5)},
        "acme/uploader": {"README.md": "Sends data with `POST /api/telemetry/batches`."},
    }
    view = write_packets(tmp_path, packets).view()
    query = Query("c1", "collector", CUTOFF)
    base = make_ranker("bm25f").rank(query, view)
    joined = make_ranker("contract-joins").rank(query, view)
    assert isinstance(make_ranker("contract-joins"), ContractJoinRanker)
    assert base[0] == joined[0] == "acme/collector"
    assert base.index("acme/uploader") > 1
    assert joined[1] == "acme/uploader"
    assert sorted(joined) == sorted(base)

    upload = Query("c2", "uploadbatch", CUTOFF)
    assert make_ranker("bm25f").rank(upload, view)[0] != "acme/collector"
    control = make_ranker("bm25f-contract-text")
    assert isinstance(control, ContractTextRanker)
    assert control.rank(upload, view)[0] == "acme/collector"
