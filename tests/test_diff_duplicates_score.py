"""Tests for the #548 scoring rule.

The verdict function is the pre-registered decision, so its boundaries are pinned
exactly: 0.60 passes, 0.40 is a null (the kill is strictly below), headroom decides
before precision, and an unscorable firing is dropped rather than scored FALSE.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import diff_duplicates_score as sc  # ty: ignore[unresolved-import]  (added to sys.path above)

OK = {"firing_rate": 0.02, "scorable": 30, "repos": 10}


@pytest.mark.parametrize(
    ("precision", "outcome"),
    [
        (18 / 30, "precision_pass"),  # exactly 0.60 passes
        (17 / 30, "null"),  # 0.5667
        (12 / 30, "null"),  # exactly 0.40 is a null, the kill is strictly below
        (11 / 30, "precision_kill"),  # 0.3667
    ],
)
def test_precision_boundaries(precision: float, outcome: str) -> None:
    assert sc.verdict(precision=precision, **OK).outcome == outcome


@pytest.mark.parametrize(
    "override",
    [
        {"firing_rate": 0.0199},  # below 1 PR in 50
        {"scorable": 29},
        {"repos": 9},
    ],
)
def test_headroom_decides_before_precision(override: dict) -> None:
    """A perfect precision cannot rescue a result that misses headroom."""
    got = sc.verdict(precision=1.0, **{**OK, **override})
    assert got.outcome == "headroom_kill"


def test_kappa_is_hand_worked() -> None:
    """a=[T,T,F,F], b=[T,F,F,F]: observed 3/4, expected .5*.25 + .5*.75 = .5,
    so kappa = (.75 - .5) / (1 - .5) = 0.5."""
    assert sc.cohen_kappa(["T", "T", "F", "F"], ["T", "F", "F", "F"]) == pytest.approx(0.5)


def test_kappa_undefined_when_chance_agreement_is_certain() -> None:
    assert sc.cohen_kappa(["T", "T"], ["T", "T"]) is None


def _write(path: Path, rows: dict[str, str]) -> Path:
    path.write_text("".join(json.dumps({"id": k, "label": v}) + "\n" for k, v in rows.items()))
    return path


def test_strict_needs_both_lenient_needs_either_and_unscorable_is_dropped(
    tmp_path: Path,
) -> None:
    a = _write(
        tmp_path / "a", {"1": "TRUE", "2": "TRUE", "3": "FALSE", "4": "UNSCORABLE", "5": "TRUE"}
    )
    b = _write(tmp_path / "b", {"1": "TRUE", "2": "FALSE", "3": "FALSE", "4": "FALSE"})
    repos = {"1": "o/x", "2": "o/y", "3": "o/z", "4": "o/w", "5": "o/v"}

    got = sc.score_labels(a, b, repos)

    # 5 has only one labeler and 4 is UNSCORABLE for one: neither is scored FALSE.
    assert got["labelled_by_both"] == 4
    assert got["scorable"] == 3
    assert got["unscorable_dropped"] == 1
    assert got["strict_true"] == 1  # only firing 1
    assert got["lenient_true"] == 2  # firings 1 and 2
    assert got["precision_strict"] == pytest.approx(1 / 3)
    assert got["repos"] == 3
