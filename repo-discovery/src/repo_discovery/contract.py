"""Reader for the universe directory contract shared with the universe builder.

A universe is a directory holding ``universe.jsonl``, one repository per line::

    {"repo": "owner/name", "commit": "<sha>", "snapshot_date": "YYYY-MM-DD",
     "role": "gold|ecosystem|distractor"}

Each repository's extracted evidence (README and docs, manifests, a file-path
list, API contract files) lives under ``repos/<owner>__<name>/<commit>/``.

Rankers get a ``UniverseView``: repository names and evidence paths only, sorted
by name, so neither ``role`` nor snapshot dates nor file order can leak gold.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from repo_discovery.cases import repo_key

Role = Literal["gold", "ecosystem", "distractor"]
ROLES: tuple[Role, ...] = ("gold", "ecosystem", "distractor")
UNIVERSE_FILE = "universe.jsonl"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def evidence_dir(root: Path, repo: str, commit: str) -> Path:
    owner, name = repo.split("/")
    return root / "repos" / f"{owner}__{name}" / commit


@dataclass(frozen=True)
class UniverseEntry:
    repo: str
    commit: str
    snapshot_date: str
    role: Role

    @classmethod
    def from_dict(cls, data: dict, where: str) -> UniverseEntry:
        missing = {"repo", "commit", "snapshot_date", "role"} - data.keys()
        if missing:
            raise ValueError(f"{where}: missing {sorted(missing)}")
        if not _REPO.match(data["repo"]):
            raise ValueError(f"{where}: repo must be owner/name, got {data['repo']!r}")
        if data["role"] not in ROLES:
            raise ValueError(f"{where}: role must be one of {ROLES}, got {data['role']!r}")
        date.fromisoformat(data["snapshot_date"])
        return cls(data["repo"], data["commit"], data["snapshot_date"], data["role"])


@dataclass(frozen=True)
class Candidate:
    repo: str
    evidence_dir: Path


@dataclass(frozen=True)
class UniverseView:
    root: Path
    candidates: tuple[Candidate, ...]

    def __len__(self) -> int:
        return len(self.candidates)


@dataclass(frozen=True)
class Universe:
    root: Path
    entries: tuple[UniverseEntry, ...]

    @classmethod
    def load(cls, root: Path) -> Universe:
        path = root / UNIVERSE_FILE
        entries, seen = [], set()
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                entry = UniverseEntry.from_dict(json.loads(line), f"{path}:{number}")
                if repo_key(entry.repo) in seen:
                    raise ValueError(f"{path}:{number}: duplicate repo {entry.repo}")
                seen.add(repo_key(entry.repo))
                entries.append(entry)
        return cls(root, tuple(entries))

    def __len__(self) -> int:
        return len(self.entries)

    def keys(self) -> set[str]:
        return {repo_key(e.repo) for e in self.entries}

    def evidence_dir(self, entry: UniverseEntry) -> Path:
        return evidence_dir(self.root, entry.repo, entry.commit)

    def missing_evidence(self) -> list[str]:
        return [e.repo for e in self.entries if not self.evidence_dir(e).is_dir()]

    def view(self) -> UniverseView:
        ordered = sorted(self.entries, key=lambda e: repo_key(e.repo))
        return UniverseView(
            self.root, tuple(Candidate(e.repo, self.evidence_dir(e)) for e in ordered)
        )


def resolve_universe(root: Path, case_id: str) -> Path:
    """``root`` if it holds ``universe.jsonl`` (shared by all cases), else ``root/<case_id>``."""
    if (root / UNIVERSE_FILE).is_file():
        return root
    per_case = root / case_id
    if (per_case / UNIVERSE_FILE).is_file():
        return per_case
    raise FileNotFoundError(f"no {UNIVERSE_FILE} in {root} or {per_case}")
