from __future__ import annotations

import numpy as np
import pytest

from repo_discovery.stats import cluster_bootstrap


def test_mean_and_interval() -> None:
    values = [1, 0, 1, 1, 0, 0, 1, 0]
    clusters = ["a", "a", "b", "b", "c", "c", "d", "d"]
    est = cluster_bootstrap(values, clusters, n_boot=500)
    assert est.mean == pytest.approx(0.5)
    assert 0 <= est.low <= est.mean <= est.high <= 1


def test_deterministic_for_seed() -> None:
    values, clusters = [0.1, 0.4, 0.9, 0.3], ["a", "b", "c", "d"]
    assert cluster_bootstrap(values, clusters, seed=3) == cluster_bootstrap(
        values, clusters, seed=3
    )


def test_single_cluster_has_no_spread() -> None:
    est = cluster_bootstrap([0, 1, 1], ["a", "a", "a"], n_boot=100)
    assert est.low == est.high == pytest.approx(2 / 3)


def test_clustering_widens_interval_for_correlated_cases() -> None:
    rng = np.random.default_rng(0)
    cluster_effects = rng.integers(0, 2, size=10)
    values = np.repeat(cluster_effects, 10).astype(float)
    clustered = cluster_bootstrap(values, np.repeat(np.arange(10).astype(str), 10))
    independent = cluster_bootstrap(values, np.arange(100).astype(str))
    assert clustered.high - clustered.low > 2 * (independent.high - independent.low)


def test_empty_is_an_error() -> None:
    with pytest.raises(ValueError):
        cluster_bootstrap([], [])
