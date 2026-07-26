#!/usr/bin/env python
"""Pre-registration for the greenfield-compliance A/B (#369).

    uv run python bench/greenfield_prereg.py --show
    uv run python bench/greenfield_prereg.py --score bench/greenfield_results.jsonl

Written **before the generation half exists and before any agent time is spent**,
per `CLAUDE.md` and #369 itself. The measurement half (`bench/greenfield_eval.py`,
#373) is already built and validated against the paper's three fixture cases.
This file is the other precondition: the thresholds, including the ones that kill
the idea.

Thresholds live here as **constants and a `verdict()` function**, not as prose,
for the reason #364 records: a number in a paragraph can be reinterpreted after
the fact, and the decay study's six artifacts every one pointed toward the answer
the study wanted. `verdict()` takes the two arms and returns WIN, KILL, EXPAND or
VOID with no judgement call left in it.

## The question

The Constraint Decay paper (arxiv:2605.06445) measures agents **constructing** a
backend under a specified architecture, and reports Clean Architecture layering
as the most expensive architectural constraint in isolation (-9.1 +/- 1.6 pp on
assertion pass rate). Its agents "received static prompts describing the
architecture but no dynamic course-correction on violations during generation."

**Does putting a structural checker in the loop change greenfield compliance?**
Nobody has measured it, the paper names it as its own open question, and it is
the one regime where the paper's numbers predict headroom.

This is NOT the question #356 answered. #356 asked whether an agent *damages* an
architecture already present, got 0 of 25, and every one of those runs edited a
mature human-architected repository that supplied the structure the agent copied.
Neither study tests the other's regime. **Any write-up of this must say so in
those words**, per the retraction in `docs/WHAT_DIDNT_WORK.md`.

## Scope, fixed now

| | |
| --- | --- |
| model | `claude-sonnet-5` |
| framework | FastAPI only |
| condition | the paper's L1-Arch: the Clean Architecture block, alone |
| task | RealWorld Conduit, from the published OpenAPI contract |
| N | 25 per arm, 50 runs |

One model and one framework is a deliberate first cut. The paper's own
framework-sensitivity result means a framework mix would let a mix shift read as
an effect, and running one framework removes that confound entirely rather than
controlling for it. The cost is external validity, and the expansion rule below
is what buys it back if the first cut is not conclusive.

## The two arms

**Arm A (control).** The paper's own condition: the static prompt, containing the
Clean Architecture block verbatim from Appendix E.2. No checker, no archy.

**Arm B (treatment).** The identical prompt, plus `bench/fixtures/conduit_clean/
archy.yaml` (transcribed from that same block, not authored by the measurer),
plus archy in the loop, plus a requirement to run `archy check` and reach exit 0
before declaring done.

Everything else is held: same model, same framework, same contract, same wall
clock budget, same tool allowlist apart from archy itself.

## What is measured

**Primary: structural compliance rate**, the paper's Appendix C.1 verifier, which
is `greenfield_eval.structural_verdict`: at least 3 of the 4 canonical layers
present as distinct directories AND zero dependency-direction violations.
Compliant iff both.

**Guardrail: behavioral pass rate among structurally compliant runs.** The
paper's A% zeroes the behavioral score of a non-compliant run, so a naive reading
of the primary alone rewards a compliant-but-broken server. Reported for both
arms, and it can VOID a structural win on its own (see `verdict()`).

**Also reported, never gating:** the paper's composite A% (behavioral, zeroed on
non-compliance), layers-present and violation counts, correction iterations in
arm B, wall time, turns, and cost per arm.

## The reading, pre-registered

Let `delta` be arm B's compliance rate minus arm A's, in percentage points, and
`CI` the 95% Newcombe interval on that difference.

| condition | verdict |
| --- | --- |
| `delta >= +25 pp` and `CI` excludes 0 | **WIN** |
| `delta < +10 pp` | **KILL** |
| anything between, or `CI` includes 0 | **EXPAND** |
| a WIN whose behavioral guardrail regressed | **VOID** |

**VOID is not a WIN with an asterisk.** If arm B's mean behavioral pass rate
among compliant runs sits more than 5 pp below arm A's, the structural gain was
bought by breaking the server and there is no result to report beyond that fact.

**Power, stated rather than implied.** At N=25 per arm and a plausible arm-A rate
near 40%, the design has 80% power for an effect of roughly +38 pp, not for the
+25 pp that counts as practically meaningful (that needs ~62 per arm). The gap
between those two numbers is exactly what EXPAND exists for, and pretending it
away is how a null gets reported as a hint.

**EXPAND is pre-registered, not a second bite.** It adds Flask and Django at the
same N, pooling to 75 per arm, which is powered for +25 pp. It runs at most once,
the same thresholds apply to the pooled result, framework is reported as a
stratum, and **no further expansion is available.** If the pooled result lands in
the middle again, that is a null and it goes in `docs/WHAT_DIDNT_WORK.md`.

**No peeking.** N is fixed, the ledger is not scored until both arms are
complete, and there is no interim analysis that could stop the run early.

## Two disclosures that a reader is entitled to before the numbers

**1. Arm B optimizes against the same instrument that scores it.** The agent runs
`archy check`; the evaluator runs `find_violations` and `min_layers_present`. If
arm B approaches 100% this is partly tautological, and "an agent passes a check
when instructed to run that check until it passes" is not a finding worth
publishing on its own. Three things survive the overlap and they are where the
interest actually is:

- **Residual non-compliance in arm B.** A gate the agent is required to satisfy
  and does not is a real measurement, and it is the number nobody has.
- **The behavioral guardrail**, which the instrument does not touch: the Hurl
  suite is the RealWorld project's, against the published contract.
- **The composite A%**, which requires both halves and cannot be reached by
  satisfying the checker alone.

The write-up leads with those, not with the primary delta in isolation.

**2. The behavioral suite is a substitute.** The paper ran a Postman collection
it never published; this runs the RealWorld project's own Hurl suite against the
same OpenAPI contract. Absolute pass rates are therefore not comparable to the
paper's table. This supports "same contract, same prompt, archy moved compliance
by X" and NOT "we reproduced their L3 and beat it". The control is internal,
which is what makes that sufficient.

## Unmeasurable is not a score, and a dead server is not unmeasurable

`greenfield_eval` already refuses to report 0 for a server that never answered,
because a server that never started and one that answers everything wrong are
indistinguishable from the pass count alone. The runner must go one step further
and split what that guard currently merges:

- the container built and the process started but never bound or crashed on
  boot -> **that is the generation's failure, and it is a behavioral zero.**
- docker unavailable, port collision, image pull failure, harness timeout ->
  **unevaluable**, dropped from the denominator, and counted in the write-up.

Scoring the first as unevaluable would drop exactly the runs that failed worst
and inflate both arms. Scoring the second as zero would put the harness into the
measurement. Denominators are reported per arm for both axes.

A run that produced no `.py` file at all is a failed generation, not an
architecture result, and is structurally unevaluable rather than non-compliant.

## Resumability, because this spends hours

The runner reuses the `bench/q1b_run.py` shape, which `tests/test_q1b_run.py`
pins and `CLAUDE.md` now states as a rule: one ledger row per completed unit
written in a single append, only `status="ok"` counts as done, stalls are retried
but results never are, a crash in one unit does not end the loop, a usage limit
is not a result, and an interrupt exits cleanly saying how to resume.

Two failure modes #356 did not have, both of which must be handled per task: a
generated server that will not start, and a container or port leaked by a crash
into the next task.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, computed_field

# ---------------------------------------------------------------------------
# Pre-registered constants. Do not revise after the first run of either arm.
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-5"
FRAMEWORK = "fastapi"
CONDITION = "L1-Arch"
N_PER_ARM = 25

#: Percentage-point difference (arm B - arm A) in structural compliance rate.
WIN_DELTA_PP = 25.0
KILL_DELTA_PP = 10.0

#: Arm B's mean behavioral pass rate among compliant runs may sit at most this
#: far below arm A's. Further, and a structural win was bought by breaking the
#: server, which is VOID rather than a qualified win.
MAX_BEHAVIORAL_REGRESSION_PP = 5.0

#: Runs once, at the same N, if and only if the first cut reads EXPAND. Pooled
#: to 75/arm, which is powered for WIN_DELTA_PP. There is no second expansion.
EXPANSION_FRAMEWORKS = ("flask", "django")

#: Arm B stops correcting after this many `archy check` cycles. Exceeding it is
#: recorded on the row and the run is scored as it stands; it is not retried,
#: because re-rolling a result selects for the runs that happened to converge.
MAX_CORRECTION_ITERATIONS = 10

Z = 1.959963984540054  # two-sided 95%


class ArmSummary(BaseModel):
    """One arm, reduced to the quantities the pre-registered reading needs."""

    model_config = ConfigDict(frozen=True)

    arm: str
    n_rows: int
    #: Runs whose tree could be evaluated at all. The primary's denominator.
    n_structural_evaluable: int
    n_compliant: int
    #: Mean Hurl pass rate over compliant runs whose behaviour was evaluable.
    #: None when no such run exists, which is not the same as 0.0.
    behavioral_mean: float | None
    n_behavioral_evaluable: int
    #: Paper's headline: behavioral pass rate, zeroed on non-compliance, over
    #: runs evaluable on both axes. Reported, never gating.
    composite_mean: float | None

    # @computed_field, not a plain @property: FastMCP and every JSON consumer
    # here go through model_dump(), which silently drops a plain property. That
    # exact omission shipped once already (#371) and read as a passing gate.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def compliance_rate(self) -> float | None:
        if self.n_structural_evaluable == 0:
            return None
        return self.n_compliant / self.n_structural_evaluable


class Verdict(BaseModel):
    """The pre-registered reading, computed rather than argued."""

    model_config = ConfigDict(frozen=True)

    verdict: str  # WIN | KILL | EXPAND | VOID | UNREADABLE
    reason: str
    delta_pp: float | None
    ci_low_pp: float | None
    ci_high_pp: float | None
    behavioral_delta_pp: float | None
    guardrail: str  # held | regressed | unevaluable


def wilson(successes: int, n: int) -> tuple[float, float]:
    """95% Wilson score interval. Handles 0/n and n/n, which normal
    approximation does not, and this study expects both."""
    if n == 0:
        raise ValueError("no observations")
    p = successes / n
    denom = 1 + Z**2 / n
    center = (p + Z**2 / (2 * n)) / denom
    half = Z * math.sqrt(p * (1 - p) / n + Z**2 / (4 * n**2)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def newcombe_diff_ci(a_successes: int, a_n: int, b_successes: int, b_n: int) -> tuple[float, float]:
    """95% CI on (B - A) by Newcombe's hybrid score method.

    Chosen over the Wald interval because the arms may well land at or near 0/n
    or n/n: arm B is required to satisfy the checker before it may finish, and a
    Wald interval of zero width at 25/25 would read as certainty.
    """
    l_a, u_a = wilson(a_successes, a_n)
    l_b, u_b = wilson(b_successes, b_n)
    p_a, p_b = a_successes / a_n, b_successes / b_n
    diff = p_b - p_a
    lower = diff - math.sqrt((p_b - l_b) ** 2 + (u_a - p_a) ** 2)
    upper = diff + math.sqrt((u_b - p_b) ** 2 + (p_a - l_a) ** 2)
    return max(-1.0, lower), min(1.0, upper)


def verdict(arm_a: ArmSummary, arm_b: ArmSummary) -> Verdict:
    """Apply the pre-registered thresholds. No judgement call is left here."""
    rate_a, rate_b = arm_a.compliance_rate, arm_b.compliance_rate
    if rate_a is None or rate_b is None:
        return Verdict(
            verdict="UNREADABLE",
            reason=(
                "an arm has no structurally evaluable run, so there is no rate "
                "to compare. This is a harness failure, not a result."
            ),
            delta_pp=None,
            ci_low_pp=None,
            ci_high_pp=None,
            behavioral_delta_pp=None,
            guardrail="unevaluable",
        )

    delta_pp = (rate_b - rate_a) * 100
    low, high = newcombe_diff_ci(
        arm_a.n_compliant,
        arm_a.n_structural_evaluable,
        arm_b.n_compliant,
        arm_b.n_structural_evaluable,
    )
    ci_low_pp, ci_high_pp = low * 100, high * 100
    ci_excludes_zero = ci_low_pp > 0 or ci_high_pp < 0

    if arm_a.behavioral_mean is None or arm_b.behavioral_mean is None:
        guardrail, behavioral_delta_pp = "unevaluable", None
    else:
        behavioral_delta_pp = (arm_b.behavioral_mean - arm_a.behavioral_mean) * 100
        guardrail = "regressed" if behavioral_delta_pp < -MAX_BEHAVIORAL_REGRESSION_PP else "held"

    def _v(name: str, reason: str) -> Verdict:
        return Verdict(
            verdict=name,
            reason=reason,
            delta_pp=delta_pp,
            ci_low_pp=ci_low_pp,
            ci_high_pp=ci_high_pp,
            behavioral_delta_pp=behavioral_delta_pp,
            guardrail=guardrail,
        )

    if delta_pp < KILL_DELTA_PP:
        return _v(
            "KILL",
            f"delta {delta_pp:+.1f} pp is below the pre-registered "
            f"{KILL_DELTA_PP:+.0f} pp floor. archy in the loop does not move "
            "greenfield compliance enough to matter, and that belongs in "
            "docs/WHAT_DIDNT_WORK.md.",
        )

    if delta_pp >= WIN_DELTA_PP and ci_excludes_zero:
        if guardrail == "regressed":
            return _v(
                "VOID",
                f"structural delta {delta_pp:+.1f} pp cleared the bar, but the "
                f"behavioral pass rate among compliant runs fell "
                f"{behavioral_delta_pp:+.1f} pp, past the "
                f"{-MAX_BEHAVIORAL_REGRESSION_PP:.0f} pp guardrail. The "
                "structure was bought by breaking the server.",
            )
        if guardrail == "unevaluable":
            return _v(
                "VOID",
                f"structural delta {delta_pp:+.1f} pp cleared the bar, but no "
                "arm has an evaluable behavioral rate, so the guardrail could "
                "not be checked. An unchecked guardrail is not a passed one.",
            )
        return _v(
            "WIN",
            f"delta {delta_pp:+.1f} pp (95% CI {ci_low_pp:+.1f} to "
            f"{ci_high_pp:+.1f}) clears {WIN_DELTA_PP:+.0f} pp with the "
            "interval excluding 0, and behaviour held. Claim: greenfield "
            "scaffolding compliance only. This says nothing about guarding an "
            "existing codebase, which #356 tested and found no headroom for.",
        )

    return _v(
        "EXPAND",
        f"delta {delta_pp:+.1f} pp (95% CI {ci_low_pp:+.1f} to {ci_high_pp:+.1f}) "
        f"is above the {KILL_DELTA_PP:+.0f} pp floor but not a powered win at "
        f"N={N_PER_ARM}. Run {' and '.join(EXPANSION_FRAMEWORKS)} at the same N "
        "and re-read the pooled result against these same thresholds. This runs "
        "once; a middling pooled result is a null.",
    )


def summarize_rows(rows: list[dict], arm: str) -> ArmSummary:
    """Reduce ledger rows to an `ArmSummary`.

    Only `status="ok"` rows are read, matching `q1b_run.py`: an `error` or
    `stalled` row is work still owed, not an outcome.
    """
    mine = [r for r in rows if r.get("arm") == arm and r.get("status") == "ok"]

    structural = [r for r in mine if (r.get("structural") or {}).get("evaluable")]
    compliant = [r for r in structural if (r["structural"] or {}).get("compliant")]

    behavioral = [
        r
        for r in compliant
        if (r.get("behavioral") or {}).get("evaluable")
        and (r.get("behavioral") or {}).get("pass_rate") is not None
    ]
    behavioral_mean = (
        sum(r["behavioral"]["pass_rate"] for r in behavioral) / len(behavioral)
        if behavioral
        else None
    )

    # Composite needs both axes, so its denominator is the runs evaluable on
    # both. A non-compliant run contributes 0, which is the paper's rule; an
    # unevaluable one is absent, which is this repo's.
    both = [
        r
        for r in structural
        if (r.get("behavioral") or {}).get("evaluable")
        and (r.get("behavioral") or {}).get("pass_rate") is not None
    ]
    composite_mean = (
        sum(r["behavioral"]["pass_rate"] if r["structural"]["compliant"] else 0.0 for r in both)
        / len(both)
        if both
        else None
    )

    return ArmSummary(
        arm=arm,
        n_rows=len(mine),
        n_structural_evaluable=len(structural),
        n_compliant=len(compliant),
        behavioral_mean=behavioral_mean,
        n_behavioral_evaluable=len(behavioral),
        composite_mean=composite_mean,
    )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render(arm_a: ArmSummary, arm_b: ArmSummary, result: Verdict) -> str:
    lines = [
        f"Greenfield compliance A/B (#369) - {MODEL}, {FRAMEWORK}, {CONDITION}",
        "",
        f"{'':22} {'arm A (static)':>16} {'arm B (archy)':>16}",
        f"{'rows (status=ok)':22} {arm_a.n_rows:>16} {arm_b.n_rows:>16}",
        f"{'structurally evaluable':22} {arm_a.n_structural_evaluable:>16}"
        f" {arm_b.n_structural_evaluable:>16}",
        f"{'compliant':22} {arm_a.n_compliant:>16} {arm_b.n_compliant:>16}",
        f"{'compliance rate':22} {_pct(arm_a.compliance_rate):>16}"
        f" {_pct(arm_b.compliance_rate):>16}",
        f"{'behavioral (compliant)':22} {_pct(arm_a.behavioral_mean):>16}"
        f" {_pct(arm_b.behavioral_mean):>16}",
        f"{'composite A%':22} {_pct(arm_a.composite_mean):>16} {_pct(arm_b.composite_mean):>16}",
        "",
        f"VERDICT: {result.verdict}",
        f"  {result.reason}",
        "",
        "Pre-registered in bench/greenfield_prereg.py. Do not revise now.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--show", action="store_true", help="print the pre-registration")
    group.add_argument("--score", type=Path, help="score a completed ledger against it")
    parser.add_argument("--json", action="store_true", help="machine-readable")
    args = parser.parse_args()

    if args.show:
        print(__doc__)
        return 0

    rows = [json.loads(line) for line in args.score.read_text().splitlines() if line.strip()]
    arm_a = summarize_rows(rows, "A")
    arm_b = summarize_rows(rows, "B")
    result = verdict(arm_a, arm_b)

    if args.json:
        print(
            json.dumps(
                {
                    "arm_a": arm_a.model_dump(),
                    "arm_b": arm_b.model_dump(),
                    "verdict": result.model_dump(),
                },
                indent=2,
            )
        )
    else:
        print(render(arm_a, arm_b, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
