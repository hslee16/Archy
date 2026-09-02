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

**Raw source.** The 20 session transcripts these records derive from are kept at
`bench/agent_footprint_transcripts/` (gitignored, ~9MB). They are **not
regenerable** short of 20 fresh live agent runs, and they were needed twice when
a metric definition changed, so they are retained deliberately: without them
#282 would be recomputable only at the record level, which is the position #289
is in and the reason its table carries a provenance caveat.

**These numbers were recomputed on 2026-07-24 after a parser bug was found
(round-4 adversarial review).** `parse_transcript` deduped Claude Code's
one-line-per-content-block transcripts by keeping the *last* line per
`message.id`, on a wrong model of the format, which silently discarded every
block but the last whenever one assistant message carried several. 9 of the 20
rows were affected and the loss was asymmetric (7 of the 9 were B rows), so it
was not a constant offset. The 20 run transcripts were still on disk, so the
records were re-derived from them rather than the study being re-run; run-level
fields (`num_turns`, duration, cost, gate outcome) come from the original CLI
results, which the bug never touched. What moved: `pre_edit_reads` median −3.5 ->
−3.0 (same 7/3 split, same p), and `pre_edit_distinct_files` 8/10 p=0.0078 ->
**10/10 p=0.002**. Both published conclusions are unchanged; the breadth cell got
stronger, which matters only for the confound discussion below.

**Result: N=10 pairs, all 10 admissible** (every run edited, completed, and left
the suite green; 0 regressions, 0 no-edit runs).

<!-- generated: python bench/agent_footprint.py --table bench/agent_footprint/records_282_flask.jsonl -->

| metric | A median | B median | median delta (B-A) | IQR of deltas | B<A / B>A / tie | sign p |
|---|---|---|---|---|---|---|
| footprint_tokens | 32,458.0 | 34,875.0 | +4,213.0 | [-7,135.5, +10,981.0] | 4 / 6 / 0 | 0.754 |
| input_tokens | 109.0 | 95.0 | +10.0 | [-48.0, +27.5] | 4 / 6 / 0 | 0.754 |
| output_tokens | 32,330.0 | 34,766.0 | +4,190.0 | [-7,357.2, +10,958.0] | 4 / 6 / 0 | 0.754 |
| num_turns | 51.5 | 46.5 | -3.5 | [-20.8, +12.2] | 5 / 5 / 0 | 1.000 |
| file_revisitations | 6.0 | 5.0 | -1.0 | [-4.8, +1.8] | 5 / 5 / 0 | 1.000 |
| canonical_distinct_files_touched | 5.0 | 4.5 | -0.5 | [-1.0, +1.5] | 5 / 3 / 2 | 0.727 |
| canonical_pre_edit_distinct_files | 3.0 | 4.0 | +1.0 | [+0.0, +1.0] | 0 / 6 / 4 | 0.031 |
| distinct_files_touched | 5.0 | 5.5 | +0.5 | [+0.0, +2.5] | 1 / 5 / 4 | 0.219 |
| pre_edit_distinct_files | 3.0 | 5.0 | +1.5 | [+1.0, +2.0] | 0 / 10 / 0 | 0.002 |
| pre_edit_reads | 10.0 | 9.0 | -3.0 | [-3.8, +1.2] | 7 / 3 / 0 | 0.344 |
| pre_edit_input_tokens | 37.0 | 30.0 | -3.0 | [-15.5, +2.5] | 7 / 3 / 0 | 0.344 |

N=10 pairs; 11 metrics tested, so a nominal p must clear p x 11 to survive Bonferroni. Regressions: 0; runs with the gate disabled by a red baseline: 0; no-edit runs: 0.

The delta column is the median of the per-pair deltas, not the difference of the two median columns; with a skewed spread those disagree, and the paired median is the one the sign test refers to. Primary metric: `file_revisitations` (spec section 4). `input_tokens` is non-cache input only (spec section 5) and is ~2 tokens per turn under prompt caching, so it is a turn proxy, not a token measure. Breadth: use the `canonical_*` rows, which count pre-refactor paths; the raw `distinct_files_touched` / `pre_edit_distinct_files` rows are descriptive only and are biased against a decomposition variant by construction (spec section 12.7).

Position drift, tie-corrected Spearman of `pre_edit_reads` vs run index: A=+0.615, B=+0.346. An arm that drifts while the other does not is why section 8 requires counterbalancing.

`footprint_tokens` is non-cache input + output (spec section 5), so it tracks
`output_tokens` closely here: non-cache input was ~100 tokens per run, since
almost all input arrived as cache reads (median 4.2M for A, 3.6M for B). Those
cache-read totals are reported for contamination visibility (spec section 8) and
are deliberately **not** in the headline: they are dominated by prompt-cache
behavior, not by how much of the repo the agent actually pulled in.

