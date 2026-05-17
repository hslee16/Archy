# Roadmap

Short, opinionated summary of where archy is going. The detailed item-by-item list (with shipped, in-flight, deferred, and rejected items, plus citations to the literature each idea came from) lives in [`FUTURE.md`](FUTURE.md). This page is the executive summary.

## Audience principle (load-bearing)

archy's outputs are consumed by **LLM coding agents**, not by humans browsing dashboards. This frames every decision below:

- Useful output shapes: structured JSON (MCP tools, `--format json`), terminal ASCII (for agent "show your work" context), and the snapshot/diff loop.
- Not useful output shapes: rendered HTML dashboards, interactive web visualizations, anything that requires a browser to consume.

When deciding whether to ship a feature, the load-bearing question is "what does this give an agent that it doesn't have today?", not "would this look nice as a chart?".

## Now

Things actively in flight or planned for the next 2-3 releases. Both items previously in this section shipped in v0.21.0 / v0.22.0; the queue below promotes from "Next" as work picks up.

- *(no items currently in flight; the queued items are in [Next](#next))*

### Shipped from this section

- ~~`archy dsm` (Design Structure Matrix)~~ shipped in v0.22.0 as a visualization-only output. CLI `archy dsm PATH` + MCP tool `archy_dsm`, with `--group=community|layer|topological`, `--weight=imports|calls`, `--focus`/`--package` filters, and `--diff` returning a `DSMDiff` whose `new_back_edges` field flags cycles an edit just introduced. ASCII for terminal context, JSON for tool consumption; HTML output dropped per the audience principle. Empirical study against the 27-project bench ruled out promoting any DSM-derived scalar to a score axis or diagnostic ([`DSM_EMPIRICS.md`](DSM_EMPIRICS.md)).
- ~~Call-weighted Newman Q as a refinement of the modularity axis~~ shipped in v0.21.0 as a parallel diagnostic on `archy score` rather than an axis replacement. The gap between unweighted and weighted Q is the load-bearing signal (it detects mismatch between import-graph and call-graph community structure). Replacement was rejected because directionality is contested for intentional small-core / broad-call shapes ([`CALL_WEIGHTED_Q_EMPIRICS.md`](CALL_WEIGHTED_Q_EMPIRICS.md)).

## Next

Things validated and queued but not yet started.

- **Smarter `archy_diff` summary** for the agent loop. Today the diff returns raw deltas; agents would benefit from a "top N significant changes, risk-weighted" structured summary so the loop-closer reasoning is sharper. Direct ROI on the canonical agent interaction.
- **Per-module score breakdown.** Today `archy score` is project-wide. A per-module breakdown lets an agent ask "did my edit make *this module* worse?" rather than "did the project overall regress?". Pairs with `archy_diff`.
- **`archy_what_to_refactor_next` MCP tool.** Combines `archy_hotspots` and `archy_high_risk_modules` into one ranked list with structured reasoning, so the agent gets one call instead of two-plus-synthesis.
- **Duplicate-function detection** via AST-shape hashing. Advisory list, not a score axis.
- **Static fragility proxy** as a pre-CC hotspot stand-in (high-instability x high-fan-in modules). Ships today without git-history overhead; the full hotspots feature (already in v0.18) remains the higher-quality target.

## Deferred (needs more thought)

- **Score-shape redesign for axis independence.** The 27-project benchmark shows 2 of 10 axis pairs at moderate Pearson correlation (`|r|` 0.5-0.7). Two candidate redesigns: replace acyclicity's tangle-ratio normalization, or replace geometric mean with a non-compensatory aggregator (e.g. Mazziotta-Pareto Index). Research project, not a roadmap item today.
- **Equality based on per-function CC.** Currently a proxy via Gini over module out-degrees. The eventual signal is `gini(per_function_cyclomatic_complexity)`; requires the cyclomatic-complexity metric (shipped in v0.17, promoted to a score axis as `complexity` in v0.20), but the substitution itself has not been validated yet.

## Rejected (explicitly will not ship)

- **Type-hint coverage in any form (axis or diagnostic).** Empirical study in 2026-05 ([`TYPE_HINT_COVERAGE_EMPIRICS.md`](TYPE_HINT_COVERAGE_EMPIRICS.md)) concluded against both axis promotion (independence is the weakest archy has measured, max `|r| = 0.551`; discriminant validity is contested) and diagnostic shipment (mypy / pyright own the typing niche, the signal is not structural, and the "single sensor for everything" framing would dilute archy's graph-shape focus). The bench script and raw numbers stay in `bench/` for future reconsiderations.
- **`calls_per_edge` as a 6th score axis.** Empirical study in 2026-05 ([`AXIS_REVIEW.md`](AXIS_REVIEW.md)) concluded against axis promotion on directionality / actionability / discriminant-validity grounds even though independence passes. The call data is genuinely useful and shipped as: a diagnostic on `archy score`, the v0.21 call-weighted Newman Q parallel diagnostic, and the LocAgent-style `archy_graph_*` MCP navigation tools.
- **HTML output formats for archy commands.** Per the audience principle: agents consume structured JSON and terminal ASCII, not browser-rendered HTML. The original "static HTML trend report" v0.3 stretch goal and the "self-contained HTML for CI artifacts" framing of `archy dsm` are both retired. If a human dashboard is ever needed, third-party tools can consume archy's JSON output.
- **Dead-function detection.** Empirical 23-project vulture run in 2026-05 confirmed default-confidence false-positive rates from 10 (msgspec) to 2,017 (django); 15-finding spot-checks on FastAPI / pytest / Django were 15/15 false positives, dominated by Pydantic validators, pytest fixtures, decorator-registered route handlers, and Django's `global_settings.py` string-lookup pattern. Static dead-code detection on real Python is too noisy to fold into a quality signal. Revisit only if archy can ingest a runtime-coverage source.
- **Multi-language analysis.** Out of scope; that division of labor with [sentrux](https://github.com/sentrux/sentrux) is settled. archy goes deep on Python instead of broad across languages.
- **Code generation or auto-fix.** archy is a sensor, not a fixer. Surfacing the problem is the value.
- **DSL or plugin system for layer rules.** YAML stays YAML. If you need more expressive contracts, use `archy contracts` (which wraps import-linter) instead.
- **Docstring coverage, test coverage, license scanning, `__all__` consistency, public-API churn, or similar "complete-codebase-health" diagnostics.** Same argument that killed type-hint coverage: better tools own these niches and adding them would dilute archy's graph-shape focus. archy is a graph-shape sensor; it is not the single source of truth for "is this codebase healthy."

## How this list changes

When a "Now" item ships, it moves to a bullet in the README's "Shipped" section and gets a citation in [`FUTURE.md`](FUTURE.md) with the validation evidence. When a "Next" item gets prioritized, it moves to "Now." Items move into "Deferred" or "Rejected" only when there is empirical evidence (a benchmark sweep, an FP-rate study, a concrete user pain point) or an explicit value-prop argument (the audience principle, the structural-niche focus), not on speculation. Every shape change to this list lands as a PR so the history is in git.

See [`FUTURE.md`](FUTURE.md) for the long-form list with citations, [`docs/RESEARCH_METRICS.md`](RESEARCH_METRICS.md) for the literature survey that informs prioritization, [`docs/AXIS_REVIEW.md`](AXIS_REVIEW.md) for the OECD framework used on every axis-promotion decision, and [`docs/LEARNINGS.md`](LEARNINGS.md) for design rationale on shipped decisions.
