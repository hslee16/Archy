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

Their `docs/quality-signal-design.md` is excellent. The five root-cause metrics aren't arbitrary - they're presented as the five independent structural properties of a directed graph (modularity, acyclicity, depth, equality, redundancy). The argument for **geometric mean** as the aggregator is the strongest part: it's the unique aggregation function satisfying Pareto optimality + symmetry + independence, which means the only way to game the score is to actually improve every dimension. That's the property worth preserving when archy gets to scoring.

We will likely not match their full metric set in v1 - redundancy in particular requires AST-level dead-code and duplicate-function detection that's a lot more work than the import graph. Modularity (Newman's Q), acyclicity (Tarjan SCC count), and depth (longest-path DAG) are all derivable from the graph we already build. Those three plus a fan-out concentration metric (Gini of out-degrees, since we don't yet have per-function CC) get us most of the way without leaving graph-theory land.

### Performance note

The real-world test was the governingdocs backend: 665 internal modules, 356 internal edges, runs in well under a second cold. Tree-sitter parses are fast; the bottleneck is filesystem traversal. No optimization needed yet.

## v0.2.0 - score: comparison with sentrux

archy's score follows sentrux's [`quality-signal-design.md`](https://github.com/sentrux/sentrux/blob/main/docs/quality-signal-design.md) closely - same model, same aggregation, four of the five sub-metrics implemented identically. Sentrux is pure Rust; archy is Python on top of `networkx`. Sub-metric formulas match where they should:

| Sub-metric | sentrux | archy v0.2.0 | Notes |
|---|---|---|---|
| Modularity | Newman's Q over greedy partition; `(Q + 0.5) / 1.5` mapped onto `[0, 1]` | identical (clamped to `[0, 1]` after the linear map) | We adopted sentrux's normalization explicitly so cross-tool numbers stay comparable. |
| Acyclicity | `1 / (1 + cycle_count)` from Tarjan SCC of size > 1 | identical | `archy.cycles.find_cycles` is the SCC count source. |
| Depth | `1 / (1 + max_depth / 8)` over longest path | identical, computed on `nx.condensation(graph)` so cycles collapse to single nodes first | Sentrux uses iterative DFS from entry points; networkx's `dag_longest_path_length` is equivalent for a DAG. |
| Equality | `1 - Gini(out-degree)` with `G = Σ (2i - n - 1) x_i / (n * Σ x_i)` | identical | Both projects report `1 - Gini` so a higher number is better. |
| Redundancy | `1 - (dead + duplicate) / total_functions` | **not implemented** | FUTURE.md keeps it deferred: dynamic dispatch, decorators, and `if __name__ == "__main__":` gates make purely-static dead-code detection too noisy. |
| Aggregation | geometric mean of 5 | geometric mean of 4 | Same rationale (Pareto + symmetry + independence). |
| Display scale | integer 0-10000 | float `[0, 1]` to three decimals | Cosmetic. |

What we get from being faithful: a v0.2.0 archy score on a given codebase is directly comparable to whatever sentrux would produce on its four-metric subset. What we lose: redundancy. When `archy redundancy` ships (FUTURE.md, deferred), aggregation will widen back to five and the numeric scale will line up with sentrux's.

The deeper agreement is methodological - sentrux's argument for geometric mean (the only aggregator that's Pareto-optimal, symmetric, and independent) is the load-bearing claim. That's why score gaming works only by improving every axis.

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
