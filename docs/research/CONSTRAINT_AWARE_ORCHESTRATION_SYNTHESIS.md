# Constraint-aware orchestration: should archy ship agent-framework integrations?

Research answer to [#298]. Reads the DevDigest article [*Constraint Decay Is the Coding Agent Bug Nobody Can Prompt Around*][devdigest] (covering arXiv [2605.06445][paper]) for what its "What This Means for Agent Frameworks" section actually asks of a tool like archy, and asks whether archy should ship first-class integrations with LangChain/LangGraph, Pydantic AI, CrewAI, or the OpenAI Agents SDK.

**Recommendation: no native adapters (wontfix), one tested docs recipe deferred behind a usage signal, and do not run the Q4 bench as specified.** Two small archy-side items fall out that stand on their own merit regardless of any framework.

## TL;DR

| Question (#298) | Answer |
| --- | --- |
| 1. Integration surfaces per framework | All four have a deterministic post-generation hook. Each maps to archy in 10 to 30 lines of glue. Nothing archy-specific has to exist. |
| 2. MCP vs native adapters | All four are already MCP clients, so `archy mcp` reaches them today with zero archy code. Adapters would wrap a subprocess call and a JSON parse across four fast-moving APIs. **Native adapters: no.** |
| 3. Hard-fail vs advisory semantics | **archy already ships this contract** (`sdp.mode: warn\|error` feeding `CheckPayload.passed`), it is just never documented as the answer to this question. Two real gaps remain, both small. |
| 4. Structural verifier as a gate (prototype + bench) | **Do not run as specified: the claim is close to tautological.** A gate that blocks until `archy check` passes produces artifacts that pass `archy check` by construction. Only a held-out-constraint design is non-trivial; recorded below, not scheduled. |
| 5. White paper tie-in | A short applications subsection, not a separate integration RFC. One paragraph plus one code block. |
| Usage-signal gate | **Zero inbound demand.** `ADOPTERS.md` is empty and every issue in the repo is maintainer-authored. This is entirely paper-motivated, which is the case the ticket's own second gate exists to catch. |

## 1. What the article actually asks for

The relevant passage lists five things an agent runtime should know:

> - which project rules are advisory and which are hard failures
> - which files are exemplars for this task
> - which checks prove architecture, data, and security constraints
> - when to stop generating and ask for a design decision
> - when a passing test suite is insufficient because the structural verifier failed

Read carefully, four of the five are *properties of a project's configuration and its verifiers*, not properties of an agent framework. archy's `archy.yaml` plus `archy check` is already a worked example of bullets 1, 3, and 5 for the architecture dimension specifically. Bullet 2 (exemplars) is [#138]; bullet 4 (stop and ask) is a policy the runtime owner writes, not something a verifier can supply.

So the article's ask decomposes into: *have a deterministic structural verifier with a machine-readable pass/fail and an advisory tier* (archy has this) and *call it from the loop* (the framework's job). The interesting question is only whether archy should ship the calling code.

## 2. The integration surfaces, mapped

Every one of the four frameworks has a deterministic hook that fires after generation, independent of what the model chose to do. This is the important property: it is where a verifier gate belongs.

| Framework | Extension point | Failure semantics available |
| --- | --- | --- |
| **LangChain / LangGraph** | `create_agent(middleware=[...])` with an `after_model` hook (or, in raw LangGraph, a plain node plus a conditional edge) | Full control: the middleware can mutate state, inject a message, or `jump_to` another node. Retry, block, and annotate are all expressible. |
| **Pydantic AI** | `@agent.output_validator` | `raise ModelRetry("...")` feeds the message back to the model and consumes one unit of the output retry budget (default 1, settable via `Agent(retries={'output': N})`). Returning normally passes. Closest fit to "inject feedback and retry". |
| **CrewAI** | `Task(guardrails=[fn], guardrail_max_retries=N)` | `fn(TaskOutput) -> (bool, Any)`; on `(False, "message")` the string becomes the retry feedback, bounded by `guardrail_max_retries`. Same shape as Pydantic AI. |
| **OpenAI Agents SDK** | `@output_guardrail` returning `GuardrailFunctionOutput(tripwire_triggered=True)` | Hard stop only: raises `OutputGuardrailTripwireTriggered` and halts the run. There is no built-in feedback-and-retry path; the caller builds the loop around the exception. |

The glue in each case is: run `archy check` (or `archy diff` against a pre-edit snapshot), read `passed`, and on failure format the violations into a message. That is 10 to 30 lines. Nothing in it is hard, and nothing in it needs to live in archy.

Worth naming the split, because it is the whole answer to question 2: **MCP gives the model the *option* to call archy; a guard runs archy whether the model wants to or not.** The article's fifth bullet ("a passing test suite is insufficient because the structural verifier failed") is specifically the deterministic path. MCP by construction cannot express it: the protocol has no post-generation hook, only tools a model may elect to call.

## 3. Question 2: MCP vs native adapters

**All four frameworks are already MCP clients.** `archy mcp` is reachable from every one of them today with zero archy code:

- LangChain/LangGraph: `langchain-mcp-adapters`, `MultiServerMCPClient(...).get_tools()`
- Pydantic AI: `MCPToolset('...')` passed as a toolset (stdio path, local script, or Streamable HTTP)
- CrewAI: the `mcps=[...]` field on `Agent`, or `MCPServerAdapter` from `crewai-tools[mcp]`
- OpenAI Agents SDK: `MCPServerStdio` / `MCPServerStreamableHttp`, or `HostedMCPTool` with per-tool approval callbacks

So the tool-access half of "integration" is already done and has been for as long as those adapters have existed. A native `archy-langgraph` package would add nothing there.

For the guard half, an adapter package would be: four packages, four release cadences, four dependency matrices, tracking four APIs that move fast (LangChain reworked its whole agent abstraction into the middleware model well inside the last year), in order to wrap a subprocess invocation and a JSON parse. The wrapped surface is trivially thin and the maintenance cost is not. **Native adapters fail the value test. Decided: no.**

## 4. Question 3: archy already ships the advisory/hard-fail contract

This is the finding that most changes the shape of the ticket. The article's first bullet ("which project rules are advisory and which are hard failures") is already a shipped archy feature, it has just never been described that way.

In `mcp.py:1129`:

```python
sdp_fails_gate = bool(sdp_violations) and config.sdp.mode == "error"
return CheckPayload(
    ...,
    violations=tuple(violations),
    sdp_violations=tuple(sdp_violations),
    passed=not violations and not sdp_fails_gate,
)
```

The properties a runtime needs are all present:

- **The advisory/hard dial is owned by the project, not the runtime.** `sdp.mode: warn` in `archy.yaml` reports violations while leaving `passed` clean; `sdp.mode: error` makes the same finding a gate. That is exactly the article's first bullet, decided in config by whoever owns the codebase.
- **Advisory findings stay visible.** `sdp_violations` is populated either way, so a guard can annotate for a human without blocking the loop. All three of the article's implied dispositions (block, feed back, annotate) are derivable from one payload.
- **Findings carry enough to write retry feedback.** Each `Violation` has `rule`, `source`, `target`, and `lines`, which is enough for a guard to say precisely which edge broke which rule and where.
- **The CLI mirrors it.** `archy check` exits 1 on failure, which is the shell-level version of the same contract for any runtime that shells out.

Two genuine gaps, both small and both worth having on their own merit even if no framework integration ever ships:

1. **No per-rule severity.** `forbid` rules are uniformly hard-fail; only SDP has a `mode`. A project that wants "this one layer rule is advisory while we migrate" has no way to say so, and today has to choose between deleting the rule (losing the signal) and blocking CI. A per-rule `severity: warn|error` generalizes the dial that `sdp.mode` already proves out.
2. **No canned remediation string.** Every consumer, guard or human, hand-rolls the message from the structured fields. A short, stable, model-readable rendering of "what broke and what to do" would be reused by `archy check`'s own output, by the review brief ([#145]), and by any guard.

Neither is a framework integration. Both are archy-side, cheap, and independently justified.

## 5. Question 4: the gate bench, and why not to run it as specified

The ticket proposes prototyping an agent loop that runs `archy check` / `archy diff` as a post-generation gate, retries on structural failure, and measures whether that reduces constraint decay on a toy backend task.

**The claim as stated cannot fail in an interesting way.** If the loop blocks until `archy check` passes, then the final artifact passes `archy check` by construction. Measuring "the gate enforced the thing the gate checks" is theater by the definition archy already uses. This is precisely the failure mode the [#289]/[#294] arm-C work was built to avoid, and that line cost a full harness plus 44 live agent runs to return a documented null and a NO-GO on `archy brief`. Repeating that spend on a claim with a guaranteed positive is worse, not better.

The only version worth running, recorded here so the design is not lost:

> **Held-out constraints.** Partition the project's rules into set A (the gate checks these) and set B (held out, never checked during the run, evaluated only at the end). Measure violations of B. A positive result means structural feedback on A made the agent globally more constraint-careful; a null means the gate buys exactly its own coverage and nothing more.

That is a real question with a real possible null, and it is the one an applications section could honestly cite. It is also a substantial bench (a toy backend with genuine layer rules, per-framework loop implementations, enough N for a sign test) for a question with no downstream feature riding on the answer. **Not scheduled.** Revisit only if the white paper needs the empirical claim, or if a usage signal appears.

## 6. Question 5: white paper tie-in

An **applications subsection, not a separate RFC.** The content is one paragraph and one code block: archy's `sdp.mode` dial as a worked example of the advisory-vs-hard-failure bullet, and `archy check`'s `passed` / exit-code contract as a structural verifier that drops into any of the four guard hooks above. The framework-by-framework table in §2 is the appendix version if more space is wanted.

Nothing here justifies a standalone integration RFC, because there is no integration to specify.

## 7. The usage-signal gate

The ticket asks explicitly: is there inbound demand for framework integrations, or is this paper-motivated?

- `ADOPTERS.md` has zero entries.
- Every issue in the repository is authored by the maintainer. No one has asked for LangGraph, CrewAI, Pydantic AI, or Agents SDK support, or for anything adjacent.

It is entirely paper-motivated. That is not disqualifying on its own, several good archy features started from a paper, but combined with §3's finding (the capability the article asks for is already shipped) it means an integration would be building a distribution channel for a capability nobody has yet asked to reach. The same discipline deferred the MCP `archy_render` tool in [`SPEC_VISUALIZATION.md`][spec-viz] §6.3 until agents demonstrably want it.

## 8. Disposition

| Item | Call |
| --- | --- |
| Native adapter packages (`archy-langgraph` et al.) | **Wontfix.** §3. |
| Docs recipe page | **Deferred behind a usage signal.** If it ever lands: one framework done properly and tested (Pydantic AI, whose `output_validator` + `ModelRetry` is the closest semantic fit and whose feedback path is the most useful shape), not four untested snippets against four moving APIs. |
| Per-rule `severity: warn\|error` on `forbid` | **File on its own merit**, independent of this ticket. §4 gap 1. |
| Canned remediation string on violations | **File on its own merit**, shared with [#145]. §4 gap 2. |
| Gate prototype + constraint-decay bench | **Do not run as specified** (tautological). Held-out-constraint design recorded in §5, not scheduled. |
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
