# Learnings

Running notes from building archy. Updated as we go.

## v0.0.1 — import graph

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

1. **Field-typed captures were noisier than capturing whole statements and walking fields.** I started by capturing `(import_statement name: (dotted_name) @abs_module)` plus several variants for aliased/from/relative imports. The grouping back into "one statement" got ambiguous fast — there was no way to tell which `@abs_module` belonged to which statement when one statement imported multiple names. Switching to "capture the whole `import_statement` / `import_from_statement` node, then use `node.children_by_field_name('name')` and `node.child_by_field_name('module_name')`" was simpler and survives every form.

2. **`from X import a, b` is genuinely ambiguous statically.** `a` could be a name in `X`'s namespace or a submodule. Without semantic analysis (which would mean executing `X/__init__.py`), you have to guess. The pragmatic rule: when `X` is internal, prefer submodule edges where they exist; otherwise attribute the edge to `X` itself. Sentrux's plugin config doesn't actually disambiguate this either — it just records `module_name` and lets the resolver figure it out.

3. **Error recovery is the headline feature.** Tree-sitter produces a partial tree on syntax errors with `ERROR` and `MISSING` nodes. The query still runs; clean imports still come through. Surfaced via `tree.root_node.has_error` and propagated as `ParseResult.has_errors` and the graph's `parse_errors` list. This was the whole reason to pick tree-sitter over `ast` and it works exactly as advertised.

### Package discovery: `src/` layout matters

A directory is a "package root" when it contains `__init__.py` AND its parent does not. That single rule covers both flat layouts and the `src/<pkg>/__init__.py` convention sentrux's Python plugin documents — `src` itself isn't a package, so `src/myapp` becomes the root and module qualnames look right (`myapp.core`, not `src.myapp.core`).

### Relative imports: dot count semantics

`from . import x` means "stay in current package." `from .. import x` means "go up one." So the walk-up count is `leading_dots - 1`. Off-by-one footgun; got it wrong on the first pass and the test for relative imports caught it.

### What "external" means in this graph

We collapse external imports to their top-level package: `import requests.adapters` becomes an edge to `requests`. This matches sentrux's behavior and keeps the external surface area tractable. Trade-off: we lose granularity inside third-party packages, but we don't care about their internal structure for our metrics.

### Sentrux's quality-signal design

Their `docs/quality-signal-design.md` is excellent. The five root-cause metrics aren't arbitrary — they're presented as the five independent structural properties of a directed graph (modularity, acyclicity, depth, equality, redundancy). The argument for **geometric mean** as the aggregator is the strongest part: it's the unique aggregation function satisfying Pareto optimality + symmetry + independence, which means the only way to game the score is to actually improve every dimension. That's the property worth preserving when archy gets to scoring.

We will likely not match their full metric set in v1 — redundancy in particular requires AST-level dead-code and duplicate-function detection that's a lot more work than the import graph. Modularity (Newman's Q), acyclicity (Tarjan SCC count), and depth (longest-path DAG) are all derivable from the graph we already build. Those three plus a fan-out concentration metric (Gini of out-degrees, since we don't yet have per-function CC) get us most of the way without leaving graph-theory land.

### Performance note

The real-world test was the governingdocs backend: 665 internal modules, 356 internal edges, runs in well under a second cold. Tree-sitter parses are fast; the bottleneck is filesystem traversal. No optimization needed yet.
