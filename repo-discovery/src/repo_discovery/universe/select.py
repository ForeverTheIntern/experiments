"""Which files of a snapshot count as evidence, and the caps that bound them.

Kinds: ``readme``, ``docs``, ``manifest`` and ``contract``. Contract
candidates are chosen by path and then confirmed by content (``sniff``):
OpenAPI/Swagger, AsyncAPI, Kubernetes-style resources (``apiVersion`` plus
``kind``), protobuf, GraphQL SDL, Thrift and Avro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

MAX_FILE_BYTES = 256 * 1024
CAPS = {"readme": 30, "docs": 80, "manifest": 300, "contract": 300}

SKIP_DIRS = frozenset(
    [
        "node_modules",
        "vendor",
        "third_party",
        "third-party",
        "bower_components",
        "site-packages",
        "__pycache__",
        ".git",
        ".venv",
        "venv",
        ".tox",
        "dist",
        "build",
        "target",
        "out",
        ".next",
        ".nuxt",
        "coverage",
    ]
)
TEST_DIRS = frozenset(
    [
        "test",
        "tests",
        "testdata",
        "test-data",
        "__tests__",
        "fixtures",
        "__fixtures__",
        "e2e",
        "mocks",
        "__mocks__",
        "examples",
        "example",
        "samples",
    ]
)

DOC_EXT = frozenset([".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc", ".asciidoc"])
DOC_DIRS = frozenset(["doc", "docs", "documentation", "wiki"])
ROOT_DOC_RE = re.compile(
    r"^(contributing|architecture|design|overview|changelog|changes|history|api|usage|guide|faq|roadmap|"
    r"security|install|installation|getting[-_]started|upgrading|migration)(\.[a-z]+)?$",
    re.I,
)

MANIFEST_NAMES = frozenset(
    [
        "package.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Pipfile",
        "environment.yml",
        "go.mod",
        "go.work",
        "Cargo.toml",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
        "composer.json",
        "Gemfile",
        "mix.exs",
        "pubspec.yaml",
        "Package.swift",
        "CMakeLists.txt",
        "meson.build",
        "deno.json",
        "lerna.json",
        "nx.json",
        "turbo.json",
        "pnpm-workspace.yaml",
        "rush.json",
        "build.sbt",
        "project.clj",
        "deps.edn",
        "rebar.config",
        "stack.yaml",
        "vcpkg.json",
        "conanfile.txt",
        "conanfile.py",
        "WORKSPACE",
        "WORKSPACE.bazel",
        "MODULE.bazel",
        "Chart.yaml",
        "galaxy.yml",
        "requirements.yml",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "Directory.Build.props",
        "global.json",
    ]
)
MANIFEST_SUFFIXES = (
    ".gemspec",
    ".csproj",
    ".fsproj",
    ".vbproj",
    ".nuspec",
    ".cabal",
    ".Dockerfile",
)
REQUIREMENTS_RE = re.compile(r"^requirements([-_.][\w.-]+)?\.(txt|in)$", re.I)

YAML_JSON = frozenset([".yaml", ".yml", ".json"])
YAML = frozenset([".yaml", ".yml"])
SCHEMA_EXT = {
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".graphqls": "graphql",
    ".gql": "graphql",
    ".thrift": "thrift",
    ".avsc": "avro",
    ".avdl": "avro",
}
SPEC_NAME_RE = re.compile(r"(openapi|swagger|asyncapi)", re.I)
SPEC_DIRS = frozenset(
    [
        "openapi",
        "swagger",
        "asyncapi",
        "api",
        "apis",
        "spec",
        "specs",
        "api-spec",
        "api-specs",
        "api-docs",
        "schema",
        "schemas",
        "contracts",
    ]
)
K8S_DIRS = frozenset(
    [
        "k8s",
        "kubernetes",
        "kube",
        "manifests",
        "deploy",
        "deployment",
        "deployments",
        "helm",
        "charts",
        "chart",
        "templates",
        "kustomize",
        "kustomization",
        "crd",
        "crds",
        "overlays",
        "base",
        "config",
    ]
)
K8S_NAME_RE = re.compile(
    r"(crd|kustomization|deployment|service|ingress|configmap|statefulset|daemonset|cronjob|rbac|"
    r"role|operator|catalog-info)",
    re.I,
)

OPENAPI_RE = re.compile(rb'^\s*"?(openapi|swagger)"?\s*:', re.M)
ASYNCAPI_RE = re.compile(rb'^\s*"?asyncapi"?\s*:', re.M)
API_VERSION_RE = re.compile(rb"^apiVersion:\s*['\"]?([\w./-]+)", re.M)
KIND_RE = re.compile(rb"^kind:\s*\S+", re.M)
K8S_GROUP_RE = re.compile(
    r"^(v1|apps/v1|batch/v1(beta1)?|policy/v1(beta1)?|autoscaling/v\d\w*|[\w.-]*k8s\.io/[\w]+|"
    r"extensions/v1beta1|[\w.-]*\.x-k8s\.io/\w+)$"
)


@dataclass(frozen=True)
class Candidate:
    path: str
    oid: str
    kind: str
    subkind: str
    sniff: bool = False


def _parts(path: str) -> tuple[list[str], str, str]:
    p = PurePosixPath(path)
    return [d.lower() for d in p.parts[:-1]], p.name, p.suffix.lower()


def classify(path: str) -> tuple[str, str, bool] | None:
    """Return ``(kind, subkind, needs_sniff)`` for a path, or ``None``."""
    dirs, name, ext = _parts(path)
    if any(d in SKIP_DIRS for d in dirs):
        return None
    if name.lower().startswith("readme") and (ext in DOC_EXT or ext == ""):
        return ("readme", "root" if not dirs else "nested", False) if len(dirs) <= 4 else None
    if not dirs and ext in DOC_EXT and ROOT_DOC_RE.match(name):
        return "docs", "root", False
    if ext in DOC_EXT and any(d in DOC_DIRS for d in dirs) and len(dirs) <= 5:
        return "docs", "docs-dir", False
    if any(d in TEST_DIRS for d in dirs):
        return None
    if (
        name in MANIFEST_NAMES
        or name.endswith(MANIFEST_SUFFIXES)
        or REQUIREMENTS_RE.match(name)
        or ("requirements" in dirs and ext in (".txt", ".in"))
    ):
        return "manifest", name if name in MANIFEST_NAMES else ext.lstrip(".") or name, False
    if ext in SCHEMA_EXT:
        return "contract", SCHEMA_EXT[ext], False
    if ext in YAML_JSON and (SPEC_NAME_RE.search(name) or any(d in SPEC_DIRS for d in dirs)):
        return "contract", "spec", True
    if ext in YAML and (any(d in K8S_DIRS for d in dirs) or K8S_NAME_RE.search(name)):
        return "contract", "resource", True
    return None


def sniff(subkind: str, content: bytes) -> str | None:
    """Confirm a path-selected contract by content; return its final subkind or ``None``."""
    head = content[:65536]
    if subkind == "spec":
        if ASYNCAPI_RE.search(head):
            return "asyncapi"
        if OPENAPI_RE.search(head):
            return "openapi"
        subkind = "resource"
    if subkind == "resource":
        version = API_VERSION_RE.search(head)
        if version and KIND_RE.search(head):
            return (
                "kubernetes"
                if K8S_GROUP_RE.match(version.group(1).decode(errors="replace"))
                else "resource"
            )
        if ASYNCAPI_RE.search(head):
            return "asyncapi"
        if OPENAPI_RE.search(head):
            return "openapi"
        return None
    return subkind


def select(entries: list[tuple[str, str]]) -> tuple[list[Candidate], dict[str, int]]:
    """Pick evidence candidates from ``(path, blob_oid)`` pairs, shallowest first within each kind.

    Returns the candidates and, per kind, how many were dropped by ``CAPS``.
    """
    by_kind: dict[str, list[Candidate]] = {k: [] for k in CAPS}
    for path, oid in entries:
        hit = classify(path)
        if hit:
            kind, subkind, needs_sniff = hit
            by_kind[kind].append(Candidate(path, oid, kind, subkind, needs_sniff))
    chosen, dropped = [], {}
    for kind, items in by_kind.items():
        items.sort(key=lambda c: (c.path.count("/"), c.path))
        chosen.extend(items[: CAPS[kind]])
        if len(items) > CAPS[kind]:
            dropped[kind] = len(items) - CAPS[kind]
    return chosen, dropped
