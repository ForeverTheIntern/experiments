"""V3 contract identifiers: what a repository's API, event and schema contracts define and use.

Each identifier is a typed key with a role, ``provides`` or ``consumes``:

- ``openapi.path``: a path in an OpenAPI or Swagger spec (provides), or an HTTP path the
  README or docs call, as in ``GET /api/fleet/agents`` or ``curl localhost:9200/_bulk``
  (consumes). Paths are lowercased, parameters and numbers become ``{}``, and leading
  ``api`` and version segments (``v1``, ``v2beta1``, ``0``) are dropped, so
  ``/api/v1/users/{id}`` and ``/users/42`` are one key. A path needs two segments after
  that: ``/status`` or ``/search`` says nothing about which API it is.
- ``openapi.operation``: an OpenAPI ``operationId`` (provides).
- ``asyncapi.channel``: an AsyncAPI channel name or address (provides).
- ``proto.package``, ``proto.service``, ``proto.message``: a protobuf package and the
  services, messages and enums it declares, fully qualified (provides); a qualified type
  from another package used in a field or RPC (consumes the type and its package).
- ``proto.file``: a ``.proto`` file anywhere in the repository's path list, under every
  path suffix, so that any ``import`` resolves (provides); an ``import`` (consumes).
- Google's well-known and common protos (``google.protobuf``, ``google.api``, ...) are
  the protobuf standard library, vendored everywhere, and are not keys.
- ``k8s.kind``: a custom resource type, ``<group>/<Kind>``. A CustomResourceDefinition
  provides it; a resource of that type, in a contract file or a YAML snippet in the docs,
  consumes it. Built-in Kubernetes kinds are not keys.
- ``k8s.name``: ``<Kind>/<name>`` for each resource a contract file declares (provides),
  and the Backstage ``dependsOn``, ``providesApis`` and ``consumesApis`` references
  (``component:package-registry`` -> ``Component/package-registry``, consumes).

Everything is extracted with line-based patterns rather than a YAML or JSON parser, because
the evidence reader truncates large specs and Helm templates are not valid YAML.
"""

from __future__ import annotations

import json as jsonlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

from repo_discovery.cards import yaml_block
from repo_discovery.evidence import Evidence, sniff_contract

PROVIDES = "provides"
CONSUMES = "consumes"
TYPES: tuple[str, ...] = (
    "openapi.path",
    "openapi.operation",
    "asyncapi.channel",
    "proto.package",
    "proto.service",
    "proto.message",
    "proto.file",
    "k8s.kind",
    "k8s.name",
)
MAX_IDENTIFIERS = 20_000

_YAML_DOCUMENT = re.compile(r"^---.*$", re.M)
_TOP_LEVEL = r"^{0}[ \t]*:[ \t]*['\"]?([^'\"#\s]+)"
_API_VERSION = re.compile(_TOP_LEVEL.format("apiVersion"), re.M)
_KIND = re.compile(_TOP_LEVEL.format("kind"), re.M)
_OPERATION_ID = re.compile(r"""["']?operationId["']?\s*:\s*["']?([\w.:/-]+)""")
_JSON_PATH_KEY = re.compile(r'"(/[^"\s]*)"\s*:\s*\{')
_JSON_ADDRESS = re.compile(r'"address"\s*:\s*"([^"\s]+)"')
_ADDRESS = re.compile(r"""^\s+address\s*:\s*["']?([^"'#\s]+)""", re.M)
_TEMPLATE = re.compile(r"\{\{|\$\{|<%")
_VERSION_SEGMENT = re.compile(r"v?\d+((alpha|beta)\d*)?")
_PARAMETER = re.compile(r"\{[^}]*\}|:[A-Za-z_]\w*|<[^>]*>|\*|\d+")
_LITERAL_SEGMENT = re.compile(r"[a-z0-9_.~-]+")

_HTTP_CALL = re.compile(
    r"\b(?:GET|POST|PUT|PATCH|DELETE|HEAD)\s+(/[^\s`'\"<>()\[\]]*)"
    r"|https?://(?:localhost|127\.0\.0\.1|\$\{?\w+\}?|[\w-]+)(?::\d+)?(/[^\s`'\"<>()\[\]]*)"
)
_DOC_RESOURCE = re.compile(
    r"apiVersion[ \t]*:[ \t]*['\"]?([\w.-]+\.[\w-]+)/[\w-]+['\"]?[ \t]*\n[ \t]*"
    r"kind[ \t]*:[ \t]*['\"]?([A-Z]\w*)"
)
_BACKSTAGE_REF = re.compile(r"^\s*-\s*['\"]?(component|api|resource|system):([\w./-]+)", re.M)
_BACKSTAGE_REF_BLOCK = re.compile(
    r"^(\s*)(dependsOn|providesApis|consumesApis)\s*:\s*$((?:\n\1\s*-.*)+)", re.M
)