`pre_edit_reads` deltas (B−A): `[+2,+2,−3,−3,−4,−4,−1,+3,−8,−3]`.
`footprint_tokens` deltas (B−A):
`[+9124,+2222,+11600,−20824,−8578,+27125,−2808,+6204,−28565,+25852]`.
`pre_edit_distinct_files` deltas (B−A): `[+1,+3,+1,+1,+2,+1,+4,+2,+1,+2]`.

**Read (honest): a documented non-result for the footprint claim.** Applying
archy's #1 recommendation did not move agent footprint outside the noise band on
this config.

**On which metric is primary, stated plainly because it cuts against the
reading below.** Spec section 4 designates `file_revisitations` the primary
metric for this A/B study (`pre_edit_reads` is arm C's, section 14.3). That
pre-registered primary is **dead flat: median −1.0, 5/5, p=1.000.** The
`pre_edit_reads` framing used below postdates the data, and the reporting code
briefly hardcoded it for every study, which would have let the table nominate
whichever metric read better after the fact. Both metrics are null, so nothing
turns on it here, but the honest headline for #282 is the flat primary, not the
suggestive secondary.

`pre_edit_reads` leans B's way in direction (median −3.0 reads,
7 of 10 pairs) but nowhere near significance (p=0.344), and the token headline
leans the *other* way (B +4,213, 6 of 10 pairs higher, p=0.754). The token
spread is the story: per-pair deltas run from −28,565 to +27,125, so a median of
+4,213 sits well inside the noise. This is the ~2.5x per-task variance the paper
warned about (spec section 8), reproduced.

**The breadth cell: about half of it was an artifact, and the rest does not
survive correction.** The raw `pre_edit_distinct_files` is higher for B in **all
10** pairs (p=0.002). But raw file counts are not variant-neutral here: B splits
one file into four, so reaching the same surface opens more files *by
construction*. Counting the same touches over **pre-refactor** paths (#302, the
`canonical_*` rows above, using the reviewable map in
[`agent_footprint/variant_b_flask_file_map.json`](agent_footprint/variant_b_flask_file_map.json))
cuts it to 0/6/4 ties, **p=0.031 nominal**, which clears no multiple-comparison
threshold (11 metrics tested; even the generous effective divisor below leaves it
far outside 0.05). Total breadth actually reverses sign under canonical counting
(`canonical_distinct_files_touched` −0.5, p=0.727).

The residual is worth stating rather than explaining away: even counting only
original-surface files, B's agents opened more distinct files before their first
edit in 6 of 10 pairs and fewer in none. That is a real, small, unconfirmed
tendency, not a null and not a finding.

On the divisor: 11 metrics are tested but several are not independent.
`footprint_tokens`, `output_tokens` and `input_tokens` return identical sign
results (4/6, p=0.754) because non-cache input is ~2 tokens per turn, and each
raw breadth metric duplicates its canonical twin. The effective number of
independent tests is nearer 6. Correcting at 6 rather than 11 still leaves the
canonical breadth cell (0.031 x 6 = 0.19) nowhere near significance. It is also
**mechanically confounded by the refactor itself**: B split one file into four, so
reaching the same surface necessarily opens more distinct files even when it takes
fewer reads. Read together with `pre_edit_reads` (fewer reads, more files), the
plausible story is that decomposition made the agent's exploration *shallower but
wider*, which is what a decomposition should do, and which this metric penalizes by
construction. That is a measurement finding, not a result about archy: **breadth
metrics are not variant-neutral when variant B changes the file count**, and any
future decomposition study needs a file-count-normalized breadth metric or must
drop the metric.

**Two further confounds, both unadmitted until adversarial review, both pushing
toward the B-favorable direction:**

- **Variant B's history leaks the treatment.** B is a commit on top of the pinned
  SHA, so `git log -1` / `git show --stat HEAD` / `git blame` (the agent has
  `Bash`) describe the exact refactor and flag the repo as instrumented: the
  commit is authored `archy bench <bench@example.invalid>` and dated the run day,
  while A's history is plain upstream flask. A neutral *message* was written but
  `git commit --amend` preserves the original author without `--reset-author`, so
  the author line leaked anyway. The artifact is left as-is because it is the
  record of what actually ran; the fix belongs to the next run (commit as the
  upstream author and date, or squash B to a root-equivalent commit).
