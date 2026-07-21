# Prewalk and the read-reduction question: can archy shrink an agent's pre-edit reads?

Findings note for [#289](https://github.com/hslee16/Archy/issues/289). Synthesis
and design gate, not an empirical study: no live agent run was executed here (that
is the [#259](https://github.com/hslee16/Archy/issues/259) bench, deliberately
deferred). This note answers the ticket's big question with a scoped go/no-go and
a pre-registered null, in the same shape as the [§14c.6 change-spread
non-result](RESEARCH_METRICS.md).

**Source:** Stencil, "Prewalk" (`omp` harness), <https://stencil.so/blog/prewalk>,
read 2026-07-20, indexed locally.

## TL;DR

- **The bill is `O(reads)`, not `O(edits)`.** In Stencil's trace (1.81B tokens,
  ~2M tool calls) edits and writes were ~9% of tokens; reading is what scales the
  cost, and every model pays full price for it.
- **Prewalk does not reduce reads.** It stops them being *repeated*: it hands off a
  warm trajectory (the live context window), not a document, then swaps frontier
  for cheap model the moment the first edit lands. archy cannot replicate that
  mechanism, because a tool result is a postcard by construction (see (B) below).
- **archy's only real shot is (A) substitution:** can a structural pre-edit brief
  let the *frontier* model reach its first confident edit with fewer *exploratory*
  (navigational) reads. This is plausible and partly already built
  (`invariant_brief`, `archy_impact`, `archy_graph(focus=)`), but unproven and
  exposed to the article's central risk.
- **The sharp risk (null hypothesis):** the article's whole thesis is that *a
  distilled artifact loses the grounded understanding*. An archy brief is exactly
  such an artifact. So the null is that archy context **adds** reads (agent reads
  the brief *and* still reads the source) rather than substituting for them.
- **Recommendation: NO-GO on any new feature or spec.** The scoped bench was built
  and run (arm C on the #259 harness). **It returned a non-result** (§6): at N=22 the
  brief did not reliably reduce reads-before-first-edit (median −2.0, 14/8, p=0.286),
  after an N=10 hint (p=0.109) that did not survive more data. NO-GO is now on
  evidence, not just an unproven prior. We measured the substitution rather than
  asserting it, and it was not there.

## 1. The article in archy's terms (research task 1)

**Problem framed.** People price agents like people: senior time is expensive, so
minimize senior involvement. Wrong axis. The expensive part of an agent's day is
not fixing, building, or thinking, it is *reading*. "Opus fixing things does not
cost money. Opus reading things costs money."

**Why `/plan` backfires (the part that matters for archy).** Opus plans read-only,
Flash implements: $3.18/task vs Opus solo $2.78 at the *same* 84.6% pass. The
"cost saving" costs 14% more. Mechanism: the frontier model reads ~100K tokens,
distills a ~2K-token `plan.md` postcard, and the executor gets the postcard but
not the understanding, so it re-reads `base.py` and the test file at its own
price. "A plan is not a file and you cannot edit prose." You did not move the
expensive part, you duplicated it.

**What `/prewalk` actually does.** It hands off the *trajectory*, not a document:

1. Frontier model starts with a hidden prefixed instruction (plan deeply, capture
   the plan as a todo list, then start).
2. It explores, writes the todo list, begins.
3. The moment the first edit lands (the confident point), swap to the cheap model
   and *prune* the planning instruction from context.
4. The cheap model inherits a warm trajectory: exploration done, todo list
   mid-checkmark, one valid edit already made (a free in-context example). It never
   re-reads to re-derive the approach.

Mechanism lineage: prefill (start the assistant's turn for it) generalized to
whole prefilled turns.

**Receipts.** Opus 4.8: prewalk 78% pass / $1.46 / 402s vs oneshot 85% / $2.78 /
606s (92% of pass, 53% of cost, 1.5x speed). GPT-5.6 Sol: prewalk 85% / $1.04 /
300s vs oneshot 88% / $1.71 / 372s (97% of pass, 61% of cost, fastest arm). Side
effect: prewalk cut SWE-bench "cheating" (googling the public fix) sharply (Opus
44% to 13%) because it terminates the frontier model in its confident phase,
before the desperation phase where googling starts.

## 2. Mapping prewalk onto archy (research task 2)

The ticket's own digest already split the big question correctly, and the split
survives scrutiny:

- **(A) Substitution.** Can an archy structural pre-edit brief let the frontier
  model reach its first confident edit with fewer exploratory reads? This is the
  true read-reduction claim, and the only one archy is positioned to affect.
- **(B) Warm-handoff analogue.** Is there an archy artifact that survives handoff
  better than a 2K `plan.md`? **archy is structurally weak here and should not
  claim it.** Prewalk's power is that it transfers the *live context window*, the
  one thing a tool result definitionally is not. Any archy artifact fed to a cheap
  executor is a postcard, the exact object the article shows loses grounding.
  Positioning archy as "prewalk for archy" would be a category error.

### 2a. Why archy is not the `/plan` postcard (the distinction that makes (A) viable)

The `/plan` postcard is *prose describing a journey the executor never took*. It
is unactionable: it must be re-derived against the code. An archy brief is a
different object: *structural pointers*, not narrative.

| Exploratory read the agent does today | archy substitute | Substitutes? | Confidence |
| --- | --- | --- | --- |
| grep/glob to find *who calls this* / *what depends on it* (blast radius) | `archy_impact`, `archy_graph(focus=)` | Plausibly replaces the fan-out search | Medium |
| Open neighbor files to learn call edges / local neighborhood | `archy_graph(focus=)` bounded subgraph | Replaces breadth-first neighbor opening | Medium |
| Read config / layer rules to learn what edits are forbidden | `invariant_brief` (in `archy_snapshot`) | Already ships; replaces the constraint hunt | Medium-high |
| Grep for *where else this logic lives* (edit-once-vs-many) | `archy_duplicates` | Replaces the duplicate hunt | Low-medium |
| Read the module to judge *how dangerous* an edit is | `edit_risk` in `invariant_brief` / `archy_score` | Ranks danger without reading | Low-medium |
| **Read the body of the file you are about to edit** | **none** | **Irreducible** | **n/a** |

The load-bearing observation: archy can only touch the *navigational* (breadth)
reads, never the *target* (depth) read. The file you edit must be read. So the
honest ceiling on the claim is "fewer files opened / less grep-glob fan-out before
the first edit", not "fewer tokens read overall".

### 2b. archy already half-does (A), which is why this is a measurement gap, not a feature gap

`archy_snapshot` already returns an `invariant_brief` (declared layers, forbidden
edges, acyclic invariant, baseline score per axis, load-bearing / highest
`edit_risk` modules), and `AGENT_LOOP.md` step 2 already tells agents to call
`archy_impact` / `archy_graph(focus=)` *before* editing. The pre-edit-brief
primitives exist. What does not exist is any measurement that they *reduce net
reads*. The correct next artifact is therefore a bench, not a surface.

### 2c. This lines up with an effect archy has already characterized

§14c.6 records that the SonarSource cleanliness study moved *footprint, not
capability*, and that its largest single effect was **file revisitation -34%**.
That is the same shape as the (A) claim here: the addressable win is on
navigation / revisitation, not on the irreducible target read, and it is a *cost*
effect with *no* pass-rate effect (+0.1 pp). Any archy read-reduction claim must
inherit that scoping verbatim: footprint only, never correctness.

## 3. The concrete measurable claim (research task 3)

> Does injecting an archy structural pre-edit brief into a coding agent reduce its
> **net reads-before-first-edit** (distinct files opened, read/grep/glob tool-call
> count, input tokens spent before the first edit) on a fixed task, **with no
> regression** in the repo's pre-existing test suite, once the brief's own token
> cost is charged against the arm?

This is a new *arm*, not a new bench. #259 already fixes the protocol, the
`FootprintRecord` telemetry, the variance/repetition plan, and the no-regression
gate. #259's manipulation is a *repo mutation* (apply an archy refactor to the
codebase); this ticket's manipulation is a *context injection* (hand the agent an
archy brief, repo unchanged). Same harness, same telemetry, same task rule
(task names no files, so file choice is the agent's). Add:

- **Arm A** = baseline agent, no archy (already the #259 baseline).
- **Arm C** = same agent + archy pre-edit brief injected before it starts.
- **Metric of record** = net reads-before-first-edit, per the #259 definitions.
- **Guard (non-optional):** brief tokens count *against* arm C, so the metric is
  net, not gross. This is what separates a real reduction from a relabeling.

An optional arm D (archy brief plus a prewalk-style early frontier-to-cheap
handoff) is out of scope for a first cut and drags in (B), which archy is weak
for. Defer it.

## 4. Anti-theater and OECD gate (research task 4)

**Anti-theater test (four failure modes it must clear):**

1. **Relabeling.** Does the brief just convert a "read" into a "tool call" of the
   same cost? Guarded only if the brief's tokens are charged against arm C and the
   metric is net. Unguarded, any apparent reduction is bookkeeping.
2. **The `/plan` trap (move cost, do not remove it).** If the frontier model
   consults archy *and still* reads everything, the brief is additive, exactly the
   $3.18-vs-$2.78 failure. This is the pre-registered **null hypothesis**: archy
   context does not reduce net reads (or reduces reads but hurts correctness). The
   bench exists to *try to reject* this null, not to confirm a hoped-for win.
3. **Irreducibility overclaim.** Reading the body you edit cannot be substituted
   (§2a). A headline of "archy reduces the reads agents do" without scoping to
   *navigational* reads is theater. Scope every claim to breadth, not depth.
4. **Correctness confound.** Cheaper reads that cost pass rate are not a win.
   §14c.6: cleanliness moved cost, not capability. Same discipline: footprint
   only, gated on no test regression.

**OECD gate.** The OECD composite-indicator gate ([`AXIS_REVIEW.md`](AXIS_REVIEW.md))
governs *score-axis* promotions (orthogonality, directionality, discriminant
validity). This is **not** an axis proposal, so that gate does not bind. The
relevant discipline instead is the surface-duplication check: an `archy brief`
feature would largely duplicate `invariant_brief` + `archy_impact` +
`archy_graph(focus=)`, which already ship. So there is no new *capability* to
propose, only *measurement and packaging*, which is another reason to gate on a
bench rather than a feature PR.

**Usage-signal weighting.** There is no current signal that agents consume the
existing `invariant_brief` at all, let alone that it saves reads. Building a new
brief surface on top of an unmeasured one, motivated by an external paper, is the
precise pattern the anti-theater rule exists to stop. Measure the primitive that
already exists before adding another.

## 5. Recommendation and go/no-go (research task 5)

**Outcome (b): run a bench first.** Not a documented non-result (unlike #260's
change-spread, the substitution mechanism is plausible *and already half-built*),
and not a feature (shipping now would assert the effect the article warns is
usually illusory).

- **NO-GO** on an `archy brief` / pre-edit surface spec or any new tool now.
- **GO** on extending the #259 harness with arm C (context-injection) and the net
  reads-before-first-edit metric, once #259's baseline harness lands. Low marginal
  cost (a third arm on an existing substrate), high information value (it tests
  archy's single most-cited agent-facing claim directly).
- **Feature decision is gated on the bench:** ship an `archy brief` surface only if
  arm C shows a *net* reduction in reads-before-first-edit with no pass-rate
  regression. If the null holds (brief is additive, or reduces reads but hurts
  correctness), this converts to a documented non-result in §14c, same as #260.
- **Do not pursue (B).** archy cannot transfer a warm trajectory; a tool result is
  a postcard. Do not position or build archy as a prewalk analogue.

## 6. Empirical result (arm-C run, N=22, 2026-07-21): a non-result

The bench recommended above was built and run (arm C added to the #259 harness;
protocol [`SPEC_AGENT_FOOTPRINT_BENCH.md` §14](../SPEC_AGENT_FOOTPRINT_BENCH.md),
numbers in [`bench/agent_footprint_results.md`](../../bench/agent_footprint_results.md)).
One config: flask @ `36e4a82`, `claude-sonnet-5` headless, a fixed teardown-ordering
task (true surface 3 files), a 581-token archy brief injected for arm C.

**The read-reduction claim is not supported.** At N=10 the brief looked promising
(pre_edit_reads median 13.0 → 8.5, −3.5, 8/10 pairs, p=0.109). Powering the run to
**N=22 pulled the effect back into the noise: median −2.0, 14/8, sign p=0.286.** The
N=10 figure was underpowered optimism, the exact failure mode the paper's ~2.5x
variance and the spec's `n>=10` / "publish the null" discipline exist to catch. So
#289's central question, "does an archy brief reduce the reads an agent does before
editing," gets a **documented non-result** at this config, not a feature.

**What the data does and does not say.** The feared `/plan` trap (brief *adds* reads)
did not hold either; the honest finding is simply *no reliable movement* on the read
count. Breadth (`pre_edit_distinct_files`) and revisitation were flat throughout: the
brief never shrank the ~3-file spine, consistent with the hand-measured brief
precision of 0.33 (9 named files, 3 on-surface, 6 dead weight the agent ignores).
`num_turns` was nominally lower (−5, p=0.041) but does not survive
multiple-comparison correction across the 5 metrics (~0.21), and it is not a read
count, so it cannot be relabeled as read reduction (§14.6). Zero regressions, so no
correctness signal either way.

**Go/no-go, on evidence now.** **NO-GO** on an `archy brief` feature, because the
effect *regressed to null on more data*, not merely for want of power. This is the
anti-theater gate doing its job: a feature shipped on the N=10 hint would have been
theater. The measurement infra remains worthwhile (#290 brief precision, #293 metric
split); a task-conditioned brief (#291) keeps only a weak prior, since breadth never
moved even with the flat brief. The one-config caveat stands (one model, one repo, one
task), but the burden now sits on a positive result to appear, not on this null to be
explained away.

## Relationships

- **[#259](https://github.com/hslee16/Archy/issues/259)** ([`SPEC_AGENT_FOOTPRINT_BENCH.md`](../SPEC_AGENT_FOOTPRINT_BENCH.md))
  is the measurement substrate. Arm C extends it; it does not reinvent it.
- **[§14c.6](RESEARCH_METRICS.md)** is the anti-theater template this note reuses,
  and the source of the footprint-not-capability scoping.
- **[`AGENT_LOOP.md`](../AGENT_LOOP.md)** already ships the pre-edit-brief
  primitives (`invariant_brief`, `archy_impact`, `archy_graph(focus=)`) this note
  would measure.
- Contrast with **[#145](https://github.com/hslee16/Archy/issues/145)** (review
  brief) and **[#283-#285](https://github.com/hslee16/Archy/issues/283)** (viz
  surface): all are "compact structural output for a consumer", and all share the
  distillation-loses-grounding risk the article names.

## References

- Stencil, "Prewalk", <https://stencil.so/blog/prewalk> (2026).
- SonarSource, "Does Code Cleanliness Affect Coding Agents?",
  [arxiv:2605.20049](https://arxiv.org/html/2605.20049v1), via §14c.6.
- Navigation Paradox, [arxiv:2602.20048](https://arxiv.org/html/2602.20048v1);
  LocAgent, [aclanthology:2025.acl-long.426](https://aclanthology.org/2025.acl-long.426/),
  via `AGENT_LOOP.md`.
