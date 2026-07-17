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

_No results table yet; this section fills in when the first pair runs._
