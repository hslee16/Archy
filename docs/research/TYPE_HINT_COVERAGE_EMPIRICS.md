# Type-hint coverage: empirical study and the diagnostic decision

This document records the empirical investigation into whether type-hint coverage should be added as the 6th score axis (the strongest such candidate per [`AXIS_REVIEW.md`](AXIS_REVIEW.md)) and the resulting design decision.

Companion artifacts: `bench/typehint_coverage.py` (the script) and `bench/typehint_coverage_results.md` (the raw numbers).

## Decision

**Do not ship type-hint coverage in any form, neither as a score axis nor as a diagnostic.** archy is a graph-shape sensor; type-hint coverage is per-function metadata at a different level of abstraction and is already handled well by existing tooling (`mypy --strict`, `pyright --strict`). Adding it to archy would widen the surface without deepening the niche.

This document is retained as a "we measured this, considered it, decided no" reference. The bench script and raw numbers stay in `bench/` so future reconsiderations have a starting point.

## What the data shows

(Full results in `bench/typehint_coverage_results.md`.)

**Metric:** `coverage = annotated_positions / total_positions` over public functions. A position is a parameter (excluding `self`/`cls`) or the return slot. A function is "public" if its name does not start with `_`, with the exception that dunders (`__init__` etc.) are counted as public.

**Distribution:** sharply bimodal across the 27 projects.

- 15 projects above 0.85 coverage (modern libraries that adopted typing as policy)
- 4 projects in 0.50-0.85 (mid-transition)
- 7 projects below 0.20 (legacy, generated, or scientific code)
- 5 projects at literal zero or near-zero (django 0.000, boto3 0.000, botocore 0.001, pygments 0.001, numpy 0.011)

**Top:** requests 0.990, flask 0.984, click 0.978, httpx 0.964, rich 0.957, starlette 0.955, mypy 0.950, aiohttp 0.948, archy 0.938. All mature, modern, deliberately-typed libraries.

**Orthogonality (Pearson r against the 5 archy axes):**

| axis | r |
| --- | ---: |
| modularity | -0.511 |
| acyclicity | -0.542 |
| depth | +0.551 |
| equality | +0.037 |
| complexity | -0.030 |

Three of five at moderate coupling (`|r|` 0.5-0.7). All below the 0.7 OECD redundancy threshold, but well above the inter-axis median for any existing pair.

## OECD four-criterion check

Per [`AXIS_REVIEW.md`](AXIS_REVIEW.md): a sub-indicator must clear four criteria to belong in a quality composite.

| criterion | calls_per_edge (rejected v0.21) | cc_mean (promoted v0.20) | type-hint coverage |
| --- | --- | --- | --- |
| Independence (max `\|r\|`) | 0.229 | 0.197 | **0.551** ← weakest |
| Directionality | contested | unambiguous | unambiguous |
| Actionability | weak | strong | strong |
| Discriminant validity | weak | mixed | mixed |

Coverage scores **between** `cc_mean` and `calls_per_edge` on the OECD criteria. Strong on direction and actionability; weak on independence and discriminant validity. That alone doesn't settle the question; see [Why not even as a diagnostic](#why-not-even-as-a-diagnostic) below for the value-prop argument that does.

### Independence: borderline

Three correlations at moderate strength tell a coherent story:

- **modularity / acyclicity / depth** all carry signal about "this is a small modern library" vs "this is a large legacy codebase." Coverage correlates with the same age dimension: modern libraries are typed, legacy ones often are not.
- **equality / complexity** are uncorrelated with coverage.