_PROTO_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_PROTO_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
_PROTO_IMPORT = re.compile(r"""^\s*import\s+(?:public\s+|weak\s+)?["']([^"']+)["']""", re.M)
_PROTO_DECLARATION = re.compile(r"^\s*(service|message|enum)\s+([A-Za-z_]\w*)", re.M)
_PROTO_FIELD_TYPE = re.compile(
    r"^\s*(?:repeated\s+|optional\s+|required\s+)?\.?([a-z_]\w*(?:\.\w+)+)\s+\w+\s*=", re.M
)
_PROTO_RPC_TYPES = re.compile(
    r"\brpc\s+\w+\s*\(\s*(?:stream\s+)?\.?([\w.]+)\s*\)\s*returns\s*\(\s*(?:stream\s+)?\.?([\w.]+)"
)

# Built-in API groups: their kinds are Kubernetes' own, not a contract between repositories.
_CORE_GROUPS = re.compile(
    r"^(|apps|batch|policy|autoscaling|extensions|[\w.-]*\.k8s\.io|k8s\.io|[\w.-]*\.x-k8s\.io)$"
)
_WELL_KNOWN = ("protobuf", "api", "rpc", "type", "longrunning")
_WELL_KNOWN_PACKAGE = re.compile(rf"^google\.({'|'.join(_WELL_KNOWN)})(\.|$)")
_WELL_KNOWN_FILE = re.compile(rf"(^|/)google/({'|'.join(_WELL_KNOWN)})/")


@dataclass(frozen=True, order=True)
class Identifier:
    type: str
    value: str
    role: str

    @property
    def key(self) -> tuple[str, str]:
        return self.type, self.value


def identifiers(evidence: Evidence) -> frozenset[Identifier]:
    """Every contract identifier in ``evidence``, at most ``MAX_IDENTIFIERS``."""
    found: dict[Identifier, None] = {}
    for item in _all(evidence):
        found[item] = None
        if len(found) >= MAX_IDENTIFIERS:
            break
    return frozenset(found)


def contract_text(found: Iterable[Identifier]) -> str:
    """The identifiers' values as text, each once, for indexing without joins."""
    return " ".join(sorted({i.value for i in found}))


def _all(evidence: Evidence) -> Iterator[Identifier]:
    for contract in evidence.contracts:
        yield from contract_identifiers(contract.path, contract.text)
    yield from proto_files(evidence.paths)
    for text in (evidence.readme, *(d.text for d in evidence.docs)):
        yield from doc_references(text)


