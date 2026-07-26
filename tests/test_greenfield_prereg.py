"""Pin the #369 pre-registration.

These tests exist to make the thresholds expensive to change. A pre-registered
reading that can be quietly widened after seeing the data is not pre-registered,
and #364 records what that costs: six artifacts, every one pointing toward the
answer the study wanted.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "greenfield_prereg", REPO_ROOT / "bench/greenfield_prereg.py"
)
assert _spec and _spec.loader
prereg = importlib.util.module_from_spec(_spec)
sys.modules["greenfield_prereg"] = prereg
_spec.loader.exec_module(prereg)


def arm(
    name: str,
    *,
    evaluable: int,
    compliant: int,
    behavioral: float | None = 0.8,
    composite: float | None = None,
) -> prereg.ArmSummary:
    return prereg.ArmSummary(
        arm=name,
        n_rows=evaluable,
        n_structural_evaluable=evaluable,
        n_compliant=compliant,
        behavioral_mean=behavioral,
        n_behavioral_evaluable=compliant,
        composite_mean=composite,
    )


def row(
    armname: str,
    *,
    status: str = "ok",
    structural_evaluable: bool = True,
    compliant: bool = True,
    behavioral_evaluable: bool = True,
    pass_rate: float | None = 0.9,
) -> dict:
    return {
        "arm": armname,
        "status": status,
        "structural": {"evaluable": structural_evaluable, "compliant": compliant},
        "behavioral": {"evaluable": behavioral_evaluable, "pass_rate": pass_rate},
    }


# --- the thresholds themselves ------------------------------------------------


def test_thresholds_are_the_registered_values():
    """Changing any of these after a run invalidates the study, so they are
    asserted literally rather than referenced."""
    assert prereg.WIN_DELTA_PP == 25.0
    assert prereg.KILL_DELTA_PP == 10.0
    assert prereg.MAX_BEHAVIORAL_REGRESSION_PP == 5.0
    assert prereg.N_PER_ARM == 25
    assert prereg.MODEL == "claude-sonnet-5"
    assert prereg.FRAMEWORK == "fastapi"
    assert prereg.EXPANSION_FRAMEWORKS == ("flask", "django")


# --- the reading --------------------------------------------------------------


def test_large_clean_effect_is_a_win():
    result = prereg.verdict(
        arm("A", evaluable=25, compliant=8, behavioral=0.80),
        arm("B", evaluable=25, compliant=23, behavioral=0.82),
    )
    assert result.verdict == "WIN"
    assert result.guardrail == "held"
    assert result.delta_pp == pytest.approx(60.0)
    # The claim's scope is not optional garnish; it is the retraction in
    # WHAT_DIDNT_WORK.md, so the verdict carries it.
    assert "existing codebase" in result.reason


def test_effect_below_the_floor_is_a_kill():
    result = prereg.verdict(
        arm("A", evaluable=25, compliant=10),
        arm("B", evaluable=25, compliant=12),
    )
    assert result.verdict == "KILL"
    assert "WHAT_DIDNT_WORK" in result.reason


def test_the_middle_expands_rather_than_being_read_as_a_hint():
    result = prereg.verdict(
        arm("A", evaluable=25, compliant=10),
        arm("B", evaluable=25, compliant=15),
    )
    assert result.verdict == "EXPAND"
    assert result.delta_pp == pytest.approx(20.0)


def test_a_big_point_estimate_with_a_straddling_interval_is_not_a_win():
    """The CI clause is the one that stops an underpowered N from producing a
    headline. At tiny N a +33 pp point estimate is noise."""
    result = prereg.verdict(
        arm("A", evaluable=3, compliant=1),
        arm("B", evaluable=3, compliant=2),
    )
    assert result.delta_pp == pytest.approx(100 / 3)
    assert result.ci_low_pp is not None and result.ci_low_pp < 0
    assert result.verdict == "EXPAND"


def test_a_structural_win_that_broke_the_server_is_void_not_qualified():
    result = prereg.verdict(
        arm("A", evaluable=25, compliant=8, behavioral=0.85),
        arm("B", evaluable=25, compliant=23, behavioral=0.60),
    )
    assert result.verdict == "VOID"
    assert result.guardrail == "regressed"


def test_a_behavioral_dip_inside_the_guardrail_still_wins():
    result = prereg.verdict(
        arm("A", evaluable=25, compliant=8, behavioral=0.85),
        arm("B", evaluable=25, compliant=23, behavioral=0.81),
    )
    assert result.verdict == "WIN"
    assert result.guardrail == "held"


def test_an_unchecked_guardrail_is_not_a_passed_one():
    """A structural win with no behavioral evidence at all must not read as a
    win: the whole risk being guarded against is a compliant broken server."""
    result = prereg.verdict(
        arm("A", evaluable=25, compliant=8, behavioral=None),
        arm("B", evaluable=25, compliant=23, behavioral=None),
    )
    assert result.verdict == "VOID"
    assert result.guardrail == "unevaluable"


def test_an_arm_with_nothing_evaluable_is_a_harness_failure_not_a_result():
    result = prereg.verdict(
        arm("A", evaluable=0, compliant=0, behavioral=None),
        arm("B", evaluable=25, compliant=23),
    )
    assert result.verdict == "UNREADABLE"
    assert result.delta_pp is None


# --- reducing the ledger ------------------------------------------------------


def test_only_ok_rows_are_read():
    """`error` and `stalled` rows are work still owed. Counting them as
    outcomes would score a crashed run as non-compliant."""
    rows = [
        row("A", status="ok", compliant=True),
        row("A", status="error", compliant=False),
        row("A", status="stalled", compliant=False),
    ]
    summary = prereg.summarize_rows(rows, "A")
    assert summary.n_rows == 1
    assert summary.n_structural_evaluable == 1
    assert summary.compliance_rate == 1.0


def test_a_structurally_unevaluable_run_is_dropped_not_counted_as_violating():
    """A generation that emitted no .py at all is a failed generation, not an
    architecture result. Counting it as non-compliant biases the rate by
    exactly the runs that broke."""
    rows = [
        row("B", compliant=True),
        row("B", structural_evaluable=False, compliant=False),
    ]
    summary = prereg.summarize_rows(rows, "B")
    assert summary.n_structural_evaluable == 1
    assert summary.compliance_rate == 1.0


def test_a_behaviorally_unevaluable_run_does_not_become_a_zero():
    rows = [
        row("A", compliant=True, pass_rate=0.9),
        row("A", compliant=True, behavioral_evaluable=False, pass_rate=None),
    ]
    summary = prereg.summarize_rows(rows, "A")
    assert summary.n_behavioral_evaluable == 1
    assert summary.behavioral_mean == pytest.approx(0.9)


def test_no_behavioral_evidence_reports_none_never_zero():
    rows = [row("A", compliant=True, behavioral_evaluable=False, pass_rate=None)]
    summary = prereg.summarize_rows(rows, "A")
    assert summary.behavioral_mean is None
    assert summary.composite_mean is None


def test_composite_zeroes_non_compliant_runs_the_way_the_paper_does():
    """The paper's A% is behavioral, zeroed on structural non-compliance. A
    compliant 1.0 and a non-compliant 1.0 must not average to 1.0."""
    rows = [
        row("B", compliant=True, pass_rate=1.0),
        row("B", compliant=False, pass_rate=1.0),
    ]
    summary = prereg.summarize_rows(rows, "B")
    assert summary.composite_mean == pytest.approx(0.5)
    # The behavioral guardrail, by contrast, is over compliant runs only.
    assert summary.behavioral_mean == pytest.approx(1.0)


def test_compliance_rate_survives_model_dump():
    """A derived value consumers need must be a computed_field. A plain
    @property is dropped by model_dump() and the JSON output carries nothing."""
    dumped = arm("A", evaluable=25, compliant=10).model_dump()
    assert dumped["compliance_rate"] == pytest.approx(0.4)


# --- the interval -------------------------------------------------------------


def test_wilson_does_not_collapse_at_the_boundaries():
    """Arm B is required to satisfy the checker before finishing, so 25/25 is a
    live possibility. A Wald interval there has zero width and reads as
    certainty; Wilson does not."""
    low, high = prereg.wilson(25, 25)
    assert low < 0.95  # a real interval, not a point
    assert high == pytest.approx(1.0)
    low_zero, high_zero = prereg.wilson(0, 25)
    assert low_zero == pytest.approx(0.0)
    assert high_zero > 0.0


def test_identical_arms_give_an_interval_containing_zero():
    low, high = prereg.newcombe_diff_ci(10, 25, 10, 25)
    assert low < 0 < high


def test_wilson_rejects_an_empty_sample():
    with pytest.raises(ValueError):
        prereg.wilson(0, 0)
