# Learnings

Running notes from building archy. Updated as we go.

## v0.0.1 - import graph

### Tree-sitter Python API (post-0.23)

The modern API is genuinely cleaner than the 0.21 era.

```python
import tree_sitter_python as tsp
from tree_sitter import Language, Parser, Query, QueryCursor

PY = Language(tsp.language())
parser = Parser(PY)
tree = parser.parse(source_bytes)

query = Query(PY, "(import_statement) @i")
cursor = QueryCursor(query)
captures = cursor.captures(tree.root_node)  # dict[name, list[Node]]
```

Three things that tripped me up:

1. **Field-typed captures were noisier than capturing whole statements and walking fields.** I started by capturing `(import_statement name: (dotted_name) @abs_module)` plus several variants for aliased/from/relative imports. The grouping back into "one statement" got ambiguous fast - there was no way to tell which `@abs_module` belonged to which statement when one statement imported multiple names. Switching to "capture the whole `import_statement` / `import_from_statement` node, then use `node.children_by_field_name('name')` and `node.child_by_field_name('module_name')`" was simpler and survives every form.

2. **`from X import a, b` is genuinely ambiguous statically.** `a` could be a name in `X`'s namespace or a submodule. Without semantic analysis (which would mean executing `X/__init__.py`), you have to guess. The pragmatic rule: when `X` is internal, prefer submodule edges where they exist; otherwise attribute the edge to `X` itself. Sentrux's plugin config doesn't actually disambiguate this either - it just records `module_name` and lets the resolver figure it out.

3. **Error recovery is the headline feature.** Tree-sitter produces a partial tree on syntax errors with `ERROR` and `MISSING` nodes. The query still runs; clean imports still come through. Surfaced via `tree.root_node.has_error` and propagated as `ParseResult.has_errors` and the graph's `parse_errors` list. This was the whole reason to pick tree-sitter over `ast` and it works exactly as advertised.

### Package discovery: `src/` layout matters

A directory is a "package root" when it contains `__init__.py` AND its parent does not. That single rule covers both flat layouts and the `src/<pkg>/__init__.py` convention sentrux's Python plugin documents - `src` itself isn't a package, so `src/myapp` becomes the root and module qualnames look right (`myapp.core`, not `src.myapp.core`).

### Relative imports: dot count semantics

`from . import x` means "stay in current package." `from .. import x` means "go up one." So the walk-up count is `leading_dots - 1`. Off-by-one footgun; got it wrong on the first pass and the test for relative imports caught it.

### What "external" means in this graph

We collapse external imports to their top-level package: `import requests.adapters` becomes an edge to `requests`. This matches sentrux's behavior and keeps the external surface area tractable. Trade-off: we lose granularity inside third-party packages, but we don't care about their internal structure for our metrics.

### Sentrux's quality-signal design

