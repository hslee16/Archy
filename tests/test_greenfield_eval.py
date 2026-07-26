"""Tests for the #369 greenfield evaluator.

The structural half is asserted against the three fixtures that reproduce the
Constraint Decay paper's own cases, so a regression shows up as archy
disagreeing with the paper's verifier rather than as an abstract failure.

Nothing here starts a server or spends agent time; the behavioral half is
tested for the distinction that matters, which is "did not run" versus "ran and
failed".
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import greenfield_eval  # ty: ignore[unresolved-import]  (added to sys.path above)

FIXTURES = Path(__file__).resolve().parent.parent / "bench/fixtures/conduit_clean"


def test_compliant_tree_matches_the_papers_verdict():
    verdict = greenfield_eval.structural_verdict(FIXTURES / "compliant")
    assert verdict.compliant is True
    assert verdict.layers_present == 4
    assert verdict.dependency_violations == ()


def test_upward_import_is_a_dependency_violation():
    """A repository importing a route handler: the paper's own example."""
    verdict = greenfield_eval.structural_verdict(FIXTURES / "violating")
    assert verdict.compliant is False
    assert verdict.dependency_violations == ("repositories.article_repo -> routes.articles",)
    # Presence still passes; only the direction check fails.
    assert verdict.presence_ok is True


def test_degenerate_single_module_fails_on_presence():
    """The structurally-degenerate output the paper says agents produce.

    It satisfies every forbidden-edge rule by having no cross-layer edges at
    all, which is why the presence floor exists.
    """
    verdict = greenfield_eval.structural_verdict(FIXTURES / "degenerate")
    assert verdict.compliant is False
    assert verdict.dependency_violations == ()
    assert verdict.presence_ok is False
    assert verdict.layers_present == 0


def test_unreadable_tree_is_unevaluable_not_compliant(tmp_path: Path):
    verdict = greenfield_eval.structural_verdict(tmp_path / "does_not_exist")
    assert verdict.evaluable is False
    assert verdict.compliant is False


def test_a_server_that_never_started_is_not_a_zero_score():
    """ "did not run" and "ran and failed everything" are different facts.

    Both make the suite report 0 of 13. Folding them together would let an arm
    that produced code which does not start masquerade as one whose code runs
    badly.
    """
    verdict = greenfield_eval.behavioral_verdict("http://localhost:59999", timeout=60.0)
    assert verdict.evaluable is False
    assert verdict.reason is not None and "nothing listening" in verdict.reason
    # None, never 0.0: a score of zero would be a behavioral claim about a
    # server that never answered.
    assert verdict.pass_rate is None
