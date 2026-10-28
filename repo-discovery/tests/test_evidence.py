from __future__ import annotations

from pathlib import Path

import pytest

from repo_discovery import evidence
from repo_discovery.contract import Universe
from repo_discovery.evidence import classify, parse_manifest, read_evidence, sample_evenly

EVIDENCE_UNIVERSE = Path(__file__).parent / "fixtures" / "evidence_universe"


def packet(repo: str) -> Path:
    view = Universe.load(EVIDENCE_UNIVERSE).view()
    return next(c.evidence_dir for c in view.candidates if c.repo == repo)


def write(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


def test_reads_a_fixture_packet() -> None:
    found = read_evidence("acme/billing", packet("acme/billing"))
    assert "monthly invoices" in found.readme
    assert [d.path for d in found.docs] == ["docs/rounding.md"]
    assert [c.path for c in found.contracts] == ["api/openapi.yaml"]
    assert found.other == ()
    assert found.package_names == ("@acme/billing",)
    assert found.descriptions == ("Invoices, tax and refunds for Acme subscriptions",)
    assert found.keywords == ("billing", "invoices", "tax")
    assert found.dependencies == ("decimal.js", "express", "jest")
    assert found.paths[-1] == ".github/workflows/ci.yml"
    assert "src/tax/rounding.ts" in found.paths


def test_every_fixture_packet_reads() -> None:
    universe = Universe.load(EVIDENCE_UNIVERSE)
    assert universe.missing_evidence() == []
    for candidate in universe.view().candidates:
        found = read_evidence(candidate.repo, candidate.evidence_dir)
        assert found.readme and found.paths and found.manifests, candidate.repo


def test_missing_packet_gives_only_the_name(tmp_path: Path) -> None:
    assert read_evidence("a/b", tmp_path / "nowhere") == evidence.Evidence("a/b")


@pytest.mark.parametrize(
    ("rel", "kind"),
    [
        ("package.json", "manifests"),
        ("services/api/pyproject.toml", "manifests"),
        ("requirements-dev.txt", "manifests"),
        ("tps.gemspec", "manifests"),
        ("manifests/anything.lock", "manifests"),
        ("api/openapi.yaml", "contracts"),
        ("swagger.json", "contracts"),
        ("proto/ledger.proto", "contracts"),
        ("schema/user.schema.json", "contracts"),
        ("contracts/events.yaml", "contracts"),
        ("docs/guide.md", "docs"),
        ("CHANGELOG.rst", "docs"),
        ("notes.txt", "docs"),
        ("src/main.go", "other"),
        ("config.yaml", "other"),
    ],
)
def test_classify(rel: str, kind: str) -> None:
    assert classify(rel) == kind


def test_packet_without_path_list(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "README.rst": "Title\n=====\n\nRST readme.\n",
            "README.md": "# Title\n\nMarkdown readme.\n",
            "docs/README.md": "Nested readmes are docs.\n",
            "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
            "LICENSE": "MIT License\n",
            "src/app.py": "def main():\n    pass\n",
        },
    )
    found = read_evidence("a/b", root)
    assert "Markdown readme" in found.readme
    assert [d.path for d in found.docs] == ["README.rst", "docs/README.md"]
    assert [f.path for f in found.other] == ["src/app.py"]
    assert set(found.paths) == {
        "LICENSE",
        "README.md",
        "README.rst",
        "docs/README.md",
        "logo.png",
        "src/app.py",
    }


def test_reads_are_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evidence, "MAX_PATHS", 10)
    monkeypatch.setattr(evidence, "MAX_DOCS_CHARS", 100)
    monkeypatch.setattr(evidence, "MAX_FILE_BYTES", 80)
    files: dict[str, str | bytes] = {f"docs/{i:02d}.md": "word " * 50 for i in range(5)}
    files["paths.txt"] = "\n".join(f"./src/f{i:03d}.py" for i in range(100))
    found = read_evidence("a/b", write(tmp_path, files))
    assert [d.path for d in found.docs] == ["docs/00.md", "docs/01.md"]
    assert all(len(d.text) == 80 for d in found.docs)
    assert found.paths == tuple(f"src/f{i:03d}.py" for i in range(0, 100, 10))


def test_sample_evenly() -> None:
    items = [str(i) for i in range(10)]
    assert sample_evenly(items, 20) == items
    assert sample_evenly(items, 5) == ["0", "2", "4", "6", "8"]


