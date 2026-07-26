# Causal Reasoning and Human Judgment: what the additive-transformative gap implies for archy

A synthesis of Phroneses' [*Agents Cannot Maintain Systems: The Additive-Transformative Gap in
LLM Software Delivery*][acms] (and its companion [*Surface Area*][surface]), read for what it
implies about where archy sits and what it should ship next. Companion to
[`AUTONOMY_CONTINUUM_SYNTHESIS.md`](AUTONOMY_CONTINUUM_SYNTHESIS.md) and the agent-research survey
in [`RESEARCH_METRICS.md` §14](RESEARCH_METRICS.md). This note is positioning, a literature
enrichment, and a gap analysis, not a new empirical study. Dated 2026-05-27.

It answers two questions the maintainer posed:

1. How can archy help agents reason **causally** about the systems of the codebase they work in?
2. How can archy surface information that improves **human judgment** about the code changes
   agents are making?

---

## 1. The source argument in one paragraph

The article splits the imagined 2026 agent workflow (read repo, map structure, plan, write code,
run and fix, produce a PR-ready diff) into two halves. The first three steps are **additive**:
reading, mapping, planning do not alter the system's causal structure. The last three are
**transformative**: they change behaviour in a live, interdependent system, which requires
understanding constraints, invariants, integration boundaries, and downstream consequences. The
thesis: LLMs generate statistically plausible token continuations, which is sufficient for
self-contained, additive work (write a function, draft a doc) but not for system-dependent,
transformative work, because "software systems are causal: components depend on each other,
invariants constrain behaviour, and changes propagate." The sharpest line is the operational one:
**"Pattern-matching can write code; only causal reasoning can maintain systems."** The prescription
is not "stop using agents" but "elevate engineering": humans hold intent, constraints, architecture,
correctness, safety, and trade-offs, and "human judgement remains the foundation of software
delivery" until agents can reason causally. The companion [*Surface Area*][surface] piece reinforces
the mechanism: probabilistic components become reliable only inside **layered, structured
constraints**, and "failure modes can be reduced, not removed."

The article is a thesis essay, not an empirical paper. Its Further Reading is entirely 2023-2024
lab announcements (o1, Devin, Copilot, Cody, Code Llama, Claude 3). The argument is sound but its
evidence base is thin and dated, so the value of this note is to (a) test the thesis against the
2025-2026 literature, and (b) translate it into concrete archy design implications.

---

## 2. archy already anticipated most of this (intellectual honesty first)

Before treating the article as new input, it is worth stating plainly that archy's existing
research already names this gap under different vocabulary:

- The **additive vs transformative** split is archy's reason for existing. The README "Why" is
  exactly this: "coding agents generate code that passed review but rotted the import graph
  underneath." That is the transformative-work failure the article describes.

  **CORRECTION, 2026-07-26.** That quoted premise has since been **retracted**, and the README no
  longer states it unqualified. 25 live agent runs on the structurally riskiest SWE-bench tasks
  produced zero structural regressions (95% upper bound 12%), and humans break their own declared
  contracts on 0.66% of commits. The event is real and rare, for agents and humans alike. Whatever
  this document argues, it cannot rest on "agents rot the import graph" as an established fact.
  See [`../WHAT_DIDNT_WORK.md`](../WHAT_DIDNT_WORK.md).
- [`AUTONOMY_CONTINUUM_SYNTHESIS.md`](AUTONOMY_CONTINUUM_SYNTHESIS.md) already frames archy as
  "minimum viable governance that grows proportionally with autonomy" and as
  "cross-session, cross-agent architecture memory that prose CLAUDE.md notes are not." That is the
  article's "humans must maintain intent" and *Surface Area*'s "layered constraints," restated.
- [`AGENT_LOOP.md`](../AGENT_LOOP.md) (snapshot, impact-before-edit, diff-after-edit) is a structural
  causal-reasoning prosthesis: it points the stateless model at the parts of the system its edit
  will propagate to.

