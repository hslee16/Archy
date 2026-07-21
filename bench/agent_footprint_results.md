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

**N=10 result (20 runs, 0 regressions, 0 no-edit, 20/20 completed):**

| metric | A median | C median | median Δ (C−A) | C<A / C>A / tie | sign p |
|---|---|---|---|---|---|
| **pre_edit_reads** (record) | 13.0 | 8.5 | **−3.5** | 8 / 2 / 0 | **0.109** |
| pre_edit_distinct_files | 4.0 | 3.0 | 0.0 | 4 / 3 / 3 | 1.000 |
| turns (num_turns) | 44.5 | 35.0 | −8.0 | 7 / 2 / 1 | 0.180 |
| footprint_tokens | 16,632 | 14,157 | −2,983 | 6 / 4 / 0 | 0.754 |
| file_revisitations | 3.0 | 2.0 | 0.0 | 4 / 3 / 3 | 1.000 |

`pre_edit_reads` deltas (C−A): `[−10, −9, −7, −5, −5, −2, −2, −1, +2, +9]`. The brief
was charged 581 tokens against arm C; net of that, arm C's mean footprint (15,024)
is still below arm A's (17,300), so the reduction is not a bookkeeping artifact.

**Read (honest, under-powered):** the brief reduced pre-edit reads directionally
(median −3.5, ~27%, 8/10 pairs) with **no correctness or regression cost**, but the
sign test (**p=0.109**) does not clear 0.05 at N=10. Per §8 this is a trend, not a
result. The **mechanism is not breadth substitution**: `pre_edit_distinct_files` is
unchanged (Δ=0), so the brief did not shrink the *set* of files opened; it cut
*redundant read-calls and turns* over the same ~3-file spine. `pre_edit_input_tokens`
is not headlined (caching makes non-cache input tiny); footprint is output-dominated
and noisy.

**Follow-ups filed:** power the run to N≈30 for significance (#292); task-conditioned
focus to lift the 0.33 precision (#291); brief precision/recall as a standing metric
(#290); split the metric into reads-per-file + turns-to-first-edit, since breadth did
not move (#293). _N≈30 extension running; this table updates to the pooled result
when it lands._
