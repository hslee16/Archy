# Agent-footprint bench: results

Protocol and metric definitions: [`docs/SPEC_AGENT_FOOTPRINT_BENCH.md`](../docs/SPEC_AGENT_FOOTPRINT_BENCH.md).
Harness: [`bench/agent_footprint.py`](agent_footprint.py). Parser unit tests:
`tests/test_agent_footprint.py`.

## Status

**Harness landed and validated end-to-end; no comparative numbers yet.** The
deterministic core (`parse_transcript`, `summarize`) is CI-tested against a
synthetic transcript fixture. The live runner (`run_variant` / `run_pair`)
invokes `claude` headless and needs real agent time, so it runs out of band,
not in CI.

**Runner validation (no API key required).** A single live `run_variant` was
exercised inside a logged-in Claude Code session, authenticating via the
session's own subscription login with no `ANTHROPIC_API_KEY`. The run completed
(a trivial edit task), and `parse_transcript`'s token totals matched the CLI's
own `usage` object exactly (input 26 / output 456, 3 turns). Two bugs the
synthetic fixture could not have caught surfaced and were fixed:

- **Project-slug sanitization.** Claude Code names `~/.claude/projects/<slug>/`
  by replacing *every* non-alphanumeric char (`/`, `_`, `.`) with `-`, not just
  `/`. The first version missed the transcript for any path containing `_`.
- **Streaming-duplicate dedup.** The transcript writes several partials of one
  assistant message (same `message.id`, identical `usage`); summing raw lines
  triple-counted tokens. `parse_transcript` now dedupes by message id.
- Config note: `--bare` breaks `-p` headless execution in this nested context;
  `--setting-sources local` is the working isolation substitute. See the spec
  section 10.

The first *comparative* minimal-pair (below) is the next step; this file will
carry its A-vs-B numbers when it runs.

## First live-pair target (chosen, not yet run)

Selected by running archy on the candidate, so variant B applies a *real*
archy recommendation rather than an arbitrary cleanup.

- **Repo:** `pallets/flask` @ `36e4a82` (already pinned in `bench/projects.yaml`;
  ~5.5k commits of history, so churn and co-change signals are populated).
- **Test gate:** flask's own suite, `pytest -q` (the regression gate of spec
  section 7; a variant is only admitted if the pristine suite is green).
