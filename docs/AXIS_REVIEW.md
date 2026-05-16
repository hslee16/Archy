# Axis review: are the 5 axes sufficient? Should `calls_per_edge` become the 6th?

This document records a deliberate review of archy's score axes after the v0.20 promotion of `cc_mean` to `complexity`. The two questions are independent and the answers diverge.

**TL;DR**

- The 5 current axes (modularity, acyclicity, depth, equality, complexity) are sufficient for what they measure. A real but narrow gap exists; it is coupling-strength and type-coverage shaped, not call-density shaped.
- **`calls_per_edge` should not be promoted to a 6th axis.** It passes the orthogonality check but fails the directionality, actionability, and discriminant-validity checks an OECD-style composite indicator requires.
- The best candidate if a 6th axis is ever added was **type-hint coverage** (Python-specific, clear direction, cheap to compute, actionable). **Empirics ran in 2026-05** and concluded against shipping it in any form: independence is the weakest archy has measured (max `|r| = 0.551`), discriminant validity is contested (django / numpy / boto3 at near-zero coverage are widely-respected codebases), and the value-prop argument against even a diagnostic surface is strong (better tools own the niche, the signal is not structural, and the slippery slope toward "complete-codebase-health sensor" dilutes archy's graph-shape focus). See [`TYPE_HINT_COVERAGE_EMPIRICS.md`](TYPE_HINT_COVERAGE_EMPIRICS.md). No 6th-axis candidate is currently active.
- The right home for call-graph data is **a refinement of the existing modularity axis** (call-weighted Newman Q) and **agent-navigation MCP tools** (already shipped). Neither requires a new axis.

The detailed argument follows.

## The question

The v0.16 introduction of `calls_per_edge` as a diagnostic, and the strong orthogonality numbers it produced on the 27-project benchmark, set up an implicit expectation that it would be promoted to a score axis "at a deliberate version boundary." After `complexity` shipped in v0.20, `calls_per_edge` is the natural next candidate. This document asks whether it should be.

There are actually two questions here:

1. **Sufficiency.** Are the 5 current axes catching the structural pathologies a Python codebase can fall into, or are there meaningful gaps?
2. **Specifically `calls_per_edge`.** Even if there are gaps, is `calls_per_edge` the right signal to fill any of them?

The answers below treat them separately.

## What each existing axis catches, and what it doesn't

| axis | catches | misses |
| --- | --- | --- |
| **modularity** | community structure of the import graph (Newman Q) | nothing about call intensity within communities |
| **acyclicity** | fraction of nodes inside cycles (Tarjan SCC + tangle ratio) | nothing about near-cycles or back-edges that carry heavy traffic |
| **depth** | longest DAG-condensation path | nothing about width or fan-out variance |
| **equality** | fan-out concentration (Gini of out-degree) | concentration of calls rather than edges (a god-module that everyone calls into 100 times each scores the same as one everyone calls into once) |
| **complexity** | per-function branch density (mean McCabe CC) | inter-function coupling intensity |

As a 5-axis system this covers:

- **macro topology** (modularity, acyclicity, depth, equality)
- **micro topology** (complexity)

What it does not directly measure:

- **Coupling strength.** An edge can be "module A's `__init__` imports a typed constant from module B" or "module A's hot path calls into module B 800 times." Both look like a single edge to the import-graph axes.
- **Interface stability** (Martin's `I`). Shipped as a diagnostic, opt-in via `sdp:` in `archy check`. Not in the score.
- **Type-hint coverage.** Not measured at all. The relevant AST node is touched by the CC walker; the data would be cheap to add.
- **Cognitive complexity** (Campbell / Sonar). Deferred per [`RESEARCH_METRICS.md` section 9](RESEARCH_METRICS.md).
- **Duplication / dead code.** Deferred per [`RESEARCH_METRICS.md` section 12](RESEARCH_METRICS.md) (the dead-code FP rate on real Python is catastrophic).

The gap is real but it is **coupling-strength and type-coverage shaped**, not call-density shaped specifically.

## The empirical case for `calls_per_edge`

From the 27-project bench ([`bench/results.md`](../bench/results.md), captured 2026-05-14):

**Orthogonality.** Max `|r| = 0.229` against modularity, acyclicity, depth, equality, propagation_cost. Below the OECD redundancy threshold (`|r| > 0.7`) by a wide margin. Substantially more orthogonal than any existing inter-axis pair (median `|r| ~ 0.45`).

**Distribution.** Heavy-tailed:

| band | range | examples |
| --- | --- | --- |
| top | `> 10` | numpy 52.68, pygments 17.90, mkdocs 11.97 |
| mid | `3-10` | mypy 9.60, sklearn 8.39, sqlalchemy 7.35, datasette 6.05, fastapi 5.33, click 4.39, httpx 4.31, requests 4.24, anyio 3.92, aiohttp 3.77, dagster 3.67, pydantic 3.63, botocore 3.45, ansible 3.19, django 3.11 |
| bottom | `< 3` | pytest 2.84, rich 2.75, archy 2.74, msgspec 2.67, boto3 2.49, flask 2.44, scrapy 2.15, starlette 1.93 |

This is the case [`RESEARCH_METRICS.md` section 16](RESEARCH_METRICS.md) makes. The orthogonality numbers are real. They are also **necessary but not sufficient** for adding the axis to the score.

## Why orthogonality is necessary but not sufficient

A random number generator would be orthogonal to the existing axes. So would "alphabetical position of the package name." So would "number of files starting with an underscore." Statistical independence is a hygiene check against double-counting; it is not evidence the signal measures architectural quality.

The composite-indicator literature (OECD Handbook, JRC reports) is explicit on this.[^oecd] A sub-indicator must satisfy **four** conditions to belong in a quality composite:

1. **Independence** (orthogonality). `calls_per_edge` passes.
2. **Directionality.** There must be a defensible answer to "is higher better, or worse?" that holds across the population.
3. **Actionability.** There must be a refactoring action a practitioner can take to improve the indicator that is independently considered good practice.
4. **Discriminant validity.** The indicator must distinguish projects that domain experts consider better-architected from those they consider worse-architected.

`calls_per_edge` fails conditions 2, 3, and 4.

### Condition 2: directionality is shape-driven, not quality-driven

The top of the distribution (numpy, pygments, mkdocs, mypy, scikit-learn) is dominated by scientific-Python and AST-heavy tools. The bottom (starlette, scrapy, flask, boto3, msgspec) is web frameworks, auto-generated SDKs, and compact serializers. Both top and bottom are full of widely-respected, well-architected codebases.

The signal is correlated with **codebase shape**, not **codebase health**:

- High `calls_per_edge` is caused by intra-module function dispatch (numpy's ndarray methods route through a handful of internal modules; mypy's analysis pipeline runs many narrow passes against the same nodes).
- Low `calls_per_edge` is caused by registry, plugin, and dynamic-dispatch patterns (starlette's middleware chain; scrapy's component registry; boto3's auto-generated SDK; msgspec's typed-dispatch shortcut).

Neither shape is intrinsically bad. [`RESEARCH_METRICS.md` section 16](RESEARCH_METRICS.md) itself observes this:

> "Scientific Python tops the distribution. The 'shape' of these codebases - small core, broad call surface against it - is exactly what the call signal captures and the import signal misses."
>
> "Plugin/registry shapes bottom the distribution. The import graph picks up the structural coupling; calls add little on top."

That section reads this as "the call signal is independent." It is. But independence with shape does not translate to a directional quality signal. Asking "is your `calls_per_edge` too high or too low?" depends on the answer to "what kind of codebase are you?" There is no defensible cross-population direction.

Compare `cc_mean`. The top of its distribution (msgspec 5.33, ansible 4.42, datasette 4.37) arguably could refactor toward less branching per function, even though those codebases work. The bottom (mkdocs 1.77, anyio 2.03, boto3 2.11) is unambiguously good: short functions, few branches. "Lower is better" holds across shapes.

### Condition 3: no canonical positive refactoring exists

If `calls_per_edge` is in the score, users can be asked "how do I improve it?" The candidate answers all fail:

- **Inline functions to avoid call sites.** Bad practice; loses abstraction, raises CC.
- **Spread call sites across more modules.** Lowers `calls_per_edge` per surviving edge but increases the number of edges, hurting modularity and equality.
- **Replace function calls with attribute access** (e.g., direct field manipulation). Bad practice; loses encapsulation.
- **Use dynamic dispatch / registries.** Lowers `calls_per_edge` but is a structural choice with its own trade-offs (loses static traceability, hurts agent-localization per LocAgent).

There is no entry in the canonical Python refactoring catalog (Fowler / Beck adapted to Python) that reads "reduce your calls per edge." The signal is not actionable.

Compare `cc_mean`. "Extract method," "replace conditional with polymorphism," "decompose function" are all standard refactorings that directly reduce CC and are independently considered good practice.

### Condition 4: discriminant validity is weak

If ten experienced Python architects ranked the 27 bench projects on architectural quality and we computed the Spearman correlation of that ranking with each archy axis, would `calls_per_edge` rank well or poorly?

The study has not been done; running it is in scope for future work but out of scope for this note. The structural argument is: the bench top and bottom both contain widely-respected codebases. The signal does not separate "good" from "bad" along a dimension experts agree on.

`cc_mean` would probably do better on this test (branchiness has a well-documented relationship with bug density in the McCabe and successor literature), though it would not score perfectly either.

## What the call-graph data **is** good for

The call graph is genuinely useful data. It just is not useful as a score axis.

1. **Weighted modularity.** Replace the unweighted Newman Q computation with one that weights edges by `call_count`. This refines the **existing** modularity axis rather than adding a new one. Module pairs that don't actually call each other contribute less to the community-structure signal. This is a candidate change to the modularity formula, not a new axis. Validation work: re-run the 27-project bench with call-weighted Q, check whether the orthogonality picture changes meaningfully, check whether the change shifts the project ordering in a way that tracks expert intuition.
2. **LocAgent-style agent navigation.** Already shipped: `archy_graph_focus`, `archy_graph_summary`, `archy_graph` expose call edges so an MCP client can navigate the graph by what-calls-what. This is the LocAgent-validated use case (ACL 2025) and the original motivation for adding call extraction.
3. **Targeted diagnostics.** `inputs.calls_per_edge` and `inputs.total_calls` are surfaced on `archy score`. A user inspecting their own project can read the number and form a judgment about whether their density is appropriate for their shape. **Diagnostic-as-context is the right home for shape-driven numbers.**

None of these require adding a score axis.

## Are the 5 axes sufficient?

Define **sufficient** as: catches the structural pathologies a Python codebase can fall into that are (a) measurable statically and (b) have a defensible "lower / higher = better" direction.

The 5 axes catch:

- decomposition (modularity)
- cycle pathology (acyclicity)
- chain pull-through (depth)
- god-module concentration (equality, via fan-out proxy)
- per-function branchiness (complexity)

The notable structural pathologies they do not catch:

| pathology | currently measured? | candidate axis |
| --- | --- | --- |
| **No type hints on public APIs** | not at all | type-hint coverage (Python-specific, clear direction, cheap to add - same AST walk as CC) |
| **Functions with deep nesting** | partially (McCabe counts branches but not nesting depth) | cognitive complexity (Campbell / Sonar 2017) - deferred for implementation cost |
| **Modules depending on less-stable modules** | partially (Martin's `I` as diagnostic, opt-in `sdp:` rule) | could be promoted; partial overlap with existing layer-rule machinery |
| **Functional duplication** | not at all | AST-shape hashing - deferred per [`RESEARCH_METRICS.md` section 12](RESEARCH_METRICS.md) |
| **Dead code** | not at all | static dead-code detection - deferred per [`RESEARCH_METRICS.md` section 12](RESEARCH_METRICS.md) (catastrophic FP rate on real Python) |

The strongest gap-filler candidate is **type-hint coverage**:

- Python-specific signal that nothing else captures.
- Clear direction: more public-API type coverage is unambiguously good per modern Python community consensus.
- Cheap to compute: the v0.17 CC walker already visits every `function_definition`; checking annotations adds roughly ten lines.
- Actionable: "add type hints to your public functions" is a standard, well-supported refactoring.
- Likely discriminant validity: heavily-typed projects (msgspec, pydantic, anyio) tend to rank high on community quality perceptions; un-typed legacy projects rank low.

This is the natural 6th axis if archy ever adds one, and the case for it is structurally stronger than the case for `calls_per_edge` on every dimension.

## Diminishing returns: the case for not adding more axes at all

Every additional axis pays a cost:

1. **Geomean mass-shift.** The 5th axis already shifted absolute scores. A 6th would shift them again. Each addition adds a "record a new baseline" event for every user.
2. **Signal dilution.** With 5 axes, the geomean is `x^0.2 * y^0.2 * ...`. A new axis at 1.0 contributes nothing; one at 0.5 takes a project from `0.6` to roughly `0.59`. The marginal explanatory power of axis 6 against axis 5 is necessarily lower (the most-orthogonal signal goes first by construction).
3. **Cognitive load on users.** Five axes is at the edge of what someone can hold in their head while reading an `archy score` breakdown. Six approaches a forced-reference situation.
4. **Test surface and doc surface.** Each axis multiplies test cases, documentation prose, and version-bump complexity. The cost was just paid for `complexity`; doing it again with weak justification is bad ROI.

The OECD Handbook explicitly warns against the temptation to keep adding indicators: adding indicators can degrade rather than improve a composite if the marginal indicator measures the same underlying construct as the existing ones, or if it lacks discriminant validity.[^oecd]

## Recommendations

1. **Do not promote `calls_per_edge` to a 6th axis.** It is orthogonal but shape-driven, not actionable, and lacks discriminant validity. Keep it as a diagnostic on `archy score`'s output where users can interpret it in context.

2. **Investigate call-weighted Newman Q** as a refinement of the existing modularity axis. This uses the call data without adding an axis. Validation work: re-run the 27-project bench with call-weighted Q and check whether the orthogonality picture and the project ordering both move in defensible directions.

3. ~~**If a 6th axis is ever desired, prioritize type-hint coverage** over call density.~~ Type-hint coverage was studied empirically in 2026-05 ([`TYPE_HINT_COVERAGE_EMPIRICS.md`](TYPE_HINT_COVERAGE_EMPIRICS.md)) and ruled out in any form (axis or diagnostic): independence and discriminant validity fail the OECD axis-promotion check, and the value-prop argument rules out even a diagnostic surface (mypy / pyright own the typing niche; archy's distinct value is graph-shape). No 6th-axis candidate is currently active.

4. **Update [`docs/ROADMAP.md`](ROADMAP.md) and [`docs/RESEARCH_METRICS.md`](RESEARCH_METRICS.md)** to reflect these conclusions. (This PR makes those updates.) Currently both list "promote call-density to a score axis" as a candidate; the candidacy is downgraded with the reasoning above, and "type-hint coverage as a candidate 6th axis" is promoted.

## Open questions (future work)

- **The 10-expert ranking study.** Would substantially raise the rigor of the axis-promotion process for any future candidate. Out of scope for this review.
- **Call-weighted Newman Q empirics.** Concrete: does it shift the modularity ranking on the 27-project bench, and in which direction for which projects? In scope for a follow-up PR.
- **Type-hint coverage empirics.** Distribution across the bench, correlation with the existing axes, candidate normalization shapes. In scope for a follow-up PR before any axis promotion is attempted.
- **Equality axis redesign.** [`SCORING.md`](SCORING.md) flags `gini(out_degree)` as a proxy for the long-term target `gini(per_function_cc)`. A redesign using CC-Gini for equality could absorb part of what `calls_per_edge` was thought to add (call concentration). Worth analyzing in concert with any future call-data work.

[^oecd]: OECD / JRC, *Handbook on Constructing Composite Indicators: Methodology and User Guide* (2008). The four-condition framing for sub-indicator inclusion appears across sections 2-6; the warning about adding indicators that lack discriminant validity is section 3.5.
