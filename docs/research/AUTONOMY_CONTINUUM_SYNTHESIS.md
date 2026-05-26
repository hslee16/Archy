# The AI Autonomy Continuum: positioning archy as autonomy-scaled verification

A synthesis of Tracy Bannon's (MITRE) InfoQ talk [*Agents, Architecture, & Amnesia: Becoming AI-Native without Losing our Minds*][bannon-talk], read for what it implies about where archy sits and what it should ship next. Companion to the agent-research survey in [`RESEARCH_METRICS.md` §14](RESEARCH_METRICS.md) and the Constraint Decay synthesis there; this note is positioning and vocabulary, not a new empirical study.

## The one-line thesis, and why it is on-thesis for archy

Bannon's central claim: **as agent autonomy increases, the verification you need increases, not decreases.** archy is a deterministic verification layer for the *structural* dimension of AI-authored code, so the talk is less a source of new metrics than an external maturity model archy can position against. The honest finding of this synthesis is exactly that: the talk yields **framing and one genuinely new design dial, not a slate of new features.** It mostly reinforces items already filed (#124, #132, #138, #139) and gives them a shared vocabulary.

The crispest positioning statement it licenses: **archy is how you afford the increased verification the continuum demands without the verification cost scaling linearly with autonomy.** Bannon frames the answer as "more humans in the loop, not fewer," and archy does not contradict that. archy sits on the *verification* side of the ledger, not the autonomy side: a deterministic structural checker substitutes for the part of human review that is mechanical (did this change introduce a cycle, cross a layer, regress the score?), freeing scarce human attention for the semantic review only a human can do. This is the "division of labor" argument already in §14c.4 (offload the structural objective to an external checker, free the agent's and the reviewer's budget for the functional one), now with a maturity model behind it.

## The continuum, mapped to archy's surface

Bannon's four levels, with where archy's shipped tools actually serve:

