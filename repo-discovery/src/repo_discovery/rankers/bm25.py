"""V0 keyword search: BM25F over repository evidence, or BM25 over extractive cards.

``bm25f`` indexes six fields per repository (``name``, ``readme``, ``docs``,
``manifests``, ``paths``, ``identifiers``) in an in-memory SQLite FTS5 table and
scores with FTS5's ``bm25()`` and per-field weights. FTS5 multiplies each field's term
frequencies by its weight and normalizes by the length of the whole row (k1 = 1.2,
b = 0.75): BM25F with one length normalization shared by all fields. Every field is
capped, so a very large repository is not buried by its own length.

``bm25-cards`` runs the same search over one field, the extractive card.

Evidence and prompts are analyzed alike: camelCase and snake_case split, lowercased,
English stopwords, single characters and bare numbers dropped, then Porter-stemmed by
FTS5. Repositories that match no prompt term follow the matches in a seeded random
order, so the tail behaves like the random floor.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from repo_discovery.aliases import owner_stems, tokenize
from repo_discovery.cards import DEFAULT_BUDGET, build_card, strip_markup
from repo_discovery.cases import Query, repo_key
from repo_discovery.contract import Candidate, UniverseView
from repo_discovery.evidence import Evidence, read_evidence, sample_evenly

FIELDS: tuple[str, ...] = ("name", "readme", "docs", "manifests", "paths", "identifiers")
DEFAULT_WEIGHTS: dict[str, float] = {
    "name": 4.0,
    "readme": 2.0,
    "docs": 1.0,
    "manifests": 1.5,
    "paths": 1.0,
    "identifiers": 1.0,
}
FIELD_TOKENS: dict[str, int] = {
    "name": 64,
    "readme": 4_000,
    "docs": 8_000,
    "manifests": 3_000,
    "paths": 8_000,
    "identifiers": 4_000,
}
PATH_LINES = 1_500
# Per-case U10000 universes over the 61 dev cases touch about 47,500 distinct snapshots
# (U5000: 24,000). Holding them all costs about 5 GB at L4.
CACHE_SIZE = 65_536
_FIELD_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
_RESERVED_COLUMNS = {"corpus", "repository", "rank", "rowid"}

STOPWORDS = frozenset(
    """
    about above after again against all also am an and any are as at be because been
    before being below between both but by can could did do does doing done down during
    each etc even ever every few for from further had has have having he her here hers
    him his how however if in into is it its itself just let may me might more most must
    my no nor not now of off on once only or other our ours out over own per same shall
    she should so some such than that the their theirs them then there these they this
    those through thus to too under until up upon us very via was we were what when where
    whether which while who whom whose why will with within without would yet you your
    yours
    """.split()  # noqa: SIM905
)


def analyze(text: str) -> list[str]:
    return [t for t in tokenize(text) if len(t) > 1 and not t.isdigit() and t not in STOPWORDS]


def _capped(texts: Iterable[str], limit: int) -> str:
    tokens: list[str] = []
    for text in texts:
        tokens.extend(analyze(text))
        if len(tokens) >= limit:
            break
    return " ".join(tokens[:limit])


def evidence_fields(evidence: Evidence) -> dict[str, str]:
    """The analyzed, capped text of each BM25F field; ``name`` holds each token once."""
    owner, _, name = evidence.repo.partition("/")
    names = [owner, *sorted(owner_stems(owner)), name, *evidence.package_names]
    texts = {
        "name": [" ".join(dict.fromkeys(analyze(" ".join(names))))],
        "readme": [strip_markup(evidence.readme)],
        "docs": (strip_markup(d.text) for d in evidence.docs),
        "manifests": (m.text for m in evidence.manifests),
        "paths": sample_evenly(list(evidence.paths), PATH_LINES),
        "identifiers": (f.text for f in (*evidence.contracts, *evidence.other)),
    }
    return {f: _capped(texts[f], FIELD_TOKENS[f]) for f in FIELDS}


def candidate_digest(universe: UniverseView) -> str:
    """Names a universe's candidate set: each repository and the evidence it was indexed from."""
    lines = (f"{c.repo}\t{c.evidence_dir.resolve()}\n" for c in universe.candidates)
    return hashlib.sha256("".join(lines).encode()).hexdigest()


