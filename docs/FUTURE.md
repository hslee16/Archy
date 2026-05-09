# Future Features

Concrete things to build next, with rough order. Items in brackets cite where the idea came from.

## Near-term (next 2–3 PRs)

- **Multi-hop re-export chains** - re-export resolution currently follows only one hop. If `pkg/__init__.py` re-exports from `pkg.sub` and `pkg.sub/__init__.py` re-exports from `pkg.sub.impl`, consumers of `pkg.Foo` resolve to `pkg.sub`, not `pkg.sub.impl`. Add transitive following capped at a small max-depth to avoid pathological loops.
- **Self-loop / size-1 SCC reporting** - cycle detection currently requires `min_size >= 2`. A module that imports itself (rare but possible, especially through `__init__.py` patterns) is a real cycle. Detect self-edges as size-1 cycles and surface them, ideally without changing default `min_size` semantics for the ≥2 case.
- **Call graph** - second edge type alongside imports. Tree-sitter query for `(call function: ...)`, resolve callee to its defining module. Doubles the signal for modularity/coupling because two modules can be independent by imports but tightly coupled by calls. [sentrux: uses both import and call edges in Q]
- **Cyclomatic complexity per function** - branch-node counts via tree-sitter. Feeds the equality (Gini) metric and the redundancy proxy. [sentrux: `[semantics.complexity]` config]

## Scoring (the headline feature)

- ~~**Single quality score**~~ shipped in v0.2.0 as `archy score`. Four sub-metrics (modularity, acyclicity, depth, equality), geometric mean. Side-by-side comparison with sentrux's design in `docs/LEARNINGS.md`.
- ~~**Per-commit JSONL history** in `.archy/history.jsonl`~~ shipped in v0.3.0 (`archy score --record`).
- ~~**`archy trend`**~~ shipped in v0.3.0 - ASCII sparkline + last-N table.
- **Static HTML trend report** - the original stretch goal. Render `.archy/history.jsonl` as a self-contained HTML page with Chart.js so trend can be linked from a CI artifact or dashboard.
- **Equality based on per-function CC** - we currently compute Gini over module out-degrees as a proxy. The eventual signal is `gini(per_function_cyclomatic_complexity)`, which requires the cyclomatic-complexity metric below.

## Deferred - needs more thought

- **Redundancy metric** - dead functions (no inbound call edges) and duplicate functions (AST-shape hash). Hard to do without false positives because of dynamic dispatch, decorators, and entry points (`if __name__ == "__main__"`). [sentrux: `redundancy_ratio`]
- **Type reference edges** - function parameter and return annotations as a third edge type. Useful for catching layer violations that hide behind `if TYPE_CHECKING:` imports. [sentrux: `tags.scm` type-reference query]
- **Treemap visualization** - D3 or Observable Plot, wrapped in a static HTML report. Sentrux's signature feature; valuable but not load-bearing for governance use.
- **MCP server** - wrap `archy score` and `archy check` as MCP tools so coding agents can read their own architectural impact. Half-day of work once the analyzer is stable.
- **Pre-commit hook + GitHub Action** - distribute the obvious wrappers.

## Hard problems that probably won't ship

- **Dynamic imports** - `importlib.import_module(name)` where `name` is a runtime string. Static analysis can flag these as opaque but can't resolve them. Acceptable.
- **Conditional imports under `sys.platform` / `TYPE_CHECKING`** - currently treated as unconditional. We could tag edges with their guarding condition for richer rules ("CI must not depend on Windows-only modules") but the YAML schema for that is non-trivial.

## Anti-goals (stay disciplined)

- No multi-language support. The first time someone proposes adding TypeScript, point them at sentrux.
- No runtime instrumentation (only static analysis). The whole pitch is "doesn't need to run your code."
- No replacement of linters or type checkers. We're orthogonal to ruff and mypy, not a competitor.
