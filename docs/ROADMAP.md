# Roadmap

Short, opinionated summary of where archy is going. The detailed item-by-item list (with shipped, in-flight, deferred, and rejected items, plus citations to the literature each idea came from) lives in [`FUTURE.md`](FUTURE.md). This page is the executive summary.

## Now

Things actively in flight or planned for the next 2-3 releases.

- **Design Structure Matrix (`archy dsm`)**: render the internal dependency graph as a square module x module matrix with rows/columns ordered to push edges below the diagonal. Canonical industrial visualization of architectural coupling. ASCII for terminals, JSON for tooling, optional self-contained HTML for CI artifacts. Diagnostic, not a score axis. See [`FUTURE.md`](FUTURE.md) near-term section.
- **Call-weighted Newman Q as a refinement of the modularity axis.** Weight import-graph edges by `call_count` so module pairs that don't actually call each other contribute less to the community-structure signal. This is a refinement of the existing modularity axis, not a new axis. Replaces the prior "promote `calls_per_edge` to a 6th axis" plan, which was reviewed in v0.20 and rejected on directionality / actionability / discriminant-validity grounds (see [`AXIS_REVIEW.md`](AXIS_REVIEW.md)). Validation work: re-run the 27-project bench with call-weighted Q, check whether the orthogonality picture and project ordering both move in defensible directions.

## Next

Things that are validated and queued but not yet started.

- **Static HTML trend report**: render `.archy/history.jsonl` as a self-contained HTML page so trend can be linked from a CI artifact or dashboard. Original v0.3 stretch goal.
- **Duplicate-function detection** via AST-shape hashing. Advisory list, not a score axis.
- **Type-hint coverage** as the candidate 6th score axis. Tree-sitter pass over `FunctionDef` nodes; rides on the same AST scope as cyclomatic complexity. Promoted from "Next" to the primary 6th-axis candidate after the [`AXIS_REVIEW.md`](AXIS_REVIEW.md) review: Python-specific, clear direction, cheap to compute, actionable, likely discriminant validity. The right path is empirics first (distribution across the bench, correlation with existing axes, normalization shape) before any promotion attempt.
- **Static fragility proxy** as a pre-CC hotspot stand-in (high-instability x high-fan-in modules). Ships today without git-history overhead; the full hotspots feature (already in v0.18) remains the higher-quality target.

## Deferred (needs more thought)

- **Score-shape redesign for axis independence.** The 23-project benchmark shows 4 of 6 axis pairs at moderate Pearson correlation (`|r|` 0.5-0.7). Two candidate redesigns: replace acyclicity's tangle-ratio normalization, or replace geometric mean with a non-compensatory aggregator (e.g., Mazziotta-Pareto Index). Research project, not a roadmap item today.
- **Equality based on per-function CC.** Currently a proxy via Gini over module out-degrees. The eventual signal is `gini(per_function_cyclomatic_complexity)`; requires the cyclomatic-complexity metric (now shipped), but the substitution itself has not been validated yet.

## Rejected (explicitly will not ship)

- **Dead-function detection.** Empirical 23-project vulture run in 2026-05 confirmed default-confidence false-positive rates from 10 (msgspec) to 2,017 (django); 15-finding spot-checks on FastAPI / pytest / Django were 15/15 false positives, dominated by Pydantic validators, pytest fixtures, decorator-registered route handlers, and Django's `global_settings.py` string-lookup pattern. Static dead-code detection on real Python is too noisy to fold into a quality signal. Revisit only if archy can ingest a runtime-coverage source.
- **Multi-language analysis.** Out of scope; that division of labor with [sentrux](https://github.com/sentrux/sentrux) is settled. archy goes deep on Python instead of broad across languages.
- **Code generation or auto-fix.** archy is a sensor, not a fixer. Surfacing the problem is the value.
- **DSL or plugin system for layer rules.** YAML stays YAML. If you need more expressive contracts, use `archy contracts` (which wraps import-linter) instead.

## How this list changes

When a "Now" item ships, it moves to a bullet in the README's "Shipped" section and gets a citation in [`FUTURE.md`](FUTURE.md) with the validation evidence. When a "Next" item gets prioritized, it moves to "Now." Items move into "Deferred" or "Rejected" only when there is empirical evidence (a benchmark sweep, an FP-rate study, a concrete user pain point), not on speculation. Every shape change to this list lands as a PR so the history is in git.

See [`FUTURE.md`](FUTURE.md) for the long-form list with citations, [`docs/RESEARCH_METRICS.md`](RESEARCH_METRICS.md) for the literature survey that informs prioritization, and [`docs/LEARNINGS.md`](LEARNINGS.md) for design rationale on shipped decisions.
