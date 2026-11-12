"""Pairs, the per-PR cap, A's expansion, cutoff calibration and pooling for #587.

Every expected value is worked by hand in the test, never taken from the code under
test. Each test asserts its fixture reaches the branch before asserting the outcome.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names cannot
# shadow stdlib/installed imports, mirroring tests/test_diff_duplicates_score.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_pairs import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    K,
    Member,
    Pair,
    calibrate_cutoff,
    cap_pairs,
    expand_a_firing,
    pool_pairs,
)


def member(qual: str, *, new: bool, nodes: int = 40, path: str | None = None, shape="s") -> Member:
    return Member(
        path=path or f"{qual.replace('.', '/')}.py",
        qualname=qual,
        is_new=new,
        nodes=nodes,
        shape=shape,
    )


def pair(pr="1", new="n.f", old="o.f", *, nodes=40, path=None, gens=("A",), sim=None, **kw) -> Pair:
    return Pair(
        pr=pr,
        repo=kw.get("repo", "r/x"),
        new_path=path or f"{new.replace('.', '/')}.py",
        new_qualname=new,
        new_nodes=nodes,
        new_shape=kw.get("new_shape", "a"),
        existing_path=f"{old.replace('.', '/')}.py",
        existing_qualname=old,
        existing_shape=kw.get("old_shape", "b"),
        similarity=sim,
        generators=gens,
    )


def test_pair_model_dump_carries_the_computed_ids_and_exact_tier():
    """Assert the serialized form: a plain property would be dropped."""
    p = pair(pr="7", new="a.f", old="b.g", new_shape="x", old_shape="x").model_dump()
    assert p["pair_id"] == "7|a.f|b.g"
    assert p["unit_id"] == "7|a.f"
    assert p["exact_tier"] is True
    assert pair(new_shape="x", old_shape="y").exact_tier is False


def test_cap_keeps_the_largest_new_functions_per_pr_and_leaves_small_prs_alone():
    big = [pair(pr="big", new=f"n.f{i}", nodes=30 + i) for i in range(12)]
    small = [pair(pr="small", new=f"n.g{i}", nodes=31) for i in range(3)]
    assert len(big) > K and len(small) < K  # the cap binds on one PR and not the other
    kept = cap_pairs([*big, *small])
    big_kept = sorted(p.new_nodes for p in kept if p.pr == "big")
    # Sizes are 30..41; the ten largest are 32..41 (hand-worked), 30 and 31 are dropped.
    assert big_kept == list(range(32, 42))
    assert sum(p.pr == "small" for p in kept) == 3


def test_cap_ties_break_on_path_then_qualname_whatever_the_input_order():
    tied = [
        pair(pr="t", new="z.f", nodes=50, path="b/z.py"),
        pair(pr="t", new="a.f", nodes=50, path="b/a.py"),
        pair(pr="t", new="m.f", nodes=50, path="a/m.py"),
    ]
    for _ in range(5):
        random.shuffle(tied)
        # All three tie on nodes, so the cap of 2 keeps the two smallest paths:
        # a/m.py then b/a.py.
        assert [p.new_path for p in cap_pairs(tied, k=2)] == ["a/m.py", "b/a.py"]


def test_expand_pairs_each_new_member_with_its_highest_overlap_existing_partner():
    members = [
        member("n.one", new=True),
        member("n.two", new=True),
        member("o.low", new=False),
        member("o.high", new=False),
    ]
    overlaps = {("n.one", "o.low"): 0.5, ("n.one", "o.high"): 0.9}
    pairs, dropped = expand_a_firing(
        "9",
        "r/x",
        members,
        overlap=lambda n, o: overlaps.get((n.qualname, o.qualname), 0.0),
        exists_at_both=lambda m: True,
    )
    assert dropped == 0
    by_new = {p.new_qualname: p.existing_qualname for p in pairs}
    # n.one has a clear best partner; n.two ties at 0 overlap, so the smaller
    # (path, qualname) wins: o/high.py sorts before o/low.py.
    assert by_new == {"n.one": "o.high", "n.two": "o.high"}
    assert all(p.generators == ("A",) for p in pairs)


def test_expand_skips_a_partner_that_does_not_exist_at_both_and_counts_drops():
    members = [member("n.f", new=True), member("o.best", new=False), member("o.ok", new=False)]
    overlap = {"o.best": 0.9, "o.ok": 0.1}
    # The best partner by overlap is gone at head: the fixture must reach that branch.
    assert overlap["o.best"] > overlap["o.ok"]
    pairs, dropped = expand_a_firing(
        "9",
        "r/x",
        members,
        overlap=lambda n, o: overlap[o.qualname],
        exists_at_both=lambda m: m.qualname != "o.best",
    )
    assert [p.existing_qualname for p in pairs] == ["o.ok"] and dropped == 0
    none, dropped = expand_a_firing(
        "9", "r/x", members, overlap=lambda n, o: 0.0, exists_at_both=lambda m: False
    )
    assert none == [] and dropped == 1  # one new member, no partner: dropped, never scored


def test_calibrate_picks_the_largest_similarity_whose_mean_meets_the_target():
    """Hand-worked on three PRs, target 1.0 pair per PR (mean over all three, zeros count).

    t=0.9: counts 1,0,0 -> 1/3.  t=0.85: 1,1,0 -> 2/3.  t=0.8: 2,1,0 -> 3/3 = 1.0.
    The first (largest) t that reaches 1.0 is 0.8.
    """
    scores = {"p1": [0.9, 0.8, 0.7], "p2": [0.85], "p3": []}
    assert calibrate_cutoff(scores, target=1.0) == 0.8


def test_calibrate_cap_limits_a_pr_and_a_missed_target_keeps_every_candidate():
    """With k=1 a PR counts once however many pass: t=0.7 gives 1,1,0 -> 2/3 < 1.0."""
    scores = {"p1": [0.9, 0.8, 0.7], "p2": [0.85], "p3": []}
    assert calibrate_cutoff(scores, target=1.0, k=1) == 0.7  # the lowest observed


def test_calibrate_includes_every_pair_tied_at_the_cutoff():
    """One PR with 0.8, 0.8, 0.5 and target 2.0: t=0.8 admits both tied pairs, mean 2."""
    assert calibrate_cutoff({"p": [0.8, 0.8, 0.5]}, target=2.0) == 0.8


@pytest.mark.parametrize("scores", [{}, {"p": []}])
def test_calibrate_refuses_when_there_is_nothing_to_match(scores):
    with pytest.raises(ValueError):
        calibrate_cutoff(scores, target=1.0)


def test_pool_merges_a_pair_both_generators_found_and_keeps_the_rest():
    a = [pair(new="n.f", old="o.f", gens=("A",)), pair(new="n.g", old="o.g", gens=("A",))]
    b = [pair(new="n.f", old="o.f", gens=("B",), sim=0.91), pair(new="n.h", old="o.h", gens=("B",))]
    assert {p.pair_id for p in a} & {p.pair_id for p in b}  # one pair is in both
    pooled = pool_pairs(a, b)
    by_id = {p.pair_id: p for p in pooled}
    assert len(pooled) == 3
    assert by_id["1|n.f|o.f"].generators == ("A", "B")
    assert by_id["1|n.f|o.f"].similarity == 0.91
    assert by_id["1|n.g|o.g"].generators == ("A",)
    assert [p.pair_id for p in pooled] == sorted(by_id)
