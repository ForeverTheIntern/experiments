"""Replace owner and repository names with stable random IDs, in names and in free text.

:class:`Pseudonyms` maps every ``owner/name`` to ``o<hex>/r<hex>``, keyed by an HMAC of
the lowercased name, so the mapping is stable for a salt and reveals nothing without it.
Repositories of one owner share its owner ID. A repository named after its owner
(``getsentry/sentry``, ``sveltejs/svelte``) takes the owner ID as its name too, so a bare
``Sentry`` reads the same whether it meant the owner or the repository, as it does for
:mod:`repo_discovery.aliases`.

:class:`Scrubber` rewrites mentions in text with the aliases of
:mod:`repo_discovery.aliases` and the same tokenization: full and owner-qualified names
become ``o<hex>/r<hex>``, bare names ``r<hex>`` and owner names or stems ``o<hex>``. Text
belongs to a context repository, and an alias the context claims wins: inside
``getsentry/sentry-cli``'s packet, ``sentry`` is its owner.

Some aliases are everyday words elsewhere (``common``: ``github`` for the many
``.github`` repositories, ``release``, ``tsconfig``). They are replaced in the packet of
the repository they name, and in packets of the same owner only where they are the
owner's flagship (``flagships``: pairs of owner and alias, such as ``elasticsearch`` in
Elastic's packets, found by :class:`AliasFrequency` lift); elsewhere they stay. Aliases in
``proper`` are replaced only where written with a capital, as a proper noun (``Stream``,
not ``data stream``). When several repositories claim an alias equally and none is the
context's, the mention becomes ``[redacted]``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import pairwise

from repo_discovery.aliases import _CAMEL_BOUNDARY, AliasKind, owner_stems, repo_aliases
from repo_discovery.cases import repo_key

REDACTED = "[redacted]"
OWNER = 0
_RUN = re.compile(r"[A-Za-z0-9]+")
_MAX_WINDOW_TOKENS = 8
_KIND_NAMES = {OWNER: "owner", **{k: k.name.lower() for k in AliasKind}}


def token_spans(text: str) -> list[tuple[int, int, str]]:
    """The tokens of :func:`repo_discovery.aliases.tokenize`, with their character spans."""
    spans = []
    for run in _RUN.finditer(text):
        piece, start = run.group(), run.start()
        cuts = [0, *(m.start() for m in _CAMEL_BOUNDARY.finditer(piece)), len(piece)]
        spans.extend((start + a, start + b, piece[a:b].lower()) for a, b in pairwise(cuts) if b > a)
    return spans


def _joined(text: str) -> str:
    return "".join(t for _, _, t in token_spans(text))


def keyed_digest(salt: bytes, label: str, key: str, length: int) -> str:
    return hmac.new(salt, f"{label}\0{key}".encode(), hashlib.sha256).hexdigest()[:length]


@dataclass(frozen=True)
class Pseudonyms:
    """Repository key -> anonymized ``owner/name``, and owner key -> anonymized owner."""

    repos: dict[str, str]
    owners: dict[str, str]

    @classmethod
    def build(cls, repos: Iterable[str], salt: bytes, length: int = 8) -> Pseudonyms:
        keys = sorted({repo_key(r) for r in repos})
        owners: dict[str, str] = {}
        for owner in sorted({k.partition("/")[0] for k in keys}):
            n = length
            while (alias := "o" + keyed_digest(salt, "owner", owner, n)) in owners.values():
                n += 1
            owners[owner] = alias
        mapped: dict[str, str] = {}
        used: set[str] = set()
        for key in keys:
            owner, _, name = key.partition("/")
            stems = {_joined(s) for s in owner_stems(owner)}
            name_id = owners[owner] if _joined(name) in stems else ""
            n = length
            while not name_id or f"{owners[owner]}/{name_id}" in used:
                name_id = "r" + keyed_digest(salt, "repo", key, n)
                n += 1
            mapped[key] = f"{owners[owner]}/{name_id}"
            used.add(mapped[key])
        return cls(mapped, owners)

    def repo(self, repo: str) -> str:
        return self.repos[repo_key(repo)]

    def owner(self, owner: str) -> str:
        return self.owners[owner.lower()]


@dataclass(frozen=True)
class _Claim:
    kind: int
    owner: str
    repo: str | None
    replacement: str


@dataclass
class ScrubStats:
    """Replacements by alias kind and by whose alias it was, relative to the context."""

    by_kind: Counter = field(default_factory=Counter)
    by_scope: Counter = field(default_factory=Counter)
    by_alias: Counter = field(default_factory=Counter)
    redacted: int = 0

    def add(self, other: ScrubStats) -> None:
        self.by_kind.update(other.by_kind)
        self.by_scope.update(other.by_scope)
        self.by_alias.update(other.by_alias)
        self.redacted += other.redacted

    def to_dict(self, top: int | None = None) -> dict:
        return {
            "by_kind": dict(self.by_kind),
            "by_scope": dict(self.by_scope),
            "by_alias": dict(self.by_alias.most_common(top)),
            "redacted": self.redacted,
        }


class Scrubber:
    def __init__(
        self,
        pseudonyms: Pseudonyms,
        common: Iterable[str] = (),
        flagships: Iterable[tuple[str, str]] = (),
        proper: Iterable[str] = (),
    ) -> None:
        self.common = frozenset(common)
        self.flagships = frozenset(flagships)
        self.proper = frozenset(proper)
        self.claims: dict[str, list[_Claim]] = {}
        for key, anon in pseudonyms.repos.items():
            owner = key.partition("/")[0]
            for alias, kind in repo_aliases(key).items():
                text = anon if kind in (AliasKind.FULL, AliasKind.QUALIFIED) else anon.split("/")[1]
                self.claims.setdefault(alias, []).append(_Claim(kind, owner, key, text))
        for owner, anon in pseudonyms.owners.items():
            for stem in {_joined(s) for s in owner_stems(owner)}:
                self.claims.setdefault(stem, []).append(_Claim(OWNER, owner, None, anon))
        self.prefixes = {alias[:i] for alias in self.claims for i in range(1, len(alias) + 1)}

    def aliases_of(self, owner: str) -> set[str]:
        return {a for a, claims in self.claims.items() if any(c.owner == owner for c in claims)}

    def _choose(self, alias: str, repo: str | None, owner: str | None) -> tuple[str, str] | None:
        claims = self.claims[alias]
        common = alias in self.common
        flagship = (owner, alias) in self.flagships
        for scope, pool in (
            ("own", [c for c in claims if repo is not None and c.repo == repo]),
            ("owner", [] if common and not flagship else [c for c in claims if c.owner == owner]),
            ("other", [] if common else claims),
        ):
            if pool:
                best = max(c.kind for c in pool)
                replacements = {c.replacement for c in pool if c.kind == best}
                kind = _KIND_NAMES[best]
                if len(replacements) > 1:
                    return REDACTED, f"{scope}:{kind}"
                return replacements.pop(), f"{scope}:{kind}"
        return None

    def scrub(self, text: str, context: str | None = None, stats: ScrubStats | None = None) -> str:
        """``text`` with known names replaced; ``context`` is the repository it belongs to."""
        repo = repo_key(context) if context else None
        owner = repo.partition("/")[0] if repo else None
        tokens = token_spans(text)
        out, last, i = [], 0, 0
        while i < len(tokens):
            joined, found = "", None
            for j in range(i, min(i + _MAX_WINDOW_TOKENS, len(tokens))):
                joined += tokens[j][2]
                if joined not in self.prefixes:
                    break
                if joined in self.proper and text[tokens[i][0] : tokens[j][1]].islower():
                    continue
                if joined in self.claims and (choice := self._choose(joined, repo, owner)):
                    found = j, joined, choice
            if found is None:
                i += 1
                continue
            j, alias, (replacement, label) = found
            start, end = tokens[i][0], tokens[j][1]
            if start > 0 and text[start - 1].isascii() and text[start - 1].isalnum():
                replacement = replacement[0].upper() + replacement[1:]
            out.append(text[last:start])
            out.append(replacement)
            last, i = end, j + 1
            if stats is not None:
                scope, _, kind = label.partition(":")
                stats.by_kind[kind] += 1
                stats.by_scope[scope] += 1
                stats.by_alias[f"{scope}:{alias}"] += 1
                stats.redacted += replacement == REDACTED
        out.append(text[last:])
        return "".join(out)


def aliases_in(scrubber: Scrubber, text: str) -> set[str]:
    """Every alias the scrubber knows that appears in ``text``, nested ones included."""
    tokens = [t for _, _, t in token_spans(text)]
    seen = set()
    for i in range(len(tokens)):
        joined = ""
        for j in range(i, min(i + _MAX_WINDOW_TOKENS, len(tokens))):
            joined += tokens[j]
            if joined not in scrubber.prefixes:
                break
            if joined in scrubber.claims:
                seen.add(joined)
    return seen


@dataclass
class AliasFrequency:
    """How many documents (one per repository) mention each alias, by whose they are.

    ``other`` counts documents of owners that claim none of the alias, ``owner`` counts,
    per ``(owner, alias)``, the documents of a claiming owner's repositories other than
    the claimants themselves.
    """

    docs: int = 0
    other: Counter = field(default_factory=Counter)
    owner: Counter = field(default_factory=Counter)
    owner_docs: Counter = field(default_factory=Counter)
    claimant_docs: Counter = field(default_factory=Counter)

    @classmethod
    def count(cls, scrubber: Scrubber, docs: Iterable[tuple[str, set[str]]]) -> AliasFrequency:
        """``docs`` are ``(repository, aliases_in(its text))`` pairs."""
        freq = cls()
        claimed: dict[str, set[str]] = {}
        for alias, claims in scrubber.claims.items():
            for c in claims:
                if c.repo is not None:
                    claimed.setdefault(c.repo, set()).add(alias)
        for repo, aliases in docs:
            key = repo_key(repo)
            owner = key.partition("/")[0]
            own = claimed.get(key, set())
            freq.docs += 1
            freq.owner_docs[owner] += 1
            freq.claimant_docs.update((owner, alias) for alias in own)
            for alias in aliases:
                if all(c.owner != owner for c in scrubber.claims[alias]):
                    freq.other[alias] += 1
                elif alias not in own:
                    freq.owner[owner, alias] += 1
        return freq

    def share(self, alias: str) -> float:
        return self.other[alias] / self.docs if self.docs else 0.0

    def lift(self, owner: str, alias: str) -> float:
        """How much likelier the owner's other repositories mention it than other owners'."""
        own_docs = self.owner_docs[owner] - self.claimant_docs[owner, alias]
        rest = self.docs - self.owner_docs[owner]
        if own_docs <= 0 or not self.owner[owner, alias]:
            return 0.0
        if rest <= 0 or not self.other[alias]:
            return float("inf")
        return (self.owner[owner, alias] / own_docs) / (self.other[alias] / rest)
