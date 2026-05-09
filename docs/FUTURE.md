# Future Features

Concrete things to build next, with rough order. Items in brackets cite where the idea came from.

## Near-term (next 2–3 PRs)

- **Multi-hop re-export chains** — re-export resolution currently follows only one hop. If `pkg/__init__.py` re-exports from `pkg.sub` and `pkg.sub/__init__.py` re-exports from `pkg.sub.impl`, consumers of `pkg.Foo` resolve to `pkg.sub`, not `pkg.sub.impl`. Add transitive following capped at a small max-depth to avoid pathological loops.
- **Self-loop / size-1 SCC reporting** — cycle detection currently requires `min_size >= 2`. A module that imports itself (rare but possible, especially through `__init__.py` patterns) is a real cycle. Detect self-edges as size-1 cycles and surface them, ideally without changing default `min_size` semantics for the ≥2 case.
- **Call graph** — second edge type alongside imports. Tree-sitter query for `(call function: ...)`, resolve callee to its defining module. Doubles the signal for modularity/coupling because two modules can be independent by imports but tightly coupled by calls. [sentrux: uses both import and call edges in Q]
- **Cyclomatic complexity per function** — branch-node counts via tree-sitter. Feeds the equality (Gini) metric and the redundancy proxy. [sentrux: `[semantics.complexity]` config]

## Scoring (the headline feature)

- **Single quality score** with the four metrics we can compute purely from the graph + AST: modularity (Newman's Q), acyclicity (`1 / (1 + cycle_count)`), depth (`1 / (1 + max_depth/8)`), equality (`1 - gini(out_degrees)` initially, eventually `1 - gini(per_function_cc)`). Aggregate via geometric mean. [sentrux: `quality-signal-design.md`]
- **Per-commit JSONL history** in `.archy/history.jsonl`. One row per `archy score` invocation with `{commit_sha, ts, score, sub_metrics, file_count, loc}`.
- **`archy trend`** — ASCII sparkline + last-N table for terminal viewing; static HTML with Chart.js as a stretch goal.

## Deferred — needs more thought

- **Redundancy metric** — dead functions (no inbound call edges) and duplicate functions (AST-shape hash). Hard to do without false positives because of dynamic dispatch, decorators, and entry points (`if __name__ == "__main__"`). [sentrux: `redundancy_ratio`]
- **Type reference edges** — function parameter and return annotations as a third edge type. Useful for catching layer violations that hide behind `if TYPE_CHECKING:` imports. [sentrux: `tags.scm` type-reference query]
- **Treemap visualization** — D3 or Observable Plot, wrapped in a static HTML report. Sentrux's signature feature; valuable but not load-bearing for governance use.
- **MCP server** — wrap `archy score` and `archy check` as MCP tools so coding agents can read their own architectural impact. Half-day of work once the analyzer is stable.
- **Pre-commit hook + GitHub Action** — distribute the obvious wrappers.

## Hard problems that probably won't ship

- **Dynamic imports** — `importlib.import_module(name)` where `name` is a runtime string. Static analysis can flag these as opaque but can't resolve them. Acceptable.
- **Conditional imports under `sys.platform` / `TYPE_CHECKING`** — currently treated as unconditional. We could tag edges with their guarding condition for richer rules ("CI must not depend on Windows-only modules") but the YAML schema for that is non-trivial.

## Anti-goals (stay disciplined)

- No multi-language support. The first time someone proposes adding TypeScript, point them at sentrux.
- No runtime instrumentation (only static analysis). The whole pitch is "doesn't need to run your code."
- No replacement of linters or type checkers. We're orthogonal to ruff and mypy, not a competitor.
