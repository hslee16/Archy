# Token-reduction claims in coding-agent tooling: what are they based on, and should archy build one?

**Status: COMPLETE.** Every checklist item is answered against primary sources
and §9 carries the call. The checklist is retained so the coverage is auditable.

**Answer in one line: NO-GO.** The category splits into prefix bloat (real,
measured, and archy already solved its share) and comprehension context
(unmeasured by its own vendors, and archy tested its version twice and got
null). No tickets filed.

## The question

Tools, harnesses, and skills increasingly claim to reduce the token usage of
coding agents. What mechanisms are those claims actually based on? Which are
measured and which are marketing? And does it make sense for archy to ship
anything in this category?

archy has a strong and uncomfortable prior here, recorded in
[`RESEARCH_METRICS.md`](RESEARCH_METRICS.md) §14c.7: **archy tested two
token-reduction propositions at its own layer and both returned null.**

- **#282**: applied archy's own top `what_to_refactor_next` recommendation,
  measured agent footprint. `footprint_tokens` median **+4,213 in the
  refactored direction** (4/6 pairs, p=0.754). The headline leaned the wrong way.
- **#289**: injected an archy structural brief before the task. `pre_edit_reads`
  median **−2.0** (14/8, **p=0.286**), after an N=10 hint at p=0.109 that did
  not survive more data. NO-GO on `archy brief`.

So this research is not "how do we build one". It is: **given that we already
failed twice, is there a mechanism in this space that we have not tested and
that would survive our own gates?** A no-go is the expected outcome and a fine
one.

## Gates any positive recommendation must clear