class KeywordIndex:
    """An FTS5 table over one universe: one row per repository, one column per field."""

    def __init__(self, fields: Sequence[str], rows: Iterable[tuple[str, Mapping[str, str]]]):
        self.fields = tuple(fields)
        bad = [f for f in self.fields if not _FIELD_NAME.match(f) or f in _RESERVED_COLUMNS]
        if bad or not self.fields:
            raise ValueError(f"field names must be plain identifiers, got {bad or 'none'}")
        self._db = sqlite3.connect(":memory:")
        self._db.execute(
            f"CREATE VIRTUAL TABLE corpus USING fts5(repository UNINDEXED, "
            f"{', '.join(self.fields)}, tokenize = 'porter ascii')"
        )
        self._db.executemany(
            f"INSERT INTO corpus VALUES ({', '.join('?' * (len(self.fields) + 1))})",
            ((repo, *(text.get(f, "") for f in self.fields)) for repo, text in rows),
        )
        self._db.commit()

    def save(self, path: Path, digest: str) -> None:
        """Write the index to ``path``, tagged with ``digest`` (see :func:`candidate_digest`);
        the file appears only once complete."""
        partial = path.with_name(path.name + ".partial")
        partial.unlink(missing_ok=True)
        target = sqlite3.connect(partial)
        try:
            self._db.backup(target)
            target.execute("CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT)")
            target.executemany(
                "INSERT INTO index_meta VALUES (?, ?)",
                [("fields", json.dumps(self.fields)), ("digest", digest)],
            )
            target.commit()
        finally:
            target.close()
        partial.replace(path)

    @classmethod
    def open(cls, path: Path, fields: Sequence[str], digest: str) -> KeywordIndex:
        """The index :meth:`save` wrote to ``path``, read-only; raises ``ValueError`` if it
        was built over other fields or candidates than ``digest`` names."""
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        meta = dict(db.execute("SELECT key, value FROM index_meta"))
        if tuple(json.loads(meta["fields"])) != tuple(fields) or meta["digest"] != digest:
            db.close()
            raise ValueError(f"{path} was built over other fields or candidates")
        index = cls.__new__(cls)
        index.fields, index._db = tuple(fields), db
        return index

    def close(self) -> None:
        self._db.close()

    def search(self, terms: Sequence[str], weights: Mapping[str, float]) -> list[tuple[str, float]]:
        """Repositories matching any term, best first, with their BM25 scores."""
        if not terms:
            return []
        query = " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))
        slots = ", ".join("?" * len(self.fields))
        rows = self._db.execute(
            f"SELECT repository, bm25(corpus, 0.0, {slots}) AS score FROM corpus "
            "WHERE corpus MATCH ? ORDER BY score, repository",
            (*(float(weights.get(f, 1.0)) for f in self.fields), query),
        )
        return [(repo, -score) for repo, score in rows]


class BM25Ranker:
    """BM25F over README, docs, manifests, file paths and identifiers."""

    name = "bm25f"
    fields: tuple[str, ...] = FIELDS

    def __init__(self, seed: int = 0, weights: Mapping[str, float] | None = None) -> None:
        self.seed = seed
        self.weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
        unknown = set(self.weights) - set(self.fields)
        if unknown:
            raise ValueError(f"weights for unknown fields: {sorted(unknown)}")
        self._documents: dict[tuple[str, str], dict[str, str]] = {}
        self._index_key: tuple | None = None
        self._index: KeywordIndex | None = None

    def document(self, evidence: Evidence) -> dict[str, str]:
        return evidence_fields(evidence)

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        matched = [repo for repo, _ in self.search(query, universe)]
        found = set(matched)
        rest = [c.repo for c in universe.candidates if c.repo not in found]
        random.Random(f"{self.seed}:{query.case_id}").shuffle(rest)
        return matched + rest

    def search(self, query: Query, universe: UniverseView) -> list[tuple[str, float]]:
        return self.index(universe).search(analyze(query.prompt), self.weights)

    def index(self, universe: UniverseView) -> KeywordIndex:
        key = (universe.root, universe.candidates)
        if self._index is None or self._index_key != key:
            rows = ((c.repo, self._document(c)) for c in universe.candidates)
            self._index, self._index_key = KeywordIndex(self.fields, rows), key
        return self._index

    def adopt(self, universe: UniverseView, index: KeywordIndex) -> None:
        """Serve ``universe`` from ``index``, such as one :meth:`KeywordIndex.open` loaded."""
        if index.fields != self.fields:
            raise ValueError(f"index fields {index.fields} are not {self.fields}")
        self._index, self._index_key = index, (universe.root, universe.candidates)

    def _document(self, candidate: Candidate) -> dict[str, str]:
        # The evidence directory is named for the commit, so (repo, commit) identifies it
        # even when each case's universe keeps its own copy.
        key = (repo_key(candidate.repo), candidate.evidence_dir.name)
        if key not in self._documents:
            if len(self._documents) >= CACHE_SIZE:
                del self._documents[next(iter(self._documents))]
            evidence = read_evidence(candidate.repo, candidate.evidence_dir)
            self._documents[key] = self.document(evidence)
        return self._documents[key]


class NameBM25Ranker(BM25Ranker):
    """``bm25f`` over names only: owner, owner stems and repository name (fetch level L0)."""

    name = "bm25f-names"

    def _document(self, candidate: Candidate) -> dict[str, str]:
        return self.document(Evidence(candidate.repo))


class CardBM25Ranker(BM25Ranker):
    """BM25 over each repository's extractive card."""

    name = "bm25-cards"
    fields = ("card",)

    def __init__(self, seed: int = 0, budget: int = DEFAULT_BUDGET) -> None:
        super().__init__(seed, weights={"card": 1.0})
        self.budget = budget

    def document(self, evidence: Evidence) -> dict[str, str]:
        return {"card": " ".join(analyze(build_card(evidence, self.budget).text))}
