"""Labels in, pre-registered verdict out, for #587.

Expected values are worked by hand. Fixtures assert they reach the branch (an exact-tier
pair that is TRUE, a unit both generators found, an interval that straddles) before the
outcome is asserted, so a drifting fixture cannot quietly stop testing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names cannot
# shadow stdlib/installed imports, mirroring tests/test_diff_duplicates_score.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import embedding_oracle_score as sc  # ty: ignore[unresolved-import]  (added to sys.path above)
import embedding_oracle_share as sh  # ty: ignore[unresolved-import]  (same)
import embedding_oracle_stats as st  # ty: ignore[unresolved-import]  (same)
from embedding_oracle_pairs import Pair  # ty: ignore[unresolved-import]  (same)


def pair(pr: str, new: str, old: str, gens: tuple[str, ...]) -> Pair:
    return Pair(
        pr=pr,
        repo=f"r/{pr}",
        new_path=f"{new}.py",
        new_qualname=new,
        new_nodes=40,
        new_shape="a",
        existing_path=f"{old}.py",
        existing_qualname=old,
        existing_shape="b",
        generators=gens,
    )


def exact(p: Pair) -> Pair:
    """The same pair with identical shapes, so archy's hasher would call it exact-tier."""
    return p.model_copy(update={"new_shape": "s", "existing_shape": "s"})


def in_repo(p: Pair, repo: str) -> Pair:
    return p.model_copy(update={"repo": repo})


def test_resolve_labels_is_strict_and_drops_unscorable():
    a = {"p1": "TRUE", "p2": "TRUE", "p3": "FALSE", "p4": "UNSCORABLE", "p5": "TRUE"}
    b = {"p1": "TRUE", "p2": "FALSE", "p3": "FALSE", "p4": "TRUE", "p5": "UNSCORABLE"}
    # p1 both TRUE; p2 split (FALSE for the headline); p3 both FALSE; p4 and p5 dropped.
    assert sh.resolve_labels(a, b) == {"p1": True, "p2": False, "p3": False}


def test_resolve_labels_refuses_when_the_labelers_saw_different_pairs():
    with pytest.raises(ValueError, match="different pairs"):
        sh.resolve_labels({"p1": "TRUE"}, {"p1": "TRUE", "p2": "TRUE"})


def test_wilson_matches_the_hand_worked_interval():
    """5 of 10: centre 0.5, half-width 0.2634 -> (0.2366, 0.7634). n=0 is the whole range."""
    low, high = st.wilson(5, 10)
    assert low == pytest.approx(0.2366, abs=1e-3)
    assert high == pytest.approx(0.7634, abs=1e-3)
    assert st.wilson(0, 0) == (0.0, 1.0)


def test_share_counts_units_by_generator_and_drops_exact_tier_before_forming_them():
    pairs = [
        pair("1", "n.a", "o.a", ("A",)),  # unit u1: only A found it
        pair("2", "n.b", "o.b", ("B",)),  # unit u2: only B found it
        pair("3", "n.c", "o.c", ("A",)),  # unit u3: A's pair...
        pair("3", "n.c", "o.d", ("B",)),  # ...and B's different pair, same unit: both
        exact(pair("4", "n.e", "o.e", ("B",))),  # TRUE but exact-tier: excluded
        pair("5", "n.f", "o.f", ("B",)),  # labeled FALSE: not in the pool
    ]
    truth = {p.pair_id: True for p in pairs}
    truth[pairs[5].pair_id] = False
    assert pairs[4].exact_tier and truth[pairs[4].pair_id]  # the exclusion is exercised
    got = sh.share(pairs, truth)
    # Three TRUE units remain: only A 1, only B 1, both 1. The exact-tier unit is gone.
    assert (got.true_units, got.only_a, got.only_b, got.both) == (3, 1, 1, 1)
    assert got.point == pytest.approx(1 / 3)
    assert got.measurable is False  # 3 units is far below the floor of 40


def _pool(n_units: int, only_b: int, *, repos: int, prs: int) -> tuple[list[Pair], dict]:
    pairs = []
    for i in range(n_units):
        gens = ("B",) if i < only_b else ("A",)
        pr = str(i % prs)
        pairs.append(in_repo(pair(pr, f"n.f{i}", f"o.f{i}", gens), f"r/{i % repos}"))
    return pairs, {p.pair_id: True for p in pairs}


@pytest.mark.parametrize(
    ("units", "repos", "prs", "measurable"),
    [
        (40, 15, 20, True),  # exactly on every floor
        (39, 15, 20, False),  # one unit short
        (40, 14, 20, False),  # one repository short
        (40, 15, 19, False),  # one PR short
    ],
)
def test_share_floors_are_exact(units, repos, prs, measurable):
    pairs, truth = _pool(units, 12, repos=repos, prs=prs)
    got = sh.share(pairs, truth)
    assert (got.repos, got.prs) == (min(repos, units), min(prs, units))  # fixture reaches it
    assert got.measurable is measurable
    assert got.only_b == 12  # 12 of the units are B-only by construction


