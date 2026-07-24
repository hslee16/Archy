# Agent-footprint bench: results

Protocol and metric definitions: [`docs/SPEC_AGENT_FOOTPRINT_BENCH.md`](../docs/SPEC_AGENT_FOOTPRINT_BENCH.md).
Harness: [`bench/agent_footprint.py`](agent_footprint.py). Parser unit tests:
`tests/test_agent_footprint.py`.

## Status

**Both studies have now run: arm C (#289, N=22) and the A-vs-B refactor study
(#282, N=10). Both are documented non-results.** See the two result sections
below. The rest of this section is the harness-validation history.

**Harness landed and validated end-to-end.** The
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

Two further bugs surfaced when the first *comparative* run was wired up, both of
which would have invalidated an A-vs-B result had they shipped:

- **No reset between runs.** `run_pair` reused the same checkout for all runs, so
  run i+1 started from run i's edits and measured a repo that no longer matched
  the variant under test. `_reset_repo` (hard reset + clean) now runs before every
  run, which is spec section 8's "fresh checkout per run".
- **Baseline never measured.** `baseline_failed` was hardcoded `False`, so the
  section 7 regression gate could not distinguish "the agent broke the suite" from
  "the suite was already red". It is now measured once per variant on the pristine
  tree and threaded into the per-run gate.
- Durability: each row is appended to `records.jsonl` as it completes. A pair run
  is hours of paid agent time and one failed `claude` invocation raises out of the
  loop; rows already paid for now survive it.

## First live-pair target (as run)

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

### Pre-registered analysis plan (fixed at 5/10 pairs, before the result)

Recorded while the run was still in flight and before any n=10 numbers existed,
so the stopping rule cannot be chosen to suit the answer:

- **N=10, no extension.** Spec section 8 allows raising N when the interval still
  crosses zero; that option is declined here in advance. Whatever the 10 pairs
  say is the reported result.
- **Stated power limit, not a hidden one.** A two-sided sign test at N=10 needs
  9-of-10 in one direction to reach p<0.05 (9-1 -> 0.021; 8-2 -> 0.109). If the
  true per-pair win rate were 0.8, this run would return significance only ~37%
  of the time. So N=10 can rule out a *large and consistent* footprint effect on
  this config; it cannot separate "no effect" from "moderate effect", and the
  writeup says so either way.
- **A borderline result does not buy more pairs of the same cell.** If the metric
  of record lands near the line, the follow-up is a second task or a second repo
  (generalizability), not more runs of one repo/task/model until something
  crosses 0.05. The arm-C study below is the precedent: N=10 looked promising
  (p=0.109) and doubling N pulled it back to null (p=0.286).
- **Admissibility** (fixed in advance): a pair counts only if both sides made an
  edit, completed, and left the pre-existing suite green; excluded pairs are
  reported with the reason, not silently dropped.

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

## A-vs-B refactor study (#282), first run

**Config (one-config caveat):** flask @ `36e4a82`; `claude-sonnet-5` headless;
`--allowedTools Read,Write,Edit,Bash,Grep,Glob --setting-sources local`;
`git reset --hard` + `git clean -fdx` before every run; A and B interleaved.
Test gate: flask's own suite (491 tests), green on both pristine variants.
Variant B diff: [`agent_footprint/variant_b_flask_sansio_app.patch`](agent_footprint/variant_b_flask_sansio_app.patch).
Task, verbatim: [`agent_footprint/task_flask_endpoint_origins.md`](agent_footprint/task_flask_endpoint_origins.md)
(add `endpoint_origins()` plus an `ENDPOINT_ORIGIN_STRICT` config key; described by
behavior, names no files). 20 runs, 145 minutes of agent wall clock.

**Variant B, as admitted:** the `App` god-class split into three mixins,
`app_templating.py` (Jinja filter/test/global registration), `app_routing.py`
(blueprint + URL-rule registration, URL-build hooks) and `app_errors.py` (error
dispatch, redirect); `sansio/app.py` 1013 -> 611 lines. Behavior-preserving:
491/491 green, identical to A. archy confirms its own recommendation landed:
`flask.sansio.app` cc_sum 87 -> 27, hotspot 44196 -> 13716, rank #1 -> #2.

**Result: N=10 pairs, all 10 admissible** (every run edited, completed, and left
the suite green; 0 regressions, 0 no-edit runs).

| metric | A median | B median | median Δ (B−A) | B<A / B>A / tie | sign p |
|---|---|---|---|---|---|
| **pre_edit_reads** (record) | 10.0 | 8.0 | −3.5 | 7 / 3 / 0 | 0.344 |
| pre_edit_distinct_files | 3.0 | 4.5 | +1.0 | 0 / 8 / 2 | 0.008 |
| num_turns | 51.5 | 46.5 | −3.5 | 5 / 5 / 0 | 1.000 |
| file_revisitations | 6.0 | 5.0 | −1.0 | 5 / 5 / 0 | 1.000 |
| footprint_tokens | 4,326,198 | 3,708,174 | +592,832 | 3 / 7 / 0 | 0.344 |
| output_tokens | 32,330 | 34,766 | +4,190 | 4 / 6 / 0 | 0.754 |

`pre_edit_reads` deltas (B−A): `[+2,+1,−3,−5,−6,−4,−1,+2,−9,−4]`.

**Read (honest): a documented non-result for the footprint claim.** Applying
archy's #1 recommendation did not move agent footprint outside the noise band on
this config. The metric of record leans B's way in direction (median −3.5 reads,
7 of 10 pairs) but nowhere near significance (p=0.344), and the token headline
leans the *other* way (B higher in 7 of 10). Note the per-variant medians and the
paired median disagree in sign on `footprint_tokens`: with an IQR spanning roughly
±1.8M tokens, that is the spread talking, and it is why the paired test, not the
medians, is the result.

**The one significant cell is in the wrong direction and does not survive
correction.** `pre_edit_distinct_files` is higher for B in 8 of 10 pairs
(p=0.008), but 9 metrics were tested; Bonferroni puts it at ~0.072. It is also
**mechanically confounded by the refactor itself**: B split one file into four, so
reaching the same surface necessarily opens more distinct files even when it takes
fewer reads. Read together with `pre_edit_reads` (fewer reads, more files), the
plausible story is that decomposition made the agent's exploration *shallower but
wider*, which is what a decomposition should do, and which this metric penalizes by
construction. That is a measurement finding, not a result about archy: **breadth
metrics are not variant-neutral when variant B changes the file count**, and any
future decomposition study needs a file-count-normalized breadth metric or must
drop the metric.

**Go/no-go:** this bench does not gate a feature (unlike #289, which gated
`archy brief`). What it rules out is a **claim**: archy must not say its
recommendations cut an agent's token footprint. `what_to_refactor_next` continues
to stand on human maintainability and edit risk, which this bench does not measure
and does not challenge.

**Two nulls now, and that is the pattern worth carrying.** #289 (context injection,
N=22) and #282 (refactor, N=10) both land null at archy's layer on this model. The
"cleaner code measurably helps the agent" thesis is not reproducing here, and
roadmap items premised on footprint reduction should be priced accordingly.

**Power, as pre-registered above:** N=10 needed 9-of-10 to reach p<0.05, and the
run was arithmetically out of reach of significance on `pre_edit_reads` by pair 7.
This rules out a large, consistent effect; it cannot separate "no effect" from
"moderate effect". Per the pre-registration, the follow-up is a second task or a
second repo, not more pairs of this cell.

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
