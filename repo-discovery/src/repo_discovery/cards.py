"""Extractive repository cards: text taken from a repository's evidence, under a word budget.

Cards are the control for generated cards (V1) and the text V4 embeds, so they are
deterministic and use nothing but the evidence packet. Sections, in priority order:

- ``title``: ``owner/name`` and the package names its manifests declare.
- ``about``: manifest descriptions and keywords.
- ``overview``: the README's prose, without badges, HTML, code, tables or headings;
  docs pages' prose follows when the README runs out.
- ``topics``: README section headings, then docs page titles, minus boilerplate such as
  "Installation" or "License".
- ``api``: names declared in API, event and schema contracts (services, operations,
  messages, types, OpenAPI paths, AsyncAPI channels, Kubernetes ``Kind/name``).
- ``dependencies``: declared dependency names.
- ``layout``: the directories holding the most files, and the commonest file types.

A word is a whitespace-separated token, labels included. Each section has a share of
the budget, and whatever the sections leave unused goes to those with text left over,
in priority order, so a card falls short of the budget only when its evidence runs out.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

from repo_discovery.contract import UniverseView
from repo_discovery.evidence import Evidence, read_evidence, sniff_contract

DEFAULT_BUDGET = 256

SECTIONS: tuple[str, ...] = (
    "title",
    "about",
    "overview",
    "topics",
    "api",
    "dependencies",
    "layout",
)
SHARES: dict[str, int] = {
    "title": 16,
    "about": 40,
    "overview": 100,
    "topics": 30,
    "api": 25,
    "dependencies": 25,
    "layout": 20,
}
LABELS: dict[str, str] = {
    "title": "",
    "about": "About:",
    "overview": "Overview:",
    "topics": "Topics:",
    "api": "API:",
    "dependencies": "Depends on:",
    "layout": "Layout:",
}

_CONTAINER_DIRS = {
    "apps",
    "cmd",
    "components",
    "crates",
    "extensions",
    "internal",
    "libs",
    "modules",
    "packages",
    "pkg",
    "plugins",
    "services",
    "x-pack",
}
_BOILERPLATE_HEADINGS = {
    "acknowledgements",
    "acknowledgments",
    "authors",
    "backers",
    "badges",
    "build",
    "building",
    "changelog",
    "codeofconduct",
    "community",
    "contents",
    "contribute",
    "contributing",
    "contributors",
    "credits",
    "development",
    "documentation",
    "faq",
    "features",
    "feedback",
    "gettingstarted",
    "help",
    "install",
    "installation",
    "installing",
    "introduction",
    "license",
    "licence",
    "links",
    "maintainers",
    "overview",
    "prerequisites",
    "quickstart",
    "requirements",
    "resources",
    "runningtests",
    "security",
    "setup",
    "sponsors",
    "support",
    "tableofcontents",
    "testing",
    "tests",
    "toc",
    "usage",
}


@dataclass(frozen=True)
class Card:
    repo: str
    text: str
    sections: dict[str, str]

    @property
    def words(self) -> int:
        return word_count(self.text)

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "words": self.words,
            "text": self.text,
            "sections": self.sections,
        }


def word_count(text: str) -> int:
    return len(text.split())


def build_card(evidence: Evidence, budget: int = DEFAULT_BUDGET) -> Card:
    content = {
        "title": card_title(evidence),
        "about": _about(evidence),
        "overview": _overview(evidence, budget),
        "topics": "; ".join(_topics(evidence)),
        "api": ", ".join(contract_names(evidence)),
        "dependencies": ", ".join(evidence.dependencies),
        "layout": layout_summary(evidence.paths),
    }
    words = {s: (LABELS[s].split() if content[s] else []) + content[s].split() for s in SECTIONS}
    allowed = allocate({s: len(w) for s, w in words.items()}, budget)
    sections = {s: " ".join(words[s][: allowed[s]]) for s in SECTIONS if allowed[s]}
    return Card(evidence.repo, "\n".join(sections.values()), sections)


def cards_for(universe: UniverseView, budget: int = DEFAULT_BUDGET) -> Iterator[Card]:
    for candidate in universe.candidates:
        yield build_card(read_evidence(candidate.repo, candidate.evidence_dir), budget)


def allocate(lengths: dict[str, int], budget: int) -> dict[str, int]:
    """Words per section: its share of ``budget`` first, then spare budget in priority order.

    A section is dropped rather than left with its label alone.
    """
    total = sum(SHARES.values())
    given = {s: min(lengths.get(s, 0), SHARES[s] * budget // total) for s in SECTIONS}
    spare = budget - sum(given.values())
    for s in SECTIONS:
        extra = min(lengths.get(s, 0) - given[s], spare)
        given[s] += extra
        spare -= extra
    return {s: n if n > len(LABELS[s].split()) else 0 for s, n in given.items()}


def card_title(evidence: Evidence) -> str:
    name = evidence.repo.partition("/")[2].lower()
    packages = [p for p in evidence.package_names if p.lower() != name]
    return evidence.repo + (f" (packages: {', '.join(packages)})" if packages else "")


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    return text if not text or text[-1] in ".!?" else text + "."


def _about(evidence: Evidence) -> str:
    parts = [_sentence(d) for d in evidence.descriptions]
    if evidence.keywords:
        parts.append(f"Keywords: {', '.join(evidence.keywords)}.")
    return " ".join(parts)


def _overview(evidence: Evidence, budget: int) -> str:
    words: list[str] = []
    sources = [evidence.readme, *(d.text for d in evidence.docs)]
    for source in sources:
        for paragraph in prose_paragraphs(source):
            words.extend(paragraph.split())
        if len(words) >= budget:
            break
    return " ".join(words[:budget])


def _topics(evidence: Evidence) -> list[str]:
    topics = list(headings(evidence.readme))
    for doc in evidence.docs:
        found = next(iter(headings(doc.text)), None)
        topics.append(found or _humanized_stem(doc.path))
    own = {_key(evidence.repo.partition("/")[2]), _key(evidence.repo)}
    seen: set[str] = set()
    kept = []
    for topic in topics:
        key = _key(topic)
        if key and key not in seen and key not in own and key not in _BOILERPLATE_HEADINGS:
            seen.add(key)
            kept.append(topic)
    return kept


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _humanized_stem(path: str) -> str:
    p = PurePosixPath(path)
    stem = p.stem if p.stem.lower() not in {"index", "readme"} else p.parent.name
    return re.sub(r"[-_.]+", " ", stem).strip()


def layout_summary(paths: Iterable[str]) -> str:
    directories: Counter[str] = Counter()
    suffixes: Counter[str] = Counter()
    for path in paths:
        parts = PurePosixPath(path).parts
        if len(parts) > 1 and not parts[0].startswith("."):
            depth = 2 if parts[0].lower() in _CONTAINER_DIRS and len(parts) > 2 else 1
            directories["/".join(parts[:depth])] += 1
        suffix = PurePosixPath(path).suffix.lower().lstrip(".")
        if suffix and suffix.isalnum():
            suffixes[suffix] += 1
    parts = []
    if directories:
        top = sorted(directories.items(), key=lambda item: (-item[1], item[0]))[:12]
        parts.append(f"directories {', '.join(d for d, _ in top)}.")
    if suffixes:
        top = sorted(suffixes.items(), key=lambda item: (-item[1], item[0]))[:8]
        parts.append(f"File types {', '.join(s for s, _ in top)}.")
    return " ".join(parts)


# Contracts: declared names, in file order.

_DECLARATION = re.compile(
    r"^\s*(?:service|rpc|message|enum|type|input|interface|union|struct|exception|record"
    r"|protocol|extend type)\s+([A-Za-z_][\w.]*)",
    re.M,
)
_OPERATION_ID = re.compile(r"""["']?operationId["']?\s*:\s*["']?([\w.-]+)""")
_JSON_NAME = re.compile(r'"(?:title|name|\$id)"\s*:\s*"([^"]+)"')
_YAML_DOCUMENT = re.compile(r"^---.*$", re.M)
_YAML_KEY = re.compile(r"""^\s*["']?([^\s"'#-][^"'#]*?)["']?\s*:(?:\s+([^#]*?))?\s*(?:#.*)?$""")
_TOP_KIND = re.compile(r"""^kind\s*:\s*["']?([A-Za-z]\w*)""", re.M)


def contract_names(evidence: Evidence) -> tuple[str, ...]:
    names: list[str] = []
    for contract in evidence.contracts:
        suffix = PurePosixPath(contract.path).suffix.lower()
        text = contract.text
        if suffix in {".json", ".avsc"}:
            names += _OPERATION_ID.findall(text) or _JSON_NAME.findall(text)
        elif suffix in {".yaml", ".yml"}:
            names += yaml_contract_names(text)
        else:
            names += _DECLARATION.findall(text)
    return tuple(dict.fromkeys(n for n in names if n))


def yaml_contract_names(text: str) -> list[str]:
    """Operation ids, OpenAPI paths, AsyncAPI channels and operations, Kubernetes resources."""
    if sniff_contract(text) == "kubernetes":
        return list(resource_names(text))
    keys = [k for block in ("paths", "channels", "operations") for k, _ in yaml_block(text, block)]
    return _OPERATION_ID.findall(text) + keys


def resource_names(text: str) -> Iterator[str]:
    """``Kind/name`` for each resource in a (multi-document) Kubernetes YAML file."""
    for document in _YAML_DOCUMENT.split(text):
        if kind := _TOP_KIND.search(document):
            name = dict(yaml_block(document, "metadata")).get("name", "")
            yield f"{kind.group(1)}/{name}" if name else kind.group(1)


def yaml_block(text: str, key: str) -> list[tuple[str, str]]:
    """The direct children of a top-level YAML mapping, as ``(key, scalar value)`` pairs."""
    lines = iter(text.splitlines())
    start = re.compile(rf"{re.escape(key)}\s*:\s*(?:#.*)?$")
    if not any(start.match(line) for line in lines):
        return []
    items, indent = [], None
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        depth = len(line) - len(line.lstrip())
        if depth == 0:
            break
        indent = depth if indent is None else indent
        if depth == indent and (match := _YAML_KEY.match(line)):
            items.append((match.group(1), (match.group(2) or "").strip("\"'")))
    return items


# Markup.

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[^\n]*$", re.S | re.M)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)|!\[[^\]]*\]\[[^\]]*\]")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)|\[([^\]]*)\]\[[^\]]*\]")
_REFERENCE = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.M)
_HTML_HEADING = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.S | re.I)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_URL = re.compile(r"https?://\S+|www\.\S+")
_ENTITY = re.compile(r"&[#\w]+;")
_EMPHASIS = re.compile(r"\*\*|__|\*|`")
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_UNDERLINE = re.compile(r"""^\s*([=\-~^*#+"'`:.])\1{2,}\s*$""")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_DIRECTIVE = re.compile(r"^\s*\.\. ")


