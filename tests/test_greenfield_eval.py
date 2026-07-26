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


def test_nothing_listening_is_detected_without_docker_or_the_suite():
    """The transport probe on its own, so this holds on any machine.

    Any HTTP answer counts as present, including 404 and 500: the question is
    whether the server came up, not whether it is correct.
    """
    assert greenfield_eval._server_responds("http://localhost:59999", timeout=2.0) is False


def test_an_unmeasurable_run_never_receives_a_score():
    """ "did not run" and "ran and failed everything" are different facts.

    Both make the suite report 0 of 13. Folding them together would let an arm
    that produced code which does not start masquerade as one whose code runs
    badly.

    Asserts the invariant, NOT the reason text. Which precondition trips first
    depends on the machine: locally the suite and Docker are present so the
    probe reports the dead host, while CI has neither and stops earlier. The
    first version of this test asserted the local message and failed in CI,
    which is a fact about the runner rather than about the code.
    """
    verdict = greenfield_eval.behavioral_verdict("http://localhost:59999", timeout=60.0)
    assert verdict.evaluable is False
    assert verdict.reason  # says which precondition failed, whichever it was
    # None, never 0.0: a score of zero would be a behavioral claim about a
    # server that never answered.
    assert verdict.pass_rate is None
    assert verdict.files_executed is None


# --- the container reaches the host, or says it could not -------------------
#
# These pin the defect found by running the evaluator against a live server on
# 2026-07-26: `--network host` is accepted by Docker Desktop on macOS and then
# reaches nothing, the suite still runs, and a correct server scores an
# evaluable 0.0. Both arms would have been zeroed.


def test_localhost_is_rewritten_for_the_container():
    """`localhost` inside a container is the container, so an unrewritten host
    makes the suite test itself and find nothing."""
    for given in ("http://localhost:8000", "http://127.0.0.1:8000", "http://0.0.0.0:8000"):
        assert greenfield_eval.container_host(given) == "http://host.docker.internal:8000"


def test_a_remote_host_is_left_alone():
    assert greenfield_eval.container_host("http://api.example.com/") == "http://api.example.com"


def test_the_port_survives_rewriting():
    """Dropping the port would point the suite at :80 and 404 everything, which
    reads exactly like a broken server."""
    assert ":9999" in greenfield_eval.container_host("http://localhost:9999")


def _sample_cmd() -> list[str]:
    """The container invocation as the real run builds it."""
    return greenfield_eval._hurl_cmd(
        Path("/suite"), "http://localhost:8000", "uid", "/suite/*.hurl"
    )


def test_the_suite_is_never_run_on_the_host_network():
    """`--network host` is the defect itself. It must not come back."""
    cmd = _sample_cmd()
    assert "--network" not in cmd
    assert "--add-host=host.docker.internal:host-gateway" in cmd


def test_every_assert_runs_so_the_denominator_is_not_a_function_of_quality():
    """Without --continue-on-error hurl abandons a file at its first failure, so
    a worse server executes fewer asserts and can post a higher ratio."""
    assert "--continue-on-error" in _sample_cmd()


# --- the score is assert-level ------------------------------------------------


def _report(*files: list[list[bool]]) -> list[dict]:
    """A hurl --report-json report: files -> entries -> assert successes."""
    return [
        {
            "filename": f"/suite/{i}.hurl",
            "success": all(all(e) for e in entries),
            "entries": [{"asserts": [{"success": ok} for ok in entry]} for entry in entries],
        }
        for i, entries in enumerate(files)
    ]


def test_asserts_are_counted_across_files_and_entries():
    passed, executed = greenfield_eval.count_asserts(
        _report([[True, False], [True]], [[True, True]])
    )
    assert (passed, executed) == (4, 5)


def test_an_empty_report_is_zero_of_zero_not_a_crash():
    assert greenfield_eval.count_asserts([]) == (0, 0)


def test_failing_early_cannot_buy_a_higher_score():
    """The reason the denominator is pinned rather than `executed`.

    A server that dies almost immediately executes a handful of asserts and
    could otherwise post a near-perfect ratio off them.
    """
    quitter = greenfield_eval.assert_pass_rate(passed=5, executed=5)
    grinder = greenfield_eval.assert_pass_rate(passed=180, executed=241)
    assert quitter < grinder
    assert quitter == round(5 / greenfield_eval.ASSERT_DENOMINATOR, 4)


def test_a_run_that_conforms_better_than_the_calibration_is_not_capped():
    """The pin is a floor taken from a near-conforming reference, not a claim
    about the suite's true total, so exceeding it must not clip to 1.0."""
    beyond = greenfield_eval.ASSERT_DENOMINATOR + 60
    rate = greenfield_eval.assert_pass_rate(passed=beyond - 10, executed=beyond)
    assert rate == round((beyond - 10) / beyond, 4)
    assert rate < 1.0


def test_a_perfect_run_scores_one():
    rate = greenfield_eval.assert_pass_rate(
        passed=greenfield_eval.ASSERT_DENOMINATOR, executed=greenfield_eval.ASSERT_DENOMINATOR
    )
    assert rate == 1.0


def test_the_file_metric_is_kept_but_is_not_the_score():
    """It stays in the payload as a strictness measure and must not be used as
    the rate: measured against one near-conforming backend the files said 0.000
    while the asserts said 0.776, and the stub that 501s everything also said
    0.000. A metric that cannot separate those cannot support the guardrail."""
    fields = greenfield_eval.BehavioralVerdict.model_fields
    assert "files_succeeded" in fields
    assert {"asserts_passed", "asserts_executed", "asserts_denominator"} <= set(fields)
