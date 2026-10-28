from __future__ import annotations

import json
from pathlib import Path

from repo_discovery import split
from repo_discovery.cases import Case

MANIFEST = Path(__file__).parents[1] / "splits" / "wideswe-release120.json"


def test_large_ecosystems_go_to_dev() -> None:
    counts = {"big": 21, "large": 11, "edge": 10, **{f"e{i}": 2 for i in range(20)}}
    splits = split.assign_ecosystems(counts)
    assert splits["big"] == splits["large"] == "dev"
    assert set(splits) == set(counts)


def test_dev_fills_to_target_fraction() -> None:
    counts = {f"e{i}": 1 + i % 4 for i in range(40)}
    splits = split.assign_ecosystems(counts, dev_fraction=0.5)
    dev = sum(n for e, n in counts.items() if splits[e] == "dev")
    total = sum(counts.values())
    assert 0.5 * total <= dev < 0.5 * total + max(counts.values())


def test_split_is_deterministic_and_seeded() -> None:
    counts = {f"e{i}": 1 + i % 3 for i in range(30)}
    assert split.assign_ecosystems(counts) == split.assign_ecosystems(
        dict(reversed(counts.items()))
    )
    assert split.assign_ecosystems(counts, seed=1) != split.assign_ecosystems(counts, seed=2)


def test_assign_splits_labels_every_case(mini_cases: list[Case]) -> None:
    labelled = split.assign_splits(mini_cases)
    by_ecosystem = {}
    for case in labelled:
        assert case.split in ("dev", "test")
        assert by_ecosystem.setdefault(case.ecosystem, case.split) == case.split


def test_committed_manifest_matches_seed() -> None:
    data = json.loads(MANIFEST.read_text())
    counts = {e: v["cases"] for e, v in data["ecosystems"].items()}
    assert data["seed"] == split.SEED
    assert data["dev_fraction"] == split.DEV_FRACTION
    assert data["large_ecosystem"] == split.LARGE_ECOSYSTEM
    assert split.assign_ecosystems(counts) == {e: v["split"] for e, v in data["ecosystems"].items()}
    assert sum(counts.values()) == 120
    assert all(data["ecosystems"][e]["split"] == "dev" for e, n in counts.items() if n > 10)
