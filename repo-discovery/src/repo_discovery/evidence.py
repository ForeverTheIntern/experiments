"""Read a repository's evidence packet: README, docs, manifests, file paths, contracts.

The universe contract fixes where a packet lives (``repos/<owner>__<name>/<commit>/``)
but not its inner layout. The universe builder labels every file it extracts, after
checking its content, in a sidecar outside the packet at
``<corpus>/meta/<owner>__<name>/<commit>.json`` (the ``evidence`` list of
``{"path", "kind", "subkind", ...}`` records). Those labels win when the sidecar exists.
Files without a label are classified by name, location and a look at their content:

- ``readme``: a README at the packet root.
- ``manifests``: package manifests (``package.json``, ``pyproject.toml``, ``go.mod``,
  ``Cargo.toml``, ``pom.xml``, ``Dockerfile``...) or anything under ``manifests/``.
- ``contracts``: API, event and schema files (OpenAPI, AsyncAPI, protobuf, GraphQL,
  Avro, Thrift, JSON Schema, Kubernetes resources) or anything under ``contracts/``.
  YAML and JSON files count when their name says so or their content does.
- ``docs``: other Markdown, reStructuredText, AsciiDoc and plain-text files.
- ``paths``: the repository's file list, one path per line, from ``paths.txt`` (or
  ``files.txt``, ``tree.txt``) at the packet root; without one, the packet's own files.
- ``other``: any remaining text file.

Binary files and license texts are skipped, and every read is capped, so a huge
repository costs about as much as a typical one.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

MAX_FILE_BYTES = 256_000
MAX_DOCS_CHARS = 400_000
MAX_OTHER_CHARS = 200_000
MAX_PATHS = 5_000

PATH_LIST_FILES = ("paths.txt", "files.txt", "tree.txt")
_README_PREFERENCE = (".md", ".markdown", ".rst", ".txt", "")
_DOC_SUFFIXES = {".md", ".mdx", ".markdown", ".rst", ".txt", ".adoc", ".asciidoc"}
_CONTRACT_SUFFIXES = {".proto", ".graphql", ".graphqls", ".gql", ".avsc", ".avdl", ".thrift"}
_CONTRACT_STEMS = ("openapi", "swagger", "asyncapi")
_STRUCTURED_SUFFIXES = {".yaml", ".yml", ".json"}
_SKIPPED_STEMS = {"license", "licence", "copying", "notice"}
_SIDECAR_KINDS = {
    "readme": "readme",
    "docs": "docs",
    "manifest": "manifests",
    "contract": "contracts",
}

SNIFF_CHARS = 65_536
_SPEC_VERSION = re.compile(
    r"""^(openapi|swagger|asyncapi)[ \t]*:[ \t]*['"]?\d|"(openapi|swagger|asyncapi)"\s*:\s*"\d""",
    re.M,
)
_API_VERSION = re.compile(
    r"""^apiVersion[ \t]*:[ \t]*['"]?[\w./-]+|"apiVersion"\s*:\s*"[\w./-]+""", re.M
)
_RESOURCE_KIND = re.compile(r"""^kind[ \t]*:[ \t]*['"]?[A-Z]\w*|"kind"\s*:\s*"[A-Z]\w*""", re.M)


@dataclass(frozen=True)
class TextFile:
    path: str
    text: str


@dataclass(frozen=True)
class Manifest:
    """A manifest file and what could be parsed from it; unparsed fields stay empty."""

    path: str
    text: str
    names: tuple[str, ...] = ()
    description: str = ""
    keywords: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class Evidence:
    repo: str
    readme: str = ""
    docs: tuple[TextFile, ...] = ()
    manifests: tuple[Manifest, ...] = ()
    paths: tuple[str, ...] = ()
    contracts: tuple[TextFile, ...] = ()
    other: tuple[TextFile, ...] = ()

    @property
    def package_names(self) -> tuple[str, ...]:
        return _unique(n for m in self.manifests for n in m.names)

    @property
    def descriptions(self) -> tuple[str, ...]:
        return _unique(m.description for m in self.manifests)

    @property
    def keywords(self) -> tuple[str, ...]:
        return _unique(k for m in self.manifests for k in m.keywords)

    @property
    def dependencies(self) -> tuple[str, ...]:
        return _unique(d for m in self.manifests for d in m.dependencies)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v.strip() for v in values if v and v.strip()))