- **Within-pair order is fixed, and A drifts.** `run_pair` always runs A then B,
  so "interleaved" covers between-pair drift only. A's `pre_edit_reads` climbs
  7 -> 13 across the run (tie-corrected Spearman rho +0.615 vs `run_index`)
  against B's +0.346. Neither clears the n=10 two-tailed 0.05 critical value of
  0.648, and the gap between the arms is narrower than the pre-parser-fix figures
  suggested, so this is a weak signal. Counterbalancing remains the right design
  (position and variant must not be confounded), but the evidence that it mattered
  *here* is thinner than first reported. Variant and within-pair position are
  therefore perfectly confounded, and the drift inflates the B-favorable
  direction of the metric of record. It does not flip the sign (first-5 median
  delta −3, last-5 −3), but "direction favors B" is partly a position artifact.
  Counterbalancing order (A/B on even pairs, B/A on odd) is the one-line fix.

- **The task's edit surface is exactly variant B's new file.** The task asks for
  endpoint-origin mapping plus a duplicate-endpoint check on URL-rule
  registration. In B that whole surface sits in one 190-line
  `sansio/app_routing.py` whose own docstring says "a routing or
  blueprint-registration change is one file"; in A it is inside the 1013-line
  `app.py`. The prompt names no files (the paper's rule is satisfied literally),
  but its wording was fixed with variant B already in hand, so the task was
  chosen against a known B boundary. This pushes toward B like the other two.
  Note it does not rescue the result in B's favour: B still lost the token
  headline and tied on the pre-registered primary, so the confound makes the null
  *more* credible, not less. It would make any future positive result on this
  task/variant pair uninterpretable.
- **`baseline_failed` is a default in this artifact, not a measurement.** The
  field was added after these rows were written, so all 20 carry the `False`
  default and the reported "gate disabled by a red baseline: 0" is not evidence.
  The pristine suites were verified green by hand before the run (491/491 on
  both), which is what actually backs the claim here.

**Go/no-go:** this bench does not gate a feature (unlike #289, which gated
`archy brief`). What it rules out is a **claim**: archy must not say its
recommendations cut an agent's token footprint. `what_to_refactor_next` continues
to stand on human maintainability and edit risk, which this bench does not measure
and does not challenge.

**Two nulls now, with one of them weaker than it looks.** #289 (context injection,
N=22) and #282 (refactor, N=10) both land null at archy's layer on this model. But
the arm-C section below claims "fresh `git reset --hard` per run", and **no reset
existed anywhere in the harness at that time** (the `_reset_repo` added by this
change is the first): `git show main:bench/agent_footprint.py` contains no reset
or clean. `run_arm_c` also shares a single `repo_dir` across both arms. So either
#289's resets were done manually out of band and were never recorded as such, or
its 44 runs accumulated edits in one checkout, which is exactly the defect this
change calls invalidating. #289 also ships no records file, so unlike #282 its
table cannot be recomputed from the repo.