| Level | Bannon's pattern | archy's role today |
| --- | --- | --- |
| 1 | **AI Assistant** - snippet help, deterministic, human writes and reviews | Mostly out of frame. `archy score` as a periodic human-facing health check; archy is not in the inner loop. |
| 2 | **AI Teammate** - single bounded task, human verifies before action | **The sweet spot today.** `archy_affected` / `archy_high_risk_modules` before the edit, `archy_diff` after - exactly the `AGENT_LOOP.md` loop. archy is the deterministic pre-screen that tells the agent (and the reviewing human) where to look. |
| 3 | **Multi-Agent Orchestration** - SDLC workflows, needs *more* oversight | Per-PR gates (`archy_snapshot` + `archy_diff`, surprise rate #124) become the shared, deterministic referee across agents that **do not share context**. The persisted `.importlinter` / `archy.yaml` + always-on MCP server is the cross-session, cross-agent architecture memory prose CLAUDE.md notes are not (the §14c.4 cross-session-decay argument). |
| 4 | **Software Flywheel** - self-improving, maximum governance | archy as an always-on gate in the self-improvement loop: the structural half of the "stop button" - do not merge if a cycle appears, a declared layer is violated, or the score regresses against a recorded baseline. The blocking end of the advisory↔blocking spectrum. |

The summary line: **archy is "minimum viable governance that grows proportionally with autonomy" for the architecture dimension specifically.** Bannon's phrase maps directly onto a design choice archy already half-makes (advisory diagnostics vs blocking gates) but has never tied to an autonomy level.

## Design implication 1 (new): autonomy-tiered gating

The talk's "verification scales with autonomy" turns the advisory-vs-blocking question - which §14c.4 treats as a *fixed property of a given check* - into a **per-deployment-context dial keyed to autonomy level.** The *same* archy signal should be advisory at Level 2 (an agent plus an attentive human reviewer) and can be blocking at Levels 3–4 (less human attention per change, so the deterministic gate carries more of the load).

The constraint that keeps this principled is archy's own v0.15.0 lesson (the `archy.yaml`-to-Forbidden auto-translation was demoted because it manufactured permanent CI false-positives it could not whitelist). A gate can only block if its false-positive rate is near zero. That yields a clean split:

- **Deterministic checks may scale to blocking gates at high autonomy:** import cycles, declared-layer / `forbid` violations, score regression vs a recorded baseline. These are FP-free because the user authored the rule or the property is mechanical.
- **Inferential checks stay advisory at every level:** convention-based layer inference (#122/#135), rule-rot detection (#139), exemplar surfacing (#138). These are heuristic by construction and would manufacture exactly the noise the v0.15.0 lesson warns against.

This is the one genuinely new, concrete idea the talk produces, and it sharpens the `archy install --hooks` framing (#132): hooks are not one strength setting, they are autonomy-tiered, and the tier selects which checks block vs advise.

## Design implication 2 (sharpest, self-critical): the score invites productivity theater

Bannon's headline antipattern - **productivity theater: gaming visible metrics** (tickets closed, lines written) instead of producing value - is a direct risk for a tool whose headline output is a 0–1 score. This is Goodhart's law: a measure that becomes a target stops measuring. An agent optimizing the visible `archy score` could merge modules to cut the edge count, collapse files to mask cycles, or add trivial re-exports - moves that raise the number without improving the architecture. The external evidence that visible-metric optimization diverges from real value is now strong: METR's RCT found experienced developers were **19% slower** with AI tools while forecasting they would be 24% *faster* ([METR, July 2025][metr-study]), and GitClear found copy-pasted lines rose from 8.3% to 12.3% while refactoring fell from 25% to under 10% of changed lines across 211M LOC ([GitClear 2025][gitclear-2025]). Felt productivity and visible output both rose while real quality fell - productivity theater, measured.

What protects archy, stated honestly:

1. **The headline number is composite (five axes - modularity, acyclicity, depth, equality, complexity; see [`AXIS_REVIEW.md`](AXIS_REVIEW.md)).** Gaming one axis usually perturbs another - collapsing modules to cut the cycle count concentrates complexity and skews the equality (coupling-distribution) axis - so there is no cheap single move that lifts the composite. Composite indicators are Goodhart-harder than scalars by construction.
2. **The score is advisory and human-facing; it is *not* the agent's reward signal.** The `AGENT_LOOP.md` loop feeds the agent *diagnostics that localize work* (`archy_affected`, `archy_high_risk_modules`, `archy_diff`), never "raise this number." Telling an agent *what to fix* rather than *which scalar to maximize* is itself the anti-productivity-theater stance.

The design rule this crystallizes, worth promoting to an explicit anti-goal: **archy's signals are diagnostics that point at work, never targets to maximize - a thermometer, not a thermostat.** This is a live constraint on archy's own roadmap. Any per-agent metric (surprise rate #124) must be framed as a diagnostic, never a leaderboard, or it manufactures the exact gaming Bannon describes. The two §14c.4 epics are the productivity-theater story's two halves: rule-rot (#139) *detects* a form of gaming (an agent satisfying a rule's letter via indirection is gaming the constraint), and exemplar surfacing (#138) is the positive counterpart (show a good example, do not just emit a score). Bannon's antipattern is the banner that unifies them.

## Design implication 3: the accountability leg, not the identity plane

Bannon's governance foundation is an **Identity Control Plane** - agent registry, policy-enforcement gateway, delegation/accountability ("who is acting, are they authorized, on whose behalf"). archy is **not** an identity plane and should not pretend to be; identity and authorization belong to the AI-gateway / MCP-auth layer. But the *accountability* leg connects directly to the surprise-rate signal (#124): attributing structural violations per agent (via the `Co-Authored-By:` commit trailer #124 already proposes) is the "on whose behalf, and who is accountable" question expressed structurally. The honest scope: archy supplies the **evidence** for architectural accountability (which agent introduced which structural regression), not the identity or the policy. Framed that way, #124 becomes the architectural-accountability signal *inside* someone else's identity control plane - a sharper adoption story than a standalone trend line, with no overclaim.

## The other antipatterns, mapped

- **Tool-led thinking** (the org bends around the tool; Conway's Law applied to tooling). A caution aimed at archy itself: archy must not become the thing teams optimize *for*. This reinforces implication 2 - diagnostic, not target.
- **Cognitive overload** (Team Topologies; more to hold in your head than a person has bandwidth for). Here archy is the *antidote*, not the cause: the propagation-cost / context-sufficiency framing (§14a) is literally "how much must you hold in your head to safely change this module," and `archy_graph_focus` returns the bounded relevant neighborhood rather than the whole graph. The design caution that follows: keep MCP outputs bounded, which is already an archy value.
- **Decision compression** ("reckless speed," per Siva Muthu - the problem is recklessness, not speed). archy's pre-edit `risk` / `affected` is the deliberate-pause beat: "here is what you are about to touch" before the commit. This is the same timing argument as the §14c.4 calcification finding (apply constraints up front, before a pattern hardens).
- **ADRs** (Bannon's core recommendation: document tradeoffs as defensible decisions). `archy.yaml` / `.importlinter` is the *executable* ADR for the dependency dimension - an architecture decision you can check, not merely write down. This is the same point as the HN thread's "executable constraints, not prose guidelines" and §14c.4's one-liner from two directions: **archy converts architectural aspirations into checkable consequences.**

## What to promote to the roadmap

Measured, because the talk's contribution is mostly framing:

1. **Autonomy-tiered gating** (implication 1) - new and concrete; a refinement of the `archy install --hooks` design (#132), not a standalone epic. The deterministic-may-block / inferential-stays-advisory split is the shippable core.
2. **Explicit anti-goal: signals are diagnostics, not optimization targets** (implication 2) - a docs change (anti-goals section + `SCORING.md`), not a feature. The cheapest, highest-leverage item here, and it Goodhart-hardens everything else on the roadmap.
3. **Reframe #124 as the architectural-accountability leg of an identity control plane** (implication 3) - a framing change to an already-filed item, no new scope.

None of these is a new metric. Unlike the Constraint Decay paper (which motivated the #122/#135 *features*), this talk's value is a vocabulary and a maturity model: it tells archy *how to describe and tier what it already does* as autonomy climbs. "It's not magic. It's just engineering." applies to archy's own claims too - the credible move is to position against the continuum, not to manufacture features from a talk.

## References

[bannon-talk]: https://www.infoq.com/presentations/ai-autonomy-continuum
[metr-study]: https://arxiv.org/abs/2507.09089
[gitclear-2025]: https://www.gitclear.com/ai_assistant_code_quality_2025_research
