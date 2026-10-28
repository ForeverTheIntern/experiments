from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from repo_discovery import harness
from repo_discovery.cases import Case, Query
from repo_discovery.contract import Universe
from repo_discovery.depgraph import DependencyGraph, build_graph, neighbour_list
from repo_discovery.packages import Packages, normalize, read_packages
from repo_discovery.rankers import make_ranker
from repo_discovery.rankers.bm25 import BM25Ranker
from repo_discovery.rankers.deps import DependencyExpansionRanker, expand, interleave

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"
CUTOFF = "2026-03-01T00:00:00+00:00"


def _packet(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def test_normalize_follows_each_registry() -> None:
    assert normalize("pypi", "Sentry_SDK.ext") == "sentry-sdk-ext"
    assert normalize("cargo", "relay_general") == "relay-general"
    assert normalize("go", "github.com/elastic/beats/v7") == "github.com/elastic/beats"
    assert normalize("npm", "@Elastic/EUI") == "@elastic/eui"
    assert normalize("maven", "${project.groupId}:core") == ""


def test_fixture_packets_declare_names_and_dependencies() -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    packets = {c.repo: read_packages(c.evidence_dir) for c in view.candidates}
    assert packets["acme/billing"].provides == {"npm:@acme/billing"}
    assert "npm:@acme/billing" in packets["acme/portal"].depends
    assert packets["globex/core"].provides == {"go:github.com/globex/core"}
    assert "go:github.com/globex/core" in packets["globex/cli"].depends
    assert packets["acme/ledger"].provides == {"pypi:acme-ledger"}
    assert {"pypi:sqlalchemy", "pypi:pydantic", "pypi:pytest"} <= packets["acme/ledger"].depends
    assert read_packages(EVIDENCE_UNIVERSE / "missing") == Packages()


def test_jvm_php_and_rust_manifests(tmp_path: Path) -> None:
    packet = _packet(
        tmp_path,
        {
            "pom.xml": """<project>
  <parent><groupId>org.acme</groupId><artifactId>parent</artifactId></parent>
  <artifactId>billing-core</artifactId>
  <dependencies>
    <dependency><groupId>${project.groupId}</groupId><artifactId>ledger</artifactId></dependency>
    <dependency><groupId>com.google.guava</groupId><artifactId>guava</artifactId></dependency>
  </dependencies>
</project>""",
            "settings.gradle": "rootProject.name = 'billing'\n",
            "gradle.properties": "group=org.acme.gradle\n",
            "build.gradle": "tasks.register('check') {\n  group = 'verification'\n}\n",
            "client/build.gradle": "dependencies { api 'io.grpc:grpc-api:1.60.0' }\n",
            "composer.json": json.dumps({"name": "acme/billing", "require": {"php": ">=8.1"}}),
            "crates/relay_x/Cargo.toml": '[package]\nname = "relay_x"\n[dependencies]\nserde = "1"',
        },
    )
    packages = read_packages(packet)
    assert packages.provides == {
        "maven:org.acme:billing-core",
        "maven:org.acme.gradle:billing",
        "maven:org.acme.gradle:client",
        "composer:acme/billing",
        "cargo:relay-x",
    }
    assert {"maven:org.acme:ledger", "maven:com.google.guava:guava"} <= packages.depends
    assert {"maven:io.grpc:grpc-api", "composer:php", "cargo:serde"} <= packages.depends


def test_build_graph_resolution_outcomes() -> None:
    packages = {
        "a/app": Packages(
            frozenset({"npm:app", "npm:shared"}), frozenset({"npm:lib", "npm:shared"})
        ),
        "b/lib": Packages(frozenset({"npm:lib"}), frozenset({"npm:left-pad", "npm:docs"})),
        **{f"x/d{i}": Packages(frozenset({"npm:docs"}), frozenset()) for i in range(4)},
    }
    graph, resolution = build_graph(packages, max_providers=3)
    assert graph.edges == {("a/app", "b/lib")}
    assert graph.outbound("a/app") == {"b/lib"} and graph.inbound("b/lib") == {"a/app"}
    assert resolution.outcomes == {"internal": 1, "resolved": 1, "unresolved": 1, "ambiguous": 1}
    assert resolution.rate(resolution.outcomes) == pytest.approx(1 / 3)
    assert resolution.by_ecosystem["npm"]["resolved"] == 1


def test_rewired_keeps_degrees() -> None:
    repos = tuple(f"o/r{i}" for i in range(40))
    rng = random.Random(1)
    edges = frozenset((rng.choice(repos[:10]), rng.choice(repos)) for _ in range(120))
    graph = DependencyGraph(repos, frozenset((u, v) for u, v in edges if u != v))
    shuffled = graph.rewired(random.Random(0))
    assert shuffled.edges != graph.edges
    assert len(shuffled.edges) >= 0.8 * len(graph.edges)
    for repo in repos:
        assert len(shuffled.outbound(repo)) <= len(graph.outbound(repo))
        assert len(shuffled.inbound(repo)) <= len(graph.inbound(repo)) + 1
    assert graph.rewired(random.Random(0)) == shuffled


def test_neighbour_list_orders_by_seeds_touched_then_base_rank() -> None:
    graph = DependencyGraph(
        ("a", "b", "c", "d", "e"), frozenset({("a", "c"), ("b", "c"), ("d", "a"), ("b", "e")})
    )
    rank = {"a": 1, "b": 2, "e": 3, "d": 4, "c": 5}
    assert neighbour_list(graph, ["a", "b"], rank) == ["c", "e", "d"]


def test_interleave() -> None:
    assert interleave(["a", "b", "c", "d"], ["x", "c", "y"]) == ["a", "x", "b", "c", "d", "y"]
    assert interleave(["a", "b", "c"], []) == ["a", "b", "c"]
    assert interleave(["a", "b", "c", "d"], ["d", "x"], head=2) == ["a", "b", "d", "c", "x"]


def test_expansion_pulls_a_dependency_up() -> None:
    base = [f"r{i}" for i in range(20)]
    graph = DependencyGraph(tuple(base), frozenset({("r0", "r15")}))
    ranking = expand(base, graph, seeds=10)
    assert ranking[:3] == ["r0", "r15", "r1"]
    assert sorted(ranking) == sorted(base)
    assert expand(base, DependencyGraph(tuple(base), frozenset()), seeds=10) == base


def test_ranker_on_the_fixture(mini_cases: list[Case]) -> None:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    query = Query("c1", "Show the subscription schedule in the customer portal.", CUTOFF)
    ranker = DependencyExpansionRanker(seeds=1)
    graph = ranker.graph(query, view)
    assert graph.edges == {("acme/portal", "acme/billing"), ("globex/cli", "globex/core")}
    base = BM25Ranker().rank(query, view)
    ranking = ranker.rank(query, view)
    assert ranking[0] == base[0] == "acme/portal"
    assert ranking[1] == "acme/billing"
    for name in ("bm25f-deps", "bm25f-deps-random"):
        predictions = list(harness.run(make_ranker(name), mini_cases, EVIDENCE_UNIVERSE))
        assert len(predictions) == len(mini_cases)
        assert all(
            sorted(p.ranking) == sorted(c.repo for c in view.candidates) for p in predictions
        )
