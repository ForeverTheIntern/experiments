from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from repo_discovery import wideswe
from repo_discovery.cases import Case
from repo_discovery.contract import evidence_dir

FIXTURES = Path(__file__).parent / "fixtures"
WIDESWE_MINI = FIXTURES / "wideswe_mini"

UniverseFactory = Callable[..., Path]


def write_universe(root: Path, entries: Iterable[dict]) -> Path:
    """Write a universe directory following the shared contract, with a stub evidence packet."""
    entries = list(entries)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "universe.jsonl").open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")
    for entry in entries:
        packet = evidence_dir(root, entry["repo"], entry["commit"])
        packet.mkdir(parents=True, exist_ok=True)
        (packet / "README.md").write_text(f"# {entry['repo']}\n", encoding="utf-8")
        (packet / "paths.txt").write_text("README.md\nsrc/main.py\n", encoding="utf-8")
    return root


def universe_for(case: Case, n_distractors: int) -> list[dict]:
    def entry(repo: str, commit: str, role: str) -> dict:
        return {"repo": repo, "commit": commit, "snapshot_date": "2026-02-28", "role": role}

    return [
        *(entry(g.repo, g.commit, "gold") for g in case.gold),
        *(entry(c.repo, c.commit, "ecosystem") for c in case.context),
        *(
            entry(f"noise{i:03d}/repo{i:03d}", f"{i:040x}", "distractor")
            for i in range(n_distractors)
        ),
    ]


@pytest.fixture
def make_universe(tmp_path: Path) -> UniverseFactory:
    def make(entries: Iterable[dict], name: str = "universe") -> Path:
        return write_universe(tmp_path / name, entries)

    return make


@pytest.fixture
def wideswe_mini() -> Path:
    """A synthetic directory shaped like a WideSWE checkout: three invented cases."""
    return WIDESWE_MINI


@pytest.fixture
def mini_cases() -> list[Case]:
    return wideswe.load(WIDESWE_MINI)


@pytest.fixture
def tiny_universes(tmp_path: Path, mini_cases: list[Case]) -> Path:
    """One universe per mini case: its gold, its ecosystem repos, and 120 distractors."""
    root = tmp_path / "universes"
    for case in mini_cases:
        write_universe(root / case.case_id, universe_for(case, n_distractors=120))
    return root
