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
    PrRef,
    calibrate_cutoff,
    cap_pairs,
    expand_a_firing,
    pool_pairs,
)


def member(qual: str, *, new: bool, nodes: int = 40) -> Member:
    return Member(
        path=f"{qual.replace('.', '/')}.py", qualname=qual, is_new=new, nodes=nodes, shape="s"
    )


def pair(pr: str, new: str, old: str, gens: tuple[str, ...] = ("A",)) -> Pair:
    return Pair(
        pr=pr,
        repo="r/x",
        new_path=f"{new.replace('.', '/')}.py",
        new_qualname=new,
        new_nodes=40,
        new_shape="a",
        existing_path=f"{old.replace('.', '/')}.py",
        existing_qualname=old,
        existing_shape="b",
        generators=gens,
    )


def with_(p: Pair, **fields) -> Pair:
    return p.model_copy(update=fields)


def test_pair_model_dump_carries_the_computed_ids_and_exact_tier():
    """Assert the serialized form: a plain property would be dropped."""
    same = with_(pair("7", "a.f", "b.g"), new_shape="x", existing_shape="x")
    # the identity of a function is its file AND its qualified name; the paths come from the helper
    assert same.model_dump()["pair_id"] == '["7","a/f.py","a.f","b/g.py","b.g"]'
    assert same.model_dump()["unit_id"] == '["7","a/f.py","a.f"]'
    assert same.model_dump()["exact_tier"] is True
    assert with_(pair("1", "n.f", "o.f"), new_shape="x", existing_shape="y").exact_tier is False


def test_cap_keeps_the_largest_new_functions_per_pr_and_leaves_small_prs_alone():
    big = [with_(pair("big", f"n.f{i}", "o.f"), new_nodes=30 + i) for i in range(12)]
    small = [with_(pair("small", f"n.g{i}", "o.f"), new_nodes=31) for i in range(3)]
    assert len(big) > K and len(small) < K  # the cap binds on one PR and not the other
    kept = cap_pairs([*big, *small])
    big_kept = sorted(p.new_nodes for p in kept if p.pr == "big")
    # Sizes are 30..41; the ten largest are 32..41 (hand-worked), 30 and 31 are dropped.
    assert big_kept == list(range(32, 42))
    assert sum(p.pr == "small" for p in kept) == 3


def test_cap_ties_break_on_path_then_qualname_whatever_the_input_order():
    tied = [
        with_(pair("t", "z.f", "o.f"), new_nodes=50, new_path="b/z.py"),
        with_(pair("t", "a.f", "o.f"), new_nodes=50, new_path="b/a.py"),
        with_(pair("t", "m.f", "o.f"), new_nodes=50, new_path="a/m.py"),
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
        PrRef(pr="9", repo="r/x"),
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
    ref = PrRef(pr="9", repo="r/x")
    pairs, dropped = expand_a_firing(
        ref,
        members,
        overlap=lambda n, o: overlap[o.qualname],
        exists_at_both=lambda m: m.qualname != "o.best",
    )
    assert [p.existing_qualname for p in pairs] == ["o.ok"] and dropped == 0
    none, dropped = expand_a_firing(
        ref, members, overlap=lambda n, o: 0.0, exists_at_both=lambda m: False
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
    a = [pair("1", "n.f", "o.f"), pair("1", "n.g", "o.g")]
    b = [
        with_(pair("1", "n.f", "o.f", ("B",)), similarity=0.91),
        pair("1", "n.h", "o.h", ("B",)),
    ]
    assert {p.pair_id for p in a} & {p.pair_id for p in b}  # one pair is in both
    pooled = pool_pairs(a, b)
    by_id = {p.pair_id: p for p in pooled}
    assert len(pooled) == 3
    merged = by_id['["1","n/f.py","n.f","o/f.py","o.f"]']
    assert merged.generators == ("A", "B") and merged.similarity == 0.91
    assert by_id['["1","n/g.py","n.g","o/g.py","o.g"]'].generators == ("A",)
    assert [p.pair_id for p in pooled] == sorted(by_id)


# --- review fix: a qualified name is local to its file ---------------------------------


def test_same_name_in_different_files_is_a_different_function() -> None:
    """One measured PR defines `main` in five files; a qualified name alone merged them."""
    a = with_(pair("1", "main", "o.f"), new_path="tools/a.py")
    b = with_(pair("1", "main", "o.f"), new_path="tools/b.py")
    assert a.new_qualname == b.new_qualname and a.existing_qualname == b.existing_qualname
    assert a.pair_id != b.pair_id  # same names, different new file
    assert a.unit_id != b.unit_id
    c = with_(pair("1", "n.f", "main"), existing_path="lib/x.py")
    d = with_(pair("1", "n.f", "main"), existing_path="lib/y.py")
    assert c.pair_id != d.pair_id  # same names, different existing file
    assert c.unit_id == d.unit_id  # the NEW function is the same one
    pooled = pool_pairs([a, c], [b, d])
    assert len(pooled) == 4  # none of them was absorbed into another


def test_an_id_cannot_collide_through_a_separator_in_a_path_or_name() -> None:
    """A naive `|` join gives these two the same id: a path or a name holds the separator."""
    x = with_(pair("1", "z", "o.f"), new_path="x|y")
    y = with_(pair("1", "y|z", "o.f"), new_path="x")
    assert f"{x.pr}|{x.new_path}|{x.new_qualname}" == f"{y.pr}|{y.new_path}|{y.new_qualname}"
    assert x.unit_id != y.unit_id and x.pair_id != y.pair_id  # but the encoded ids differ


def test_the_id_escapes_quotes_backslashes_and_non_ascii_deterministically() -> None:
    """Hand-worked: JSON escapes a quote and a backslash, and writes U+00E9 as an ASCII escape."""
    p = with_(pair("1", "n.f", "o.f"), new_path='caf\u00e9"\\|.py')
    assert p.unit_id == '["1","caf\\u00e9\\"\\\\|.py","n.f"]'
    assert p.unit_id.isascii()  # no raw non-ASCII in an id, whatever the path holds
