"""The near-miss overlap is computed without building new bags (#812).

Faster must mean the same numbers: these pin them against the definition,
|a & b| / |a | b| over token counts.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from archy.complexity import FunctionComplexity
from archy.duplicates import _Candidate, _cluster_by_similarity, _jaccard


def test_jaccard_by_hand():
    """a = {x:3, y:1, only_a:2}, b = {x:1, y:4, only_b:5}.

    Overlap takes the smaller count of each shared token: x 1 + y 1 = 2.
    Union takes the larger: x 3 + y 4 + only_a 2 + only_b 5 = 14.
    """
    a = Counter({"x": 3, "y": 1, "only_a": 2})
    b = Counter({"x": 1, "y": 4, "only_b": 5})

    assert _jaccard(a, b) == _jaccard(b, a) == 2 / 14
    assert _jaccard(a, Counter({"z": 4})) == 0.0
    assert _jaccard(a, Counter()) == 0.0


def _random_bags(count: int) -> list[Counter[str]]:
    rng = random.Random(812)
    return [
        Counter({t: rng.randint(1, 6) for t in rng.sample("abcdefgh", rng.randint(1, 8))})
        for _ in range(count)
    ]


def _by_definition(a: Counter[str], b: Counter[str]) -> float:
    return sum((a & b).values()) / sum((a | b).values())


def test_jaccard_matches_the_definition_on_random_bags():
    bags = _random_bags(120)

    for a in bags:
        for b in bags:
            assert _jaccard(a, b) == _by_definition(a, b)


@pytest.mark.parametrize("floor", [0.3, 0.5, 0.85, 1.0])
def test_skipping_hopeless_pairs_drops_no_edge(floor: float):
    """Every function the same size, so only the bag totals can rule a pair out."""
    bags = [*_random_bags(80), Counter("aabb"), Counter("aabb")]
    items = [
        _Candidate(
            str(i),
            f"{i}.py",
            FunctionComplexity(
                name="f", qualified_name="f", line=1, cyclomatic=1, shape_hash=str(i), size=10
            ),
            bag,
        )
        for i, bag in enumerate(bags)
    ]
    expected = {
        frozenset((i, j)): _by_definition(bags[i], bags[j])
        for i in range(len(bags))
        for j in range(i + 1, len(bags))
        if _by_definition(bags[i], bags[j]) >= floor
    }
    assert expected, "fixture drifted: nothing reaches the floor"

    _components, weights, truncated = _cluster_by_similarity(items, floor, 1_000_000)

    assert not truncated
    assert weights == expected