def strip_markup(text: str) -> str:
    """Drop comments, images, link targets, URLs and HTML tags; keep words and code."""
    text = _HTML_COMMENT.sub(" ", text)
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(lambda m: m.group(1) or m.group(2) or " ", text)
    text = _REFERENCE.sub(" ", text)
    text = _HTML_TAG.sub(" ", text)
    text = _URL.sub(" ", text)
    return _ENTITY.sub(" ", text)


def _clean_inline(text: str) -> str:
    return " ".join(_EMPHASIS.sub("", text).split())


def headings(text: str) -> Iterator[str]:
    """Markdown, setext, reStructuredText and HTML headings, in order."""
    text = _CODE_FENCE.sub("", _HTML_COMMENT.sub("", text))
    for match in _HTML_HEADING.finditer(text):
        if heading := _clean_inline(strip_markup(match.group(1))):
            yield heading
    lines = strip_markup(text).splitlines()
    for i, line in enumerate(lines):
        atx = _ATX_HEADING.match(line)
        if atx:
            heading = atx.group(1)
        elif (
            line.strip()
            and i + 1 < len(lines)
            and _UNDERLINE.match(lines[i + 1])
            and not _UNDERLINE.match(line)
        ):
            heading = line
        else:
            continue
        if heading := _clean_inline(heading):
            yield heading


def prose_paragraphs(text: str) -> Iterator[str]:
    """Paragraphs of running text: no headings, code, tables, badges or link lists."""
    text = _CODE_FENCE.sub("\n", _HTML_COMMENT.sub(" ", text))
    text = _HTML_HEADING.sub("\n", text)
    for block in re.split(r"\n\s*\n", strip_markup(text)):
        lines = block.splitlines()
        drop = {i for i, line in enumerate(lines) if _UNDERLINE.match(line)}
        drop |= {i - 1 for i in drop}
        kept = []
        for i, line in enumerate(lines):
            if i in drop or _ATX_HEADING.match(line) or _DIRECTIVE.match(line):
                continue
            if line.lstrip().startswith("|") or line.startswith(("    ", "\t")):
                continue
            kept.append(line)
        items = [_LIST_MARKER.sub("", line) for line in kept]
        is_list = len(items) > 1 and all(_LIST_MARKER.match(line) for line in kept)
        if is_list and all(len(item.split()) <= 4 for item in items):
            continue
        paragraph = _clean_inline(" ".join(item.lstrip("> ").strip() for item in items))
        if len(paragraph.split()) >= 3 and sum(c.isalpha() for c in paragraph) > 10:
            yield paragraph