Their `docs/quality-signal-design.md` is excellent. The five root-cause metrics aren't arbitrary - they're presented as the five independent structural properties of a directed graph (modularity, acyclicity, depth, equality, redundancy). The argument for **geometric mean** as the aggregator is the strongest part: it's the unique aggregation function satisfying Pareto optimality + symmetry + independence, which means single-axis cosmetic optimization is bounded by the score's other axes. (Empirical caveat refined by `docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md` in 2026-05: on the 28-project benchmark, 2 of 10 axis pairs sit at moderate Pearson correlation, both involving `depth`. All ten pairs remain below the OECD `|r| > 0.7` redundancy threshold. The previously stronger framing - "the only way to game the score is to actually improve every dimension" - overstates what the data supports: under every aggregator tested, `depth` correlates only weakly with `overall` (`|r| ≤ 0.187`), which limits *cross-project* depth-side gaming. That low correlation reflects low cross-project variance in depth scores, not low leverage: the 2026-06 adversarial review measured depth as the second most locally influential axis (`d(overall)/d(axis)` = 0.225 at the corpus baseline; see [`SCORING.md`](SCORING.md) and [#176]), so a weak depth score does pull `overall` down sharply. All five axes carry non-trivial leverage and the geometric mean's non-compensatory property bites on each.)

We will likely not match their full metric set in v1 - redundancy in particular requires AST-level dead-code and duplicate-function detection that's a lot more work than the import graph. Modularity (Newman's Q), acyclicity (Tarjan SCC count), and depth (longest-path DAG) are all derivable from the graph we already build. Those three plus a fan-out concentration metric (Gini of out-degrees, since we don't yet have per-function CC) get us most of the way without leaving graph-theory land.

### Performance note

The real-world test was the governingdocs backend: 665 internal modules, 356 internal edges, runs in well under a second cold. Tree-sitter parses are fast; the bottleneck is filesystem traversal. No optimization needed yet.

## v0.2.0 - score: comparison with sentrux

archy's score follows sentrux's [`quality-signal-design.md`](https://github.com/sentrux/sentrux/blob/main/docs/quality-signal-design.md) closely - same model, same aggregation, four of the five sub-metrics implemented identically. Sentrux is pure Rust; archy is Python on top of `networkx`. Sub-metric formulas match where they should:

| Sub-metric | sentrux | archy v0.2.0 | Notes |
|---|---|---|---|
| Modularity | Newman's Q over greedy partition; `(Q + 0.5) / 1.5` mapped onto `[0, 1]` | identical (clamped to `[0, 1]` after the linear map) | We adopted sentrux's normalization explicitly so cross-tool numbers stay comparable. |
| Acyclicity | `1 / (1 + cycle_count)` from Tarjan SCC of size > 1 | **diverged in v0.7.x**: `1 - tangle_ratio` where `tangle_ratio = nodes_in_cycles / total_nodes`. Same SCC source via `archy.cycles.find_cycles`. | The new form follows Structure101's "Tangle" metric and reads as fraction-of-codebase-in-cycles rather than a count. See `docs/SCORING.md` §Acyclicity and `docs/research/RESEARCH_METRICS.md` §6. Cross-tool numbers no longer line up here. |
| Depth | `1 / (1 + max_depth / 8)` over longest path | identical, computed on `nx.condensation(graph)` so cycles collapse to single nodes first | Sentrux uses iterative DFS from entry points; networkx's `dag_longest_path_length` is equivalent for a DAG. |
| Equality | `1 - Gini(out-degree)` with `G = Σ (2i - n - 1) x_i / (n * Σ x_i)` | identical | Both projects report `1 - Gini` so a higher number is better. |
| Redundancy | `1 - (dead + duplicate) / total_functions` | **not implemented** | FUTURE.md keeps it deferred: dynamic dispatch, decorators, and `if __name__ == "__main__":` gates make purely-static dead-code detection too noisy. |
| Aggregation | geometric mean of 5 | geometric mean of 4 | Same rationale (Pareto + symmetry + independence). |
| Display scale | integer 0-10000 | float `[0, 1]` to three decimals | Cosmetic. |

What we get from being faithful: a v0.2.0 archy score on a given codebase is directly comparable to whatever sentrux would produce on its four-metric subset. What we lose: redundancy. When this was written archy had four axes to sentrux's five, and aggregation was expected to "widen back to five" if `archy redundancy` shipped. v0.20 instead took the fifth slot with `complexity` (the score is now `(mod * acy * dep * eq * cpx) ** 0.2`), so a future `archy redundancy` would be a sixth axis rather than restoring parity with sentrux's four-metric subset.

The deeper agreement is methodological - sentrux's argument for geometric mean (the only aggregator that's Pareto-optimal, symmetric, and independent) is the load-bearing claim. That's why score gaming works only by improving every axis. (Refined in 2026-05 by `docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md`: the 28-project empirics found that `depth` correlates only weakly with `overall` under every tested aggregator (`|r| ≤ 0.187`), which limits cross-project depth-side gaming. That reflects low cross-project variance in depth scores, not low leverage: the 2026-06 adversarial review measured depth as the second most locally influential axis ([#176]), so the non-compensatory property bites on all five axes.)

## v0.3.0 - history persistence: comparison with sentrux

archy and sentrux solve overlapping but different problems with persistence:

| | sentrux | archy v0.3.0 |
|---|---|---|
| File | `.sentrux/baseline.json` | `.archy/history.jsonl` |
| Format | Single pretty-JSON record | JSONL, one row per recorded run |
| Retention | One point only (overwritten on each `gate --save`) | All recorded runs |
| Verbs | `gate --save` writes; `gate` (no flag) compares current vs saved | `score --record` appends; `trend` reads back; `score --strict` compares current vs last recorded row |
| Scope | Within-session regression gating for AI agent loops | Long-term drift visualization + per-commit regression gating |

Sentrux is optimized for the cybernetic feedback loop its README pitches: agent saves a baseline, makes changes, runs `gate`, sees pass/fail, self-corrects. A single rolling file is sufficient and JSONL would be wasteful.

archy keeps both capabilities. JSONL is a strict superset - we get long-term history *and* per-commit regression gating from the same file. `archy score --strict` reads the last row and compares against it (the same logic as sentrux's `gate`); `archy trend` reads the full history (which sentrux can't, because it overwrites). The default tolerance (0.02) matches sentrux's threshold so cross-tool intuition transfers.

The trade-off: a JSONL history grows unboundedly. For most projects (one record per commit, ~250 bytes/row) that's a few hundred KB per year of churn. We considered rotating but defaulted to letting it accumulate; users who want bounded history can post-process with `tail -n 1000 history.jsonl > history.jsonl` or similar.

## v0.3.x follow-ups - chain follower + self-loop reporting

Two small refinements to features shipped earlier.

**Multi-hop re-export chains.** v0.2 only resolved one hop: if `pkg/__init__.py` did `from .sub import Foo` and `pkg.sub/__init__.py` did `from .impl import Foo`, consumers of `pkg.Foo` landed at `pkg.sub` rather than the `pkg.sub.impl` source. The fix is a fixed-point pass over the re-export map after the per-package build:

```
for (pkg, name) -> target in maps:
    visited = {pkg}
    while target has a deeper re-export for `name` and target not in visited:
        visited.add(target); target = deeper_target
    # capped at max_depth=8 to short-circuit pathological loops
```

The visited-set + max-depth guard handle the malicious "evil twin" case where two `__init__.py` files re-export each other under the same name, which would otherwise loop forever. `max_depth=8` is enough for real codebases (FastAPI's deepest re-export chain we have seen is 3) and short enough that cycle-induced misbehaviour is bounded.

**Self-loop reporting.** `find_cycles` previously required `min_size >= 2`, which meant a module that imports itself - rare but possible, particularly through `__init__.py` re-exports - was silently dropped. The new semantics: self-loops are *always* reported regardless of `min_size`; the gate only suppresses incidental DAG-singleton SCCs (which never represent a cycle anyway). This was a behaviour-change for `find_cycles(g, min_size=1)` - it no longer reports isolated nodes - but that mode was uninteresting in practice (it was effectively "list all SCCs," not "list cycles"), so the change makes the function's name match what it does.

## v0.4.0 - MCP server: comparison with sentrux

archy and sentrux both ship an MCP server so AI agents can call the analyzer directly. The surfaces overlap but trade off scope vs. statefulness.

| | sentrux | archy |
|---|---|---|
| Tools | 9 (`scan`, `health`, `session_start`, `session_end`, `rescan`, `check_rules`, `evolution`, `dsm`, `test_gaps`) | 19 (`archy_score`, `archy_cycles`, `archy_check`, `archy_contracts`, `archy_trend`, `archy_impact`, `archy_affected`, `archy_snapshot`, `archy_diff`, `archy_record_baseline`, `archy_graph_focus`, `archy_graph_summary`, `archy_graph`, `archy_high_risk_modules`, `archy_hotspots`, `archy_what_to_refactor_next`, `archy_dsm`, `archy_status`, `archy_simulate`) |
| Session model | Stateful: `session_start` saves an in-process baseline; `session_end` compares against it. | File-based: `archy_snapshot` writes `.archy/baseline.json`, `archy_diff` compares current state against it. The long-lived score history (`.archy/history.jsonl`) is separate and feeds `archy_trend` / `archy_score --strict`. |
| Data behind tools | Snapshot-and-diff against an in-memory baseline. | The CLI's existing JSON shapes - same data agents would read by piping CLI output. |
| Dependencies | Pure Rust, no Python runtime. | Python `mcp` SDK over stdio. |
| Distribution | Single binary. | `uv run archy mcp` (or `pipx run archy mcp` once on PyPI). |

Why the different surface: archy's CLI primitives are already orthogonal (`graph` is the building block; `cycles`, `check`, `score`, `trend`, `impact`, `snapshot`, `diff` are projections). Wrapping each as an MCP tool gives the agent the same composable primitives. `archy_snapshot` + `archy_diff` provide the file-based equivalent of sentrux's stateful session pair, and `archy_impact` adds a forward-looking blast-radius query with no sentrux equivalent. The remaining sentrux tools (`evolution`, `dsm`, `test_gaps`, `rescan`) are either FUTURE.md items (`evolution` ≈ trend deltas, `dsm` ≈ a richer graph projection) or out-of-scope (`test_gaps` requires a coverage source archy doesn't ingest). The 19-tool count includes four graph-navigation tools (`archy_graph_focus`, `archy_graph_summary`, `archy_graph`, `archy_high_risk_modules`) added in v0.10-v0.14 to address the Navigation Paradox finding (large context windows do not eliminate the need for structural navigation), plus `archy_hotspots` (v0.19) for the churn-aware refactor-priority view, `archy_affected` (v0.25, Phase 1 of [`SPEC_INDEX_AND_INSTALL.md`](SPEC_INDEX_AND_INSTALL.md)) for CI-shaped impact lookup, `archy_status` (v0.27, Phase 2) reporting the persistent index's freshness, `archy_simulate` (v0.29) for counterfactual pre-edit consequence checking, and `archy_what_to_refactor_next` (v0.30) fusing the hotspots and edit-risk lenses into one ranked refactor-priority list; these have no sentrux equivalent.

Why stateless: the `.archy/history.jsonl` file is already the source of truth for trend and gating, so making the MCP surface stateless avoids two ways to compare scores. An agent that wants the sentrux session feel calls `archy_record_baseline(path)` at session start and `archy_score(path, strict=True)` at session end - same pattern, different storage shape.

## v0.13.x and beyond - external empirical validation

When archy's agent-feedback-loop framing was first written, it was a positioning bet on a thesis: AI coding agents need a structural-graph feedback loop, not just larger context windows. As of mid-2026 a small but converging external literature directly supports that bet. Three pieces matter most for archy's design rationale:

- **The Navigation Paradox** (Feb 2026, [arxiv:2602.20048](https://arxiv.org/html/2602.20048v1)) builds an MCP-based graph navigation tool (CodeCompass) shaped almost identically to archy's `archy_graph_*` family and finds that larger context windows do not eliminate the need for structural navigation: the failure mode shifts from retrieval capacity to *navigational salience*. This is direct external validation of archy's MCP graph-tool surface as a category.
- **LocAgent** (ACL 2025, [aclanthology:2025.acl-long.426](https://aclanthology.org/2025.acl-long.426/)) ablates four edge types in a code knowledge graph and finds invoke (call) edges contribute the most to LLM-agent code-localization accuracy, more than import edges. This sharpens archy's call-graph roadmap item from "second edge type, nice to have" to "the missing edge type with the strongest measured contribution in an agent context."
- **Coding-agent failure-mode literature** (Columbia DAPLab, Anthropic, Stack Overflow synthesis, 2026) names the recurring patterns - scope drift, context exhaustion, cross-file reasoning failure, deprecated-pattern propagation - that an architectural feedback loop is supposed to catch. Mapping these to archy's surface yields a concrete priority ordering: cycle/violation gating (shipping), propagation-cost-weighted blast radius (roadmap), per-module risk composite (roadmap).

Detailed citations, a failure-mode-to-archy-capability mapping, and the implied roadmap priority order live in [`RESEARCH_METRICS.md §14c`](research/RESEARCH_METRICS.md). The roadmap entries themselves are in [`FUTURE.md`](FUTURE.md).

## v0.16.0 - call edges as a second edge type (diagnostic)

LocAgent (ACL 2025) ablated four edge types in a heterogeneous code graph and found invoke edges contributed more to LLM-agent code-localization accuracy than imports - the empirical justification for archy adding a second edge type. v0.16.0 ships call edges as a *diagnostic*, mirroring the [`MacCormack v0.13.3 propagation-cost rollout`](research/RESEARCH_METRICS.md): ship the signal, validate orthogonality on the 27-project benchmark, promote to a score axis at a deliberate version boundary once the design choice (weighted-Newman-Q vs a new sixth axis, the fifth slot having gone to `complexity` in v0.20) is settled.

Three things that shaped the implementation:

1. **Static call resolution in Python is mostly alias-table lookup, not call-graph construction.** The tree-sitter `(call) @call` query gives the callee expression; the leftmost identifier of that expression is what you look up. The per-file alias table built from import statements (`from X import Y as Z` binds `Z` → resolved-target-of-`X.Y`; `import X.Y as Z` binds `Z` → `X.Y`; `import X.Y` binds only `X` per Python's actual import semantics) covers the common cases. Going further - chasing assignments, tracking class attribute resolution, pyan-style heuristics - adds false-positive rate faster than coverage. Matches LocAgent's static-extraction approach.
2. **The depth-differential edges are the interesting ones.** `import pkg; pkg.sub.foo()` adds an import edge to `pkg` (top-level package binding only) and a *call* edge to `pkg.sub` when the submodule is internal. Those call-only edges are the empirical evidence that calls carry signal imports miss - the FUTURE.md framing "two modules can be independent by imports but tightly coupled by calls" holds because attribute access through a single import binding produces statically resolvable call edges deeper than the import target.
3. **Orthogonality was the load-bearing question.** Pre-bench, the worry was that call counts would correlate ≈1.0 with edge counts and thus with modularity / equality. The 27-project bench showed `|r| ≤ 0.229` against every existing axis plus propagation cost - far below the OECD redundancy threshold and the most orthogonal new signal archy has added since v0.2.0. The skew toward extreme values (numpy 52.68 calls/edge, starlette 1.93) does most of the orthogonality lift: scientific-Python and plugin-registry shapes carry very different call densities at similar import-graph shapes, and that's exactly what a new signal should be doing.

The score number didn't change in v0.16 (call diagnostics aren't folded in), but absolute edge counts moved on some projects (e.g., numpy 1192 → 1342, +13%) because call-only edges to deeper submodules are now created. That flows into modularity / equality at the second decimal place. Pre-existing trend histories remain comparable within the existing 0.02 tolerance, but the cleanest practice is `archy score --record` once on each project after the upgrade.

## v0.17.0 - per-function cyclomatic complexity (diagnostic)

McCabe CC ships as a diagnostic in v0.17. The walker in `src/archy/complexity.py` counts branch nodes (if/elif/for/while/except/case/conditional_expression/boolean_operator/comprehension-clauses) over the same tree-sitter parse the import-graph build already uses, so the AST cost is amortized. Per-function rows roll up to per-module aggregates (`function_count`, `cc_sum`, `cc_max`, `cc_mean`) on internal graph nodes and to project-wide aggregates on `archy score`'s `inputs`. Same diagnostic-first precedent as v0.16 calls and v0.13.3 propagation cost: no score-axis change in this release.

Three things shaped the implementation:

1. **The tree-sitter walk is cheap, but only if it shares the parse.** First cut had `complexity.compute_function_complexity(source)` parsing each file independently. Refactor: expose `walk_functions(root_node, source)` and call it from `parser.parse_source` after the import / call extractions, so one `Parser.parse()` per file feeds all three walks. The integration test in `tests/test_graph.py` covers the wiring; the unit tests in `tests/test_complexity.py` still call the bytes-in convenience entry point.
2. **Nested defs and class bodies need separate counters.** A naive descendant walk inflates outer-function CC with branches inside `def inner(): ...` or class-scope `if`. Skip both during the branch-counting BFS (each inner def gets its own FunctionComplexity row), and recurse into class bodies for method discovery only - branches at class top-level (`if SETTING: x = 1` patterns at module / class scope) belong to no function. The test `test_class_body_top_level_branches_do_not_count_for_any_function` pins this.
3. **The empirical headline is orthogonality, not absolute numbers.** On the 27-project bench, `cc_mean` lands at max `|r| = 0.197` against the four score axes plus propagation cost plus calls-per-edge - more orthogonal than v0.16's call density (max 0.229) and substantially more so than any existing axis pair (median `|r| ~ 0.45`). That promotion landed in v0.20.0 as a 5th score axis (`complexity = 1 - clamp((cc_mean - 1) / 5, 0, 1)`, widened to `/8` in v0.23.0 after the original calibration zeroed the geomean on real-world repos with `cc_mean in [6, 9)`), additive rather than a replacement. The Gini-of-CC redesign of the equality axis stays on the roadmap; promoting cc_mean as a new axis was the smaller blast radius and the design left room for the equality redesign to land later without undoing this work. Detailed empirics in [`RESEARCH_METRICS.md` §17](research/RESEARCH_METRICS.md).

Cognitive complexity (Sonar / Campbell 2017) didn't ride along despite the original "free with CC" framing - the Sonar definition needs nesting-depth bookkeeping that doesn't fit the single-pass BFS. Type-hint coverage same status. Both are open follow-ups using the same `function_definition` AST surface; the call-graph PR established the precedent that diagnostic-first means one signal at a time so the bench can isolate its contribution.

## v0.18.0 - hotspots = CC x per-file churn (diagnostic)

`archy hotspots` ships in v0.18 as a per-file refactor-priority ranking: `cc_sum * git-commit-count` per internal module, with zero-CC and zero-churn rows filtered out so the list flags only files that score on both axes. Tornhill's "Code Red" formulation in `docs/research/RESEARCH_METRICS.md` §8, made cheap by v0.17's CC pass (the `cc_sum` aggregate is already attached to every internal graph node) and a single `git log --name-status -M --format=` invocation (rename-aware: `R`-status lines fold pre-rename churn onto the current path). Diagnostic only - not folded into `archy score`; the metric is a per-file ranking, not a project-level signal, so it's never going to be a score axis.

Three things shaped the implementation:

1. **`cc_sum` over `cc_max` for the CC half.** Radon's classic hotspots formulation uses per-file CC totals, not the worst single function. A file with twenty CC-7 functions is a bigger refactoring target than one with a single CC-15 function, even though `cc_max` would rank the second one higher. The `setuptools` extreme in `RESEARCH_METRICS.md` §17 (one function at CC=340, mean of 2.91) is the cautionary case: ranking on `cc_max` would surface that single dispatcher every time and bury the actual breadth-of-complexity signal.
2. **Zero-component filtering, not just sort-then-truncate.** Bare `__init__.py` files have `cc_sum=0` and would silently land at the bottom of the ranking with score=0. Stable files with `churn=0` (since the chosen `--since` window) would too. Both are noise rather than signal, and dropping them at the source makes the top-K interpretable: every row scored on both axes.
3. **One `git log` pass, no per-file commands.** The naive shape (`git log -- <file>` per file) is N+1 and slow on big repos. `git log --name-status -M --format=` streams one process and one parse for every `.py` file at once, with rename detection (`R`-status lines) folding a moved file's pre-rename commits onto its current path. Implementation note that mattered: paths must be `Path(...).resolve()`d on both sides to match the graph node's `path` attribute, which is the absolute filesystem path produced by `parse_file`.

The `--since` window default was settled empirically against the 27-project bench in `bench/hotspots_results.md`. Headline: median Jaccard(full, 12mo) = 0.60 (the window genuinely matters), median Jaccard(12mo, 6mo) = 0.74 (the meaningful boundary is "full vs ~12 months", not "12 vs 6"), median `stale_full_frac` = 0.25 (full history carries about 25% recency contamination on the median project). The kicker: low-activity codebases (`mkdocs` collapses to 1 hotspot at 12 months, `httpx` to 10) make a 12-month default unworkable - the metric would vanish on stable codebases. Default stays full history; `--since` is documented as the "what should I refactor right now" lens, and the docstring carries the recency-contamination caveat so readers understand what they're reading.

The open follow-up - an MCP-tool surface so an agent can read the ranking without spawning a CLI - landed in v0.19.0 (`archy_hotspots`).

## v0.19.0 - archy_hotspots MCP tool

`archy_hotspots` ships as the MCP-side surface for the v0.18 ranking. Signature mirrors the CLI: `archy_hotspots(path, top=20, since=None)` returns `{since, total, shown, hotspots: [{module, path, cc_sum, churn, score}], note}`. The agent loop now has two coordinated answers to the "should I be careful here?" family of questions: `archy_high_risk_modules` for the structural / git-free view ("is this edit dangerous?" via propagation cost x fan-in x instability), and `archy_hotspots` for the churn-aware view ("where is the refactoring leverage?" via cc_sum x commit count). Both surface top-N rankings with each row broken into its components so the agent can see why a module ranks high.

One design decision was load-bearing here, and it took a pushback to get right:

**Graceful diagnostic on non-git projects, not a hard failure.** First instinct was to mirror the CLI's `ClickException` ("path is not inside a git repository"). The pushback: there is a wider audience than that error message implies. The MCP tool runs in an agent loop, and an agent that hits a hard error on a perfectly valid Python project (vendored snapshot, freshly-unpacked tarball, sparse checkout, anything that hasn't been `git init`'d locally yet) has no good way to recover except to crash and report. Returning `{hotspots: [], note: "..."}` with a pointer at `archy_high_risk_modules` as the git-free alternative lets the agent pivot in a single tool call. The bar to enable hotspots is much lower than it sounds - a local `git init` is sufficient, no remote required - but the tool doesn't need to know that; it just needs to say "this metric needs git history; if you don't have it, here's the structural cousin." Same pattern as `archy_contracts` returning a `not_available` payload when `archy[contracts]` isn't installed.

The synthesize-`churn=1`-for-everything alternative was rejected: it makes the metric degrade to "rank by cc_sum", which is already trivially available via `cc_sum` on every internal node of `archy_graph_summary`. Adding a second pathway to the same information would just create confusion about which tool to call.

## Competitive landscape (May 2026 survey)

Sentrux was the inspiration and is treated as a peer throughout this document, but it is not the only adjacent tool. A May 2026 web survey grouped the field into seven buckets. Recording it here so the design rationale doesn't drift toward "us vs. sentrux" when the actual landscape is wider.

**1. Real-time architectural sensors for AI agents.** Closest in *positioning*.
- [sentrux](https://github.com/sentrux/sentrux) - 52 languages, Rust + tree-sitter, single `quality_signal` (0-10000), live treemap. Breadth-first by design. Archy's depth-first counterpart on the Python side.
- [`camilooscargbaptista/architect`](https://github.com/camilooscargbaptista/architect) - 2026 Claude Code plugin, language-generic, 0-100 score, 9 MCP tools, anti-pattern detection. Closest in *shape* (AI-agent-facing, MCP, single score) but generic across languages and less rigorous than archy or sentrux.

**2. Python architectural-contract tools.** The enforcement slice archy already integrates with.
- [import-linter](https://github.com/seddonym/import-linter) - de-facto contract enforcer, five contract types (Layers, Forbidden, Independence, Protected, AcyclicSiblings). Archy wraps it via `archy contracts` (v0.8.x). `.importlinter` is the canonical contracts config; archy.yaml's `forbid:` fallback is a zero-config onramp only (cannot express `ignore_imports`, so any project that needs to whitelist a legitimate transitive edge must use `.importlinter`).
- [grimp](https://github.com/python-grimp/grimp) - the import-graph library powering import-linter.
- [tach](https://github.com/tach-org/tach) (gauge.sh) - Rust-implemented Python-target enforcer; `tach sync` / `tach check`, layered architecture, VS Code + pre-commit. The most active mainstream competitor for *enforcement*. No score, no trend, no MCP.
- [layer-linter](https://pypi.org/project/layer-linter/) - deprecated, folded into import-linter.

**3. Architecture-as-tests (pytest-style).** Different DX, same target.
- [PyTestArch](https://pypi.org/project/PyTestArch/), [pytest-archon](https://github.com/jwbargsten/pytest-archon), [ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython), [pytest-imports](https://github.com/nwilbert/pytest-imports) - all ArchUnit-inspired; rules expressed as unit tests. No trended score, no MCP.

**4. Visualization / cycle detection (older generation).**
- [pydeps](https://github.com/thebjorn/pydeps) - Graphviz visualization, cycle highlighting. No enforcement.
- [pycycle](https://github.com/bndr/pycycle) - cycles only.
- snakefood - Python-2 era, unmaintained.
- [modulegraph](https://pypi.org/project/modulegraph/), [importlab](https://github.com/google/importlab) - bytecode/AST graph libraries.

**5. Complexity / health trends (adjacent, not import-graph).**
- [wily](https://pypi.org/project/wily/) - trends *complexity* across git history; closest to archy's "trended score" idea but at the function/cyclomatic level, not architectural.
- radon, lizard, [cohesion](https://github.com/mschwager/cohesion) - point-in-time complexity / LCOM metrics.
- [deptry](https://github.com/fpgmaas/deptry) - package-level unused/missing deps; orthogonal.

**6. Semantic code lookup for AI agents (the *librarian* role).** Adjacent, not competing.
- [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph) - tree-sitter symbol graph (19+ languages) into SQLite + FTS5, MCP tools for `search` / `callers` / `callees` / `context` / `impact`, native FS-event watcher, framework-route detection across 13 web frameworks, one-shot `npx` installer with auto-detection across Claude Code / Cursor / Codex / opencode. Benchmark headline: 92% fewer tool calls / 71% faster on six real codebases vs raw grep/Read exploration. Plays a *different* role from archy: it answers "where is X and what calls it?" (librarian) where archy answers "is this getting worse?" (judge). Surveyed May 2026 while scoping v0.25's install + affected + cache work. We ported three things explicitly: (1) the *persistent SQLite index + debounced FS watcher* pattern (Phase 2 of [`SPEC_INDEX_AND_INSTALL.md`](SPEC_INDEX_AND_INSTALL.md)), (2) the `affected` CLI shape for mapping `git diff` to impacted tests, (3) the installer registry that auto-detects agents and writes MCP config + rules-file + permission allowlist (plus a Claude Code plugin manifest as the lowest-friction surface for Claude users). We *did not* port tree-sitter symbol indexing, FTS5 symbol search, framework-route detection, or any `context`-style tool that returns code; those belong to the librarian role and would dilute archy's judge positioning. Distribution mirror: codegraph ships via `npx`, archy ships via `uvx` (Python-native equivalent), no Node toolchain pulled into the install path.
- [tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph) (CRG) - the largest tool in this bucket by traction (~18k GitHub stars by mid-2026 vs archy's handful; v2.3.5, Python package, MIT). Same plumbing as archy at the surface (tree-sitter -> SQLite graph, incremental re-parse, `watchdog` watcher + multi-repo daemon, MCP + CLI, agent-install auto-detection, blast-radius), but a *librarian*, not a judge: its job is to return the minimal token-efficient context an agent needs to read. 30 MCP tools, 5 workflow prompts, 30+ languages, optional vector embeddings + FTS5 hybrid search, D3 visualization, GraphML/Neo4j/Obsidian/SVG export, hub/bridge/surprise/knowledge-gap analysis. Published reproducible benchmarks are all *retrieval* metrics: 38x-528x token reduction, 100% impact recall, 0.71 avg F1 across 6 repos with pinned SHAs (`eval/scorer.py` computes token-efficiency / MRR / precision-recall-F1 - it scores CRG's own retrieval, never the codebase). Code-verified June 2026 (cloned v2.3.5, read source) for what it does *not* have, which is precisely archy's wedge: **no composite architectural-health score, no trended history, no regression gate** (CLI subcommands are `install/init/build/update/watch/status/visualize/wiki/repos/eval/mcp` - no `score`/`check`/`contracts`/`trend`/`--strict`); **no layer-rule or import-linter contract enforcement** (the `contract`/`layer` source hits are Solidity parsing and CDK class suffixes); **no counterfactual `simulate`**; "modularity" appears only as the Leiden community-detection objective, not a scored axis. Two capabilities sit on CRG's side of the line that archy deliberately lacks: (1) a per-change *review-risk* score (`changes.py::compute_risk_score`, 0-1 from flow criticality + test-coverage gap + cross-community coupling + caller count, surfaced via `detect_changes_tool`) - the nearest cousin to archy's `edit_risk`/`high_risk_modules`, but ephemeral per-call and aimed at "what should a reviewer read now," never recorded or gated; (2) **source-mutating refactor** (`refactor_tool`/`apply_refactor_tool`: rename previews, dead-code detection, preview-then-apply edits to disk) - archy is strictly read-only/advisory and writes no source. The takeaway is the same as codegraph but with a starker traction gap: CRG owns the librarian/review-assistant role at scale; archy's defensible answer is *not* to chase its breadth (languages, retrieval, visualization, refactor-apply - all off-mission per Non-goals) but to be the governance/verification layer CRG verifiably is not (score + gate + contracts + simulate). It is a clean compose target for the judge-as-reranker seam below: CRG retrieves and even edits, archy says whether the structure regressed.

**7. Commercial / enterprise.** Different buyer, similar idea.
- [CodeScene](https://codescene.com/product/code-health) - `CodeHealth` score, behavior-aware (combines git history with code). Strongest commercial story for "single trended health metric."
- [Structure101](https://structure101.com/) (acquired by Sonar) - tangles + excessive complexity as TD measures.
- Sonargraph, NDepend (.NET), Lattix - DSM-based architectural analysis, enterprise pricing.

### What this implies for archy's design

The only tools sharing **all** of {Python-native, import-graph, layer rules, trended score, MCP server} are archy and the generic Claude Code `architect` plugin. Cross-referenced against the most-cited competitors:

| Capability | archy | sentrux | tach | import-linter | pytest-archon | CodeScene |
|---|---|---|---|---|---|---|
| Python-deep (transitive contracts) | ✅ | ❌ | partial | ✅ | partial | ❌ |
| Multi-language | ❌ | ✅ (52) | ❌ | ❌ | ❌ | ✅ |
| Single trended score | ✅ | ✅ | ❌ | ❌ | ❌ | ✅ |
| MCP server for agents | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Tree-sitter (in-flight edits) | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| YAML rules (no code) | ✅ | partial | ✅ | ✅ (INI/TOML) | ❌ | n/a |
| OSS | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |

Archy's defensible positioning is **Python depth**: transitive contracts via import-linter, SDP-violation rule, NCCD/propagation cost, `if TYPE_CHECKING:` awareness, type-hint coverage, decorator/descriptor-aware call resolution - the things a 52-language tool structurally cannot fold in without privileging Python. Sentrux's defensible positioning is **language breadth**. That division is settled; the two tools are not on a collision course and the roadmap does not need to defend against multi-language pressure.

The realistic threats on archy's wedge come from inside its own bucket:
- **tach** adding a trended score + MCP server (technically small additions on its existing Rust foundation).
- **sentrux** adding deeper Python semantics, which would erode the depth wedge for users with Python-only repos.

The defense in both cases is to keep moving on Python-canonical metrics (Martin's SDP, MacCormack's propagation cost, the import-linter contract algebra, LocAgent's invoke-edges finding) that are differentiated by their Python-specific resolution, not by their existence.

## Judge vs librarian: still a boundary, now also a synergy seam (May 2026)

Revisited the bucket-6 "judge not librarian" stance against the recent agent research (Navigation Paradox, LocAgent, Constraint Decay, the exemplar-surfacing deep-dive in [`research/RESEARCH_METRICS.md` §14c.5](research/RESEARCH_METRICS.md)). The stance holds, but it sharpens.

**The boundary is not "no navigation," it is "no code-return and no semantic/symbol search."** archy already ships a lot of navigation - `archy_impact` (blast radius), `archy_graph_focus` (bounded neighborhood with edge metadata + import line numbers), `archy_affected`, `archy_high_risk_modules`, `archy_hotspots`. None of these are librarian features: they navigate *the judgment* (which modules are structurally implicated/fragile), not *the code* (they never return source, and never do FTS5/symbol/embedding search). The Navigation Paradox (§14c.1) and LocAgent (§14c.2) findings validate exactly this: a judge whose verdicts are navigable is more useful than one that emits only a number, and structural graph navigation is not made redundant by large context windows.

**Why the boundary still holds** (no FTS5/symbol search, no code-returning `context` tool, no embeddings retrieval, no framework-route detection):
1. It is commodity and well-owned - Cursor, Cody, and [codegraph](https://github.com/colbymchenry/codegraph) do retrieval, and do it well (codegraph: 92% fewer tool calls). A worse second copy dilutes the depth wedge.
2. Naive retrieval actively *hurts* - the exemplar deep-dive surfaced the ICL result that uncontrolled similar-code retrieval degrades generation up to 15%. archy adding retrieval would inherit that liability.
3. Depth is the defensible wedge - Python-canonical judgment is what a 52-language librarian structurally cannot fold in.

**The synergy seam (new): judge-as-reranker, downstream of a librarian.** The highest-value move is not archy *becoming* a librarian but archy sitting *after* one: the librarian retrieves candidates (similarity), archy ranks/filters/annotates them (structural quality). The ICL literature is explicit that example *quality* dominates outcomes while *similarity* is the retriever's job, so the two compose cleanly. `archy_exemplars` ([#138](https://github.com/hslee16/archy/issues/138)) is the first deliberate instance: "given a peer group, which member is the structurally cleanest to copy?" `archy_affected` / `archy_impact` are earlier shapes of the same idea. The win is interop, not absorption: keep verdicts trivially consumable by a librarian (stable module IDs, `file:line` anchors, which `archy_graph_focus` already emits), so "Cody finds the candidates, archy says which one is structurally safe" rather than "archy replaces Cody."

Refined stance in one line: **archy stays a judge, keeps navigating its own graph to make verdicts addressable, refuses code-return and semantic search, and gets *more* synergistic by acting as a re-ranking/annotation layer a librarian feeds into.** Caveat: "synergy" invites scope creep, so any new surface must still pass the audience test ("what does this give an agent it doesn't already have?") and the no-retrieval line.

## Community partitioner: greedy stays, louvain rejected (June 2026, #190)

Prompted by Graphify (which uses Leiden community detection), evaluated swapping archy's two greedy-modularity call sites (`score.py:compute_modularity`, `dsm.py:_group_by_community`) to NetworkX's native `louvain_communities`. Built `bench/community_partitioner.py` to measure greedy vs seeded louvain across the 11-repo corpus plus synthetic graphs. The data (`bench/community_partitioner_results.md`) rejected the swap for both paths:

1. **Marginal real-corpus Q gain.** Louvain's Q advantage over greedy is large only on the regular synthetic graphs greedy handles poorly (+0.13 to +0.22); on real codebases it is marginal and sometimes negative (mean +0.008, range -0.010..+0.050). Community counts barely differ on real repos (e.g. fastapi 267/267).
2. **No DSM cohesion win (the advisory path's actual product).** Block coverage -- the fraction of import edges staying inside a community, the interpretable "are the blocks cohesive?" measure the DSM grouping is for -- is *lower* under louvain on all 11 real repos (mean -0.053). Because community counts are comparable, that is not a granularity artifact: at similar block counts louvain's blocks are simply less cohesive, since it spreads edges across more boundary crossings. The swap's entire rationale (cleaner blocks) is unsupported.
3. **Louvain is not insertion-order deterministic.** Across three deterministic node reorderings under a fixed `seed=0`, louvain is repeat-stable (run to run) but order-stable on only 2/14 graphs: its partition *membership* changes with parse order. Greedy is order-stable on all 14, measured directly here, not just cited from the [2026-06 adversarial review](https://github.com/hslee16/archy/issues/180). For the score this is disqualifying (a parse-order-dependent score breaks cross-environment reproducibility and sentrux comparability). For the advisory DSM path it means the blocks an agent reads differ run to run on the same code -- a visual-consistency cost. (It is *not* a `diff_dsm` regression: `diff_dsm` is name-keyed and reorder-robust, so its added/removed/weight-changed output is invariant to grouping. Greedy's own layout is membership-stable; only equal-size block *labels* can reorder, a minor pre-existing tiebreak detail.)
4. **Score-path switch breaks continuity.** Re-deriving the modularity axis from louvain would move it by up to +0.14 per project, breaking `.archy/history.jsonl` trend continuity and sentrux number-matching for no real-corpus quality win.

Leiden's well-connectedness guarantee (the reason it would beat *louvain*) could change the advisory-path calculus, but it needs the `leidenalg` + `python-igraph` C dependency, which is off archy's pure-Python path; deferred, not adopted. Net: the experiment did its job and produced a negative result. Greedy stays. The follow-up score-shape question (should `overall` reflect single structural regressions at scale, independent of partitioner) is tracked separately in [#192](https://github.com/hslee16/archy/issues/192).