1. **Anti-theater**: does the mechanism have a measurable, falsifiable effect,
   or is it advice dressed as a feature? (Templates: §14c.6, and the #288 /
   #298 non-results.)
2. **OECD / discriminant validity**: does it measure something archy does not
   already measure, and is it actionable?
3. **Usage signal**: is anyone asking, or is this paper/hype-motivated?
   (Baseline: `ADOPTERS.md` is empty; every issue is maintainer-authored.)
4. **Beat the null**: any proposed mechanism must explain why it would succeed
   where #282 and #289 failed.

## Research checklist

### Part A: map the landscape
- [x] A1. Enumerate the categories of token-reduction tooling. Candidate axes:
      context compaction / summarization, retrieval over code (RAG, embeddings),
      sub-agent context isolation, tool-surface reduction, progressive disclosure
      (skills / lazy-loaded instructions), repo packing (repomix-style), code
      maps / skeletons / outlines (tree-sitter, ctags), LSP-backed navigation,
      diff-scoped context, output-format compression, prompt caching.
- [x] A2. For each category, name 2-4 concrete tools/products and what exactly
      they claim, quoted, with the source.
- [x] A3. For each, state the **mechanism**: what physically removes tokens?
      (fewer files read, shorter representation of the same files, cache hits,
      fewer turns, smaller tool schemas, deferred loading.)

### Part B: separate measured from marketing
- [x] B1. Which claims come with a published methodology and numbers? Record
      N, baseline, model, task set. Which are bare assertions?
- [x] B2. Any independent replication or third-party benchmark? (Look for
      adversarial or negative results specifically, not just vendor blogs.)
- [x] B3. What do the negative / null results in the literature say? (e.g.
      context-rot and "more context hurts" findings, the bitter-lesson claims
      already cited in §14c.6.)
- [x] B4. **The prompt-caching confound.** With prompt caching, re-sending the
      same prefix is cheap. Does "input token reduction" still mean what these
      tools imply? What is the right cost metric (cache-read vs cache-write vs
      output)? Note archy's own finding that `pre_edit_input_tokens` was
      near-useless under caching.

### Part C: does any mechanism apply to archy
- [x] C1. Which mechanisms are even available to a *static dependency-graph*
      tool, as opposed to a harness or an editor?
- [x] C2. For each available mechanism, why would it beat #282 / #289? Be
      specific about what was wrong with those designs (easy/grep-able task,
      brief precision 0.33, no headroom because the agent found the 3-file
      spine unaided) and whether the mechanism addresses it.
- [x] C3. What does archy already ship that is in this category but not
      marketed as such? (MCP surface consolidation #227/#265 shrank
      always-in-context tool schemas; `response_format="summary"` defaults;
      DSM summary ~89% smaller.) Is the honest position "we already did the
      only version that works"?
- [x] C4. Competitive check: do comparable static-analysis / graph tools ship
      token-reduction features, and is there evidence they work?

### Part D: the call
- [x] D1. Apply the four gates to every surviving candidate.
- [x] D2. Write the go / no-go in §9 with reasons, and if no-go, record the
      reopen path so this does not get re-proposed.
- [x] D3. File follow-up tickets only if a candidate clears all four gates.

## 1. Landscape (A1-A3)

Ten categories. The column that matters is **mechanism**: what physically
removes tokens. Sorting by mechanism rather than by product collapses most of
the marketing into four real levers (substitute a smaller representation, defer
loading, isolate context, shrink the always-resident prefix) plus one category
that reduces nothing and one that changes cost without changing tokens.

| # | category | examples | mechanism: what actually removes tokens |
| --- | --- | --- | --- |
| 1 | **Repo packing** | repomix, gitingest, code2prompt | **None.** Serializes a repo into one blob. Repomix's own feature list says "Token Counting: **Tracks** token usage for LLM context limits". That is budgeting, not reduction; the blob is usually far larger than what an agent would have read on its own. |
| 2 | **Code maps / skeletons** | **aider repo map**, ctags/tree-sitter outlines | Substitute signatures for bodies. Aider ranks a file-dependency graph and fits the result to `--map-tokens` (default **1k**). |
| 3 | **Symbol-level retrieval (LSP)** | serena, JetBrains/LSP MCP servers | Read a symbol and its references instead of whole files. |
| 4 | **Progressive disclosure** | Anthropic Agent Skills | Only `name` + `description` sit in the system prompt; SKILL.md loads on trigger; bundled files "stay on the filesystem and cost zero tokens" until read. |
| 5 | **Compaction / summarization** | Claude Code `/compact`, memory tool | Replace a long history with a summary and reinitialize. Costs a summarization call to save future turns. |
| 6 | **Sub-agent isolation** | Claude Code subagents, multi-agent harnesses | Exploration happens in a throwaway context; only a distillate returns. Anthropic's own figure: a subagent may spend "tens of thousands of tokens or more" and return "often 1,000-2,000 tokens". |
| 7 | **Tool-surface reduction** | MCP consolidation, deferred/searchable tool schemas | Shrink the always-resident tool-definition prefix. **archy already did this** (#227, #265): 19 tools to 11. |
| 8 | **Output-format efficiency** | token-efficient tool use, concise response formats | Same information, fewer tokens on the wire. archy ships `response_format="summary"` defaults; DSM summary is ~89% smaller. |
| 9 | **Code retrieval / embeddings** | Cursor-style codebase indexing | Fetch ranked chunks instead of whole files. |
| 10 | **Prompt caching** | Anthropic prompt caching | **Reduces cost, not tokens.** The prefix is still sent and still occupies context; it is billed differently. See §4. |

**Three observations that already shape the call.**

**(a) Category 2 is archy's #289, built by an editor.** Aider's repo map is a
static dependency graph, ranked, truncated to a token budget, and injected
before the task. That is precisely arm C, and archy's version returned null
(`pre_edit_reads` median −2.0, p=0.286). Aider ships the same mechanism as a
default. So the question for archy is not "is this idea plausible" but "why
does aider ship it and our measurement of it come back empty". §6 owes an
answer.

**(b) The strongest mechanisms are harness-level, not analysis-level.**
Categories 4, 5, and 6 are the ones with clean causal stories, and all three
are properties of *the loop*, not of a static analyzer. archy is not a harness
and cannot compact a conversation, spawn a subagent, or defer its own loading
into someone else's system prompt. That is a structural constraint on what
archy could even build, developed in §5.

**(c) Categories 7 and 8 are the ones archy has already shipped**, without
ever calling them token reduction. This is the most likely honest landing
place for the whole investigation (§7).



## 2. Measured vs asserted (B1-B2)

Sorting every headline claim by **what its denominator is** explains the whole
category. The finding is not that these tools are lying. It is that the big
percentages are almost all computed against a **baseline nobody actually
runs**, and that the single study using a real baseline on a real benchmark
reports a **cost**, not a free win.

### The headline claims, with their actual baselines

| claim | source | baseline it is measured against | what kind of evidence |
| --- | --- | --- | --- |
| **"99.9% fewer input tokens"** | Cloudflare Code Mode (Feb 2026) | The Cloudflare API's **2,500+ endpoints exposed as individual MCP tools**, estimated at **1.17M tokens**, replaced by `search()` + `execute()` + a typed SDK at ~1,000 tokens | Arithmetic against a configuration **no one would ever ship**. 1.17M tokens exceeds every production context window; the "before" case is impossible, not merely bad. |
| **"98.7% saving, 150,000 to 2,000 tokens"** | Anthropic, code execution with MCP (Nov 2025) | **One constructed worked example** (a meeting transcript passing through the model twice on its way from Google Drive to Salesforce) | A single illustrative example in an engineering blog post. No task set, no N, no model named for the measurement. |
| **"85% reduction in token usage"** | Anthropic Tool Search Tool (Nov 2025) | Full tool library loaded upfront vs loaded on demand | Vendor benchmark; the mechanism is real and now ships in Claude Code (Tool Search auto-activates when MCP tool descriptions exceed **10% of the context budget**). |
| **"~95%+ on code-reading tasks"** | jCodeMunch MCP (`TOKEN_SAVINGS.md`) | Its own table: "Explore repo structure: **~200,000 tokens** raw vs ~2k" | The raw column is **reading the entire repository**, which is not what an agent does. Self-published, 15 task-runs across 3 repos. |
| **Repo map** | aider | **None published.** | See below. |
| **"faster, more efficiently and more reliably"** | serena | **None published.** | Qualitative vendor assertion, no numbers in the README. |
| **42% fewer input tokens, −12pp resolution rate** | Hrubec & Cito, arXiv 2606.01326 (May 2026) | **SWE-bench Verified, full benchmark, GPT-5-mini**, independent re-implementation | **The only rigorous entry in the table**, and the only one that reports what the reduction costs. |

### B1: what is actually measured

**Almost nothing, in the code-comprehension category.** The two most
influential implementations of the mechanism archy would build are both
**unmeasured on tokens**:

- **aider's repo map** is the canonical code-map implementation (tree-sitter
  symbol extraction, graph ranking over a file-dependency graph, truncated to
  `--map-tokens`, default 1k). Neither the docs page nor the 2023 design
  writeup publishes a token measurement. Aider's *published benchmark* is a
  different thing entirely: **133 Exercism practice exercises**, scored on
  **pass rate**, where each task is a single self-contained stub file. That is
  the setting in which a whole-repo map is least relevant. So the most-cited
  repo map in the ecosystem has **no published evidence that it reduces
  tokens**, because its benchmark measures edit-format success on
  single-file problems.
- **serena** asserts efficiency in prose and publishes no numbers.

**What is measured is tool-definition bloat**, and there the problem is real
and independently documented: a user measurement on claude-code issue #11364
found **seven MCP servers consuming 67,300 tokens of tool definitions, 33.7%
of a 200k context window, before the user types anything** (GitHub MCP alone
~18k for 27 tools). This is the one place in the category where a real,
non-strawman baseline is documented by someone with no product to sell.

### B2: independent replication

Sparse, and the one solid instance is the most important source in this file.

**Hrubec & Cito (arXiv 2606.01326)** is an independent replication and
extension of the state-in-context agent framework, evaluated on **SWE-bench
Verified end to end** with GPT-5-mini plus GPT-4.1 ablations. They identify
source code as the dominant token consumer and apply semantics-preserving
minification. Result:

> minification reduces average input token usage by **42%** with a **12
> percentage-point drop in resolution rate**.

Two things to take from it:

1. **Token reduction is a tradeoff, not a free win**, once you measure against
   a real agent on a real benchmark instead of against a strawman. A 12-point
   resolution-rate drop is not a rounding error; on a benchmark where absolute
   resolution rates sit in the tens of percent, that is a large relative loss
   of capability for a 42% input-token saving.
2. The authors' own framing ("a promising path toward more cost-effective
   agents") is a **choice about which side of the tradeoff to emphasize**. The
   same numbers support "compressing code context measurably degrades the
   agent". Read the numbers, not the abstract's adjective.

**No independent replication exists** for the 99.9% / 98.7% / 95% headline
claims. They are vendor or self-published, and their baselines are
constructed. Note also how they propagate: a secondary aggregator lists 85% /
99.9% / 98.7% side by side as "three families of fix", which is how strawman
denominators become ecosystem common knowledge.

### The distinction that organizes everything

Two different problems wear the same "token reduction" label:

- **Prefix bloat** (tool definitions, always-resident schemas). Real,
  measured (67.3k / 33.7%), and **solved by mechanisms that are now
  platform-level defaults**, not product features.
- **Comprehension context** (which code the agent reads to do the task).
  This is where repo maps, briefs, and symbol retrieval live. It is
  **unmeasured by its own vendors**, and the one rigorous measurement of
  compressing it found it costs capability.

archy's #289 brief was squarely in the second bucket. So was its null.


## 3. Negative results and the context-rot literature (B3)

The negative literature does **not** say "token reduction is pointless". It
says something more specific and more useful: **context length degrades model
performance non-uniformly, and compressing context to fix that degrades
performance too.** Both directions cost something.

**Long context genuinely degrades.** Chroma's context-rot evaluation across
**18 LLMs** (closed and open-weight) finds performance is non-uniform with
increasing input length, and that the standard Needle-in-a-Haystack benchmark
flatters models because it tests only lexical retrieval. Extensions that break
lexical matching (NoLiMa) or test recognizing absence (AbsenceBench) show
substantial drops as input grows. Their distractor experiment is the sharpest
result for our purposes: **even a single distractor reduces performance
relative to a needle-only baseline, and four distractors compound it**, with
non-uniform impact per distractor.

This is the strongest *pro*-token-reduction evidence in the whole file, and
note what it actually licenses: a claim about **relevance and distractors**,
not about volume. Adding a mediocre-precision artifact to the context is
adding distractors. archy's #289 brief had **precision 0.33** (it named 9
files, 3 of them on the true surface). Under the context-rot result, six
irrelevant files in a pre-task brief are not neutral padding; they are
distractors with a measured cost.