- **Variant B refactor (archy's #1 `what_to_refactor_next`, fused lens):**
  decompose **`flask.sansio.app`** (`src/flask/sansio/app.py`, the `App` base
  class). archy ranks it first by a wide margin:

  ```
  1. flask.sansio.app  [hotspot+edit_risk]  priority=1.837
     complexity x churn hotspot (cc_sum=87, churn=508) AND high edit-risk
     (risk=0.40: central and fragile)
  2. flask.cli          priority=1.295
  3. flask.helpers      priority=1.025
  ```

  It is a genuine god-object (the central Flask application base class), and its
  co-change partner `flask.globals` shows up as the top hidden-coupling pair
  (`conf=0.68`, 15 co-commits), so edits here already ripple. The variant-B diff
  keeps `App`'s public behavior identical (the full suite must stay green) while
  splitting the class so a typical edit lands in one grep-targetable place
  instead of scattering across the god-class.

- **Task (externally described, names no files, per the paper's task rule):** a
  Flask feature/bug task whose natural implementation touches the
  application-configuration / blueprint-registration surface that lives in
  `App` (so it spans `sansio.app` + `blueprints` + `ctx`), phrased purely by
  input/output behavior. Exact wording is fixed at run time and recorded
  verbatim with the results.

- **Runs:** `n >= 10` per variant, interleaved; report the paired B-minus-A
  distribution (median delta + sign counts), never a single pair.

Why flask: a dominant, unambiguous archy recommendation on a real god-object; a
fast, green, well-known test suite for the gate; and an app-centric task that
spans modules, which is where the paper's footprint effect concentrates.

## Reading these numbers (when they exist)

- **Footprint, not correctness.** No pass-rate claim; cleanliness moved
  footprint but not pass rate in the motivating study (§14c.6 of
  `RESEARCH_METRICS.md`). `task_completed` means the agent finished, not that it
  was correct.
- **Tokens, not dollars** in any headline; `total_cost_usd` stays in the raw
  table only.
- **One-config caveat:** every result names the exact model and CLI flags; one
  model/harness is not a general law.
- **Publish the null.** If applying archy's recommendation does not move
  footprint outside the noise band (the paper saw ~2.5x per-task variance, hence
  `n >= 10`), that is the result and it ships as such.

_No A-vs-B (refactor-study) results yet. The first live comparative run was the
arm-C context-injection study below (#289), which reuses the same harness._

## Arm C: context-injection (#289), first run

The #289 read-reduction question (does injecting an archy pre-edit brief reduce a
coding agent's exploratory reads before its first edit) ran first, because it needs
no refactored variant B, only a brief prepended to the prompt. Protocol:
[`SPEC_AGENT_FOOTPRINT_BENCH.md` §14](../docs/SPEC_AGENT_FOOTPRINT_BENCH.md);
framing: [`docs/research/PREWALK_READ_REDUCTION_SYNTHESIS.md`](../docs/research/PREWALK_READ_REDUCTION_SYNTHESIS.md).

**Config (one-config caveat):** flask @ `36e4a82`; `claude-sonnet-5` headless;
`--allowedTools Read,Write,Edit,Bash,Grep,Glob --setting-sources local`; fresh
`git reset --hard` per run; interleaved A (no brief) / C (581-token archy brief).
Task: add priority ordering to Flask's app-context teardown callbacks, described by
behavior only (names no files). Its true edit surface is 3 files
(`sansio/app.py`, `app.py`, `ctx.py`); the brief named 9 (recall 3/3, **precision
0.33**).

**Result: N=22 pooled (44 runs, 0 regressions, 0 no-edit, all completed).** The run
targeted N≈30 (#292); a stalled headless `claude` stream at run 22 was killed and the
22 completed pairs pooled (runs 0-9 + 10-12 + 13-21; run 22 unpaired, dropped).

| metric | A median | C median | median Δ (C−A) | C<A / C>A / tie | sign p |
|---|---|---|---|---|---|
| **pre_edit_reads** (record) | 11.5 | 9.0 | −2.0 | 14 / 8 / 0 | **0.286** |
| pre_edit_distinct_files | 4.0 | 3.0 | 0.0 | 9 / 6 / 7 | 0.607 |
| turns (num_turns) | 42.0 | 35.0 | −5.0 | 15 / 5 / 2 | 0.041 |
| footprint_tokens | 16,632 | 12,687 | −2,325 | 12 / 10 / 0 | 0.832 |
| file_revisitations | 2.5 | 2.0 | 0.0 | 7 / 6 / 9 | 1.000 |

`pre_edit_reads` deltas (C−A): `[−10,−9,−8,−7,−5,−5,−5,−4,−4,−3,−2,−2,−2,−1,+1,+1,+2,+2,+3,+4,+5,+9]`,
mean −1.82.

**Read (honest): a documented non-result for the read-reduction claim.** At N=10 the
brief looked promising (median −3.5, 8/10, p=0.109); **doubling N pulled it back into
the noise** (median −2.0, 14/8, **p=0.286**). So #289's central question, "does an
archy brief reduce the reads an agent does before editing," is **not supported at this
config.** The N=10 figure was underpowered optimism, exactly the failure mode `n>=10`
and "publish the null" (§8-9) exist to catch. `num_turns` is nominally lower
(p=0.041, −5 median) but with 5 metrics tested it does **not survive multiple-comparison
correction** (~0.21), and it is not a read count, so it is not headlined as read
reduction (that would be the relabeling §14.6 forbids). Breadth
(`pre_edit_distinct_files`) and revisitation are flat, as at N=10: the brief never
shrank the ~3-file spine. Zero regressions throughout, so no correctness signal either
way.

**Go/no-go:** **NO-GO** on an `archy brief` feature, now on evidence (the effect
regressed to null on more data), not just low power. This validates the anti-theater
gate: a feature shipped on the N=10 hint would have been theater.

**Follow-up disposition:** #292 (power to N≈30) is effectively answered here (null at
N=22); #291 (task-conditioned focus) keeps a weak prior since breadth never moved;
#290 (brief precision metric) and #293 (metric split) remain useful measurement infra.
One-config caveat stands: one model (`claude-sonnet-5`), one repo, one task; a
different model or a multi-module task with a wider true surface could differ, but the
burden is now on a positive result to appear, not on this null to be explained away.