def test_b_precision_is_over_every_labeled_b_pair_with_a_deterministic_interval():
    pairs = [
        pair("1", "n.a", "o.a", ("B",)),
        pair("1", "n.b", "o.b", ("B",)),
        pair("2", "n.c", "o.c", ("B",)),
        pair("2", "n.d", "o.d", ("B", "A")),
        pair("3", "n.e", "o.e", ("A",)),  # A only: not B's
    ]
    truth = {p.pair_id: t for p, t in zip(pairs, [True, False, True, False, True], strict=True)}
    got = sc.b_precision(pairs, truth)
    # Four B pairs from two repos, two TRUE: 0.5.
    assert (got.scorable, got.repos, got.true, got.point) == (4, 2, 2, 0.5)
    assert 0.0 <= got.low <= 0.5 <= got.high <= 1.0
    assert sc.b_precision(pairs, truth) == got  # fixed seed, so the same interval twice


def test_b_precision_interval_collapses_when_every_pair_is_true():
    pairs = [pair(str(i), f"n.f{i}", f"o.f{i}", ("B",)) for i in range(6)]
    got = sc.b_precision(pairs, {p.pair_id: True for p in pairs})
    assert (got.point, got.low, got.high) == (1.0, 1.0, 1.0)


def prec(point, low, high) -> sc.BPrecision:
    """B precision at the pre-registered floors (30 pairs, 10 repos) unless `counts` says so."""
    return sc.BPrecision(
        scorable=30, repos=10, true=round(point * 30), point=point, low=low, high=high
    )


def counts(p: sc.BPrecision, scorable: int, repos: int) -> sc.BPrecision:
    return p.model_copy(update={"scorable": scorable, "repos": repos})


def headline(point, low, high, *, measurable=True) -> sh.Share:
    return sh.Share(
        true_units=40,
        only_b=round(point * 40),
        only_a=0,
        both=0,
        repos=15,
        prs=20,
        measurable=measurable,
        point=point,
        low=low,
        high=high,
    )


@pytest.mark.parametrize(
    ("p", "s", "outcome"),
    [
        (prec(0.20, 0.10, 0.30), headline(0.5, 0.4, 0.6), "kill"),  # precision kill, interval clear
        (prec(0.35, 0.25, 0.45), headline(0.5, 0.4, 0.6), "null"),  # point < 0.40 but straddles
        (prec(0.70, 0.62, 0.80), headline(0.05, 0.02, 0.08), "kill"),  # share kill, clear
        (prec(0.70, 0.62, 0.80), headline(0.05, 0.02, 0.12), "null"),  # share straddles 0.10
        (prec(0.70, 0.62, 0.80), headline(0.50, 0.35, 0.65), "pass"),  # both bars, both clear
        (prec(0.70, 0.55, 0.80), headline(0.50, 0.35, 0.65), "null"),  # precision low straddles
        (prec(0.70, 0.62, 0.80), headline(0.40, 0.25, 0.55), "null"),  # share low straddles 0.30
        (prec(0.50, 0.40, 0.60), headline(0.20, 0.12, 0.30), "null"),  # between the bars
    ],
)
def test_verdict_applies_a_bar_only_when_the_interval_clears_it(p, s, outcome):
    assert sc.verdict(p, s).outcome == outcome


def test_a_straddling_precision_does_not_hide_a_definite_share_kill():
    """Kill is EITHER bar. Precision straddles 0.40, the share is clearly below 0.10."""
    got = sc.verdict(prec(0.35, 0.25, 0.45), headline(0.05, 0.02, 0.08))
    assert got.outcome == "kill"
    assert "share" in got.reason


def test_an_unmeasurable_share_can_never_pass_and_a_precision_kill_still_stands():
    unmeasurable = headline(0.5, 0.4, 0.6, measurable=False)
    assert sc.verdict(prec(0.70, 0.62, 0.80), unmeasurable).outcome == "not_measurable"
    assert sc.verdict(prec(0.20, 0.10, 0.30), unmeasurable).outcome == "kill"


@pytest.mark.parametrize(("scorable", "repos"), [(29, 10), (30, 9)])
def test_below_b_floors_nothing_is_decided_even_a_would_be_kill(scorable, repos):
    got = sc.verdict(counts(prec(0.1, 0.0, 0.2), scorable, repos), headline(0.5, 0.4, 0.6))
    assert got.outcome == "not_measurable"


def test_two_functions_with_one_name_in_different_files_are_two_units() -> None:
    """The unit is (PR, file, qualified name): `main` in two files is two TRUE units, not one."""
    one = pair("1", "main", "o.a", ("B",)).model_copy(update={"new_path": "tools/a.py"})
    two = pair("1", "main", "o.b", ("B",)).model_copy(update={"new_path": "tools/b.py"})
    assert one.new_qualname == two.new_qualname and one.pr == two.pr  # the fixture reaches it
    got = sh.share([one, two], {one.pair_id: True, two.pair_id: True})
    assert got.true_units == 2 and got.only_b == 2
