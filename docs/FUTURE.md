# Future Features

Concrete things to build next, with rough order. Items in brackets cite where the idea came from. Items annotated `[RESEARCH_METRICS.md §N]` are validated against the survey in [`RESEARCH_METRICS.md`](RESEARCH_METRICS.md).

## Pre-call-graph wins (validated, low cost)

These are essentially free - no new edge type, no AST work, no git mining - and would substantially widen archy's signal surface before the call-graph PR lands.

- ~~**Tangle ratio**~~ shipped in v0.7.x as `acyclicity = 1 - tangle_ratio` (fraction of nodes inside SCCs of size ≥ 2). Replaced the old `1/(1+cycle_count)` form. See `docs/SCORING.md` §Acyclicity. [RESEARCH_METRICS.md §6]
- ~~**`archy.yaml` Forbidden + Independence contracts**~~ shipped in v0.8.x as `archy contracts` / `archy_contracts` MCP tool. Wraps import-linter rather than reimplementing natively (~1/3 the LOC, gets all five contract types: Layers, Forbidden, Independence, Protected, AcyclicSiblings). Reads `.importlinter`. Optional dep behind `archy[contracts]`. [RESEARCH_METRICS.md §10; docs/AGENT_LOOP_CONTRACTS_TEST.md]
- **NCCD as fifth score axis** - validated empirically orthogonal to depth (Pearson r=0.000 on the 9-library benchmark). Captures average-case reach where depth captures worst-case chain. Adding it shifts the geometric-mean exponent 1/4 → 1/5, which breaks cross-version score comparisons - flag explicitly per the OECD comparability-over-time guidance in `SCORING.md`. [RESEARCH_METRICS.md §3]
- **Martin's `I` per module + SDP-violation check rule** - `I = Ce/(Ce+Ca)` is a single-pass ratio on the existing graph. Surface per-module in `archy graph --format json`; add a Stable Dependencies Principle violation check ("a module imports one with strictly higher `I`") as a new rule type for `archy check`. [RESEARCH_METRICS.md §2]
- **PageRank + core size as diagnostics** - per-module PageRank weights "importance by importance of dependents." Core size = largest SCC fraction. Both are NetworkX one-liners. Surface in `archy graph --format json` and `archy_impact` output; not score axes. [RESEARCH_METRICS.md §3, §5]

## Near-term (next 2–3 PRs)

- **Call graph** - second edge type alongside imports. Tree-sitter query for `(call function: ...)`, resolve callee to its defining module. Doubles the signal for modularity/coupling because two modules can be independent by imports but tightly coupled by calls. Also raises NCCD/propagation cost resolution. [sentrux: uses both import and call edges in Q]
- **Cyclomatic complexity per function** - branch-node counts via tree-sitter. Feeds the equality (Gini) metric and unblocks hotspot analysis. Cognitive complexity rides along free in the same AST pass. [sentrux: `[semantics.complexity]` config; RESEARCH_METRICS.md §9]

## Scoring (the headline feature)

- ~~**Single quality score**~~ shipped in v0.2.0 as `archy score`. Four sub-metrics (modularity, acyclicity, depth, equality), geometric mean. Side-by-side comparison with sentrux's design in `docs/LEARNINGS.md`.
- ~~**Per-commit JSONL history** in `.archy/history.jsonl`~~ shipped in v0.3.0 (`archy score --record`).
- ~~**`archy trend`**~~ shipped in v0.3.0 - ASCII sparkline + last-N table.
- **Static HTML trend report** - the original stretch goal. Render `.archy/history.jsonl` as a self-contained HTML page with Chart.js so trend can be linked from a CI artifact or dashboard.
- **Equality based on per-function CC** - we currently compute Gini over module out-degrees as a proxy. The eventual signal is `gini(per_function_cyclomatic_complexity)`, which requires the cyclomatic-complexity metric above.
- **Type-hint coverage as sub-stat or sixth axis** - percentage of public functions with full parameter and return annotations. Differentiated Python signal that no graph-level metric captures. Tree-sitter pass over `FunctionDef` nodes; rides on the same AST scope as cyclomatic complexity. Could ship first as a sub-stat and promote to a score axis if the signal proves load-bearing. [RESEARCH_METRICS.md §13]
- **Hotspots = CC × per-file churn** - standalone `archy hotspots` command once CC ships. Needs only a one-pass `git log --name-only` parser, not a full cross-file co-change matrix. Produces a prioritized list ("refactor these three files first") rather than a single number. [RESEARCH_METRICS.md §8]

