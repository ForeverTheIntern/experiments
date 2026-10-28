"""Benchmark case schema and JSONL storage."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

Visibility = Literal["none", "partial", "complete"]
Split = Literal["dev", "test"]

VISIBILITIES: tuple[Visibility, ...] = ("none", "partial", "complete")
SPLITS: tuple[Split, ...] = ("dev", "test")


def repo_key(repo: str) -> str:
    """Case-insensitive identity for an ``owner/name`` string, as GitHub treats it."""
    return repo.strip().lower()


@dataclass(frozen=True)
class RepoAtCommit:
    repo: str
    commit: str


@dataclass(frozen=True)
class Case:
    """One change request and the repositories it needed.

    ``gold`` holds the target repositories at their base commits. ``cutoff`` is the
    earliest base-commit time (ISO 8601): nothing after it may inform a ranking.
    ``context`` lists same-ecosystem repositories that did not change, at their
    snapshot commits.
    """

    case_id: str
    source: str
    ecosystem: str
    prompt: str
    gold: tuple[RepoAtCommit, ...]
    cutoff: str
    context: tuple[RepoAtCommit, ...] = ()
    category: str | None = None
    scope_visibility: Visibility | None = None
    visible_gold: tuple[str, ...] = ()
    split: Split | None = None

    def __post_init__(self) -> None:
        if not self.gold:
            raise ValueError(f"{self.case_id}: a case needs at least one gold repository")
        keys = [repo_key(g.repo) for g in self.gold]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.case_id}: duplicate gold repositories")
        datetime.fromisoformat(self.cutoff)
        if self.scope_visibility is not None and self.scope_visibility not in VISIBILITIES:
            raise ValueError(f"{self.case_id}: bad scope_visibility {self.scope_visibility!r}")
        if self.split is not None and self.split not in SPLITS:
            raise ValueError(f"{self.case_id}: bad split {self.split!r}")

    @property
    def gold_repos(self) -> tuple[str, ...]:
        return tuple(g.repo for g in self.gold)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Case:
        data = dict(data)
        data["gold"] = tuple(RepoAtCommit(**g) for g in data["gold"])
        data["context"] = tuple(RepoAtCommit(**c) for c in data.get("context", ()))
        data["visible_gold"] = tuple(data.get("visible_gold", ()))
        return cls(**data)


@dataclass(frozen=True)
class Query:
    """The part of a case a ranker may see: no gold, ecosystem, or visibility label."""

    case_id: str
    prompt: str
    cutoff: str

    @classmethod
    def from_case(cls, case: Case) -> Query:
        return cls(case_id=case.case_id, prompt=case.prompt, cutoff=case.cutoff)


def write_cases(path: Path, cases: Iterable[Case]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    return count


def iter_cases(path: Path) -> Iterator[Case]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield Case.from_dict(json.loads(line))


def read_cases(path: Path) -> list[Case]:
    cases = list(iter_cases(path))
    ids = [c.case_id for c in cases]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate case_id")
    return cases


def select_cases(
    cases: Iterable[Case],
    split: Split | None = None,
    visibility: Iterable[Visibility] | None = None,
) -> list[Case]:
    allowed = set(visibility) if visibility is not None else None
    return [
        c
        for c in cases
        if (split is None or c.split == split)
        and (allowed is None or c.scope_visibility in allowed)
    ]
