# codegraph vs archy: is Python-only, module-level scope defensible, or a gap?

Answers [#316](https://github.com/hslee16/Archy/issues/316). The benchmark
forensics on [codegraph](https://github.com/colbymchenry/codegraph) are already
closed in [`TOKEN_REDUCTION_CLAIMS.md`](TOKEN_REDUCTION_CLAIMS.md) §8 (task
class, no correctness control) and are not redone here. This document answers
the capability and positioning question instead: **is archy's scope a
deliberate position or a gap, and is archy differentiated enough to keep
building?**

Fact-gathering date: 2026-07-24, with §3a and §3b re-verified against the live
repository on 2026-07-27 after #369 made the descriptive-versus-normative split
archy's headline. Every number below is measured, not asserted; where a number
could not be pulled it says so.

## Summary

**The scope is defensible and the differentiation is real, but it is not the
thing that decides whether archy is worth working on.**

- codegraph and archy do **different jobs**: codegraph is **descriptive**
  (what is in this codebase, where, and what calls what), archy is
  **normative** (what did you declare this codebase should look like, and is it
  drifting). codegraph ships **no quality score, no architecture rules, no
  cycle analysis, no history, and no counterfactual check**. That is not an
  oversight on their part; it is a different product.
- Where the two **do** overlap, archy loses on the merits, and the overlap is
  narrow: 2 of archy's 12 MCP tools (3 CLI commands), and none of the 10 that
  carry its actual thesis.
- **Multi-language: still no** (C1), but the honest reason changed. Not "wrong
  axis"; "the axis where a funded, viral competitor wins and archy would spend
  everything to draw".
- **Module-level: correct** (C2), and not a rationalization. Archy's five axes
  are module and package constructs; four of them are undefined or degenerate
  at symbol granularity.
- **The uncomfortable finding is not about scope at all.** archy has 5 stars,
  3 outside PRs, and an empty `ADOPTERS.md` after 2.5 months. codegraph has
  62,279 stars and 385,293 npm downloads per month after 6. The gap between
  those two numbers is not explained by language coverage or graph
  granularity. It is distribution. **Differentiation is not archy's problem.**

## 1. What each project actually is (A1)

| | codegraph | archy |
| --- | --- | --- |
| Pitch | "turns any codebase into a queryable knowledge graph for AI coding agents" | architectural sensor: score, rules, drift |
| Graph unit | **symbol** (functions, classes, methods) | **module** |
| Edges | calls, imports, extends, implements, framework routes, dynamic dispatch | imports, calls (`call_count` on the module edge) |
| Languages | **20 native (Rust kernel) + ~14 more portable** | **Python only** |
| Engine | Rust kernel, tree-sitter, SQLite + FTS5 | Python, tree-sitter, SQLite index |
| MCP surface | **1 listed tool** (`codegraph_explore`), 7 unlisted | **12 tools** |
| Freshness | native OS file events, 2s debounce, auto-sync | `watcher.py`, 2s debounce, cached graph |
| Scale claim | Linux kernel (70k files, 2M symbols) under 12 min on a 2-core VPS | 10k+ modules, warm graph in seconds |
| Distribution | npx installer, 8 agent integrations, signed/attested builds, telemetry | pip, plugin, `archy install` for 5 clients, no telemetry |
| Business | MIT, **hosted platform coming** (waitlist) | MIT, **no commercial version planned** |

### What archy has that codegraph has none of

This is the load-bearing list, and it is longer than expected:

- **Five-axis composite score** (modularity, acyclicity, depth, equality,
  complexity) and its recorded history (`.archy/history.jsonl`, `archy trend`,
  `archy render --view trend`).
- **Declared architecture rules**: `archy.yaml` layers and `forbid` edges
  (`archy check`), plus transitive import-linter contracts
  (`archy check --contracts`).
- **Cycle analysis as a first-class output** (`archy cycles`, SCC/tangle
  ratio, the acyclicity axis).
- **DSM** with community / layer / topological grouping.
- **Counterfactual pre-edit check** (`archy simulate`): propose an edge delta,
  get the would-be cycles, violations, score delta, and blast-radius change
  without writing a file.
- **Refactor prioritization** (`what-to-refactor-next`, hotspots as CC x churn,
  edit-risk), **duplicate detection**, **change coupling** (git co-change).
- **Snapshot/diff loop** with a risk-weighted summary that reframes each delta
  as a judgment question.

### What codegraph has that archy has none of

- Symbol-level resolution, and everything that follows from it.
- 30+ languages.
- Verbatim source return: `codegraph_explore` hands back the actual code, not
  a description of the graph.
- Dynamic-dispatch hops (callbacks, interface to impl, React re-render) and
  framework-aware routes.
- **Measured** per-language cross-file coverage, published with its residual
  named honestly (73.8% to 100%).

## 2. The overlap is narrow, and archy loses it (A2)

Archy's navigation-shaped surfaces sit in codegraph's job:

| archy surface | codegraph counterpart | honest verdict |
| --- | --- | --- |
| `archy_impact`, `mode="blast"` (blast radius + import chains) | `codegraph_impact` / `explore` blast radius | codegraph wins: symbol granularity, dynamic dispatch, 30+ languages |
| `archy_impact`, `mode="affected"` (CI test selection; CLI `archy affected`) | `codegraph affected` (same flag shape, `--stdin --quiet`) | codegraph wins on the same axis |
| `archy_graph(focus=)` (bounded subgraph) | `codegraph_explore` | codegraph wins: returns source, not structure |

**At MCP-tool granularity that is 2 of 12** (`archy_impact`, whose two modes
both land here, and `archy_graph`), leaving **10 with no codegraph counterpart
at all**. At CLI granularity it is 3 commands (`archy impact`, `archy
affected`, `archy graph`), since `archy affected` is a separate command over
the same logic that `archy_impact(mode="affected")` serves; `archy_affected`
has not been a distinct MCP tool since v0.36 (#227 folded it in). Both counts
appear here because the two granularities give different numbers and quoting
only the larger one would overstate the overlap.

Either way the shape is the same: the surfaces codegraph competes with are the
ones archy built to help an agent *find* things, and none of them carry archy's
thesis. codegraph never claims the other nine.

**The consequence is a backlog decision, not a feeling.** archy should stop
investing in navigation parity. The concrete casualty is
[#270](https://github.com/hslee16/Archy/issues/270) (typed connectors:
inheritance / call / decorator / exception / type-only edges), which is
**symbol-level edge typing, in the job archy does not win, against a competitor
that already ships it across 20 languages with a Rust kernel**. Its ADL
first-class-connector motivation is unchanged and still intellectually sound,
but its competitive position moved. See §6.

## 3. The distinction that actually holds: descriptive vs normative (B1)

codegraph's graph is a **description**: it can tell you `UserService.save` is
called by 14 things, one of them through a callback grep cannot follow. It
cannot tell you that `domain` importing `infrastructure` is *wrong*, because
nothing in a codebase says so. Intent is not in the source.

This is exactly the finding [`PATTERN_DETECTION_EMPIRICS.md`](PATTERN_DETECTION_EMPIRICS.md)
reached the hard way: archy tried to *infer* architectural intent
(hexagonal, Repository, Service Layer) from the graph and it was a documented
NO-GO. Its own conclusion, quoted: "'domain' is a statement of INTENT and the
import graph doesn't carry intent. Contracts work because the user supplies
it."

So the split is not "archy is a worse codegraph". It is:

- **Navigation / comprehension** (codegraph): the graph *is* the answer. The
  user asks a question about code that exists.
- **Structural judgment** (archy): the graph is one input; the *other* input is
  user-supplied intent (declared layers, forbidden edges, an acyclicity
  invariant, a recorded score baseline). The answer is a verdict about drift.

### 3a. Re-verified 2026-07-27, after #369 made this the headline

#369 narrowed archy's claim to direction, transitive reach and cycles, which
makes this section load-bearing rather than background. So it was re-checked
against the live repository (62.8k stars, 774 commits) rather than restated
from the 2026-07-24 pass:

| capability archy now leads with | codegraph, 2026-07-27 |
| --- | --- |
| import cycle detection | **not shipped**; no command |
| declared layers / forbidden edges | **no config format**; nowhere to state intent |
| dependency-direction violations | **no rule concept**, so nothing to violate |
| transitive traversal | yes, inside `affected`, for **test selection** |
| a command that exits non-zero on a violation | **none** |

Its surface is `explore`, `node`, `query`, `callers`, `callees`, `impact`,
`files`, `affected`, `status`, with a single MCP tool listed by default
(`codegraph_explore`).

**The sharpening this forced.** "archy checks the part you cannot see" is not
quite true and codegraph is why: ask it the right question and it *will* show
you that `models` imports `repositories`. What it will not do is say so
unprompted, call it wrong, or fail a build. The honest line is not visibility,
it is **assertion**: descriptive tools answer questions, archy makes a claim
that breaks CI. The README now says it that way.

### 3b. The risk this analysis previously left as comfort

**The moat is product intent, not capability, and that should be written down
rather than assumed.** codegraph already holds the graph. Tarjan's algorithm is
textbook, and a rules config is a weekend's work. If enforcement ever serves
their job, the technical barrier is approximately zero and the distribution gap
(62.8k stars against 5) means they would arrive with an audience archy does not
have.

What protects archy today is that enforcement does not serve navigation and
token reduction, which is the job they have measurably chosen (a reported 60%
cost reduction across seven benchmark repos). That is a real and durable reason,
but it is a decision on their side, not a wall on ours. **Treat any codegraph
release note mentioning rules, policy, lint, or CI gating as the signal that
this document needs redoing.**

archy's own two nulls (#282 footprint, #289 brief) both measured **editing**
and found zero headroom, which is consistent with this: archy was never good at
the navigation job, and its measured failures were in trying to help with it.
The tools that carry archy's thesis have never been the ones it benchmarked.

**Does codegraph do anything in the judgment job? No.** No score, no rules, no
cycles, no trend, no counterfactual. Its docs site names three pillars:
tree-sitter parsing, MCP server, impact analysis. All three are descriptive.

**Caveat with a date on it.** codegraph's announced hosted platform is pitched
as "for every PR, know exactly what to test, what could break, which flows are
affected, and whether business logic is compromised". "What could break" on a
PR is adjacent to [#145](https://github.com/hslee16/Archy/issues/145) (archy's
per-PR structural review brief), which is archy's strongest unbuilt feature.
The judgment space is unoccupied **today**; it is not guaranteed to stay that
way, and the encroachment would come from the direction of PR review.

## 4. C1: multi-language, answered honestly

**Disposition: still Python-only. The rejection stands, the reasoning is
updated and weaker than it was.**

The old reason (ROADMAP: "out of scope; that division of labor with sentrux is
settled") reads as a principled boundary. codegraph makes that harder to say
with a straight face: it demonstrates 20 languages via tree-sitter grammars
compiled into a Rust kernel, with per-language graphs validated byte-for-byte
against a reference engine. Breadth is clearly achievable.

Two things are true and both need saying:

1. **Archy's metrics are not Python-specific.** Newman modularity, Gini,
   SCC/tangle ratio, layer depth, and McCabe are all language-agnostic
   constructs over a module graph. Only the *parser* and the import-resolution
   layer are Python-bound. So "Python-only because the metrics are Pythonic"
   would be false, and this document declines to make that claim.
2. **The cost is the parser and resolution layer per language, and that is
   exactly where a funded, viral competitor with a Rust kernel wins.** archy
   is one maintainer with zero usage signal. Spending the next year on
   language breadth would be spending everything to draw against an opponent
   who is already ahead on it.

So the honest formulation: **Python-only is a resourcing decision that happens
to align with a defensible position, not a first-principles boundary.** The
defensible part is real, though: an agent working in a Python codebase gets
Python-specific structure (import semantics, `__init__` re-exports, relative
imports, `TYPE_CHECKING` blocks) that a 30-language generalizer has less
incentive to model exactly.

**Revisit only if** a usage signal appears *and* the request is specifically
for archy's judgment surface in another language, which is a different and much
smaller ask than general multi-language navigation.

## 5. C2: symbol vs module granularity

**Disposition: module-level is correct for archy's axes, and this one is a
genuine defense rather than a rationalization.**

Walk the five axes down to symbol granularity and four of them break:

| axis | at symbol level |
| --- | --- |
| **modularity** (Newman Q over the module graph) | Computable over a call graph, but [`AXIS_REVIEW.md`](AXIS_REVIEW.md) already rejected the call-weighted variant as an axis on directionality and actionability grounds. Adding granularity does not fix a direction-contested signal. |
| **acyclicity** | **Degenerates.** Function-level cycles are overwhelmingly recursion and mutual recursion, which are normal and often correct. The signal exists at module level precisely because a module cycle is usually unintended. |
| **depth** (layering) | **Undefined.** Layers are a package and directory concept. "What layer is this function in" only has an answer by way of the module it lives in. |
| **equality** (Gini over out-degree) | Computable, but it would measure fan-out of individual functions, which is a style property, not an architecture property. |
| **complexity** (per-function CC) | **Already symbol-level**, aggregated up. No change needed. |

The one place symbol granularity genuinely wins is **impact precision**:
"changing this function affects these 6 call sites" beats "changing this module
affects these 14 modules". That is real, and it is *precisely* the surface
codegraph already dominates (§2). So the finding is not "archy should go
symbol-level"; it is **archy should stop competing where symbol granularity is
the deciding factor**.

Cost note, recorded so a future revisit starts correctly: archy's graph is
**internal-only by construction** (`_external_target` collapses external nodes,
`assemble_graph` drops external edges), and `PATTERN_DETECTION_EMPIRICS.md`
already found that any analysis needing external imports has to reach one layer
below the graph. A symbol-level rebuild would be a bigger change than that, not
a smaller one: new node type through graph, index, cache, every tool, and the
score.

## 6. C3: the one falsifiable sentence

> **archy is the only local, agent-facing tool that holds a codebase's
> *declared* structure (layers, forbidden edges, an acyclicity invariant, a
> recorded score baseline) and reports when an edit or a week of edits violates
> it; every graph tool in its category, codegraph included, describes the
> structure that exists and has no representation of the structure you
> intended.**

Falsifiable three ways, and worth re-checking on any revisit:

1. codegraph (or an equivalent) ships architecture rules, a quality score, or a
   trend. Today: none of the three.
2. import-linter, deptry, or a similar Python tool ships an agent-facing MCP
   surface with a composite score and history. import-linter is the closest
   (archy *wraps* it for transitive contracts) but is CI-shaped, not
   agent-shaped, and has no score.
3. Someone demonstrates that the normative job does not need a tool, because an
   agent reading `archy.yaml` plus `CLAUDE.md` complies just as well.
   **Partially true already**, and recorded in
   [`FUTURE.md`](../FUTURE.md): the 2026-05 agent-loop test in
   `governingdocs/backend` found a fresh agent caught a forbidden cross-layer
   import by *reading the config*, not by calling `archy_check`. This is the
   most dangerous of the three and the one archy has the least evidence
   against.

## 7. Is archy worth working on? (C3, the part the ticket actually cares about)

Separate two questions that keep getting merged.

**Is archy differentiated? Yes, and more cleanly than expected.** 10 of 12 MCP
tools have no counterpart in the most successful tool in the adjacent category. The
normative job is real, unoccupied, and structurally hard for a
navigation-first tool to enter, because it needs user-supplied intent rather
than better parsing.

**Is archy succeeding? No, and the reason is not differentiation.**

| | archy | codegraph |
| --- | --- | --- |
| Age at measurement | 2.5 months | 6 months |
| Stars | **5** | **62,279** |
| Forks | 5 | 3,900 |
| Issues filed by outsiders | **0 of 89** | n/a |
| PRs from outsiders | **3** | n/a |
| Package downloads | pypistats rate-limited at time of writing | **385,293/month (npm)** |
| `ADOPTERS.md` | empty | n/a |

A four-orders-of-magnitude gap is not caused by language coverage or graph
granularity. codegraph did three things archy has not: a one-command `npx`
install with an interactive installer, integrations with **8** agent clients
shipped as a headline, and a benchmark packaged as a viral artifact. archy did
a Show HN in May 2026 and then **31 releases** of engineering with no second
distribution attempt.

**So the answer to "is it worth working on" is conditional, and the condition
is not technical.** The product is differentiated. The differentiation is
invisible. Every research ticket in this directory for the last three months
has correctly deferred features "behind a usage signal", and a usage signal has
not appeared, because nothing has been done to produce one. Continuing to build
judgment features and defer them behind a signal that no one is generating is a
closed loop.

That is a finding about **priority**, and it is the honest answer to the
ticket's question: the next unit of work with the highest expected value is
not #270, not #129, and not another research doc. It is distribution.

## 8. Dispositions (D1, D2)

| candidate | anti-theater | discriminant | usage signal | beats the null? | verdict |
| --- | --- | --- | --- | --- | --- |
| Multi-language support | n/a | n/a | zero | No: competes where the competitor is strongest | **WONTFIX** (confirmed, reasoning updated per §4) |
| Symbol-level graph rebuild | n/a | n/a | zero | No: serves the job archy loses; 4 of 5 axes degrade (§5) | **WONTFIX** |
| Match `codegraph_explore` (return source) | Fails: it is their product, not a signal archy computes | No | zero | No | **WONTFIX** |
| Collapse the MCP surface toward 1 tool | Passes (measurable in-repo) | Marginal | zero | Unproven either way | **Not scheduled.** Recorded in §9 as evidence #265's direction was right; archy's tools answer distinct *decisions*, which is the #265 test, so a 1-tool surface is not obviously correct |
| #270 typed connectors | Passes on ADL grounds | Yes | zero | **Weakened**: symbol-level edge typing is navigation work (§2) | **Downgrade, do not close.** Retain the ADL thesis; drop the priority |
| Honest positioning in README + this doc | Passes | n/a | n/a | n/a | **Do it** (this document; §9 covers the README line) |
| Distribution work | n/a | n/a | **the point is to create one** | n/a | **The recommendation** (§7). Out of scope for this ticket to design |

**No new feature tickets are filed from this research.** That is the fourth
consecutive research ticket to end in no new features, and at this point that
pattern is itself the signal §7 describes.

## 9. Follow-ups

1. **README positioning line.** archy's README does not currently say what it
   is *not*. One honest sentence ("archy is a structural-judgment tool, not a
   code-navigation tool; if you want an agent to find and read code fast, use a
   navigation-first graph tool, and run archy alongside it for the rules and
   the score") costs nothing and prevents the wrong comparison.
2. **Coexistence is the correct stance, not competition.** Both are local MCP
   servers; nothing stops a user running both. archy should say so rather than
   pretend the navigation surface is competitive.
3. **#270 is downgraded, not closed.** Its ADL motivation survives §2; its
   priority does not.

## Sources

- codegraph README, fetched 2026-07-24
  (`raw.githubusercontent.com/colbymchenry/codegraph/main/README.md`), and its
  docs site (`colbymchenry.github.io/codegraph/`).
- GitHub API, 2026-07-24: repo stats for both projects, archy issue and PR
  authorship over all 89 issues and 230 PRs.
- npm registry API, 2026-07-24: `@colbymchenry/codegraph` last-month downloads.
- Prior archy research, not re-derived here:
  [`TOKEN_REDUCTION_CLAIMS.md`](TOKEN_REDUCTION_CLAIMS.md) §8 (benchmark
  forensics, task-class insight, missing correctness control),
  [`PATTERN_DETECTION_EMPIRICS.md`](PATTERN_DETECTION_EMPIRICS.md) (intent is
  not in the graph), [`AXIS_REVIEW.md`](AXIS_REVIEW.md) (call-weighted Q
  rejected as an axis), [`PREWALK_READ_REDUCTION_SYNTHESIS.md`](PREWALK_READ_REDUCTION_SYNTHESIS.md)
  and `RESEARCH_METRICS.md` §14c.7 (the two nulls, both on editing tasks).