def read_evidence(repo: str, packet: Path) -> Evidence:
    """Everything usable in ``packet``; a missing packet gives evidence with only the name."""
    if not packet.is_dir():
        return Evidence(repo)
    files = sorted(
        (p for p in packet.rglob("*") if p.is_file()),
        key=lambda p: (len(p.relative_to(packet).parts), p.relative_to(packet).as_posix()),
    )
    relative = {p: p.relative_to(packet).as_posix() for p in files}
    labels = sidecar_labels(packet)
    labelled = {p: labels.get(relative[p]) for p in files}
    path_list = next((p for p in files if relative[p] in PATH_LIST_FILES), None)
    readme = _pick_readme(
        [
            p
            for p in files
            if labelled[p] == "readme" or (labelled[p] is None and _is_root_readme(relative[p]))
        ]
    )

    manifests, contracts, docs, other = [], [], [], []
    docs_chars = other_chars = 0
    for path in files:
        rel = relative[path]
        if path in (path_list, readme) or path.stem.lower() in _SKIPPED_STEMS:
            continue
        kind = labelled[path] or classify(rel)
        if kind == "readme":
            kind = "docs"
        text = None
        if kind == "other" and PurePosixPath(rel).suffix.lower() in _STRUCTURED_SUFFIXES:
            text = read_text(path)
            if text is None:
                continue
            kind = classify(rel, text)
        if kind == "docs" and docs_chars >= MAX_DOCS_CHARS:
            continue
        if kind == "other" and other_chars >= MAX_OTHER_CHARS:
            continue
        text = read_text(path) if text is None else text
        if text is None:
            continue
        if kind == "manifests":
            manifests.append(parse_manifest(rel, text))
        elif kind == "contracts":
            contracts.append(TextFile(rel, text))
        elif kind == "docs":
            docs.append(TextFile(rel, text))
            docs_chars += len(text)
        else:
            other.append(TextFile(rel, text))
            other_chars += len(text)

    if path_list is not None:
        listed = (read_text(path_list, whole=True) or "").splitlines()
        paths = [line.strip().removeprefix("./") for line in listed if line.strip()]
    else:
        paths = [relative[p] for p in files]
    return Evidence(
        repo=repo,
        readme=(read_text(readme) or "") if readme else "",
        docs=tuple(docs),
        manifests=tuple(manifests),
        paths=tuple(sample_evenly(paths, MAX_PATHS)),
        contracts=tuple(contracts),
        other=tuple(other),
    )


def sidecar_path(packet: Path) -> Path | None:
    """The builder's label file for ``packet``, beside the packet or beside its link target."""
    for root in dict.fromkeys((packet, packet.resolve())):
        if root.parent.parent.name != "repos":
            continue
        sidecar = root.parent.parent.parent / "meta" / root.parent.name / f"{root.name}.json"
        if sidecar.is_file():
            return sidecar
    return None


def sidecar_labels(packet: Path) -> dict[str, str]:
    """Packet-relative path -> kind from the builder's sidecar; empty without a usable one."""
    sidecar = sidecar_path(packet)
    if sidecar is None:
        return {}
    try:
        records = _list(_dict(json.loads(sidecar.read_text(encoding="utf-8"))).get("evidence"))
    except (OSError, ValueError):
        return {}
    labels = {}
    for record in map(_dict, records):
        path, kind = record.get("path"), _SIDECAR_KINDS.get(str(record.get("kind")))
        if isinstance(path, str) and kind:
            labels[path] = "docs" if kind == "readme" and "/" in path else kind
    return labels