## Deferred - needs more thought

- **Duplicate-function detection** - AST-shape hashing (normalize identifiers, hash structure, cluster) over a length threshold. Lower empirical FP rate than dead-function detection. Ship as an advisory list, not a score axis. [sentrux: `redundancy_ratio`; RESEARCH_METRICS.md §12]
- ~~**Dead-function detection**~~ - **deferred indefinitely.** Empirical 23-project vulture run in 2026-05 confirmed default-confidence FP rates from 10 (msgspec) to 2,017 (django); 15-finding spot-checks on FastAPI / pytest / Django were 15/15 false positives, dominated by Pydantic validators, pytest fixtures, decorator-registered route handlers, and Django's `global_settings.py` string-lookup pattern. Static dead-code detection on real Python is too noisy to fold into a quality signal. Revisit only if archy can ingest a runtime-coverage source. [RESEARCH_METRICS.md §12]
- **Score-shape redesign for axis independence.** The 23-project benchmark shows 4 of 6 axis pairs at moderate Pearson correlation (`|r|` 0.5-0.7), all below the OECD redundancy threshold but enough to qualify the geometric-mean independence argument. Two candidate redesigns: (a) replace acyclicity's tangle-ratio normalization with a graph-size-invariant alternative to break its coupling with depth and equality; (b) accept the coupling and replace geometric mean with a non-compensatory aggregator that handles correlated indicators explicitly (e.g., Mazziotta-Pareto Index). Both are research projects, not roadmap items today. [SCORING.md §Empirical axis independence]
- **Type reference edges** - function parameter and return annotations as a third edge type. Useful for catching layer violations that hide behind `if TYPE_CHECKING:` imports. [sentrux: `tags.scm` type-reference query]
- **Treemap visualization** - D3 or Observable Plot, wrapped in a static HTML report. Sentrux's signature feature; valuable but not load-bearing for governance use.
- **Deterministic contract-check hook for AI sessions.** Empirical finding from the agent-loop test in `governingdocs/backend` (2026-05): a fresh agent caught a forbidden cross-layer import before implementing it, but did so by reading `archy.yaml` + `CLAUDE.md` directly rather than calling `archy_check` / `archy_contracts`. The MCP-tool angle's marginal value over "agent reads docs" is narrower than the hypothesis assumed - it shows up mainly when rules live only in `.importlinter` (no archy.yaml) or when violations are transitive multi-hop. To make contract-check feedback deterministic regardless of agent disposition, ship a Claude Code Stop hook that runs `archy contracts` once per task and surfaces violations into the agent's context. Per-task cost: ~3-5s wall, ~150-600 tokens. PostToolUse-on-Edit fires too often (5-15x per task, edit-thrashing noise). Project-side configuration only; archy doesn't need new code. Worth shipping as a documented snippet in `docs/AGENT_LOOP.md`, not a feature.
- ~~**Pre-commit hook + GitHub Action**~~ shipped in v0.4.1/v0.4.2 - see `README.md` for usage.

## Hard problems that probably won't ship

- **Dynamic imports** - `importlib.import_module(name)` where `name` is a runtime string. Static analysis can flag these as opaque but can't resolve them. Acceptable.
- **Conditional imports under `sys.platform` / `TYPE_CHECKING`** - currently treated as unconditional. We could tag edges with their guarding condition for richer rules ("CI must not depend on Windows-only modules") but the YAML schema for that is non-trivial.

## Anti-goals (stay disciplined)

- No multi-language support. The first time someone proposes adding TypeScript, point them at sentrux.
- No runtime instrumentation (only static analysis). The whole pitch is "doesn't need to run your code."
- No replacement of linters or type checkers. We're orthogonal to ruff and mypy, not a competitor.
