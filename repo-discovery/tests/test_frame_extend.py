from __future__ import annotations

import random
import re

import pytest

from repo_discovery.universe import frame

WINDOW = 20


class FakeGitHub:
    """Each creation window holds ``WINDOW`` repositories whose ids encode the window."""

    def __init__(self, n_windows: int) -> None:
        self.total = n_windows * WINDOW
        self.index = {frame._iso(lo): i for i, (lo, _) in enumerate(frame.windows(n_windows))}
        self.requests = 0

    def search_count(self, query: str) -> int:
        return self.total

    def search_repositories(self, query: str, page: int = 1, per_page: int = 100) -> dict:
        self.requests += 1
        lo = re.search(r"created:(\S+)\.\.", query).group(1)
        window = [self.index[lo]] if lo in self.index else range(len(self.index))
        items = [
            {"full_name": f"o{i}/r{j}", "id": i * 1000 + j} for i in window for j in range(WINDOW)
        ]
        return {"total_count": len(items), "incomplete_results": False, "items": items}


def _draw(gh: FakeGitHub, target: int) -> frame.CellSample:
    rng = random.Random("seed:Go:small")
    return frame.sample_cell(gh, "Go", "small", target, rng, cluster_size=WINDOW)


def test_extending_a_cell_matches_a_fresh_draw_at_the_larger_target() -> None:
    gh = FakeGitHub(n_windows=60)
    small, large = _draw(gh, 70), _draw(gh, 210)
    seen = {item["id"] for item in small.items}
    clusters = {item["cluster"] for item in small.items}
    items, drawn = frame.extend_cell(
        gh, small.summary(), 210, random.Random("seed:Go:small"), seen, clusters
    )
    assert small.items + items == large.items
    assert drawn == large.windows_drawn == 11


def test_extending_with_the_wrong_seed_is_refused() -> None:
    gh = FakeGitHub(n_windows=60)
    small = _draw(gh, 70)
    clusters = {item["cluster"] for item in small.items}
    with pytest.raises(AssertionError, match="does not match"):
        frame.extend_cell(gh, small.summary(), 210, random.Random("other"), set(), clusters)


def test_a_census_cell_is_not_extended() -> None:
    gh = FakeGitHub(n_windows=2)
    census = _draw(gh, 70)
    before = gh.requests
    assert census.design == "census"
    assert frame.extend_cell(gh, census.summary(), 210, random.Random(0), set(), {0}) == ([], 1)
    assert gh.requests == before