**Compressing context also degrades.** The minification replication (§2)
is the direct test: 42% fewer input tokens, **−12 percentage points of
resolution rate** on SWE-bench Verified. You can buy tokens with capability.

**Anthropic's own framing agrees with the tradeoff.** Their context-engineering
guidance treats context as "a critical but finite resource" subject to
"context pollution and information relevance concerns", and says larger
windows will not solve it. Their prescribed fixes are compaction, note-taking,
and sub-agents, all of which are **loop-level architecture**, not
analysis-level artifacts.

**Synthesis for archy.** The literature supports "put less irrelevant material
in the context". It does not support "inject a precomputed structural summary".
Those are different interventions, and archy tested the second one.

## 4. The prompt-caching confound (B4)

This section changes how every number above should be read, and it is the part
most of the marketing ignores.

**The mechanics** (Anthropic prompt caching, verified from the pricing table):

| | multiplier vs base input |
| --- | --- |
| cache write, 5-minute TTL | **1.25x** |
| cache write, 1-hour TTL | **2x** |
| **cache hit / refresh** | **0.1x** |

**Consequence 1: "input tokens" is not a cost unit.** After the first turn, a
token sitting in a stable prefix costs **one tenth** of list price on every
subsequent turn. So a tool that removes 100k "input tokens" from a cached
prefix saves the equivalent of 10k uncached tokens, not 100k. Every headline
percentage in §2 is computed on uncached list-price arithmetic, which
**overstates the real cost saving by up to 10x** for anything that would have
been cached.

