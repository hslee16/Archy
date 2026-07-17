# Spec: agent-footprint minimal-pair bench (#259)

Status: design (no harness code yet). This spec fixes the protocol, the
telemetry schema, and the anti-theater guardrails before any code or any
(expensive, nondeterministic) live agent run.

## 1. The claim under test

> Does applying an **archy-recommended, behavior-preserving** refactor to a
> repository measurably reduce a coding agent's **token footprint** and
> **file revisitation** on a fixed task, **with no regression** in the repo's
> pre-existing test suite?

This is the first bench in a new category. Every existing `bench/*_sweep.py`
measures an architecture metric over real repos; **none measures agent
behavior**. This one runs a real coding agent (Claude Code, headless) on
before/after variants and parses its transcript.

The framing is deliberately narrow: **footprint (cost), not correctness.** The
motivating study ([arxiv:2605.20049](https://arxiv.org/html/2605.20049v1),
SonarSource) found cleanliness moves footprint but leaves pass rate flat
(+0.1 pp). archy must not claim a capability effect. See
[`docs/research/RESEARCH_METRICS.md` §14c.6](research/RESEARCH_METRICS.md) for
the anti-theater lineage (this bench is the "how we'd test it instead of
asserting it" answer to the #260 non-result).

## 2. Why archy's layer is the interesting place to test it

The paper's footprint effect concentrates on **multi-module tasks** and is
driven by **file revisitation** (the agent looping back to files it already
touched). That is the layer archy operates at (`coupling`, `dsm`, `cycles`,
`impact`, `what_to_refactor_next`).

**Honest caveat, restated so no result overstates it:** the paper's own
manipulated lever was SonarQube *line-level* rule violations plus intra-file
god-method decomposition; it did **not** test module-dependency tooling.
"archy's module layer targets the effect layer" is an *inference from where the
payoff concentrated*, not a paper finding. This bench exists to test that
inference, not to assume it.

## 3. Protocol (minimal-pair, before/after, paired)

1. Choose a repo with a real, green pre-existing test suite, and a fixed task
   described **only** by external behavior (inputs/outputs), naming **no files**
   (the paper's task rule, so file choice is the agent's, not ours).
2. **Variant A** = repo as-is (`HEAD`).
   **Variant B** = repo with archy's top `what_to_refactor_next` (or `coupling`)
   recommendation applied as a **behavior-preserving, test-gated** refactor
   (see §6). A and B differ only by that refactor.
3. Run the identical task with the identical agent config on both variants,
   **N times each** (§8), from a clean checkout per run.
4. Capture one `FootprintRecord` (§4) per run.
5. Run the repo's **full pre-existing test suite** on the agent's output for
   every run and record pass/fail (§7). This is the gate the paper admits it
   lacks (its §6: "We do not check whether the agent broke unrelated tests").
6. Report paired deltas B-vs-A per metric, as a distribution, with the
   regression outcome (§9).

## 4. Telemetry: the `FootprintRecord`

Captured per run. Sources verified against Claude Code CLI v2.1.x headless
output (`--output-format json` for totals; the persisted session transcript
`~/.claude/projects/<slug>/<session-id>.jsonl` for per-tool-call detail, which
is the most complete record: it carries both `usage` on assistant messages and
every `tool_use` block).

| Field | Type | Source | Notes |
|---|---|---|---|
| `variant` | "A"\|"B" | harness | |
| `run_index` | int | harness | 0..N-1 |
| `input_tokens` | int | `usage.input_tokens` | non-cache input |
| `cache_read_input_tokens` | int | `usage.cache_read_input_tokens` | reported separately, never folded silently into `input_tokens` (cache state varies run to run, §8) |
| `cache_creation_input_tokens` | int | `usage.cache_creation_input_tokens` | |
| `output_tokens` | int | `usage.output_tokens` | |
| `num_turns` | int | `num_turns` | proxy for "conversation messages" |
| `distinct_files_touched` | int | transcript | count of unique file paths across all `Read`/`Edit`/`Write` `tool_use` blocks |
| `file_revisitations` | int | transcript | **primary metric.** Count of `Read`/`Edit` `tool_use` blocks on a path that was already `Edit`/`Write`-touched earlier in the same transcript (definition in §5) |
| `duration_ms` | int | `duration_ms` | secondary; wall-clock is noisy |
| `total_cost_usd` | float | `total_cost_usd` | recorded for reference only, never headlined (§9) |
| `test_regression` | bool | harness | true if the pre-existing suite has a new failure vs the variant's own baseline (§7) |
| `task_completed` | bool | `subtype=="success"` and not `is_error` | the agent finished; NOT a correctness claim |
| `model` | str | run config | pinned model id, recorded on every row |

**Dropped metric (be explicit):** the paper's **"reasoning characters"** cannot
be replicated. Claude Code persists `thinking` blocks with the content
**encrypted** (`signature`), and no reasoning/thinking token or char count is
exposed. We do **not** invent a proxy; the metric is simply absent, and any
comparison to the paper says so.

## 5. Metric definitions (so two implementers agree)

- **`distinct_files_touched`**: the size of the set of resolved file paths
  appearing in any `Read`/`Edit`/`Write` tool input over the run.
- **`file_revisitations`**: iterate tool calls in order; maintain the set
  `edited` of paths seen in a prior `Edit`/`Write`. Increment the counter each
  time a `Read`/`Edit` targets a path already in `edited`. This operationalizes
  the paper's "returns to files it has already edited". `Grep`/`Glob`/`Bash` do
  not count as file touches (no single unambiguous target path).
- **Token footprint headline** = `input_tokens + output_tokens` (cache fields
  reported alongside but excluded from the headline, since cache hit/miss is a
  cross-run artifact, not agent effort).

## 6. Variant B construction rules (the edge over the paper)

- **archy-recommended**: B applies the specific refactor archy's
  `what_to_refactor_next` / `coupling` surfaced, not an arbitrary cleanup.
- **behavior-preserving**: the refactor changes structure, not semantics.
- **test-gated**: the repo's full pre-existing suite must pass on B before B is
  admitted (else B is not behavior-preserving and is rejected).
- **human/tool-authored, NOT LLM-synthesized**: the paper built variants with an
  agent pipeline; its top HN critic attacked exactly that plus the missing
  regression control. Our variants are real refactors gated by tests, recorded
  as a reviewable diff in the bench artifacts.

## 7. The regression gate the paper lacks

For each variant, record the pre-existing suite result on the pristine variant
(baseline) and on the agent's output. `test_regression = (new failures in the
agent's output that were not failing in that variant's baseline)`. A run where
the agent broke unrelated tests is flagged and excluded from the footprint
headline (a smaller footprint achieved by breaking the repo is not a win).

## 8. Variance and the repetition plan (non-optional)

Claude Code headless exposes **no seed, no temperature, no max-turns**; sampling
variance is inherent. The paper measured **~2.5x** input-token variation across
reruns of the *same* task and warns a single per-task delta near 10% "may be
agent noise". Therefore:

- **N >= 10 runs per variant** (start at 10; raise if the paired delta's CI
  still crosses zero). A single A/B pair is explicitly **not** a result.
- Fresh checkout per run; run A and B **interleaved** to average out drift.
- Cold session per run to limit prompt-cache contamination; cache fields are
  reported so contamination is visible.
- Report the **paired** distribution (per-run B-A), not two independent means.

## 9. Analysis and reporting

- Headline per metric: **median paired delta** (B-A) with an interval (IQR or
  bootstrap CI) and a paired sign test on the per-run deltas. Direction and
  spread, not a single point.
- **Tokens, never dollars** in any headline (paper §6: token->dollar mapping is
  nonlinear and config-dependent). `total_cost_usd` stays in the raw table only.
- **One-config caveat on every result**: state the exact model + CLI flags; a
  result on one model/harness is not a general law. A second model is a
  follow-up, not a blocker.
- **Publish the null.** If applying archy's recommendation does not move
  footprint outside the noise band, that is the result and it ships as such
  (the #260 discipline: we do not manufacture a win).

## 10. Reproducibility config (pinned)

Baseline invocation (exact model id recorded per result):

```
claude -p "<TASK>" \
  --output-format json \
  --model <pinned-model-id> \
  --dangerously-skip-permissions \
  --allowedTools "Read,Write,Edit,Bash,Grep,Glob" \
  --setting-sources local
```

`--setting-sources local` isolates the run to the project's own settings, so the
user/global CLAUDE.md / hooks / MCP do not leak in and the repo under test is
what varies. (`--bare` would isolate more but breaks `-p` headless execution in
a nested/sandboxed context, verified against a live run; `--setting-sources
local` is the working substitute. `stdin` is fed from `/dev/null` so the CLI
does not block waiting for piped input.) The persisted session transcript is
copied into the bench artifact dir immediately after each run (it is the parse
source, and it is overwritten by later sessions).

## 11. Harness shape (for the follow-up implementation PR)

- `bench/agent_footprint.py`:
  - `parse_transcript(path) -> FootprintRecord` (pure, deterministic; the unit
    boundary).
  - `run_variant(repo_dir, task, config) -> FootprintRecord` (invokes the agent,
    copies the transcript, runs the test gate).
  - `run_pair(repo, task, refactor, n) -> list[FootprintRecord]`.
  - aggregation + `bench/agent_footprint_results.md` emitter.
- **Unit-testable without any live agent**: ship a recorded/synthetic transcript
  fixture and assert the parser's token totals, `distinct_files_touched`, and
  `file_revisitations` (especially the revisitation ordering logic in §5). The
  expensive live run is opt-in and gated on an API key.

## 12. Anti-theater guardrails (carried from the #260 review)

1. Footprint, **not** correctness. No pass-rate or capability claim.
2. Real archy-recommended, test-gated, behavior-preserving refactors, recorded
   as reviewable diffs. No LLM-synthesized variants.
3. N>=10 with a reported distribution. No single-pair headline.
4. Tokens, not dollars. One-config caveat on every number.
5. Publish the null. A metric only ships if the effect clears the noise band.
6. Do not restate the "module layer targets the effect layer" inference as a
   finding; it is the hypothesis under test.

## 13. Open decisions (resolve at implementation time)

- **First target**: which repo + task + specific archy recommendation seeds the
  first pair. Candidate: dogfood on a mid-size well-tested OSS repo already in
  `bench/cache/`, with a `coupling`- or `what_to_refactor_next`-surfaced split.
- **N** and the stopping rule (fixed N vs run-until-CI-tightens).
- Cache handling: whether to attempt any further cold-cache isolation beyond
  fresh sessions.
- Whether a second model sweep is in the first results doc or a later one.

## References

- Motivating paper: <https://arxiv.org/html/2605.20049v1> (SonarSource, Claude
  Sonnet 4.6 / Claude Code, Python + Java).
- Agent-cost-as-eval-axis prior art: SWE-Effi, <https://arxiv.org/abs/2509.09853>.
- Anti-theater lineage + the killed #260 co-change proxy:
  [`docs/research/RESEARCH_METRICS.md` §14c.6](research/RESEARCH_METRICS.md).
- HN thread: <https://news.ycombinator.com/item?id=48798815>.
