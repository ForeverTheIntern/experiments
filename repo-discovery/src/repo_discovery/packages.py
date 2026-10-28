"""Package names a repository declares, and the packages it depends on, by ecosystem.

Only manifest files are read: the universe builder's sidecar labels say which files are
manifests, and without a sidecar every file with a manifest name below counts. Every
name is tagged with its ecosystem and normalized the way that ecosystem's registry
compares names, so ``npm:@elastic/eui`` declared in one repository matches a
``@elastic/eui`` dependency in another:

=========  ==========================================================  =====================
Ecosystem  Declares (``provides``)                                     Normalization
=========  ==========================================================  =====================
npm        ``package.json`` ``name``                                   lowercase
pypi       ``pyproject.toml``, ``setup.py``, ``setup.cfg`` name        PEP 503
go         ``go.mod`` ``module``                                       lowercase, no ``/vN``
cargo      ``Cargo.toml`` ``[package] name``                           lowercase, ``_`` = ``-``
maven      ``pom.xml`` ``groupId:artifactId``; Gradle ``group:name``   lowercase
composer   ``composer.json`` ``name``                                  lowercase
gem        ``*.gemspec`` name                                          lowercase
nuget      ``*.csproj``/``*.fsproj`` ``PackageId`` or ``AssemblyName``  lowercase
pub        ``pubspec.yaml`` ``name``                                   lowercase
helm       ``Chart.yaml`` ``name``                                     lowercase
=========  ==========================================================  =====================

Dependencies come from the same files plus ``requirements*.txt`` and ``Gemfile``. Gradle
projects declare ``group:name`` only when a ``group`` is set somewhere in the build;
the project names are ``rootProject.name`` and every directory holding a build script.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from repo_discovery.evidence import PATH_LIST_FILES, parse_manifest, read_text, sidecar_labels

ECOSYSTEMS: tuple[str, ...] = (
    "npm",
    "pypi",
    "go",
    "cargo",
    "maven",
    "composer",
    "gem",
    "nuget",
    "pub",
    "helm",
)

_PYPI_FILES = {"pyproject.toml", "setup.py", "setup.cfg"}
_GRADLE_BUILD = {"build.gradle", "build.gradle.kts"}
_GRADLE_SETTINGS = {"settings.gradle", "settings.gradle.kts"}
_GO_MAJOR = re.compile(r"/v\d+$")
_PEP503 = re.compile(r"[-_.]+")
_XML_TAG = r"<{0}>\s*([^<]+?)\s*</{0}>"
_POM_DEPENDENCY = re.compile(r"<dependency>(.*?)</dependency>", re.S)
_POM_OWN_BLOCKS = re.compile(
    r"<(parent|dependencies|dependencyManagement|build|profiles|reporting)>.*?</\1>", re.S
)
_POM_PARENT = re.compile(r"<parent>(.*?)</parent>", re.S)
# A Maven group has a dot; dotless values are Gradle task groups ("verification", "build").
_GRADLE_GROUP = re.compile(r"""^\s*group\s*=?\s*["']([\w-]+(?:\.[\w-]+)+)["']""", re.M)
_GRADLE_PROPERTY_GROUP = re.compile(r"^\s*group\s*=\s*([\w-]+(?:\.[\w-]+)+)\s*$", re.M)
_GRADLE_ROOT_NAME = re.compile(r"""rootProject\.name\s*=\s*["']([^"']+)["']""")
_GRADLE_COORDINATE = re.compile(r"""["']([\w.-]+):([\w.-]+)(?::[^"'\s]*)?["']""")
_GRADLE_MAP = re.compile(
    r"""group\s*[:=]\s*["']([\w.-]+)["']\s*,\s*name\s*[:=]\s*["']([\w.-]+)["']"""
)
_SETUP_CFG_REQUIRES = re.compile(r"^install_requires\s*=\s*\n((?:[ \t]+\S.*\n?)+)", re.M)
_REQUIREMENT_NAME = re.compile(r"[<>=!~\[;\s]")


@dataclass(frozen=True)
class Packages:
    """``ecosystem:name`` keys a repository declares (``provides``) and uses (``depends``)."""

    provides: frozenset[str] = frozenset()
    depends: frozenset[str] = frozenset()
    manifests: int = 0


@dataclass
class _Collector:
    provides: set[str] = field(default_factory=set)
    depends: set[str] = field(default_factory=set)
    # (depth of the file that set it, group): the shallowest group names the project.
    gradle_groups: list[tuple[int, str]] = field(default_factory=list)
    gradle_projects: set[str] = field(default_factory=set)

    def add(self, ecosystem: str, provides: Iterable[str], depends: Iterable[str]) -> None:
        self.provides.update(k for k in (package_key(ecosystem, n) for n in provides) if k)
        self.depends.update(k for k in (package_key(ecosystem, d) for d in depends) if k)


def normalize(ecosystem: str, name: str) -> str:
    """``name`` as ``ecosystem``'s registry compares it; empty when it can't be a package."""
    name = name.strip().strip("\"'").lower()
    if not name or name.startswith(("${", "@{")) or " " in name:
        return ""
    if ecosystem == "pypi":
        return _PEP503.sub("-", name)
    if ecosystem == "cargo":
        return name.replace("_", "-")
    if ecosystem == "go":
        return _GO_MAJOR.sub("", name)
    return name


def package_key(ecosystem: str, name: str) -> str:
    normalized = normalize(ecosystem, name)
    return f"{ecosystem}:{normalized}" if normalized else ""


def ecosystem_of(key: str) -> str:
    return key.partition(":")[0]


def manifest_ecosystem(rel: str) -> str | None:
    """The ecosystem whose names and dependencies a manifest at ``rel`` declares."""
    name = PurePosixPath(rel).name.lower()
    if name == "package.json":
        return "npm"
    if name in _PYPI_FILES or (name.startswith("requirements") and name.endswith((".txt", ".in"))):
        return "pypi"
    if name == "go.mod":
        return "go"
    if name == "cargo.toml":
        return "cargo"
    if name == "pom.xml" or name in _GRADLE_BUILD or name in _GRADLE_SETTINGS:
        return "maven"
    if name == "gradle.properties":
        return "maven"
    if name == "composer.json":
        return "composer"
    if name.endswith(".gemspec") or name == "gemfile":
        return "gem"
    if name.endswith((".csproj", ".fsproj")):
        return "nuget"
    if name == "pubspec.yaml":
        return "pub"
    if name == "chart.yaml":
        return "helm"
    return None


def manifest_paths(packet: Path) -> list[str]:
    """Packet-relative paths of the manifests in ``packet``, from the sidecar when there is one."""
    labels = sidecar_labels(packet)
    if labels:
        paths = [path for path, kind in labels.items() if kind == "manifests"]
    else:
        paths = [
            p.relative_to(packet).as_posix()
            for p in packet.rglob("*")
            if p.is_file() and p.name not in PATH_LIST_FILES
        ]
    return sorted(rel for rel in paths if manifest_ecosystem(rel) is not None)


def read_packages(packet: Path) -> Packages:
    """What the manifests in ``packet`` declare; a missing packet declares nothing."""
    if not packet.is_dir():
        return Packages()
    collector = _Collector()
    count = 0
    for rel in manifest_paths(packet):
        path = packet / rel
        if not path.is_file():
            continue
        text = read_text(path)
        if text is None:
            continue
        count += 1
        _collect(collector, rel, text)
    if collector.gradle_groups and collector.gradle_projects:
        _, group = min(collector.gradle_groups)
        collector.add("maven", (f"{group}:{p}" for p in collector.gradle_projects), ())
    return Packages(frozenset(collector.provides), frozenset(collector.depends), count)


def _collect(collector: _Collector, rel: str, text: str) -> None:
    ecosystem = manifest_ecosystem(rel)
    name = PurePosixPath(rel).name.lower()
    if ecosystem == "maven":
        _collect_jvm(collector, rel, name, text)
        return
    if ecosystem is None:
        return
    manifest = parse_manifest(rel, text)
    depends = list(manifest.dependencies)
    if name == "setup.cfg" and (block := _SETUP_CFG_REQUIRES.search(text)):
        depends += [
            _REQUIREMENT_NAME.split(line.strip())[0] for line in block.group(1).splitlines()
        ]
    collector.add(ecosystem, manifest.names, depends)


def _collect_jvm(collector: _Collector, rel: str, name: str, text: str) -> None:
    depth = len(PurePosixPath(rel).parts)
    if name == "pom.xml":
        provides, depends = _pom(text)
        collector.add("maven", provides, depends)
        return
    if name == "gradle.properties":
        collector.gradle_groups += [(depth, g) for g in _GRADLE_PROPERTY_GROUP.findall(text)]
        return
    depends = [f"{g}:{a}" for g, a in _GRADLE_COORDINATE.findall(text)]
    depends += [f"{g}:{a}" for g, a in _GRADLE_MAP.findall(text)]
    collector.add("maven", (), depends)
    if name in _GRADLE_SETTINGS:
        collector.gradle_projects.update(_GRADLE_ROOT_NAME.findall(text))
        return
    collector.gradle_groups += [(depth, g) for g in _GRADLE_GROUP.findall(text)]
    if depth > 1:
        collector.gradle_projects.add(PurePosixPath(rel).parent.name)


def _pom(text: str) -> tuple[list[str], list[str]]:
    own = _POM_OWN_BLOCKS.sub("", text)
    parent = _POM_PARENT.search(text)
    group = re.search(_XML_TAG.format("groupId"), own) or (
        re.search(_XML_TAG.format("groupId"), parent.group(1)) if parent else None
    )
    artifact = re.search(_XML_TAG.format("artifactId"), own)
    own_group = group.group(1) if group else ""
    provides = [f"{own_group}:{artifact.group(1)}"] if own_group and artifact else []
    depends = []
    for block in _POM_DEPENDENCY.findall(text):
        dep_group = re.search(_XML_TAG.format("groupId"), block)
        dep_artifact = re.search(_XML_TAG.format("artifactId"), block)
        if not (dep_group and dep_artifact):
            continue
        g = dep_group.group(1)
        if g in {"${project.groupId}", "${groupId}", "${pom.groupId}"}:
            g = own_group
        if g and "${" not in g and "${" not in dep_artifact.group(1):
            depends.append(f"{g}:{dep_artifact.group(1)}")
    return provides, depends