**Consequence 2: two objectives are being conflated under one word.**

| objective | is it fixed by caching? | what it is really about |
| --- | --- | --- |
| **Cost** of resending a stable prefix | **Largely yes**, 0.1x | billing |
| **Context occupancy** and its quality cost | **No.** Cached tokens still occupy the window and still act as distractors | §3, context rot |

A claim of "98.7% token reduction" is a *cost* claim, and caching has already
taken most of that win. The *quality* problem it implicitly gestures at is
untouched by caching and is not measured by any of the vendor numbers.

**Consequence 3, and the one that specifically indicts an archy brief.** A
precomputed brief is paid on **every task, unconditionally**, whether or not
it helps. The reads it hopes to displace are paid **only when the agent
chooses to make them**, and from the second turn onward they are cache reads at
0.1x. So the brief's break-even is far worse than the naive "brief tokens vs
read tokens" arithmetic suggests, and it must clear that bar on *every* run
including the ones where the agent would have found the answer immediately.

This is not hindsight. It is exactly what the #289 spec pre-registered as the
trap:

> An archy brief *is* a distilled artifact, so the null is that it **adds**
> reads (agent reads the brief AND still reads source) rather than
> substituting. Charging the brief's own tokens against the arm is what
> separates a real reduction from bookkeeping.

And archy's own harness independently rediscovered the mechanism: the
`pre_edit_input_tokens` metric was recorded as **near-useless under prompt
caching**, which is why the bench headlines read *counts* (reads, distinct
files) rather than input tokens. archy already learned, the hard way, the
thing this section says the vendor numbers get wrong.

## 5. What is available to a graph tool (C1)

