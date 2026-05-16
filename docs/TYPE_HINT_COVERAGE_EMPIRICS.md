# Type-hint coverage: empirical study and the diagnostic decision

This document records the empirical investigation into whether type-hint coverage should be added as the 6th score axis (the strongest such candidate per [`AXIS_REVIEW.md`](AXIS_REVIEW.md)) and the resulting design decision.

Companion artifacts: `bench/typehint_coverage.py` (the script) and `bench/typehint_coverage_results.md` (the raw numbers).

## Decision

**Ship type-hint coverage as a parallel diagnostic on `archy score`, not as a 6th score axis.** Same path as the v0.21 call-weighted Newman Q decision, for compatible reasons. Implementation is deferred to a future PR; this document captures the decision and the reasoning behind it so the implementation work can proceed against a stable target.

Concrete shape (when shipped):

```
complexity:  0.712  (566 functions, cc_mean=2.44, cc_max=24)
# typing: 56 public functions, 93.8% annotated  (diagnostic, not in score)
```

Two new fields on `ScoreInputs`. New helper in `archy.complexity` that adds annotation counting to the existing v0.17 tree-sitter walk over `function_definition` nodes (amortizes the parse). No score-formula change. No baseline reset.

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

Coverage scores **between** `cc_mean` and `calls_per_edge`. Strong on direction and actionability; weak on independence and discriminant validity.

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

## Why Path B

1. **Directionality and actionability are strong.** This argues for surfacing the signal in some form.
2. **Independence is borderline.** This argues against axis promotion. Adding an axis with `|r| = 0.551` against three existing axes weakens the geomean's non-compensatory argument substantially.
3. **Discriminant validity is contested.** This also argues against axis promotion. Penalizing django / numpy as a headline score reduction is not defensible.
4. **The diagnostic shape (cheap, parallel) gets the value without the cost.**

## Implementation summary (to be done in a follow-up PR)

Code surface (small):

- `src/archy/complexity.py`: extend the existing `function_definition` walk to also count parameter annotations and return-type slot per public function. Return aggregates alongside CC.
- `src/archy/score.py`: two new `ScoreInputs` fields (`public_function_count: int = 0`, `type_hint_coverage: float = 0.0`) with backwards-compatible defaults.
- `src/archy/cli.py`: one new diagnostic line in `_score_to_text`; two new keys in `_score_to_dict`.
- `src/archy/mcp.py`: picked up automatically via `ScoreInputs`; no shape break.
- `tests/`: unit tests for the annotation counter (annotated / unannotated / dunder / private / `self`-skip / `*args` / `**kwargs` cases).
- Brief subsection in `docs/SCORING.md` Deferred metrics pointing at this doc.

Roughly 3-4 hours of work; smaller than the v0.21 call-weighted Q PR (no graph-copy work, just extending one tree-sitter walk).

## Deprecation principle

Same as call-weighted Q: if the diagnostic field is not referenced in any user-facing artifact (bug reports, blog posts, third-party tooling) within ~3 releases of shipping, plan to remove it rather than let unused diagnostics accumulate.

## What this analysis does *not* settle

- **Stub-file coverage (`.pyi`).** numpy and others ship types via stubs; this tool ignores them. A v2 of the metric could read sibling `.pyi` files. Worth doing before any axis-promotion attempt.
- **`Any` as cover-all.** `x: Any` counts as an annotation here but provides no quality value. `mypy` / `pyright` distinguish; this tool does not.
- **`__all__` vs underscore-prefix public detection.** Some projects export selectively via `__all__`; the empirical impact of switching is unknown.
- **The 10-expert ranking study from `AXIS_REVIEW.md`.** Out of scope here; would substantially raise the rigor of any future axis-promotion decision.
