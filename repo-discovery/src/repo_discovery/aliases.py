"""Automated repository-name aliases and matching them in free text.

Text and aliases are compared as runs of lowercase alphanumeric tokens, after
splitting camelCase, so ``huggingface_hub``, ``HuggingFace Hub`` and
``huggingface-hub`` all match. An alias matches when the concatenation of
consecutive text tokens equals the alias's concatenated tokens.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from repo_discovery.cases import repo_key

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_TOKEN = re.compile(r"[a-z0-9]+")

# Single-token repository names that are ordinary words in change requests. They
# only match in owner-qualified form ("nextcloud server", "sentry cli").
GENERIC_NAMES = frozenset(
    """
    actions ai android api app apps backend bridge build calendar cli client common
    community config core docs documentation engine examples framework frontend go
    infra integrations java javascript kit lib libs mcp node packages packaging
    persistence php plugin plugins polyfill python router ruby rust rl sdk server
    service site spec tools ui utils ux web website
    """.split()  # noqa: SIM905
)

_LANGUAGE_EXPANSIONS = {"js": "javascript", "py": "python", "ts": "typescript"}
_OWNER_PREFIXES = ("get",)
_OWNER_SUFFIXES = ("-community", "-lang", "-api", "graphql", "js", "ci", "hq")
_MAX_WINDOW_TOKENS = 8


class AliasKind(IntEnum):
    """Alias strength; when two repositories share an alias, the stronger claim wins."""

    QUALIFIED = 1
    NAME = 2
    FULL = 3


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(_CAMEL_BOUNDARY.sub(" ", text).lower())


def _joined(text: str) -> str:
    return "".join(tokenize(text))


def owner_stems(owner: str) -> set[str]:
    """``getsentry`` -> {getsentry, sentry}; ``sveltejs`` -> {sveltejs, svelte}."""
    lowered = owner.lower()
    stems = {lowered}
    for prefix in _OWNER_PREFIXES:
        if lowered.startswith(prefix) and len(lowered) - len(prefix) >= 4:
            stems.add(lowered[len(prefix) :])
    for suffix in _OWNER_SUFFIXES:
        if lowered.endswith(suffix) and len(lowered) - len(suffix) >= 3:
            stems.add(lowered[: -len(suffix)].rstrip("-"))
    return stems


def _language_variants(name_tokens: list[str]) -> list[list[str]]:
    expanded = [_LANGUAGE_EXPANSIONS.get(t, t) for t in name_tokens]
    return [expanded] if expanded != name_tokens else []


def repo_aliases(repo: str) -> dict[str, AliasKind]:
    """All aliases of ``owner/name`` as concatenated token strings, with their kind."""
    owner, _, name = repo.partition("/")
    name_tokens = tokenize(name)
    aliases: dict[str, AliasKind] = {}

    def add(alias: str, kind: AliasKind) -> None:
        if alias and kind > aliases.get(alias, 0):
            aliases[alias] = kind

    add(_joined(owner) + "".join(name_tokens), AliasKind.FULL)
    generic = len(name_tokens) == 1 and name_tokens[0] in GENERIC_NAMES
    name_forms = [name_tokens, *_language_variants(name_tokens)]
    for form in name_forms:
        if not generic:
            add("".join(form), AliasKind.NAME)
        for stem in owner_stems(owner):
            add(_joined(stem) + "".join(form), AliasKind.QUALIFIED)
    if any("".join(name_tokens) == _joined(stem) for stem in owner_stems(owner)):
        add(_joined(owner), AliasKind.NAME)
    return aliases


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    repo: str
    kind: AliasKind


@dataclass(frozen=True)
class AliasIndex:
    """Aliases for a set of repositories.

    A shared alias goes to the strongest claim: ``sentry cli`` is a qualified
    alias of ``getsentry/cli`` but the name of ``getsentry/sentry-cli``, so when
    both are indexed only the latter keeps it.
    """

    by_alias: dict[str, tuple[tuple[str, AliasKind], ...]]

    @classmethod
    def build(cls, repos: Iterable[str]) -> AliasIndex:
        claims: dict[str, list[tuple[str, AliasKind]]] = defaultdict(list)
        for repo in dict.fromkeys(repos):
            for alias, kind in repo_aliases(repo).items():
                claims[alias].append((repo, kind))
        by_alias = {}
        for alias, holders in claims.items():
            best = max(kind for _, kind in holders)
            by_alias[alias] = tuple((repo, kind) for repo, kind in holders if kind == best)
        return cls(by_alias)

    def _spans(self, tokens: list[str]) -> list[_Span]:
        spans = []
        for start in range(len(tokens)):
            joined = ""
            for end in range(start, min(start + _MAX_WINDOW_TOKENS, len(tokens))):
                joined += tokens[end]
                for repo, kind in self.by_alias.get(joined, ()):
                    spans.append(_Span(start, end + 1, repo, kind))
        return spans

    def matches(self, text: str) -> dict[str, AliasKind]:
        """Repositories mentioned in ``text``, each with its strongest matching alias kind.

        A match nested inside a longer match of another repository does not count:
        "SvelteKit" mentions ``sveltejs/kit``, not ``sveltejs/svelte``.
        """
        spans = self._spans(tokenize(text))
        found: dict[str, AliasKind] = {}
        for span in spans:
            nested = any(
                other.repo != span.repo
                and other.start <= span.start
                and span.end <= other.end
                and other.end - other.start > span.end - span.start
                for other in spans
            )
            if not nested and span.kind > found.get(span.repo, 0):
                found[span.repo] = span.kind
        return found


def mentioned(text: str, repos: Iterable[str], universe: Iterable[str] = ()) -> set[str]:
    """Keys of ``repos`` mentioned in ``text``.

    ``universe`` adds repositories that compete for shared aliases but are not reported.
    """
    targets = {repo_key(r): r for r in repos}
    pool = {repo_key(r): r for r in universe} | targets
    found = AliasIndex.build(pool.values()).matches(text)
    return {repo_key(r) for r in found if repo_key(r) in targets}
