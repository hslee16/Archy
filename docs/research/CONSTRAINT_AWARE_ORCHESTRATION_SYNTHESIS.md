# Constraint-aware orchestration: should archy ship agent-framework integrations?

Research answer to [#298]. Reads the DevDigest article [*Constraint Decay Is the Coding Agent Bug Nobody Can Prompt Around*][devdigest] (covering arXiv [2605.06445][paper]) for what its "What This Means for Agent Frameworks" section actually asks of a tool like archy, and asks whether archy should ship first-class integrations with LangChain/LangGraph, Pydantic AI, CrewAI, or the OpenAI Agents SDK.

**Recommendation: no native adapters (wontfix), one tested docs recipe deferred behind a usage signal, and the Q4 bench not scheduled.** The finding that drives it: the half of the article's ask that archy does not already satisfy is an **archy config gap**, not an integration gap, so two small archy-side items fall out that stand on their own merit regardless of any framework.

## TL;DR

| Question (#298) | Answer |
| --- | --- |
| 1. Integration surfaces per framework | All four have a deterministic post-generation hook. Each maps to archy in 10 to 30 lines of glue. Nothing archy-specific has to exist. |
| 2. MCP vs native adapters | All four are already MCP clients, so `archy mcp` reaches them today with zero archy code. Adapters would wrap a subprocess call and a JSON parse across four fast-moving APIs. **Native adapters: no.** |
| 3. Hard-fail vs advisory semantics | **archy ships a working precedent for the dial, not the contract.** `sdp.mode: warn\|error` feeding `CheckPayload.passed` proves the mechanism out, but it is scoped to one built-in heuristic that is off by default; project-authored `forbid` rules have no advisory tier at all. So the article's first bullet is a real gap, and it is archy-side, not framework-side. |
| 4. Structural verifier as a gate (prototype + bench) | **Not scheduled, on cost and zero usage signal.** Its pass-rate endpoint is tautological (a gate that blocks until `archy check` passes yields passing artifacts by construction), but three bounded-retry endpoints are not: retries-to-green, gate cost, collateral regressions. Plus a held-out-constraint design that tests generalization. All recorded, none scheduled. |
| 5. White paper tie-in | A short applications subsection, not a separate integration RFC. One paragraph plus one code block. |
| Usage-signal gate | **Zero inbound demand.** `ADOPTERS.md` is empty and every issue in the repo is maintainer-authored. This is entirely paper-motivated, which is the case the ticket's own second gate exists to catch. |

## 1. What the article actually asks for

The relevant passage lists five things an agent runtime should know:

> - which project rules are advisory and which are hard failures
> - which files are exemplars for this task
> - which checks prove architecture, data, and security constraints
> - when to stop generating and ask for a design decision
> - when a passing test suite is insufficient because the structural verifier failed

Read carefully, four of the five are *properties of a project's configuration and its verifiers*, not properties of an agent framework. archy's `archy.yaml` plus `archy check` is a worked example of bullets 3 and 5 for the architecture dimension specifically, and a partial one of bullet 1 (see §4). Bullet 2 (exemplars) is [#138]; bullet 4 (stop and ask) is a policy the runtime owner writes, not something a verifier can supply.

So the article's ask decomposes into: *have a deterministic structural verifier with a machine-readable pass/fail and an advisory tier* and *call it from the loop* (the framework's job). archy has the verifier and the pass/fail; the advisory tier turns out to be only half-built (§4). The interesting question is whether archy should ship the calling code, and the answer is entangled with the fact that the missing half is on archy's side of the line, not the framework's.

## 2. The integration surfaces, mapped

Every one of the four frameworks has a deterministic hook that fires after generation, independent of what the model chose to do. This is the important property: it is where a verifier gate belongs.

| Framework | Extension point | Failure semantics available |
| --- | --- | --- |
| **LangChain / LangGraph** | `create_agent(middleware=[...])` with an `after_model` hook (or, in raw LangGraph, a plain node plus a conditional edge) | Full control: the middleware can mutate state, inject a message, or `jump_to` another node. Retry, block, and annotate are all expressible. |
| **Pydantic AI** | `@agent.output_validator` | `raise ModelRetry("...")` feeds the message back to the model and consumes one unit of the output retry budget (default 1, settable via `Agent(retries={'output': N})`). Returning normally passes. Closest fit to "inject feedback and retry". |
| **CrewAI** | `Task(guardrails=[fn], guardrail_max_retries=N)` | `fn(TaskOutput) -> (bool, Any)`; on `(False, "message")` the string becomes the retry feedback, bounded by `guardrail_max_retries`. Same shape as Pydantic AI. |
| **OpenAI Agents SDK** | `@output_guardrail` returning `GuardrailFunctionOutput(tripwire_triggered=True)` | Hard stop only: raises `OutputGuardrailTripwireTriggered` and halts the run. There is no built-in feedback-and-retry path; the caller builds the loop around the exception. |

The glue in each case is: run `archy check` (or `archy diff` against a pre-edit snapshot), read `passed`, and on failure format the violations into a message. That is 10 to 30 lines. Nothing in it is hard, and nothing in it needs to live in archy.

Worth naming the split precisely before §3 takes up question 2, because the imprecise version of it is wrong.

**MCP has no mechanism for a server to intercept or gate the host agent's output.** Its server-initiated primitives (`sampling/createMessage`, `elicitation/create`, notifications) all run inside a server-handled operation and cannot be attached to the host's own loop. So the article's fifth bullet ("a passing test suite is insufficient because the structural verifier failed") describes something the host must arrange; installing an MCP server does not arrange it.

What is *not* true, and what an earlier draft of this note asserted: that MCP calls are model-elected by construction. `tools/call` is issued by the client, not the model, so nothing stops a guard from deterministically invoking `archy_check` over the same MCP session instead of shelling out. The distinction is **who initiates the call**, not the protocol. That cuts in favor of the disposition below rather than against it: a guard can reuse an MCP session the runtime already holds, which shrinks the glue further and removes the last argument for a package to own it.

## 3. Question 2: MCP vs native adapters

**All four frameworks are already MCP clients.** `archy mcp` is reachable from every one of them today with zero archy code:

- LangChain/LangGraph: `langchain-mcp-adapters`, `MultiServerMCPClient(...).get_tools()`
- Pydantic AI: `MCPToolset('...')` passed as a toolset (stdio path, local script, or Streamable HTTP)
- CrewAI: the `mcps=[...]` field on `Agent`, or `MCPServerAdapter` from `crewai-tools[mcp]`
- OpenAI Agents SDK: `MCPServerStdio` / `MCPServerStreamableHttp`, or `HostedMCPTool` with per-tool approval callbacks

So the tool-access half of "integration" is already done and has been for as long as those adapters have existed. A native `archy-langgraph` package would add nothing there.

For the guard half, an adapter package would be: four packages, four release cadences, four dependency matrices, tracking four APIs that move fast (LangChain reworked its whole agent abstraction into the middleware model well inside the last year), in order to wrap a subprocess invocation and a JSON parse, or an MCP `tools/call` the runtime is already positioned to make. The wrapped surface is trivially thin and the maintenance cost is not. **Native adapters fail the value test. Decided: no.**

### What about a docs recipe instead?

The cheap version of "integration" is a page of copy-paste guards, one per framework. It is not free either: four untested snippets against four APIs that move at the rate just described is a page that rots, and a rotted recipe is worse than no recipe because it carries archy's name. The version worth shipping is **one framework, tested in CI, kept current** rather than four snapshots. Pydantic AI is the pick if it ever happens: `output_validator` + `ModelRetry` is the closest semantic fit (feedback-and-retry rather than hard-stop-only), it is Python-native so the test runs in archy's existing suite, and pydantic is already a first-order dependency.

Even that is gated on §7, because a recipe is a distribution channel and there is nothing yet asking to be distributed to.

## 4. Question 3: archy ships the mechanism, scoped to one built-in check

This is the finding that most changes the shape of the ticket, though not in the direction first assumed. The article's first bullet ("which **project rules** are advisory and which are hard failures") is *half* shipped: the dial exists and works, but only on a built-in heuristic, and not on the rules a project actually authors.

In `mcp.py:1124`:

```python
sdp_fails_gate = bool(sdp_violations) and config.sdp.mode == "error"
return CheckPayload(
    ...,
    violations=tuple(violations),
    sdp_violations=tuple(sdp_violations),
    passed=not violations and not sdp_fails_gate,
)
```

What is genuinely present:

- **The mechanism works and is project-owned.** With `sdp.enabled: true`, `sdp.mode: warn` reports violations while leaving `passed` clean; `sdp.mode: error` makes the same finding a gate. The disposition is decided in `archy.yaml` by whoever owns the codebase, not by the runtime, which is the right shape for the article's first bullet.
- **Advisory findings stay visible.** `sdp_violations` is populated under either mode, so a guard can annotate for a human without blocking the loop.
- **Findings carry enough to write retry feedback.** Each `Violation` has `rule`, `source`, `target`, and `lines`, which is enough for a guard to say precisely which edge broke which rule and where.
- **The CLI mirrors it.** `cli.py:236` recomputes the same `sdp_fails` predicate and exits 1, so the shell-level contract matches the payload for any runtime that shells out.

What is **not** present, stated plainly because the first draft of this note overstated it:

- **The dial is off by default and defaults to hard-fail when on.** `SdpConfig` is `enabled: bool = False`, `mode: str = "error"` (`layers.py:53-55`), and `mcp.py:1122` gates the whole SDP computation on `enabled`. Setting `mode: warn` alone does nothing. On a default archy install there is no advisory tier at all.
- **SDP is not a project rule.** It is a built-in global heuristic (the Stable Dependencies Principle). The rules a project authors, `forbid`, have **no** severity: every one of them is hard-fail, always. So what ships is a working *precedent* for the dial on one built-in check, not the contract the article asks for.

That reframes the disposition rather than reversing it. The article's first bullet is a genuine gap, but it is an **archy-side** gap (one config field, described in §8 and filed) rather than anything a framework integration would address. Both of the following are worth having on their own merit even if no integration ever ships, and the first is now load-bearing rather than a nicety:

1. **No per-rule severity on `forbid`.** A project that wants "this one layer rule is advisory while we migrate" has no way to say so, and today has to choose between deleting the rule (losing the signal) and blocking CI. A per-rule `severity: warn|error` generalizes the dial that `sdp.mode` already proves out, and is the thing that would actually make the article's first bullet true of archy.
2. **No canned remediation string.** Every consumer, guard or human, hand-rolls the message from the structured fields. A short, stable, model-readable rendering of "what broke and what to do" would be reused by `archy check`'s own output, by the review brief ([#145]), and by any guard.

Neither is a framework integration. Both are archy-side, cheap, and independently justified.

## 5. Question 4: the gate bench, and why not to run it as specified

The ticket proposes prototyping an agent loop that runs `archy check` / `archy diff` as a post-generation gate, retries on structural failure, and measures whether that reduces constraint decay on a toy backend task.

**The pass-rate endpoint is tautological, but only that endpoint, and only under unbounded retry.** If the loop blocks until `archy check` passes, the final artifact passes `archy check` by construction, and measuring "the gate enforced the thing the gate checks" is theater by the definition archy already uses. That is the trap the [#289]/[#294] arm-C work was built to avoid, at a cost of a full harness plus 44 live agent runs for a documented null on `archy brief`'s read-substitution claim (the NO-GO that null carried was withdrawn in v0.46 ([#421](https://github.com/hslee16/archy/issues/421)) on maintainer judgment, ahead of the scheduled local-model arm rather than on a result).

It would overstate the case to stop there, so: every retry mechanism in §2 is **bounded** (Pydantic AI's output budget defaults to 1, CrewAI has `guardrail_max_retries`, a LangChain `jump_to` loop needs a cap). Under a bounded budget the terminal artifact is not guaranteed to pass, and at least three endpoints have a real possible null:

- **Retries-to-green / fix-rate within budget.** Handed archy's violation text, does the model repair the layer violation or thrash? This is the sharpest of the three, because it measures whether archy's findings are *model-actionable*, which is the premise §4 gap 2 rests on.
- **Cost of the gate.** Added tokens and wall-clock per run. Plainly non-tautological, and the #259 harness already records both.
- **Collateral quality under structural pressure.** Does the agent satisfy `archy check` by gaming it (deleting the import, inserting a pass-through indirection) and regress the suite? `bench/agent_footprint_results.md` already tracks regressions as a first-class metric.

**The disposition is unchanged, but it rests on cost and §7, not on tautology.** None of the three has a feature riding on the answer today, all of them need the live-agent harness and enough N for a sign test, and the usage signal is zero. Recorded here so a future revisit starts from the right endpoints rather than the obvious wrong one. Of the three, retries-to-green is the one to run first if §4 gap 2 is ever built and its value questioned.

The most interesting design, recorded so it is not lost:

> **Held-out constraints.** Partition the project's rules into set A (the gate checks these) and set B (held out, never checked during the run, evaluated only at the end). Measure violations of B. A positive result means structural feedback on A made the agent globally more constraint-careful; a null means the gate buys exactly its own coverage and nothing more.

That is the one an applications section could honestly cite, and the only one that tests *generalization* rather than the gate's own coverage. It is also the most expensive of the four (a toy backend with genuine layer rules, per-framework loop implementations, enough N for a sign test). **Not scheduled**, on cost and on §7. Revisit only if the white paper needs the empirical claim, or if a usage signal appears.

## 6. Question 5: white paper tie-in

An **applications subsection, not a separate RFC.** The content is one paragraph and one code block: `archy check`'s `passed` / exit-code contract as a structural verifier that drops into any of the four guard hooks above, with `sdp.mode` as a worked example of the advisory-vs-hard-failure dial and the honest note from §4 that it is a precedent on one built-in check rather than a general severity system. The framework-by-framework table in §2 is the appendix version if more space is wanted.

Nothing here justifies a standalone integration RFC, because there is no integration to specify.

## 7. The usage-signal gate

The ticket asks explicitly: is there inbound demand for framework integrations, or is this paper-motivated?

- `ADOPTERS.md` has zero entries.
- Every issue in the repository is authored by the maintainer. No one has asked for LangGraph, CrewAI, Pydantic AI, or Agents SDK support, or for anything adjacent.

It is entirely paper-motivated. That is not disqualifying on its own, several good archy features started from a paper, but combined with §3's finding (the tool-access half is already reachable via MCP) and §4's (the missing half is an archy config field, not an integration) it means an integration would be a distribution channel for a capability nobody has yet asked to reach, wrapped around a gap it would not close. The same discipline deferred the MCP `archy_render` tool in [`SPEC_VISUALIZATION.md`][spec-viz] §6 (phasing item 3) until agents demonstrably want it.

## 8. Disposition

| Item | Call |
| --- | --- |
| Native adapter packages (`archy-langgraph` et al.) | **Wontfix.** §3. |
| Docs recipe page | **Deferred behind a usage signal.** §3, "What about a docs recipe instead?". If it ever lands: one framework tested in CI (Pydantic AI), not four snapshots against four moving APIs. |
| Per-rule `severity: warn\|error` on `forbid` | **File on its own merit**, and it is the thing that would make the article's first bullet true of archy. §4 gap 1. |
| Canned remediation string on violations | **File on its own merit**, shared with [#145]. §4 gap 2. |
| Gate prototype + constraint-decay bench | **Not scheduled**, on cost and §7. Recorded in §5: three bounded-retry endpoints plus a held-out-constraint design, none tautological; the pass-rate endpoint is the one that is. |
| White paper | Short applications subsection. §6. |

## Related

- [#139] rule rot / constraint staleness, the mirror of constraint decay
- [#124] constraint-conformance / surprise-rate signal
- [#271] intended-vs-actual conformance score
- [#138] exemplar surfacing, the article's second bullet
- [#294] the arm-C null that sets the anti-theater precedent applied in §5
- [`AUTONOMY_CONTINUUM_SYNTHESIS.md`](AUTONOMY_CONTINUUM_SYNTHESIS.md), whose autonomy-tiered gating argument is the same advisory-vs-blocking dial reached from a different direction

[devdigest]: https://www.developersdigest.tech/blog/constraint-decay-ai-coding-agents
[paper]: https://arxiv.org/abs/2605.06445
[spec-viz]: ../SPEC_VISUALIZATION.md
[#124]: https://github.com/hslee16/Archy/issues/124
[#138]: https://github.com/hslee16/Archy/issues/138
[#139]: https://github.com/hslee16/Archy/issues/139
[#145]: https://github.com/hslee16/Archy/issues/145
[#271]: https://github.com/hslee16/Archy/issues/271
[#289]: https://github.com/hslee16/Archy/issues/289
[#294]: https://github.com/hslee16/Archy/issues/294
[#298]: https://github.com/hslee16/Archy/issues/298
