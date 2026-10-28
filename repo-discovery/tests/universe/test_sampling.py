from __future__ import annotations

import random
from collections import Counter
from itertools import pairwise

from repo_discovery.universe.frame import allocation, cell_query, windows
from repo_discovery.universe.sampling import interleave, order_pool, topic_group


def test_interleave_prefixes_track_weights() -> None:
    groups = {g: [f"{g}{i}" for i in range(400)] for g in "abc"}
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    merged = interleave(groups, weights, random.Random(1))
    assert sorted(merged) == sorted(x for g in groups.values() for x in g)
    for n in (10, 50, 250, 600):
        counts = Counter(x[0] for x in merged[:n])
        for g, w in weights.items():
            assert abs(counts[g] - n * w) <= 1
        for g in groups:
            taken = [x for x in merged[:n] if x[0] == g]
            assert taken == groups[g][: len(taken)]


def test_interleave_puts_zero_weight_last() -> None:
    merged = interleave({"a": [1, 2], "z": [9]}, {"a": 1.0, "z": 0.0}, random.Random(0))
    assert merged == [1, 2, 9]


def test_order_pool_ranks_and_mixes_cells() -> None:
    def items(cell: str, n: int) -> list[dict]:
        return [{"repo": f"o/{cell}{i}", "topic_group": "untagged"} for i in range(n)]

    cells = {("Go", "small"): items("g", 30), ("Rust", "large"): items("r", 30)}
    ordered = order_pool(cells, {("Go", "small"): 0.75, ("Rust", "large"): 0.25}, random.Random(3))
    assert [i["rank"] for i in ordered] == list(range(60))
    assert sum(i["repo"].startswith("o/g") for i in ordered[:20]) == 15


def test_topic_group() -> None:
    assert topic_group([]) == "untagged"
    assert topic_group(["kubernetes", "helm", "cli"]) == "infra-devops"
    assert topic_group(["knitting"]) == "other"


def test_windows_partition_the_frame() -> None:
    slots = windows(7)
    assert len(slots) == 7
    for (_, hi), (lo, _) in pairwise(slots):
        assert (lo - hi).total_seconds() == 1


def test_allocation_is_product_of_marginals() -> None:
    meta = [
        {"language": "Go", "size_kb": 100},
        {"language": "Go", "size_kb": 200_000},
        {"language": "Elixir", "size_kb": 10_000},
        {"language": None, "size_kb": 10_000},
    ]
    shares, lang, size = allocation(meta)
    assert lang["Go"] == 0.5 and lang["Other"] == 0.5
    assert size == {"small": 0.25, "medium": 0.5, "large": 0.25}
    assert shares[("Go", "medium")] == 0.25
    assert abs(sum(shares.values()) - 1) < 1e-9


def test_cell_query_other_excludes_named_languages() -> None:
    q = cell_query("Other", "large")
    assert "-language:go" in q and "size:>=100000" in q and "stars:>=10" in q