def read_text(path: Path, whole: bool = False) -> str | None:
    """Decoded text of ``path``, capped at ``MAX_FILE_BYTES`` unless ``whole``; None if binary."""
    with path.open("rb") as handle:
        data = handle.read() if whole else handle.read(MAX_FILE_BYTES)
    if b"\x00" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def sample_evenly(items: list[str], limit: int) -> list[str]:
    """At most ``limit`` items spread evenly over ``items``, keeping their order."""
    if len(items) <= limit:
        return items
    return [items[i * len(items) // limit] for i in range(limit)]


def _is_root_readme(rel: str) -> bool:
    path = PurePosixPath(rel)
    return len(path.parts) == 1 and path.stem.lower() == "readme"


def _pick_readme(candidates: list[Path]) -> Path | None:
    def preference(path: Path) -> int:
        suffix = path.suffix.lower()
        return _README_PREFERENCE.index(suffix) if suffix in _README_PREFERENCE else 99

    return min(candidates, key=lambda p: (preference(p), p.name), default=None)


def classify(rel: str, text: str | None = None) -> str:
    """``manifests``, ``contracts``, ``docs`` or ``other`` for a packet-relative path.

    With ``text``, a YAML or JSON file the name leaves as ``other`` is a contract when
    :func:`sniff_contract` recognizes it.
    """
    path = PurePosixPath(rel)
    name, suffix = path.name.lower(), path.suffix.lower()
    parents = {p.lower() for p in path.parts[:-1]}
    if (
        "manifests" in parents
        or _manifest_parser(name) is not None
        or name in _RAW_MANIFESTS
        or _is_dockerfile(name)
    ):
        return "manifests"
    if (
        "contracts" in parents
        or suffix in _CONTRACT_SUFFIXES
        or name.endswith(".schema.json")
        or (suffix in _STRUCTURED_SUFFIXES and name.startswith(_CONTRACT_STEMS))
    ):
        return "contracts"
    if suffix in _DOC_SUFFIXES:
        return "docs"
    if text is not None and suffix in _STRUCTURED_SUFFIXES and sniff_contract(text):
        return "contracts"
    return "other"


def sniff_contract(text: str) -> str | None:
    """``openapi``, ``asyncapi`` or ``kubernetes`` when the head of a YAML or JSON file says so.

    Specs need a top-level version key (``openapi: 3.0.0``, ``"swagger": "2.0"``); resources
    need top-level ``apiVersion`` and a capitalized ``kind``.
    """
    head = text[:SNIFF_CHARS]
    if spec := _SPEC_VERSION.search(head):
        return "asyncapi" if (spec.group(1) or spec.group(2)) == "asyncapi" else "openapi"
    if _API_VERSION.search(head) and _RESOURCE_KIND.search(head):
        return "kubernetes"
    return None


def _is_dockerfile(name: str) -> bool:
    return (
        name in {"dockerfile", "containerfile"}
        or name.startswith("dockerfile.")
        or name.endswith(".dockerfile")
    )


# Manifest parsing: names, description, keywords and dependency names, best effort.

_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_GO_MODULE = re.compile(r"^module\s+(\S+)", re.M)
_GO_REQUIRE = re.compile(r"^\s*(?:require\s+)?([\w.~-]+\.[\w.~/-]+)\s+v[0-9]", re.M)
_GRADLE_DEP = re.compile(r"""["']([\w.-]+):([\w.-]+)(?::[^"'\s]*)?["']""")
_GRADLE_NAME = re.compile(r"""rootProject\.name\s*=\s*["']([^"']+)["']""")
_SETUP_NAME = re.compile(r"""^\s*name\s*=\s*["']?([A-Za-z0-9._-]+)""", re.M)
_SETUP_DESCRIPTION = re.compile(r"""^\s*description\s*=\s*["']?([^"'\n]+)""", re.M)
_XML_TAG = r"<{0}>\s*([^<]+?)\s*</{0}>"
_GEMSPEC_FIELD = r"""\.{0}\s*=\s*["']([^"']+)["']"""
_GEMSPEC_DEP = re.compile(r"""add_(?:runtime_|development_)?dependency\s*\(?\s*["']([^"']+)""")
_GEM = re.compile(r"""^\s*gem\s+["']([^"']+)""", re.M)
_CSPROJ_REF = re.compile(r'<PackageReference\s+Include="([^"]+)"')

# Recognized as manifests and indexed as text, but not parsed.
_RAW_MANIFESTS = {
    "pipfile",
    "mix.exs",
    "package.swift",
    "deno.json",
    "lerna.json",
    "pnpm-workspace.yaml",
    "go.work",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
}


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _texts(value: object) -> list[str]:
    return [v for v in _list(value) if isinstance(v, str)]


def _keys(*values: object) -> list[str]:
    return [str(k) for value in values for k in _dict(value)]


def _requirement_names(lines: Iterable[str]) -> list[str]:
    names = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith(("#", "-")) and (match := _REQUIREMENT.match(line)):
            names.append(match.group(1))
    return names


def _load_json(text: str) -> dict:
    try:
        return _dict(json.loads(text))
    except ValueError:
        return {}


def _load_toml(text: str) -> dict:
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return {}


def _load_yaml(text: str) -> dict:
    try:
        return _dict(yaml.safe_load(text))
    except yaml.YAMLError:
        return {}


Parsed = tuple[list[str], str, list[str], list[str]]


def _package_json(text: str) -> Parsed:
    d = _load_json(text)
    deps = _keys(
        d.get("dependencies"),
        d.get("devDependencies"),
        d.get("peerDependencies"),
        d.get("optionalDependencies"),
    )
    return [_text(d.get("name"))], _text(d.get("description")), _texts(d.get("keywords")), deps


def _composer_json(text: str) -> Parsed:
    d = _load_json(text)
    deps = _keys(d.get("require"), d.get("require-dev"))
    return [_text(d.get("name"))], _text(d.get("description")), _texts(d.get("keywords")), deps


def _pyproject(text: str) -> Parsed:
    d = _load_toml(text)
    project = _dict(d.get("project"))
    poetry = _dict(_dict(d.get("tool")).get("poetry"))
    optional = _dict(project.get("optional-dependencies"))
    groups = _dict(poetry.get("group"))
    deps = [
        *_requirement_names(_texts(project.get("dependencies"))),
        *_requirement_names(r for extra in optional.values() for r in _texts(extra)),
        *_keys(poetry.get("dependencies"), poetry.get("dev-dependencies")),
        *_keys(*(_dict(g).get("dependencies") for g in groups.values())),
    ]
    names = [_text(project.get("name")), _text(poetry.get("name"))]
    description = _text(project.get("description")) or _text(poetry.get("description"))
    keywords = _texts(project.get("keywords")) + _texts(poetry.get("keywords"))
    return names, description, keywords, [d for d in deps if d.lower() != "python"]


def _cargo(text: str) -> Parsed:
    d = _load_toml(text)
    package = _dict(d.get("package"))
    deps = _keys(
        d.get("dependencies"),
        d.get("dev-dependencies"),
        d.get("build-dependencies"),
        _dict(d.get("workspace")).get("dependencies"),
    )
    return (
        [_text(package.get("name"))],
        _text(package.get("description")),
        _texts(package.get("keywords")),
        deps,
    )


def _go_mod(text: str) -> Parsed:
    module = _GO_MODULE.search(text)
    deps = [m.group(1) for m in _GO_REQUIRE.finditer(text)]
    return [module.group(1)] if module else [], "", [], deps


def _requirements(text: str) -> Parsed:
    return [], "", [], _requirement_names(text.splitlines())


def _setup(text: str) -> Parsed:
    name = _SETUP_NAME.search(text)
    description = _SETUP_DESCRIPTION.search(text)
    deps = _requirement_names(
        re.findall(r"""["']([A-Za-z0-9][^"'\n]*)["']""", _install_requires(text))
    )
    return (
        [name.group(1)] if name else [],
        description.group(1).strip() if description else "",
        [],
        deps,
    )


def _install_requires(text: str) -> str:
    match = re.search(r"install_requires\s*=\s*\[([^\]]*)\]", text)
    return match.group(1) if match else ""


def _pom(text: str) -> Parsed:
    blocks = re.findall(r"<dependencies>(.*?)</dependencies>", text, re.S)
    deps = [a for block in blocks for a in re.findall(_XML_TAG.format("artifactId"), block)]
    own = re.sub(
        r"<(parent|dependencies|dependencyManagement|build|profiles)>.*?</\1>", "", text, flags=re.S
    )
    name = re.search(_XML_TAG.format("artifactId"), own)
    description = re.search(_XML_TAG.format("description"), own)
    return [name.group(1)] if name else [], description.group(1) if description else "", [], deps


def _gradle(text: str) -> Parsed:
    name = _GRADLE_NAME.search(text)
    deps = [m.group(2) for m in _GRADLE_DEP.finditer(text)]
    return [name.group(1)] if name else [], "", [], deps


def _gemspec(text: str) -> Parsed:
    name = re.search(_GEMSPEC_FIELD.format("name"), text)
    summary = re.search(_GEMSPEC_FIELD.format("summary"), text) or re.search(
        _GEMSPEC_FIELD.format("description"), text
    )
    deps = _GEMSPEC_DEP.findall(text)
    return [name.group(1)] if name else [], summary.group(1) if summary else "", [], deps


def _gemfile(text: str) -> Parsed:
    return [], "", [], _GEM.findall(text)


def _chart_yaml(text: str) -> Parsed:
    d = _load_yaml(text)
    deps = [_text(_dict(dep).get("name")) for dep in _list(d.get("dependencies"))]
    return [_text(d.get("name"))], _text(d.get("description")), _texts(d.get("keywords")), deps


def _pubspec(text: str) -> Parsed:
    d = _load_yaml(text)
    deps = _keys(d.get("dependencies"), d.get("dev_dependencies"))
    return [_text(d.get("name"))], _text(d.get("description")), [], deps


def _csproj(text: str) -> Parsed:
    name = re.search(_XML_TAG.format("PackageId"), text) or re.search(
        _XML_TAG.format("AssemblyName"), text
    )
    description = re.search(_XML_TAG.format("Description"), text)
    return (
        [name.group(1)] if name else [],
        description.group(1) if description else "",
        [],
        _CSPROJ_REF.findall(text),
    )


_PARSERS: dict[str, Callable[[str], Parsed]] = {
    "package.json": _package_json,
    "composer.json": _composer_json,
    "pyproject.toml": _pyproject,
    "setup.py": _setup,
    "setup.cfg": _setup,
    "cargo.toml": _cargo,
    "go.mod": _go_mod,
    "pom.xml": _pom,
    "build.gradle": _gradle,
    "build.gradle.kts": _gradle,
    "settings.gradle": _gradle,
    "settings.gradle.kts": _gradle,
    "gemfile": _gemfile,
    "chart.yaml": _chart_yaml,
    "pubspec.yaml": _pubspec,
}


def _manifest_parser(name: str) -> Callable[[str], Parsed] | None:
    if name in _PARSERS:
        return _PARSERS[name]
    if name.startswith("requirements") and name.endswith((".txt", ".in")):
        return _requirements
    if name.endswith(".gemspec"):
        return _gemspec
    if name.endswith((".csproj", ".fsproj")):
        return _csproj
    return None


def parse_manifest(rel: str, text: str) -> Manifest:
    parser = _manifest_parser(PurePosixPath(rel).name.lower())
    if parser is None:
        return Manifest(rel, text)
    names, description, keywords, deps = parser(text)
    return Manifest(
        path=rel,
        text=text,
        names=_unique(names),
        description=description.strip(),
        keywords=_unique(keywords),
        dependencies=_unique(deps),
    )
