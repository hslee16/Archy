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


def _item(name: str, bag: Counter[str]) -> _Candidate:
    """Every function the same size, so only the bag totals can rule a pair out."""
    fn = FunctionComplexity(
        name="f", qualified_name="f", line=1, cyclomatic=1, shape_hash=name, size=10
    )
    return _Candidate(name, f"{name}.py", fn, bag)


@pytest.mark.parametrize("floor", [0.3, 0.5, 0.85, 1.0])
def test_skipping_hopeless_pairs_drops_no_edge(floor: float):
    """Every function the same size, so only the bag totals can rule a pair out."""
    bags = [*_random_bags(80), Counter("aabb"), Counter("aabb")]
    items = [_item(str(i), bag) for i, bag in enumerate(bags)]
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


@pytest.mark.parametrize(
    ("floor", "smaller", "larger"),
    [(0.28, 7, 25), (0.56, 14, 25), (0.55, 55, 100), (0.07, 7, 100), (0.81, 81, 100)],
)
def test_a_pair_exactly_on_the_floor_is_kept(floor: float, smaller: int, larger: int):
    """`smaller / larger` equals the floor, while `floor * larger` rounds just past it."""
    items = [_item("a", Counter(x=smaller)), _item("b", Counter(x=larger))]
    assert smaller / larger >= floor, "fixture drifted: the pair is below the floor"

    _components, weights, _truncated = _cluster_by_similarity(items, floor, 10)

    assert list(weights.values()) == [smaller / larger]


def test_two_empty_bags_do_not_divide_by_zero():
    items = [_item("a", Counter()), _item("b", Counter())]

    _components, weights, _truncated = _cluster_by_similarity(items, 0.5, 10)

    assert weights == {}