So the article does not overturn archy's strategy; it confirms it. The genuinely new contribution
this note can make is narrower and therefore more useful: a **causal-dimension gap analysis** (§5)
and one **new tool concept** (§6, the counterfactual pre-edit check) that mechanizes the article's
single most quotable claim.

---

## 3. Does the 2025-2026 literature support the thesis? Mostly yes, with one caveat

**Causal reasoning is genuinely the weak axis (supports the thesis).** Fresh benchmarks find LLMs
perform "level-1" causal reasoning, essentially pattern recall over causal-sounding language, and
drop sharply on held-out causal tasks; "most lack genuine comprehension of causal mechanisms"
([*Unveiling Causal Reasoning in LLMs: Reality or Mirage?*][mirage], arXiv 2506.21215). The
transformer autoregression objective is not inherently causal. This is direct empirical backing for
"they predict tokens, not consequences."

**Functional tests pass while architecture rots (supports the thesis, and is archy's exact niche).**
ICSE 2026 work and the agentic-architecture literature converge on a finding archy was built around:
"agent-generated code can pass functional tests while violating architectural patterns, so you should
test for architectural conformance explicitly, not just for correct outputs"
([*Architecture Without Architects*][arch-without], arXiv 2604.04990; the
[Architecture Fitness Function][fitness] pattern). Incorporating architectural documentation /
constraints measurably improves conformance and modularity, not just correctness.

**The benchmark-to-production gap is real (supports the thesis).** SWE-bench Verified passes 80%+
for frontier models, but the same models score ~23% on SWE-bench Pro (held-out, multi-step,
proprietary repos), and a controlled study found AI tools made experienced developers ~19% *slower*
([*Agentic Coding in Production*][tianpan]). The recurring framing: "as codebases scale, code
**reading** rather than writing becomes the dominant bottleneck." This is the transformative gap
measured. The Phroneses companion [*Evaluating AI Systems*][evaluate] makes the methodological point
that follows: evaluation should "measure real system behaviour, not synthetic benchmarks," with
explicit failure-mode and longitudinal-drift tracking. That is the right shape for §10's open
question, an archy-specific behavioural evaluation rather than another leaderboard number.

**The KG-grounding result is the constructive lever (this is the actionable part).** The same
literature that documents the causal weakness also documents the fix: grounding LLM reasoning in an
**external structured graph** improves multi-hop reasoning and reduces hallucination, because the
graph "acts as an external memory that grounds model responses in factual relationships"
([*Grounding LLM Reasoning with Knowledge Graphs*][kg-ground], arXiv 2502.13247;
[*Graph-based Agent Memory*][graph-mem], arXiv 2602.05665; the Mind Map construct in
[*Agentic Reasoning*][agentic-reason], arXiv 2502.04644). **An import/call dependency graph is
exactly such a structure for the one causal sub-domain archy owns.** archy does not need the model to
*become* a causal reasoner; it needs to *supply the causal model* the model lacks. This is the
single most important reframing in this note.

**The one caveat (steelman against the thesis).** "Agents cannot maintain systems" is too absolute.
The empirical record shows agents *can* maintain real systems **when wrapped in rigorous process**:
explicit planning, accumulated-learning files ("every mistake becomes a rule"), and aggressive
verification, with Anthropic reporting ~90% of Claude Code written by Claude Code and Nubank's
multi-million-LOC migration via Devin ([*Agentic Refactoring: An Empirical Study*][agentic-refactor],
arXiv 2511.04824; [Mason, *Coherence Through Orchestration, Not Autonomy*][mason]). The honest
restatement is not "agents cannot maintain systems" but "**agents maintain systems only as well as
the verification scaffolding around them.**" That restatement is *more* favourable to archy, not
less: archy is a piece of that scaffolding (the structural-conformance verifier), and its value
scales precisely with how much autonomy the human cedes (the autonomy-tiered argument in
[`AUTONOMY_CONTINUUM_SYNTHESIS.md`](AUTONOMY_CONTINUUM_SYNTHESIS.md)).

---

## 4. The reframe: archy is an externalized, deterministic causal model

Stitching §3 together: the agent's deficiency is causal reasoning; the literature's fix is grounding
reasoning in an external structured graph; archy maintains exactly such a graph for the
import/call/layer sub-domain and computes consequences over it deterministically. So the operating
principle for both questions below is:

> archy's job is not to make the model reason causally. It is to **be the causal model** for the
> structural dimension, expose its causal claims (X change implies Y consequence, because of edges
> e1..en), and persist the invariants the stateless model forgets between turns.

This stays inside archy's settled non-goals (sensor not fixer, judge not librarian, graph-shape not
whole-codebase-health). It does not ask archy to model behaviour, runtime state, or data invariants,
which it cannot see and which `mypy`, property-based tests, and runtime tracing own.

