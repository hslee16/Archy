# Should `overall` reflect a single structural regression at scale? (issue #192)

Companion to [`SCORING.md`](../SCORING.md), [`SCORE_SHAPE_REDESIGN_EMPIRICS.md`](SCORE_SHAPE_REDESIGN_EMPIRICS.md)
(the score-shape empirical method this reuses), and the #178 direction harness
[`bench/delta_direction.py`](../../bench/delta_direction.py). Harness for this
study: [`bench/acyclicity_dilution.py`](../../bench/acyclicity_dilution.py); raw
numbers: [`bench/acyclicity_dilution_results.md`](../../bench/acyclicity_dilution_results.md).
Run 2026-06-24 over the 11 repos in `bench/replay_cache/` plus synthetic sizes.

## Decision

**Keep the acyclicity axis as `1 - tangle_ratio`. Do not blend a count-sensitive
term into the composite. The dilution of a single cycle's effect on `overall` at
scale is an intended, now-empirically-validated property, not a bug.** The
per-edge regression signal lives in `archy_diff` (`cycles.added`, FP-free, and
the acyclicity-axis delta sign, asserted strictly negative by
`delta_direction.py`), not in `overall`, which is a slow health-state metric.

This closes the question #178/PR #191 deferred. No change to `src/archy/score.py`;
no `.archy/history.jsonl` renormalization; `bench/delta_direction.py`'s survival
column is unchanged.

## The question

PR #191 pinned the *narrow* #178 question ("is the dilution a bug?") as
**intended**, consistent with `tangle_ratio` design: a small isolated cycle in a
large codebase is a smaller pathology than the same cycle dominating a small one.
It left open the *deeper* design question: even if intended, **should `overall`
be redesigned so a newly-introduced cycle still registers on it at scale**,
rather than diluting to a fraction of a percent on a 5000-node graph?

[#192](https://github.com/hslee16/archy/issues/192) listed three options:

1. Leave as-is (status quo).
2. A non-diluting / less-diluting acyclicity term (blend `tangle_ratio` with a
   count-sensitive term).
3. A dedicated per-edge regression signal folded into the composite.

This is explicitly an empirical question (it changes the score number and breaks
trend continuity), so it was answered with a corpus sweep, not a judgment call.

## Method

Three count-sensitive candidates were compared against the current axis. Only the
acyclicity term is swapped; the other four axes (modularity, depth, equality,
complexity) and the geometric mean are unchanged, so the comparison isolates the
axis change.

| candidate | acyclicity formula | idea |
| --- | --- | --- |
| current | `1 - tangle_ratio` | proportional pathology |
| A_countlin | `1 - clamp(tangle_ratio + 0.05 * cycle_count)` | flat penalty per cycle |
| B_floor | `1 - max(tangle_ratio, min(0.5, 0.05*cycle_count))` | per-cycle floor under the proportion |
| C_logcount | `1 - clamp(tangle_ratio + 0.06 * ln(1+cycle_count))` | diminishing count penalty |

Three axes of evidence (full tables in the results file): the clean-graph axis
penalty, the absolute single-cycle `overall` response, and corpus rank stability.

## Findings

### 1. The count candidates DO raise the single-cycle response (this is not the issue)

On the largest synthetic graph (5000 modules), injecting one 2-cycle moves
`overall` by `5.3e-6` under the current axis and by `2.7e-3` under `A_countlin` --
a ~500x increase. So a count term *can* make a single regression register on
`overall` at scale. The case against it is not that it fails to do what it is
for; it is the cost of doing it.

### 2. The cost: a count term inverts the proportional-pathology rationale and penalizes large healthy codebases

Because a count penalty is decoupled from graph size, it docks a large,
near-acyclic codebase the same per-cycle amount as a tiny tangled one:

| graph | modules | tangle_ratio | cycles | current acy | A_countlin acy |
| --- | --: | --: | --: | --: | --: |
| **fastapi** | 1118 | 1.0% | 2 | **0.990** | **0.890** |
| click | 22 | 59.1% | 2 | 0.409 | 0.309 |

fastapi is **99.0% acyclic** -- 2 small isolated cycles in 1118 modules -- yet
`A_countlin` scores its acyclicity axis at 0.890, docking it `0.10` for those two
cycles, the *same absolute dock* a 22-module repo that is 59% tangled pays. A
count term scores fastapi as if 2 isolated cycles were a tenth of its structural
health. This directly contradicts the documented `tangle_ratio` rationale and the
[`CASE_STUDIES.md`](../CASE_STUDIES.md) observation that large mature repos carry
more cycles in stock; it would systematically rank big codebases as less acyclic
for proportionally-tiny pathology. It also confounds the axis with raw module
count, eroding the inter-axis independence the OECD composite check requires (the
same gate that governs every other axis decision in `SCORE_SHAPE_REDESIGN_EMPIRICS.md`).

### 3. The per-edge signal the change is meant to provide already exists, FP-free

The motivation for wanting `overall` to register a single cycle is the agent edit
loop. But that signal is already delivered, exactly and without false positives,
by `archy_diff`: `cycles.added` (the precise module pair) and the acyclicity-axis
delta sign, which `delta_direction.py` HARD-asserts strictly negative on every
corpus and synthetic graph. Folding a count term into `overall` would duplicate
that FP-free signal with a lossy, size-confounded proxy and add nothing an agent
reads. `overall` is the slow-moving health-state trend; making it a per-edge
detector is a category error.

### 4. No axis swap fixes the actual wrong-direction case

On a clean single-2-cycle inject, *every* candidate -- including the current axis
-- gets the `overall` sign correct (14/14). The real wrong-direction events
(#178's `+0.005` at N=281; the
[in-loop prevalence](INLOOP_PREVALENCE_EMPIRICS.md) finding that the corpus's
worst structural regression, the requests 8-module SCC, carried a `+0.012`
`overall` delta) come from *multi-change* commits where modularity, depth, and
equality move at once. That is inherent to any composite of several axes; no
acyclicity-term change addresses it. The lesson those cases teach is archy's
existing discipline -- gate on `cycles.added`, not on `overall` direction -- not
"make `overall` more cycle-sensitive."

### 5. Even rank stability argues against bothering

`B_floor` and `C_logcount` barely move the corpus ranking (Spearman rho = 0.996
vs current), and `A_countlin` moves it more (0.960). High rank stability means the
candidate would break `.archy/history.jsonl` trend continuity and sentrux
comparability for every user while changing almost nothing about the leaderboard
-- all migration cost, no discriminant benefit, on top of the active harm in
finding 2.

## Conclusion

All three options reduce to: status quo is correct. A count-sensitive acyclicity
term works mechanically (finding 1) but only by penalizing large healthy
codebases for proportionally-small pathology (finding 2), duplicating a signal
that already exists FP-free (finding 3), failing to fix the real wrong-direction
case (finding 4), and breaking trend continuity for no leaderboard benefit
(finding 5). The dilution is a property of a correctly-normalized health metric,
not a defect. Documented as such in [`SCORING.md`](../SCORING.md); the per-edge
direction signal remains `archy_diff`'s `cycles.added` and the acyclicity delta,
validated by [`bench/delta_direction.py`](../../bench/delta_direction.py).
