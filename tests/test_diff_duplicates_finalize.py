"""Tests for the #548 reported-table arithmetic. Numbers are hand-worked."""

from __future__ import annotations

import sys
from pathlib import Path

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import diff_duplicates_finalize as fin  # ty: ignore[unresolved-import]  (added to sys.path above)


def test_reason_tally_counts_only_false_labels() -> None:
    labels = {
        "1": {"label": "FALSE", "reason": "sibling"},
        "2": {"label": "FALSE", "reason": "sibling"},
        "3": {"label": "TRUE", "reason": None},
        "4": {"label": "FALSE", "reason": "other"},
        "5": {"label": "UNSCORABLE", "reason": None},
    }
    assert fin.reason_tally(labels) == {"sibling": 2, "other": 1}


def test_subgroup_precision_is_hand_worked() -> None:
    """Group x holds firings 1 and 2 (1 strict, both lenient); group y holds firing 3."""
    got = fin.subgroup_precision({"1": "x", "2": "x", "3": "y"}, {"1"}, {"1", "2"})
    assert got == {"x": (1, 2, 2), "y": (0, 0, 1)}
    strict, lenient, _n = got["x"]
    assert lenient > strict  # the fixture separates the two readings


def test_oracle_outcomes_split_by_the_primary_verdict() -> None:
    outcomes = {"1": "removed", "2": "unchanged", "3": "removed"}
    got = fin.oracle_vs_primary(outcomes, {"1"})
    assert got["primary TRUE"] == {"removed": 1}
    assert got["primary FALSE"] == {"unchanged": 1, "removed": 1}