---

## 5. Causal-dimension gap analysis (the analytical core)

The article names five things "system-dependent" work requires. Mapping each to archy's current
surface is the most useful single output of this note, because it shows precisely where archy is the
right tool and where it must hand off.

| Article's causal dimension | What it means | archy today | Verdict |
| --- | --- | --- | --- |
| **Dependencies** | how components depend on each other | `archy_graph_focus`, `archy_impact`, `archy_affected`, full import + call graph with line numbers | **Owned.** This is archy's core. |
| **Downstream consequences / propagation** | how a change ripples outward | reverse-impact set, MacCormack propagation cost, `archy_high_risk_modules`, blast radius | **Owned.** |
| **Invariants (structural)** | constraints the change must not break | layer rules, forbidden edges, acyclicity, SDP via `archy_check` / `archy_contracts` | **Owned, but reactive** (checked after the edit, not surfaced as a brief before it). See §6.B. |
| **Persistent state / temporal dependencies** | "how the system got here": prior writes, accumulated data, long-lived objects | **Not modelled.** The import graph is static and a-temporal. The closest proxy is git co-change, which is the **temporal-coupling** diagnostic ([#131](https://github.com/hslee16/archy/issues/131)), still gated on FP validation. | **Partial / blind spot.** §6.D. |
| **Integration boundaries** | seams to external systems and behaviour | external deps appear in `archy_graph_summary`; no first-class "this module sits on an integration seam" signal | **Partial blind spot.** Behavioural / runtime boundaries are correctly out of scope. |

The shape of the answer falls out of this table. archy should **deepen and better expose the two
columns it owns** (dependencies, propagation), **make the invariants it already checks proactive**
(surface before the edit, not only after), and **be explicit about the two it does not own**
(temporal state, behavioural boundaries), pointing the agent and human at where the un-modelled
causality lives rather than pretending to cover it.

---

## 6. Question 1: helping agents reason causally about the system

Ordered by leverage. Each is tagged NEW (no current tool or filed issue), FILED (issue exists), or
REFRAME (existing output, repackaged). Each carries the anti-theater test the maintainer's own
[#142](https://github.com/hslee16/archy/pull/143) principle demands: *what does an agent do
differently because of this?*

### 6.A `archy_simulate`: counterfactual pre-edit consequence check  [filed [#144](https://github.com/hslee16/archy/issues/144), highest leverage]

The article's most quotable claim is "LLMs predict tokens, not consequences." archy can compute the
consequence deterministically, **before the edit is written**. Today the loop is asymmetric:
`archy_impact` reasons over edges that *already exist*, and `archy_diff` only works *after* the edit
against a snapshot. There is no way for an agent to ask "if I add an import from A to B and remove
the one from C to D, what breaks?" without first writing the files.

Proposal: a tool that takes the **current graph plus a proposed edge delta** (imports to add, imports
to remove, modules to add or move) and returns the structural consequence: new cycles, new
back-edges in topological order, new layer / forbidden / SDP violations, score delta, and the change
in propagation cost / blast radius. No files are written. It is the same machinery as `archy_diff`
and the `DSMDiff.new_back_edges` path, run against a hypothetical graph instead of a re-parsed one.

- **Why it is the strongest idea:** it literally mechanizes the additive-to-transformative leap for
  the structural dimension. The agent can test a refactoring *hypothesis* causally before committing
  to it, which is exactly the "understand how the system will change if this PR is applied" the
  article says agents cannot do unaided.
- **Anti-theater test:** an agent that calls `archy_simulate` *abandons or reshapes a planned edit
  before writing a single file* when the simulation shows a new cycle. That is a different action,
  not a different dashboard.
- **Cost / risk:** the input contract (edge deltas) is small and clean, but it requires the agent to
  express its plan as graph deltas, which not every agent will do well. Ship it as advisory, validate
  on the dogfood repos that the simulated delta matches the post-edit `archy_diff` (they must agree
  by construction), and gate wider promotion on whether real agent loops actually call it.

### 6.B Session-start invariant brief  [REFRAME of `archy_snapshot` + `archy_contracts`]

The article: LLMs "cannot maintain a stable internal representation of a system." *Surface Area*: the
OpenAI-style API "is stateless." archy already persists the invariants (`.importlinter`, `archy.yaml`
layers, recorded baseline) but surfaces them **reactively**, only when a check fails after an edit.
A stateless agent benefits more from being *told the constraints up front*. Proposal: have
`archy_snapshot` (or the `loop` prompt) emit a compact, machine-readable **invariant brief** at
session start: declared layers and their direction, forbidden edges, the current acyclic invariant,
the baseline score per axis, and the top-5 highest-`edit_risk` modules ("treat these as load-bearing").

- **Anti-theater test:** the agent reads the brief and *avoids proposing a cross-layer edit in the
  first place*, rather than being told off after the fact. Prevention over correction.
- **Cost / risk:** near-zero, it is a recombination of existing outputs. The risk is prompt bloat;
  keep it to a tight, ranked payload.

### 6.C Causal narrative on `archy_impact`  [REFRAME]

The KG-grounding literature is explicit that the graph helps most when it supplies the **"because,"**
the relationship path, not just the node set. `archy_impact` returns the impacted set; it could also
return the **shortest import path** from the changed module to each high-value impacted module, with
line numbers (the data is already on the edges). "Editing `auth.tokens` can affect `billing.invoice`
*because* `billing.invoice -> auth.session -> auth.tokens` (lines 12, 47)." That converts a retrieval
answer into a causal-chain answer, which is what the agent needs to reason about consequences.

- **Anti-theater test:** the agent cites the specific edge it must preserve when it writes the edit,
  instead of guessing which dependents matter.

### 6.D Temporal coupling: the one true blind spot  [FILED #131, reinforce]

This note *raises the priority* of [#131](https://github.com/hslee16/archy/issues/131). The article's
deepest section, "Persistent state creates temporal dependencies," names the exact causal layer the
import graph cannot see: modules that change together for reasons not visible as an import edge.
Git co-change is the only signal archy can compute that approaches this dimension, and it is the only
proposed feature that addresses the article's blind spot rather than deepening an owned column. It
remains correctly gated on the FP-validation pass (co-change is noisy; needs commit-size
normalization and a threshold), but the article is independent evidence that the dimension matters,
not just a CodeScene-lineage nicety.

- **Anti-theater test:** the agent editing module A is warned that B changes with A 80% of the time
  despite no import between them, and *opens B to check* a coupling it would otherwise have missed.

### 6.E Already-filed reinforcements

- **Conformance / surprise signal** ([#124](https://github.com/hslee16/archy/issues/124)): flag a
  change that does something structurally unusual for *this* repo (new edge against the dominant
  direction, coupling two previously independent communities). This is "where your judgment is
  needed," computed against the repo's own conventions.
- **Exemplar surfacing** ([#138](https://github.com/hslee16/archy/issues/138)): showing the agent the
  cleanest existing peer module beats describing the rule in prose (the Constraint Decay finding).
  This is the constructive complement to the invariant brief.

---

## 7. Question 2: improving human judgment over agent changes

The 2025-2026 review literature is the strongest evidence that this is the higher-value question
right now. AI-authored PRs wait ~4.6x longer for review pickup and are accepted at 32.7% vs 84.4% for
human PRs; a 90% rise in AI adoption tracked a 91% rise in review time and a 154% rise in PR size;
agent-authored changes carry higher long-term churn; and "engineers are increasingly reviewing
outputs instead of understanding behaviour end to end" ([*These Aren't the Reviews You're Looking
For*][reviews], arXiv 2605.02273; [*Early-Stage Prediction of Review Effort in AI-Generated PRs*][effort],
arXiv 2601.00753, MSR 2026). The bottleneck has moved from writing to **reading and judging**. The
article's own "Related Work" makes the same point: the real gains from AI are in the shared work of
review and coordination, not individual coding speed. archy's contribution to human judgment is to
**route scarce attention to the causally-consequential parts of a large diff.**

### 7.A Per-PR structural review brief  [NEW packaging of shipped parts, high leverage]

archy already computes everything needed; it has never assembled it into a **human-first** artifact.
Proposal: `archy review` (CLI, and a CI comment) that, given a diff or branch, produces a short
review brief ranked for a human reader:

- the headline (already in `DiffSummary`): cycles added/resolved, layer crossings, score delta;
- the **structural blast radius**: how many modules the change can reach, and which high-`edit_risk`
  modules it touches;
- a ranked **"look here first"** list: the riskiest touched modules and the new cross-boundary edges,
  each phrased as a question a reviewer should ask ("`auth` now imports `billing`, a new cross-layer
  edge; intended?");
- what archy **cannot** vouch for, stated explicitly: behaviour, persistent-state effects, and
  integration semantics are out of scope, so do not treat a clean archy brief as a clean review.

This is the structural analogue of the MSR-2026 "predict review effort" work: archy supplies a
*structural* effort estimate and an attention ranking, so a human reviewing a 154%-larger AI PR
spends their limited judgment where propagation is highest.

- **Anti-theater test:** the reviewer opens the three modules archy ranked first and finds the real
  problem there, instead of skimming a 40-file diff uniformly and rubber-stamping it (the automation-
  bias failure the literature documents).

### 7.B Diff deltas as judgment prompts, not just numbers  [REFRAME]

`archy_diff` returns deltas; a human-facing layer should translate each into a question. "Acyclicity
dropped because `models -> services -> models` is now a cycle" is a judgment prompt; "acyclicity
-0.04" is not. The translation is mechanical and the data exists (`new_back_edges`, added cycles).

### 7.C Autonomy-tiered gating dial  [FILED, links to #132]

From [`AUTONOMY_CONTINUUM_SYNTHESIS.md`](AUTONOMY_CONTINUUM_SYNTHESIS.md): the *same* archy signal
should be advisory when a human reviews every change (Level 2) and blocking when humans review less
per change (Levels 3-4). Human judgment is best served by letting the team set *where* archy blocks
vs advises, keyed to how much they trust the agent, with the v0.15.0 discipline that **only FP-free
deterministic checks may ever block** (cycles, declared-layer violations, score regression). This is
the `archy install --hooks` framing ([#132](https://github.com/hslee16/archy/issues/132)): hooks are
not one strength setting, they are an autonomy dial.

---

## 8. What archy should NOT do (discipline)

The article will tempt scope creep toward "model all the causality." Resist it, on the settled
non-goals and the empirical FP record:

- **Do not model behavioural or data invariants, persistent runtime state, or integration semantics.**
  archy cannot see them statically; `mypy`/`pyright`, property-based and contract tests, and runtime
  tracing own them. archy's honesty about *what it does not cover* is part of its value to human
  judgment (§7.A's explicit "cannot vouch for" line).
- **Do not become a fixer.** The article's prescription is human judgment at the centre; archy
  surfaces the structural consequence, the human or agent decides. "Code generation or auto-fix" is
  already a rejected item.
- **Do not add a sixth metric to answer this article.** The maintainer's recorded anti-theater test
  applies to archy-the-project: another axis or synthesis that changes no user action is the vanity
  thermometer. The recommendations above are deliberately tools and packaging (things an agent or
  human *calls and acts on*), not new numbers.

---

## 9. Recommendation and the honest caveat

If exactly one thing ships from this note, it is **`archy_simulate` (§6.A, [#144](https://github.com/hslee16/archy/issues/144))**:
it is the only proposal that mechanizes the article's core claim ("predict consequences, not tokens"),
it had no current or filed equivalent before this note, and it changes what an agent *does* before it
writes code. The strongest human-judgment move is the **per-PR review brief (§7.A, [#145](https://github.com/hslee16/archy/issues/145))**,
which is mostly repackaging of shipped machinery and directly targets the documented review-burden
crisis. The smaller causal-framing reframes (§6.B/C, §7.B) are filed together as
[#146](https://github.com/hslee16/archy/issues/146).

The honest caveat, recorded so this note does not itself become productivity theater: per the
maintainer's own 2026-05-26 prioritization, the highest-leverage next move is a **usage signal**
(does anyone call archy inside an agent loop?), not more capability. Both proposals above should be
gated on that question. `archy_simulate` is worth building only if there is evidence agents will
express plans as edge deltas and call it; the review brief is worth building only if humans are
actually drowning in archy-adjacent AI PRs. This document is positioning and design input. It is not,
by itself, a reason to write code. The anti-theater test applies to it too: *what does anyone do
differently because this note exists?* The answer should be "they build §6.A or §7.A, or they
consciously decide not to, on usage evidence", not "the design is now documented."

---

## 10. Open questions and follow-ups (recorded 2026-05-27)

What this synthesis left genuinely open, separated from the design proposals above so the
distinction between "needs evidence" and "needs filing" stays clear.

### Research questions (need evidence, not just a decision)

1. **The load-bearing question: does archy-in-the-loop measurably reduce structurally-bad edits?**
   **Q1a (prevalence) is now answered empirically** in
   [`INLOOP_PREVALENCE_EMPIRICS.md`](INLOOP_PREVALENCE_EMPIRICS.md): across 1,072 human-authored
   commits in 11 mature repos, the FP-free signal archy gates on (a new import cycle) appears in only
   **0.5%** of commits, but those commits are large (median 7 `.py` files changed vs 1) and non-trivial
   (multi-module tangles, new-SCC median 3), and the composite score drops on 29% of commits but 98% of
   those drops are sub-0.005 noise. The reframed value prop: archy is a **rare-firing, low-FP gate on
   severe structural damage concentrated in large transformative changes**, the regime agents produce
   most. **Q1b (the causal claim, does archy-in-loop reduce agent regressions) remains open** but now
   has a powered, executable A/B protocol and a control baseline (this study). Q1b is gated on a usage
   signal (does anyone run archy inside an agent loop?), which the maintainer deprioritized on
   2026-05-26.
2. **Temporal coupling ([#131](https://github.com/hslee16/archy/issues/131)) still needs its
   FP-validation pass.** Unchanged by this note except that the article is independent motivation:
   it is the only proposed feature that addresses the article's true blind spot (persistent / temporal
   state). The empirical work (commit-size normalization plus a threshold sweep to drive the
   false-positive rate low) is the gate.
3. **The KG-grounding evidence is about factual QA, not code dependency graphs.** The papers cited in
   §3-§4 (arXiv 2502.13247, 2602.05665) ground reasoning in knowledge graphs to improve multi-hop QA
   and reduce hallucination. The transfer to *code* dependency graphs is plausible but unverified in
   that literature; question 1 above is how archy would actually test the transfer on itself.

### Capture-able follow-ups (proposals filed as a result of this note)

- **Per-PR structural review brief** (§7.A): the human-judgment (Q2) counterpart to #144, filed as
  [#145](https://github.com/hslee16/archy/issues/145).
- **Causal-framing umbrella** (§6.B invariant brief, §6.C `archy_impact` causal narrative, §7.B
  deltas-as-judgment-prompts): the smaller reframes of shipped machinery, filed as
  [#146](https://github.com/hslee16/archy/issues/146).

### Sourcing gap (honest note)

Two of the source article's three "Related Work" links, `ai-engineering-team-based-ai.html`
("the real gains from AI come from improving the shared work between engineers") and
`engineers-need-to-know.html` ("engineers must understand tokens, structure, and probabilistic
behaviour"), are **dead on the author's own site (HTTP 404 as of 2026-05-27)**; they are unpublished
or renamed. Only their one-line Related Work descriptions are available, used above without their
full text. The third related piece, [*Evaluating AI Systems*][evaluate], does resolve and is folded
into §3 and §10. The "team-based AI" thesis (gains live in shared review/coordination work, not
individual coding speed) is directly relevant to §7 and worth re-sourcing if the author republishes.

---

## References

Source articles:

- [Phroneses, *Agents Cannot Maintain Systems: The Additive-Transformative Gap in LLM Software Delivery*][acms]
- [Phroneses, *Surface Area / Programmatic Interfaces to AI Systems*][surface]
- [Phroneses, *Evaluating AI Systems: Metrics that Matter*][evaluate]
- Phroneses, *AI Engineering as Team-Based Work* and *What Engineers Need to Know* (Related Work links from the source; both HTTP 404 on the author's site as of 2026-05-27, one-line descriptions only)

Causal reasoning in LLMs:

- [*Unveiling Causal Reasoning in Large Language Models: Reality or Mirage?*][mirage] (arXiv 2506.21215)

Grounding reasoning in external structured graphs:

- [*Grounding LLM Reasoning with Knowledge Graphs*][kg-ground] (arXiv 2502.13247)
- [*Graph-based Agent Memory: Taxonomy, Techniques, and Applications*][graph-mem] (arXiv 2602.05665)
- [*Agentic Reasoning*][agentic-reason] (arXiv 2502.04644)

Architectural conformance and fitness functions for agents:

- [*Architecture Without Architects: How AI Coding Agents Shape Software Architecture*][arch-without] (arXiv 2604.04990)
- [Architecture Fitness Function, Encyclopedia of Agentic Coding Patterns][fitness]

Benchmark-to-production gap:

- [*Agentic Coding in Production: What SWE-bench Scores Don't Tell You*][tianpan]

Human review burden for AI-authored changes:

- [*These Aren't the Reviews You're Looking For: How Humans Review AI-Generated Pull Requests*][reviews] (arXiv 2605.02273)
- [*Early-Stage Prediction of Review Effort in AI-Generated Pull Requests*][effort] (arXiv 2601.00753, MSR 2026)

Counter-evidence (agents can maintain systems with rigorous process):

- [*Agentic Refactoring: An Empirical Study of AI Coding Agents*][agentic-refactor] (arXiv 2511.04824)
- [Mason, *AI Coding Agents in 2026: Coherence Through Orchestration, Not Autonomy*][mason]

archy's own prior synthesis (this note builds on, does not replace):

- [`AUTONOMY_CONTINUUM_SYNTHESIS.md`](AUTONOMY_CONTINUUM_SYNTHESIS.md), [`RESEARCH_METRICS.md` §14](RESEARCH_METRICS.md), [`../AGENT_LOOP.md`](../AGENT_LOOP.md)
- Navigation Paradox (arXiv 2602.20048), LocAgent (ACL 2025), Constraint Decay (arXiv 2605.06445)

[acms]: https://phroneses.com/articles/build/notes/agents-cannot-maintain-systems.html
[surface]: https://phroneses.com/articles/build/notes/surface-area.html
[evaluate]: https://phroneses.com/articles/build/notes/evaluate-ai.html
[mirage]: https://arxiv.org/abs/2506.21215
[kg-ground]: https://arxiv.org/pdf/2502.13247
[graph-mem]: https://arxiv.org/html/2602.05665v1
[agentic-reason]: https://arxiv.org/pdf/2502.04644
[arch-without]: https://arxiv.org/html/2604.04990v1
[fitness]: https://aipatternbook.com/architecture-fitness-function
[tianpan]: https://tianpan.co/blog/2026-04-09-agentic-coding-production-swebench-gap
[reviews]: https://arxiv.org/html/2605.02273v1
[effort]: https://arxiv.org/pdf/2601.00753
[agentic-refactor]: https://arxiv.org/html/2511.04824v1
[mason]: https://mikemason.ca/writing/ai-coding-agents-jan-2026/