MANIFESTS = [
    (
        "package.json",
        '{"name": "pkg", "description": "A package", "keywords": ["k"],'
        ' "dependencies": {"a": "1"}, "devDependencies": {"b": "2"}}',
        ("pkg",),
        "A package",
        ("a", "b"),
    ),
    (
        "composer.json",
        '{"name": "acme/lib", "require": {"php": ">=8", "monolog/monolog": "^3"}}',
        ("acme/lib",),
        "",
        ("php", "monolog/monolog"),
    ),
    (
        "pyproject.toml",
        '[project]\nname = "lib"\ndescription = "Py lib"\ndependencies = ["requests>=2", "attrs"]\n'
        '[project.optional-dependencies]\ntest = ["pytest"]\n',
        ("lib",),
        "Py lib",
        ("requests", "attrs", "pytest"),
    ),
    (
        "pyproject.toml",
        '[tool.poetry]\nname = "poet"\n[tool.poetry.dependencies]\npython = "^3.11"\nhttpx = "*"\n'
        '[tool.poetry.group.dev.dependencies]\nruff = "*"\n',
        ("poet",),
        "",
        ("httpx", "ruff"),
    ),
    (
        "Cargo.toml",
        '[package]\nname = "crate"\ndescription = "Rusty"\n[dependencies]\nserde = "1"\n'
        '[dev-dependencies]\ntokio = "1"\n',
        ("crate",),
        "Rusty",
        ("serde", "tokio"),
    ),
    (
        "go.mod",
        "module github.com/a/b\n\ngo 1.22\n\nrequire github.com/x/y v1.0.0\n"
        "require (\n\tgolang.org/x/sync v0.6.0 // indirect\n)\nreplace github.com/x/y => ../y\n",
        ("github.com/a/b",),
        "",
        ("github.com/x/y", "golang.org/x/sync"),
    ),
    (
        "requirements.txt",
        "# pinned\nDjango==4.2\n-r base.txt\ncelery[redis]>=5\n",
        (),
        "",
        ("Django", "celery"),
    ),
    (
        "setup.py",
        'setup(\n    name="sp",\n    description="Setup lib",\n'
        '    install_requires=["six", "numpy>=1"],\n)\n',
        ("sp",),
        "Setup lib",
        ("six", "numpy"),
    ),
    (
        "pom.xml",
        "<project><parent><artifactId>parent</artifactId></parent>"
        "<artifactId>core</artifactId><description>Java core</description>"
        "<dependencies><dependency><artifactId>guava</artifactId></dependency></dependencies>"
        "</project>",
        ("core",),
        "Java core",
        ("guava",),
    ),
    (
        "build.gradle.kts",
        'dependencies {\n    implementation("com.squareup.okhttp3:okhttp:4.12.0")\n}\n',
        (),
        "",
        ("okhttp",),
    ),
    ("settings.gradle", "rootProject.name = 'app'\n", ("app",), "", ()),
    (
        "x.gemspec",
        's.name = "gem"\ns.summary = "A gem"\ns.add_runtime_dependency "rack"\n',
        ("gem",),
        "A gem",
        ("rack",),
    ),
    (
        "Gemfile",
        "source 'https://rubygems.org'\ngem 'rails'\ngem \"pg\"\n",
        (),
        "",
        ("rails", "pg"),
    ),
    (
        "Chart.yaml",
        "name: chart\ndescription: Helm chart\ndependencies:\n  - name: redis\n",
        ("chart",),
        "Helm chart",
        ("redis",),
    ),
    ("pubspec.yaml", "name: app\ndependencies:\n  http: ^1.0\n", ("app",), "", ("http",)),
    (
        "Lib.csproj",
        "<Project><PropertyGroup><PackageId>Acme.Lib</PackageId></PropertyGroup>"
        '<ItemGroup><PackageReference Include="Newtonsoft.Json" /></ItemGroup></Project>',
        ("Acme.Lib",),
        "",
        ("Newtonsoft.Json",),
    ),
]


@pytest.mark.parametrize(("name", "text", "names", "description", "deps"), MANIFESTS)
def test_parse_manifest(
    name: str, text: str, names: tuple[str, ...], description: str, deps: tuple[str, ...]
) -> None:
    manifest = parse_manifest(f"sub/{name}", text)
    assert manifest.path == f"sub/{name}"
    assert manifest.text == text
    assert manifest.names == names
    assert manifest.description == description
    assert manifest.dependencies == deps


@pytest.mark.parametrize("name", ["package.json", "pyproject.toml", "Cargo.toml", "Chart.yaml"])
def test_malformed_manifests_keep_their_text(name: str) -> None:
    manifest = parse_manifest(name, "{ not [valid")
    assert manifest.text == "{ not [valid"
    assert (manifest.names, manifest.dependencies) == ((), ())
