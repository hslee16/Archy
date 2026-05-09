# Future Features

Concrete things to build next, with rough order. Items in brackets cite where the idea came from.

## Near-term (next 2–3 PRs)

- **`__init__.py` re-export resolution** — when `pkg/__init__.py` does `from .x import Foo`, downstream `from pkg import Foo` should resolve to `pkg.x` rather than `pkg`. Without this, any well-factored package that uses `__init__.py` as a public surface (i.e. most of them) over-reports cycles: submodules import the package's re-exported names, the package imports its submodules, and the resulting back-edges look like real cycles. **Hit this immediately on FastAPI** — 7 of the 12 cycle members in the core cluster were artifacts of this. Must land before cycle detection ships.
- **Cycle detection** — Tarjan SCC over the existing graph; report each cycle with the participating modules and the offending import lines. Foundation for the acyclicity metric.
- **Layer rules from YAML** — `archy.yaml` declaring layer membership and forbidden directions; `archy check` exits non-zero on violation. Minimum viable governance. [sentrux: `.sentrux/` rules]
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
