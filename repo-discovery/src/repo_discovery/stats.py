"""Cluster bootstrap for means of per-case values."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Estimate:
    mean: float
    low: float
    high: float


def cluster_bootstrap(
    values: Sequence[float],
    clusters: Sequence[str],
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Estimate:
    """Mean with a percentile CI from resampling whole clusters with replacement.

    Each replicate draws as many clusters as there are, keeps all their cases,
    and takes the case-level mean, so ecosystems with more cases weigh more.
    """
    values_arr = np.asarray(values, dtype=float)
    if values_arr.size == 0:
        raise ValueError("no values to bootstrap")
    _, cluster_index = np.unique(np.asarray(clusters), return_inverse=True)
    sums = np.bincount(cluster_index, weights=values_arr)
    counts = np.bincount(cluster_index)
    draws = np.random.default_rng(seed).integers(0, len(sums), size=(n_boot, len(sums)))
    replicates = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    low, high = np.quantile(replicates, [alpha / 2, 1 - alpha / 2])
    return Estimate(float(values_arr.mean()), float(low), float(high))
