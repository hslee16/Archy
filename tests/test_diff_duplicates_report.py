"""Tests for the #548 results arithmetic. Numbers are hand-worked in the docstrings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import diff_duplicates_report as rep  # ty: ignore[unresolved-import]  (added to sys.path above)


def _rows() -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for i in range(6):  # 6 measured, 2 with a firing
        rows[f"m{i}"] = {"repo": f"o/r{i % 3}", "firings": [{"x": 1}] if i < 2 else []}
    for i in range(3):  # 3 Python PRs lost to collisions
        rows[f"c{i}"] = {"repo": "o/big", "dropped": "QualnameCollisionError: 2 module names"}
    rows["np"] = {"repo": "o/docs", "dropped": "no added python lines"}  # never a Python PR
    return rows


def test_headroom_is_hand_worked() -> None:
    """measured = 6, firing PRs = 2, so rate = 2/6. Dropped Python PRs = 3 (the
    collisions; the no-Python-lines PR was never a Python PR), so counting them as
    clean the rate is 2 / (6 + 3) = 2/9."""
    got = rep.headroom(_rows())
    assert got["measured"] == 6 and got["prs_with_firing"] == 2
    assert got["rate"] == pytest.approx(2 / 6)
    assert got["rate_drops_as_clean"] == pytest.approx(2 / 9)
    assert got["dropped"] == {"QualnameCollisionError": 3, "no added python lines": 1}
    assert got["repos_with_firing"] == 2  # firing PRs m0 (o/r0) and m1 (o/r1)


def test_subgroups_split_every_firing_exactly_once() -> None:
    firings = [
        {"exact": True, "category": "duplicate", "new_file": True, "budget_hit": False},
        {"exact": False, "category": "duplicate", "new_file": False, "budget_hit": False},
        {"exact": False, "category": "near_miss", "new_file": True, "budget_hit": True},
    ]
    groups = rep.subgroup_counts(firings)
    assert groups["tier"] == {"exact": 1, "duplicate": 1, "near_miss": 1}
    assert groups["file"] == {"new file": 2, "existing file": 1}
    assert groups["budget"] == {"complete": 2, "budget hit": 1}
    for counts in groups.values():
        assert sum(counts.values()) == len(firings)


def test_render_reports_the_verdict_from_the_score_rule() -> None:
    head = {**rep.headroom(_rows()), "rate": 0.30}
    scored = {
        "scorable": 30,
        "repos": 10,
        "unscorable_dropped": 2,
        "strict_true": 18,
        "lenient_true": 20,
        "precision_strict": 18 / 30,
        "precision_lenient": 20 / 30,
        "kappa": 0.5,
    }
    text = rep.render(head, [], scored)
    assert "## Verdict: precision_pass" in text  # 18/30 = 0.60 exactly passes
    assert "Cohen's kappa: 0.5" in text
    assert "## Verdict" not in rep.render(head, [], None)  # no labels, no verdict


def test_strict_decides_when_strict_and_lenient_straddle_a_threshold() -> None:
    """Strict 17/30 = 0.567 is a null; lenient 20/30 = 0.667 would pass. The
    pre-registration says the strict reading decides."""
    head = {**rep.headroom(_rows()), "rate": 0.30}
    scored = {
        "scorable": 30,
        "repos": 10,
        "unscorable_dropped": 0,
        "strict_true": 17,
        "lenient_true": 20,
        "precision_strict": 17 / 30,
        "precision_lenient": 20 / 30,
        "kappa": 0.4,
    }
    assert scored["precision_lenient"] >= 0.60 > scored["precision_strict"]  # they straddle
    assert "## Verdict: null" in rep.render(head, [], scored)
