from __future__ import annotations

import pytest

from repo_discovery.universe.select import CAPS, classify, select, sniff


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("README.md", ("readme", "root", False)),
        ("packages/core/README", ("readme", "nested", False)),
        ("node_modules/x/README.md", None),
        ("CONTRIBUTING.md", ("docs", "root", False)),
        ("docs/guide/setup.md", ("docs", "docs-dir", False)),
        ("src/notes.md", None),
        ("package.json", ("manifest", "package.json", False)),
        ("package-lock.json", None),
        ("requirements-dev.txt", ("manifest", "txt", False)),
        ("src/App.csproj", ("manifest", "csproj", False)),
        ("tests/fixtures/package.json", None),
        ("proto/v1/service.proto", ("contract", "protobuf", False)),
        ("api/openapi.yaml", ("contract", "spec", True)),
        ("deploy/app.yaml", ("contract", "resource", True)),
        ("examples/deploy/app.yaml", None),
        ("src/config.yaml", None),
    ],
)
def test_classify(path: str, expected: tuple | None) -> None:
    assert classify(path) == expected


@pytest.mark.parametrize(
    ("subkind", "content", "expected"),
    [
        ("spec", b"openapi: 3.0.0\ninfo: {}\n", "openapi"),
        ("spec", b'{\n  "swagger": "2.0"\n}', "openapi"),
        ("spec", b"asyncapi: 2.6.0\n", "asyncapi"),
        ("spec", b"name: not a spec\n", None),
        ("resource", b"apiVersion: apps/v1\nkind: Deployment\n", "kubernetes"),
        ("resource", b"apiVersion: argoproj.io/v1alpha1\nkind: Workflow\n", "resource"),
        ("resource", b"apiVersion: v1\n", None),
        ("protobuf", b"syntax = 'proto3';", "protobuf"),
    ],
)
def test_sniff(subkind: str, content: bytes, expected: str | None) -> None:
    assert sniff(subkind, content) == expected


def test_select_caps_keep_shallowest() -> None:
    entries = [(f"docs/{'d/' * (i % 3)}page{i:03}.md", f"{i:040x}") for i in range(100)]
    chosen, dropped = select([*entries, ("README.md", "f" * 40)])
    docs = [c for c in chosen if c.kind == "docs"]
    assert len(docs) == CAPS["docs"]
    assert dropped == {"docs": 100 - CAPS["docs"]}
    assert max(c.path.count("/") for c in docs) <= min(
        p.count("/") for p, _ in entries if p not in {c.path for c in docs}
    )
    assert [c.kind for c in chosen].count("readme") == 1