Of the ten categories in §1, most are simply **not archy's to build**. archy is
a static analyzer exposed over MCP. It does not own the agent loop, so it
cannot compact a conversation, spawn a sub-agent, or defer its own loading.

| # | category | available to archy? |
| --- | --- | --- |
| 1 | Repo packing | No, and it reduces nothing anyway |
| 2 | **Code maps / briefs** | **Yes. Built it, measured it, null (#289).** |
| 3 | Symbol retrieval | Partly, and already shipped as `archy_graph(focus=)` / `archy_impact`, which the agent calls when it wants them |
| 4 | Progressive disclosure | **Not archy's to control.** The client decides; Claude Code now defers MCP tools by default |
| 5 | Compaction | No. Harness-level |
| 6 | Sub-agent isolation | No. Harness-level |
| 7 | **Tool-surface reduction** | **Yes, and already done** (#227, #265) |
| 8 | **Output-format efficiency** | **Yes, and already done** (`response_format="summary"`) |
| 9 | Embeddings / RAG over code | Possible, but off-thesis and duplicates 3 |
| 10 | Prompt caching | No. Platform-level |

**Three are genuinely archy's: 2, 7, and 8.** Category 2 is the one archy
tested and could not make work. Categories 7 and 8 are already shipped. That
is the entire available surface, and it is already spent.

## 6. Would anything beat our two nulls (C2)

No, and the landscape survey makes the case *stronger* rather than weaker.

**Why #289 failed, precisely.** The null was not "briefs are a bad idea in
principle". It was mechanism-specific: the task's true surface was **3 files**
and the agent **found them unaided**, so there was no headroom to recover;
brief precision was **0.33** (9 files named, 3 on-surface); and breadth
(`pre_edit_distinct_files`) was **flat at delta 0**. The recorded lesson was
that the path to an effect is a **harder setting**, not a better brief, because
breadth never moved even with a noisy brief.

**What the survey adds to that.**

1. **The precision problem is worse than a precision problem.** §3's
   distractor result means a 0.33-precision brief is not merely diluted, it is
   **six distractors injected into the context**, with a measured cost to
   retrieval performance. Improving brief precision is not tuning; it is
   necessary just to stop the artifact doing harm.
2. **The one rigorous compression study reports a capability cost.** 42% fewer
   input tokens for **−12pp resolution rate**. A brief is a compression of the
   codebase into the prompt. The prior from the best-measured neighbour is that
   this trades capability for tokens.
3. **The strongest precedent has no evidence behind it.** Aider ships the repo
   map as a default and publishes **no token measurement**; its benchmark is
   133 single-file Exercism exercises scored on pass rate. So "aider does it"
   is not evidence that it works, and archy's null is not contradicted by
   anyone's published number. archy is, as far as this survey found, **the
   only party in this category that actually measured the mechanism and
   reported the result.**
4. **Caching makes the arithmetic worse** (§4): the brief is paid on every run
   at full price, while the reads it hopes to displace are cache reads at 0.1x
   from the second turn.

**Conclusion for C2.** Nothing in the landscape supplies a mechanism that
addresses the specific reason #289 failed. The honest reopen condition remains
what `PREWALK_READ_REDUCTION_SYNTHESIS.md` §7 already recorded: a harder
setting (large unfamiliar repo, non-grep-discoverable surface, weak executor),
not a better artifact.

## 7. What archy already ships in this category (C3)

**This is the answer to the ticket's real question, and it is already done.**

The one part of the token-reduction space with a real, independently measured,
non-strawman problem is **prefix bloat** (§2): 67,300 tokens of tool
definitions across seven MCP servers, 33.7% of a 200k window, before the user
types anything. archy attacked exactly that in #227 and #265, going from 19
tools to 11, explicitly reasoning that "a smaller, less-overlapping surface
costs fewer always-in-context tokens and improves tool-selection accuracy".

**So how big is archy's own surface today?** Measured directly from
`create_server().list_tools()` with `tiktoken` cl100k_base:

| component | tokens | note |
| --- | --- | --- |
| descriptions | 2,256 | |
| `inputSchema` | 1,662 | |
| **model-visible total** | **3,918** | **1.96% of a 200k window, ~356 tokens/tool** |
| `outputSchema` | 14,427 | **client-side only, see caveat** |
| full MCP payload | 18,345 | 9.17% of a 200k window |

**The caveat that decides how to read this, and it matters.** The Anthropic
Messages API tool definition carries `name`, `description`, and `input_schema`.
There is **no output-schema field**. So MCP `outputSchema` (adopted in #228 for
`structuredContent`) does not map into the model-visible tool spec: a client
bridging MCP to the API consumes it for validation rather than forwarding it to
the model. An earlier draft of this section reported the 18,345 figure as
archy's context cost and called archy a major bloat contributor. **That was
wrong**, and the corrected number is 5x smaller.

Residual uncertainty, stated rather than hidden: this is an inference from the
API surface, not an observation of every client's behaviour. A client is free
to inline `outputSchema` into the description. Confirming archy's real resident
cost per client would need the `count_tokens` endpoint with the server's
`tools/list` attached, which needs an API key and has not been run.

**Two further facts that close the category.**

- **Claude Code now defers MCP tools by default.** Tool search is on by
  default; "only tool names and server instructions load at session start", and
  only tools Claude actually uses enter context. `ENABLE_TOOL_SEARCH=auto`
  loads upfront when tools fit within **10% of the context window**. So on the
  dominant client, archy's surface is deferred, and even under the threshold
  mode its 1.96% loads comfortably.
- **archy's per-tool cost is already lean**: ~356 model-visible tokens per
  tool against GitHub MCP's ~18,000 tokens for 27 tools (~667/tool).

**The honest position: archy already did the only thing in this category with
real evidence behind it, and it did it two releases ago without calling it
token reduction.** The remaining work is maintenance (keep descriptions tight
as tools are added), not a feature.

## 8. Competitive check (C4)

Yes, archy's direct analogues ship this claim, and one of them has the best
evidence in the entire survey. It also, on inspection, **explains archy's
nulls rather than contradicting them.**

### CodeScene CodeHealth MCP

Claims "up to 45% fewer tokens burned" alongside "60% lower defect risk" and
"MCP-guided agents fix 2-5x more Code Health issues". The supporting material
on the product page is rendered as images with no methodology in text, and
"up to" is doing load-bearing work. Treat as a **vendor claim**. Note also that
it is the *cleanliness lowers agent cost* proposition from
[`RESEARCH_METRICS.md`](RESEARCH_METRICS.md) §14c.6, which is exactly the
proposition archy tested at its own layer in #282 and could not reproduce.

### codegraph: the closest analogue, and the most serious benchmark found

`codegraph` is a local-first, MCP-native code knowledge graph: symbols, call
edges, dependencies, blast radius. That is archy's category. Its published
benchmark is markedly better than anything else in this survey:

- **7 open-source repos across 7 languages** (VS Code ~11k files, Excalidraw,
  Django, Tokio, OkHttp, Gin, Alamofire)
- **Claude Code headless**, with and without the index
- **median of 4 runs per arm**, re-validated 2026-07-21 on Opus 4.8
- per-repo absolute numbers, not just percentages

Headline: **89% fewer tool calls, 69% fewer tokens, 60% cheaper, file reads cut
to zero on all seven repos.** Per-repo: VS Code 2 vs 40 tool calls and 83%
fewer tokens; Django 2 vs 29 and 78% fewer; Alamofire 3 vs 53 and 90% fewer.

It is also **honest about its own weak spots** in a way vendor benchmarks
rarely are: it flags a small-repo floor effect where a strong model's grep loop
wins wall-clock while spending 5-10x the tokens, notes that OkHttp's
without-arm "got lucky in 5 calls", and calls time the noisiest metric. It
publishes measured cross-file coverage per language (86.7% to 100%) rather
than asserting it.

**So why did archy measure null?** Because of the task class, and this is the
most useful finding in the section:

| | codegraph benchmark | archy #282 / #289 |
| --- | --- | --- |
| task | **answer one architecture question** | **make a code edit** (refactor flask, teardown-ordering fix) |
| does the graph contain the answer? | **Yes.** "What calls this?" is a graph query | **No.** The agent must read code to write code |
| baseline behaviour | agent greps the tree to rebuild structure, up to 57 tool calls and millions of tokens | agent reads the ~3-file surface it needs, and finds it unaided |
| headroom | enormous | **none, measured** (`pre_edit_distinct_files` delta = 0) |

**These results are not in conflict.** A dependency graph is an *answer* to a
structural question and merely a *hint* for an editing task. codegraph
measured the case where the graph is the answer; archy measured the case where
it is a hint. Both got the result their task class implies.

That reframes archy's nulls from "archy's graph does not help agents" to the
narrower and better-supported "**a structural brief does not measurably reduce
the footprint of an editing task on an easy, grep-discoverable surface**",
which is what `PREWALK_READ_REDUCTION_SYNTHESIS.md` §7 already concluded from
the inside.

## 9. Recommendation (D1-D3)

**NO-GO on building any token-reduction feature. No tickets filed.**

The category splits cleanly once you sort by mechanism, and archy's position in
each half is already settled:

- **Prefix bloat** (tool definitions). The one real, independently measured
  problem. **archy already solved its share** (#227, #265) and now measures
  **3,918 model-visible tokens, 1.96% of a 200k window, ~356 tokens per tool**.
  The platform has also moved underneath it: Claude Code defers MCP tools by
  default. Nothing to build.
- **Comprehension context** (briefs, maps, retrieval). Unmeasured by its own
  vendors, and the one rigorous compression study reports **42% fewer tokens
  for −12pp resolution rate**. archy tested its version twice and got null.
  Nothing to build that we have reason to believe would work.

### D1. The gates, applied to every candidate

| candidate | anti-theater | discriminant | usage signal | beats the null? | verdict |
| --- | --- | --- | --- | --- | --- |
| `archy brief` / repo map (redo) | Fails. Measured null already; the strongest precedent (aider) publishes no token evidence | No | None | **No.** Nothing addresses the measured zero headroom | **Wontfix** |
| Compress archy's tool descriptions further | Passes (deterministic, in-repo measurable) | Marginal | None | n/a | **Not worth it.** 1.96% is already lean; effort better spent elsewhere |
| Trim `outputSchema` | **Fails on premise.** It is client-side payload, not model context (§7) | No | None | n/a | **Wontfix** |
| Token-savings marketing claim | Fails hard. We have two nulls of our own | No | None | No | **Forbidden.** See §14c.7 |
| Structural-Q&A footprint bench | Passes (falsifiable, real headroom per §8) | Yes | **None** | **Yes**, different task class | **Recorded as reopen path, not scheduled** |

### D2. The call, and why

**Do not build.** Three independent reasons, any one sufficient:

1. **The available surface is spent.** Of ten categories, only three are a
   static analyzer's to build (§5). Two are shipped. The third is measured
   null, twice.
2. **The headline numbers do not survive their baselines.** 99.9% is against a
   1.17M-token configuration nobody ships; 98.7% is arithmetic on one
   constructed example; 95% is against reading an entire repo. And caching
   already took most of the cost win those numbers describe, overstating real
   savings by up to 10x for cached prefixes (§4).
3. **Zero usage signal.** `ADOPTERS.md` is empty, every issue is
   maintainer-authored, and nobody has asked archy to reduce their token bill.
   This is hype-motivated, which is precisely the case gate 3 exists to catch.

**What archy should say instead.** The honest positioning, consistent with
§14c.7: archy is a deterministic structural checker. It catches cycles, layer
violations, and score regressions an agent would otherwise introduce. It does
not claim to make agent runs cheaper, because we tested that and could not show
it.

### D3. Tickets

**None filed.** Nothing cleared all four gates.

### The reopen path, recorded so this is not re-proposed from scratch

§8 supplies, for the first time, a real answer to gate 4 ("why would it beat
the null?"): **task class**. Every archy footprint study to date used an
**editing** task, where the graph is a hint and the measured headroom was zero.
codegraph's benchmark shows a large effect on **structural question answering**,
where the graph *is* the answer, and archy already ships the tools that would
serve it (`archy_impact`, `archy_graph(focus=)`, `archy_cycles`).

If this is ever revisited, the design is:

> **Arm D, structural Q&A.** Fix a set of structural questions with verifiable
> answers ("what breaks if I change X?", "what are the cycles through Y?",
> "what is the blast radius of Z?"). Compare an agent answering them with the
> archy MCP server available vs with only grep/read. Measure tool calls, file
> reads, and tokens to a *correct* answer, scoring correctness explicitly so a
> fast wrong answer cannot win. Use a large, tangled repo, since §8 reports the
> effect scales with repo size and vanishes on ~100-file projects.

Three conditions before running it, and they are not close to met today:

1. A **usage signal**. Someone asking, or an adopter reporting the cost.
2. Acceptance that a positive result substantiates a claim about **existing
   tools**, not a new feature. There is nothing to ship at the end of it.
3. A pre-registered correctness gate, because "answered in 2 tool calls" is
   worthless if the answer is wrong, and the whole category's failure mode is
   optimizing a proxy.

Absent those, running it would be measuring for marketing, which is the
definition of the theater this repo keeps declining to build.

## Sources

Tiered by what kind of evidence they are. This tiering is the point of the
whole file: most of the category's famous numbers sit in the bottom tier.

### Independent / peer-reviewable measurement

- Hrubec & Cito, *Reducing Token Usage of State-in-Context Agents using
  Minification*, arXiv [2606.01326](https://arxiv.org/abs/2606.01326) (May
  2026). Independent replication, SWE-bench Verified, GPT-5-mini. **42% fewer
  input tokens, −12pp resolution rate.** The single most important source here.
- Fan et al., *SWE-Effi: Re-Evaluating Software AI Agent System Effectiveness
  Under Resource Constraints*, arXiv
  [2509.09853](https://arxiv.org/abs/2509.09853). Establishes agent cost as an
  evaluation axis.
- Chroma, [*Context Rot*](https://research.trychroma.com/context-rot). 18 LLMs;
  non-uniform degradation with input length; a single distractor measurably
  hurts. Replication code published.
- Community measurement on
  [claude-code issue #11364](https://github.com/anthropics/claude-code/issues/11364):
  seven MCP servers, **67,300 tokens of tool definitions, 33.7% of a 200k
  window**. The one non-strawman baseline in the prefix-bloat half.

### Vendor benchmarks (methodology published, vendor-run)

- [codegraph](https://github.com/colbymchenry/codegraph). 7 repos, 7 languages,
  Claude Code headless, median of 4 runs per arm. 89% fewer tool calls, 69%
  fewer tokens. **Scope: answering one architecture question**, not editing.
  Publishes its own weak spots and measured per-language coverage.
- Anthropic, [Tool Search Tool](https://www.anthropic.com/engineering/advanced-tool-use)
  (Nov 2025). "85% reduction in token usage."

### Vendor claims (illustrative arithmetic or unquantified)

- Anthropic, [code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp).
  "150,000 tokens to 2,000 tokens, a saving of 98.7%", from **one constructed
  example**.
- Cloudflare, [Code Mode](https://blog.cloudflare.com/code-mode/) and the Feb
  2026 follow-up. "99.9%", against 2,500+ endpoints at an estimated 1.17M
  tokens, a configuration that cannot be run.
- [CodeScene CodeHealth MCP](https://codescene.com/product/code-health-mcp).
  "Up to 45% fewer tokens burned." Evidence rendered as images.
- [jCodeMunch `TOKEN_SAVINGS.md`](https://github.com/jgravelle/jcodemunch-mcp/blob/main/TOKEN_SAVINGS.md).
  "~95%+", baseline = reading the whole repository.
- [serena](https://github.com/oraios/serena). Efficiency asserted in prose, no
  numbers.
- [aider repo map](https://aider.chat/docs/repomap.html) and the
  [tree-sitter writeup](https://aider.chat/2023/10/22/repomap.html). Mechanism
  documented, **no token measurement published**; the
  [benchmark](https://aider.chat/docs/benchmarks.html) is 133 single-file
  Exercism exercises scored on pass rate.
- [repomix](https://repomix.com/guide/). Counts tokens, does not reduce them.

### Platform mechanics (primary documentation)

- Anthropic, [prompt caching](https://docs.claude.com/en/docs/build-with-claude/prompt-caching).
  Cache write 1.25x (5m) / 2x (1h); **cache hit 0.1x**.
- Anthropic, [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview).
  Progressive disclosure; unused bundled files "cost zero tokens".
- Anthropic, [effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).
  Compaction, note-taking, sub-agents; sub-agent distillate "often 1,000-2,000
  tokens".
- [Claude Code MCP docs](https://docs.claude.com/en/docs/claude-code/mcp).
  Tool search on by default, MCP tools deferred; `auto` mode uses a 10%
  context-window threshold.
- [MCP specification, Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).
  `outputSchema` semantics.
- Anthropic [tool use overview](https://docs.claude.com/en/docs/agents-and-tools/tool-use/overview).
  Tool definitions carry `name`, `description`, `input_schema`; **no
  output-schema field**, which is what makes §7's correction necessary.

### archy's own evidence

- [`RESEARCH_METRICS.md`](RESEARCH_METRICS.md) §14c.6, §14c.7.
- [`PREWALK_READ_REDUCTION_SYNTHESIS.md`](PREWALK_READ_REDUCTION_SYNTHESIS.md).
- [`../../bench/agent_footprint_results.md`](../../bench/agent_footprint_results.md)
  (#282 N=10, #289 N=22, both null).
- Direct measurement of archy's own MCP surface, this file §7, reproducible via
  `create_server().list_tools()` plus `tiktoken`.
