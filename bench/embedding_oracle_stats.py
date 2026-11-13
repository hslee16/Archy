"""Intervals for the #587 verdict: Wilson for proportions, a PR-clustered bootstrap for precision.

Kept apart from the labels and the verdict so each stays small enough to read in one go.
The bootstrap constants are fixed here and recorded in the post-pilot addendum.

archy:owns        cluster_bootstrap, interval, ratio, wilson
archy:mirrored-by ratio -> bench.embedding_oracle_score, bench.embedding_oracle_share
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 587
Z95 = 1.959963984540054


def wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% interval for k of n; the whole unit interval when n is 0."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + Z95**2 / n
    centre = (p + Z95**2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def ratio(k: int, n: int) -> float | None:
    """k of n, or None when n is 0 (a rate over nothing is not 0)."""
    return k / n if n else None


def interval(k: int, n: int) -> tuple[float | None, float | None]:
    """Wilson for k of n, or (None, None) when n is 0."""
    return wilson(k, n) if n else (None, None)


def cluster_bootstrap(clusters: Sequence[Sequence[bool]]) -> tuple[float | None, float | None]:
    """Percentile interval of precision, resampling whole PRs with a fixed seed."""
    if not clusters:
        return None, None
    rng = random.Random(BOOTSTRAP_SEED)
    stats: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        draw = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        total = sum(len(c) for c in draw)
        if total:
            stats.append(sum(sum(c) for c in draw) / total)
    stats.sort()
    return stats[int(0.025 * len(stats))], stats[min(len(stats) - 1, int(0.975 * len(stats)))]