def contract_identifiers(path: str, text: str) -> Iterator[Identifier]:
    """Identifiers declared or used by one contract file."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".proto":
        yield from proto_identifiers(text)
        return
    if suffix not in {".yaml", ".yml", ".json"}:
        return
    kind = sniff_contract(text)
    if kind == "openapi":
        yield from openapi_identifiers(text, json=suffix == ".json")
    elif kind == "asyncapi":
        yield from asyncapi_identifiers(text, json=suffix == ".json")
    elif kind == "kubernetes":
        yield from resource_identifiers(text)


def normalize_path(path: str) -> str | None:
    """``/users/{}`` for ``/api/v1/Users/{id}/``; None without a literal segment."""
    path = path.split("?")[0].split("#")[0].strip().lower()
    segments = [s for s in path.split("/") if s]
    while segments and (segments[0] == "api" or _VERSION_SEGMENT.fullmatch(segments[0])):
        segments.pop(0)
    segments = ["{}" if _PARAMETER.fullmatch(s) else s for s in segments]
    if any(s != "{}" and not _LITERAL_SEGMENT.fullmatch(s) for s in segments):
        return None
    if len(segments) < 2 or not any(s != "{}" for s in segments):
        return None
    return "/" + "/".join(segments)


def openapi_identifiers(text: str, json: bool = False) -> Iterator[Identifier]:
    keys = _JSON_PATH_KEY.findall(text) if json else [k for k, _ in yaml_block(text, "paths")]
    for raw in keys:
        if (path := normalize_path(raw)) is not None:
            yield Identifier("openapi.path", path, PROVIDES)
    for operation in _OPERATION_ID.findall(text):
        yield Identifier("openapi.operation", operation.lower(), PROVIDES)


def asyncapi_identifiers(text: str, json: bool = False) -> Iterator[Identifier]:
    if json:
        names = list(_dict(_load_json(text).get("channels")))
        names += _JSON_ADDRESS.findall(text)
    else:
        names = [k for k, _ in yaml_block(text, "channels")] + _ADDRESS.findall(text)
    for name in names:
        if name.strip() and not _TEMPLATE.search(name):
            yield Identifier("asyncapi.channel", name.strip().lower(), PROVIDES)
    for operation in _OPERATION_ID.findall(text):
        yield Identifier("openapi.operation", operation.lower(), PROVIDES)


def proto_identifiers(text: str) -> Iterator[Identifier]:
    code = _PROTO_COMMENT.sub(" ", text)
    package = _PROTO_PACKAGE.search(code)
    own = package.group(1) if package else ""
    if _WELL_KNOWN_PACKAGE.match(own):
        return
    if own:
        yield Identifier("proto.package", own, PROVIDES)
    for kind, name in _PROTO_DECLARATION.findall(code):
        qualified = f"{own}.{name}" if own else name
        type_ = "proto.service" if kind == "service" else "proto.message"
        yield Identifier(type_, qualified, PROVIDES)
    for imported in _PROTO_IMPORT.findall(code):
        if not _WELL_KNOWN_FILE.search(imported.lower()):
            yield Identifier("proto.file", imported.lower(), CONSUMES)
    used = _PROTO_FIELD_TYPE.findall(code)
    used += [t for pair in _PROTO_RPC_TYPES.findall(code) for t in pair if "." in t]
    for qualified in used:
        other, _, name = qualified.rpartition(".")
        if (
            other
            and other != own
            and other[0].islower()
            and name[:1].isupper()
            and not _WELL_KNOWN_PACKAGE.match(other)
        ):
            yield Identifier("proto.package", other, CONSUMES)
            yield Identifier("proto.message", qualified, CONSUMES)


def proto_files(paths: Iterable[str]) -> Iterator[Identifier]:
    """``proto.file`` keys for every path suffix of each ``.proto`` file, so imports resolve."""
    for path in paths:
        if not path.lower().endswith(".proto") or _WELL_KNOWN_FILE.search(path.lower()):
            continue
        parts = path.lower().split("/")
        for start in range(len(parts)):
            yield Identifier("proto.file", "/".join(parts[start:]), PROVIDES)


def resource_identifiers(text: str) -> Iterator[Identifier]:
    """CRDs, custom resources, ``Kind/name`` and Backstage references in Kubernetes-style YAML."""
    for document in _YAML_DOCUMENT.split(text):
        version, kind = _API_VERSION.search(document), _KIND.search(document)
        if not version or not kind:
            continue
        group, kind_name = version.group(1).rpartition("/")[0], kind.group(1)
        metadata = dict(yaml_block(document, "metadata"))
        name = metadata.get("name", "")
        if name and not _TEMPLATE.search(name):
            yield Identifier("k8s.name", f"{kind_name}/{name}".lower(), PROVIDES)
        if kind_name == "CustomResourceDefinition":
            yield from _crd(document)
        elif not _CORE_GROUPS.match(group):
            yield Identifier("k8s.kind", f"{group}/{kind_name}".lower(), CONSUMES)
        for block in _BACKSTAGE_REF_BLOCK.finditer(document):
            for ref_kind, ref_name in _BACKSTAGE_REF.findall(block.group(3)):
                value = f"{ref_kind}/{ref_name.rpartition('/')[2]}".lower()
                yield Identifier("k8s.name", value, CONSUMES)


def _load_json(text: str) -> dict:
    try:
        return _dict(jsonlib.loads(text))
    except ValueError:
        return {}


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _crd(document: str) -> Iterator[Identifier]:
    spec = yaml_block(document, "spec")
    group = dict(spec).get("group", "")
    kind = re.search(r"^\s+kind\s*:\s*['\"]?([A-Z]\w*)", document.split("names:", 1)[-1], re.M)
    if group and kind and not _TEMPLATE.search(group):
        yield Identifier("k8s.kind", f"{group}/{kind.group(1)}".lower(), PROVIDES)


def doc_references(text: str) -> Iterator[Identifier]:
    """HTTP paths called and custom resources written in README and docs examples."""
    for method_path, url_path in _HTTP_CALL.findall(text):
        raw = method_path or url_path
        if raw and (path := normalize_path(raw)) is not None:
            yield Identifier("openapi.path", path, CONSUMES)
    for group, kind in _DOC_RESOURCE.findall(text):
        if not _CORE_GROUPS.match(group):
            yield Identifier("k8s.kind", f"{group}/{kind}".lower(), CONSUMES)