Treat the pair as **one verifiable null (#282) and one null of unverified
provenance (#289)**, not two independent confirmations. The "cleaner code
measurably helps the agent" thesis still is not reproducing, and roadmap items
premised on footprint reduction should be priced accordingly, but the evidence is
thinner than a bare "two nulls" implies. Re-running arm C on the fixed harness
would settle it and is the cheaper of the two follow-ups.

**Power, as pre-registered above:** N=10 needed 9-of-10 to reach p<0.05, and the
run was arithmetically out of reach of significance on `pre_edit_reads` by pair 7.
This rules out a large, consistent effect; it cannot separate "no effect" from
"moderate effect". Per the pre-registration, the follow-up is a second task or a
second repo, not more pairs of this cell.

## Third cell (#300, rich): cancelled by pre-run review, not run

The planned rich cell was **stopped before any agent time was spent**. No runs,
no numbers, and that is the result worth recording.

**What was built and verified.** Variant A pinned at `Textualize/rich` @
`46cebbb` (956 passed / 25 skipped, 3.6s). Variant B decomposed `rich.console`,
archy's #1 recommendation (cc_sum 386, churn 467, fan_in 119, edit_risk 0.46),
into `console_export.py`, `console_options.py` and `console_screen.py`;
`console.py` 2698 -> 2036 lines, cc_sum 386 -> 296. Behavior preservation was
checked well past the suite: `dir(Console)` identical, rendered output identical
by hash, re-export identity preserved (`rich.console.ConsoleOptions is
rich.console_options.ConsoleOptions`, and the `NO_CHANGE` singleton), every
moved function's `LOAD_GLOBAL` resolved against its new module namespace, type
hints resolvable, pickling intact. The diff is
[`agent_footprint/variant_b_rich_console.patch`](agent_footprint/variant_b_rich_console.patch);
the task is
[`agent_footprint/task_rich_style_resolution.md`](agent_footprint/task_rich_style_resolution.md).

**Why it was cancelled.** A pre-run adversarial pass (a blocking prerequisite in
the #300 pre-registration) returned NO-GO on five findings. Three were fixed and
are in the harness now: `.archy/index.db` had been committed into variant B's
tree only (an instrument leak the provenance check missed, because it compares
commit identity and not tree contents); the pre-registered primary metric
`file_revisitations` was path-keyed and therefore not variant-neutral, scoring a
split variant better by construction, so `canonical_file_revisitations` was
added; and the #302 file map was never wired from the CLI into the runner, so
canonical breadth would silently have equalled raw breadth.

The two that ended the cell could not be patched:

1. **The task's premise was factually false.** It assumed an undefined style name
   "fails quietly"; `Console.get_style` already raises `MissingStyle` and already
   performs the theme-stack and combination resolution the task asked for. The
   task was therefore near-trivial and ambiguous, and 20 runs would each have
   invented a different reconciliation.
2. **The treatment never touched the task's working set.** `get_style`,
   `_theme_stack` and `push_theme` stay in `console.py` in both arms, so the
   design actually tested "does deleting 662 lines of unrelated code from the far
   end of a file the agent must read anyway change its footprint" -- and a null
   there would have been written up as evidence about cleaner code helping
   agents, which it is not.

**Why it was not re-paired instead.** The obvious repair is a task aimed at the
surface that *did* move (SVG/HTML export, now a 484-line file in B against 2698
lines in A). That was rejected as **near-tautological**: it measures whether an
agent finds code faster in a small file than a large one, which needs no agent
study, and a positive result would be indefensible relocation. By this project's
own anti-theater standard (§12) that is worse than the confound it replaces,
because it would be deliberate.

**The conclusion, which is about the method rather than about archy.** This
minimal-pair design appears **structurally unable to produce an interpretable
positive result** at this scale. Any positive is attackable as relocation of the
answer; any null on a dose weak enough to avoid that charge is attackable as
insufficient treatment. One repo, one task, N=10, one model does not escape that
squeeze. Two nulls (#289 N=22, #282 N=10) plus this cancellation close the line:
**further cells are not funded**, and any future attempt needs a different design
(many repos and tasks, or a naturally-occurring before/after corpus), not another
pair.

## Arm C: context-injection (#289), first run

The #289 read-reduction question (does injecting an archy pre-edit brief reduce a
coding agent's exploratory reads before its first edit) ran first, because it needs
no refactored variant B, only a brief prepended to the prompt. Protocol:
[`SPEC_AGENT_FOOTPRINT_BENCH.md` §14](../docs/SPEC_AGENT_FOOTPRINT_BENCH.md);
framing: [`docs/research/PREWALK_READ_REDUCTION_SYNTHESIS.md`](../docs/research/PREWALK_READ_REDUCTION_SYNTHESIS.md).

**Config (one-config caveat):** flask @ `36e4a82`; `claude-sonnet-5` headless;
`--allowedTools Read,Write,Edit,Bash,Grep,Glob --setting-sources local`; fresh
`git reset --hard` per run (**note: not code-enforced at the time; see the
provenance caveat in the #282 section above**); interleaved A (no brief) / C
(581-token archy brief).
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

**Two caveats this table predates.** It is hand-built and shows **5 metrics, not
the 9 `summarize()` computes**, which is the selective reporting §9 now forbids;
the `num_turns` multiple-comparison figure below was therefore corrected over 5
(~0.21), and over the full 9 it would be ~0.37, i.e. even further from
significance. It also **cannot be regenerated**: #289 shipped no records file, so
unlike the #282 table above it is not pinned to any artifact and no golden test
covers it. Both are reasons the re-run noted above is worth doing, and neither
changes the null it reports.

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

**Withdrawn in v0.46** ([#421](https://github.com/hslee16/archy/issues/421)), on maintainer judgment taken ahead of a measurement
rather than on one. Nothing in this bench was refuted or re-run: the reads null above
stands exactly as reported, and the local-model brief-injection arm that motivated the
override is scheduled and has not yet reported.

**Follow-up disposition:** #292 (power to N≈30) is effectively answered here (null at
N=22); #291 (task-conditioned focus) keeps a weak prior since breadth never moved;
#290 (brief precision metric) and #293 (metric split) remain useful measurement infra.
One-config caveat stands: one model (`claude-sonnet-5`), one repo, one task; a
different model or a multi-module task with a wider true surface could differ, but the
burden is now on a positive result to appear, not on this null to be explained away.