Coverage is partially measuring **codebase age and library generation** rather than purely measuring **typing policy**. This is the lowest-independence score archy has measured for any axis candidate (`complexity`'s max `|r| = 0.197` was a layup; `calls_per_edge`'s max `|r| = 0.229` was strong; coverage's max `|r| = 0.551` is borderline).

### Directionality: clear

"More type coverage is better" holds across the modern Python community consensus. `mypy` / `pyright` are standard linters; `--strict` is a known quality bar; PEP 484 was 2014.

Direction is the *strongest* criterion for this axis. No shape-driven exception of the kind that defeats `calls_per_edge` (numpy's small-core call density is "fine"; numpy's 1.1% type coverage is meaningfully *worse* than it could be).

### Actionability: strong

"Add type hints to your public functions" is a canonical refactoring with first-class tool support: `mypy --strict`, `pyright --strict`, `monkeytype`, `pytype`, `pyre`, and IDE prompts. The action is well-defined, the tools exist, and improvement is measurable.

### Discriminant validity: mixed

The bottom of the distribution contains widely-respected codebases:

- **django** at 0.000: arguably the most widely-deployed Python web framework. Typing was deferred for backwards-compatibility reasons; ongoing effort exists. Calling django "architecturally weak because it lacks types" is not defensible.
- **numpy** at 0.011: ships extensive type stubs (`.pyi` files) that this tool does not see. Inline coverage is low; practical typing coverage via stubs is much higher.
- **boto3** at 0.000: generated SDK over botocore. Adding inline types would mean modifying the generator or post-processing the output; the lack of types is a generation-pipeline choice, not an architectural quality signal.
- **pygments** at 0.001: lexer registry from 2006. Predates PEP 484 by 8 years.

Same problem `calls_per_edge` had: the metric penalizes intentional or legacy design choices that are not architectural pathologies.

**Counter-argument:** all of these projects *would* be better with full type coverage. The metric is pointing at real technical debt; the question is whether to make it a headline-score reduction or a diagnostic the user can read in context.

## Three paths considered

### Path A: ship as 6th score axis

- **Pros:** honors clear directionality and strong actionability; surfaces a real Python-specific signal that the existing 5 axes don't capture.
- **Cons:** would crush absolute scores for django (0.497 → ~0), numpy (0.633 → ~0), boto3 (0.640 → ~0), pygments (0.617 → ~0), botocore (0.565 → ~0). Bimodal distribution amplifies under geomean. Independence is the weakest archy has measured. Requires another baseline reset.

### Path B: ship as diagnostic on `archy score`

- **Pros:** surfaces the signal cheaply; matches the v0.21 call-weighted Q precedent; users and agents read the number and judge contextually; no score-shape change; no baseline reset.
- **Cons:** "calculate but don't score" reads as half-finished; adds another sub-stat to the output.

### Path C: don't ship

- **Pros:** `mypy` / `pyright` / `monkeytype` already measure this; archy doesn't have to.
- **Cons:** archy's value proposition is "single number for architectural health"; typing is widely considered part of that picture, and not measuring it means an agent using archy as the loop-closer signal will not know about typing gaps.

## Why not even as a diagnostic

The OECD scorecard rules out axis promotion. A separate argument rules out diagnostic shipment.

The argument by analogy to v0.21's call-weighted Newman Q decision (ship as parallel diagnostic when axis case is contested) does *not* apply here. Call-weighted Q earned a diagnostic slot because the *gap* between two values was the load-bearing signal (you can only read it when both values sit side by side). Type-hint coverage is one number; there is no gap to surface. "Consistency with the v0.21 precedent" was a rhetorical match, not a substantive one.

Five concrete arguments against the diagnostic:

1. **Better tools exist and own the niche.** `mypy --strict` and `pyright --strict` report exactly which functions are untyped, with file paths and line numbers. archy's "93.8% annotated" is a pale shadow; a user who wants to improve the number runs `mypy` next anyway. archy adds zero value in that loop.

2. **The signal is noisy without significant additional work.** Stub files (`.pyi`) are not read, so numpy reads as 1.1% when its real practical coverage via stubs is much higher. `Any`-as-cover-all is not detected. `__all__`-exported public surface is not honored. A user looking at the number could be meaningfully misled. Closing these gaps requires substantially more than the "3-4 hours" the diagnostic was originally estimated at.

3. **It is not structural.** archy's distinctive value is graph-shape: modularity, acyclicity, depth, equality, complexity. Type coverage is per-function metadata at a different level of abstraction. Adding it dilutes the focus without deepening the niche.

4. **Audience mismatch.** archy is club-shaped (per Eghbal's *Working in Public*, and per the AXIS_REVIEW.md framing). The small group of Python infra people who care about graph-shape signals already use mypy / pyright for typing. Widening archy's surface is the wrong move for a club project; deepening the niche is the right move.

5. **Slippery slope.** If type-hint coverage warrants a diagnostic, so does docstring coverage, test coverage, license scanning, `__all__` consistency, public-API churn. Every one of these has the same "but agents want one read" argument. The diagnostic surface inflates and the structural niche dilutes.

The actual question this empirical study answered, in retrospect, was not "does the OECD criteria check pass?" but "is archy a graph-shape sensor or a complete-codebase-health sensor?" The answer to that strategic question is the former. The OECD scorecard was a useful tool for the axis-promotion question; the value-prop question is what closes out the diagnostic-or-not question.

## What this analysis does *not* settle

- **Stub-file coverage (`.pyi`).** numpy and others ship types via stubs; this tool ignores them. A v2 of the metric could read sibling `.pyi` files. Worth doing before any axis-promotion attempt.
- **`Any` as cover-all.** `x: Any` counts as an annotation here but provides no quality value. `mypy` / `pyright` distinguish; this tool does not.
- **`__all__` vs underscore-prefix public detection.** Some projects export selectively via `__all__`; the empirical impact of switching is unknown.
- **The 10-expert ranking study from `AXIS_REVIEW.md`.** Out of scope here; would substantially raise the rigor of any future axis-promotion decision.
