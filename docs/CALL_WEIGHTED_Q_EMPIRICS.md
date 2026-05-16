# Call-weighted Newman Q: empirical study and the diagnostic decision

This document records an empirical investigation into whether the modularity axis should be refined to weight edges by `call_count`, and the resulting design decision. The companion artifacts are `bench/call_weighted_modularity.py` (the script) and `bench/call_weighted_modularity_results.md` (the raw numbers). The parent question is recommendation 2 of [`AXIS_REVIEW.md`](AXIS_REVIEW.md).

## Decision

Ship call-weighted Q as a **parallel diagnostic** on `archy score`, not as a replacement for the existing modularity axis. The gap between unweighted and weighted Q is the load-bearing signal, and that gap is only legible when both numbers are visible side by side.

Concrete shape on `archy score`:

```
modularity:  0.582  (10 communities, raw Q=0.373)
  call-weighted Q=0.649  (calls amplify community structure)
```

The bracketed prose interprets the gap in one of three buckets:

- `calls amplify community structure` (weighted - unweighted > +0.05)
- `calls cross community boundaries` (weighted - unweighted < -0.05)
- `calls roughly track community structure` (within +/- 0.05)

The overall `archy score` formula is unchanged. No "record a new baseline" event for users.

## What this diagnostic is for

Five concrete use cases, all of which require the *gap* between the two values rather than either value alone:

1. **Mismatch detector.** When `Q_weighted < Q_unweighted`, the package layout doesn't reflect call patterns. The decomposition is aspirational, not realized. Maintainers and agents should know this before treating package boundaries as load-bearing.
2. **Architectural drift detection over time.** A widening gap means the package layout is drifting from actual usage. Leading indicator that's invisible to the existing five axes.
3. **Layer-rule validation.** When `archy check` passes but the gap is large, the imports respect the declared layers but the calls don't. That's a real anti-pattern, visible only through this comparison.
4. **Agent feedback.** When an agent moves a function or adds an import, weighted Q is the metric most likely to flag "you introduced a cross-boundary call that wasn't there." Unweighted Q wouldn't notice.
5. **Cross-codebase comparison.** Two projects with identical modularity scores can have very different gaps. Useful when comparing org codebases against each other or against open-source references.

None of the other five axes (modularity, acyclicity, depth, equality, complexity) compare two views of the same codebase the way this gap does. They all look at one view in isolation.

## The empirical study

### Setup

Built each of the 27 bench projects (`bench/projects.yaml`) and computed two Q values:

- `Q_unweighted`: current `compute_modularity` shape; every edge counts equally.
- `Q_weighted`: same Clauset-Newman-Moore greedy algorithm, edges weighted by `call_count` with a weight=1 fallback for import-only edges. The fallback preserves every structural edge in the modularity computation rather than zeroing out import-only edges that genuinely carry coupling signal.

### Headline finding

Pearson `r = +0.136` between `Q_unweighted` and `Q_weighted` across the 27 projects. The two formulations are nearly independent signals. Distribution detail:

- Mean delta: `+0.094`. Weighted Q is on average higher than unweighted Q.
- Median delta: `+0.113`.
- Range: `-0.214` (msgspec) to `+0.251` (rich). Very wide.

### Project-level rank shifts

The largest rank movements cluster into two distinguishable patterns. (Full table in `bench/call_weighted_modularity_results.md`.)

**Moved up under weighted Q** (call traffic aligns with the import-graph communities):

| project | un. rank | w. rank | calls/edge | reading |
| --- | ---: | ---: | ---: | --- |
| mkdocs | 20 | 7 | 11.97 | dense intra-community dispatch |
| rich | 17 | 5 | 2.75 | call patterns stay inside renderer/console boundaries |
| pytest | 23 | 12 | 2.84 | plugin and fixture pipelines tight within their packages |
| archy | 12 | 2 | 2.74 | small enough that everything stays in-community |
| botocore | 19 | 9 | 3.45 | per-service modules call mostly into themselves |

**Moved down under weighted Q** (call traffic crosses community boundaries):

| project | un. rank | w. rank | calls/edge | reading |
| --- | ---: | ---: | ---: | --- |
| msgspec | 3 | 27 | 2.67 | community detection found tiny clusters; calls cross them |
| pygments | 6 | 25 | 17.90 | lexers all call the shared token/formatter machinery, crossing communities |
| sqlalchemy | 9 | 26 | 7.35 | ORM core <-> dialect/engine traffic crosses the import-defined communities |
| httpx | 8 | 21 | 4.31 | client/transport/auth crosstalk dominates intra-cluster calls |
| numpy | 10 | 22 | 52.68 | famously small-core, broad call surface; calls cross every boundary |

### Orthogonality picture vs the other axes

| signal | r against `Q_weighted` (normalized) | r against `Q_unweighted` (from sec 16) |
| --- | ---: | ---: |
| modularity_unweighted | +0.136 | n/a |
| acyclicity | +0.217 | +0.423 |
| depth | -0.397 | -0.576 |
| equality | +0.044 | -0.344 |
| complexity | -0.015 | very low for cc_mean too |

Weighted Q is **substantially more orthogonal** to the other axes than unweighted Q. All four cross-axis correlations drop in absolute value. (This is one of the points that favored ship-as-axis; it's not enough to outweigh the directionality argument below.)

## Why the empirics don't justify replacing unweighted Q

The v0.20 promotion of `cc_mean` to a score axis was a layup: cross-population directionality unambiguous, refactoring action canonical, signal additive (didn't change what the existing four axes meant).

Call-weighted Q fails the analogous tests:

1. **Direction is contested across the population.** numpy's drop from rank 10 to rank 22 reflects its small-core / broad-call shape, which is *intentional* and widely considered well-architected. sqlalchemy's drop reflects a deliberate ORM-core / dialect-adapter split where adapters call into the core a lot. Penalizing these designs as a headline-score reduction is not defensible.
2. **Refactoring action is unclear.** "Reduce cross-community call traffic" implies either reorganizing packages to follow call patterns (rare in mature projects) or reducing the number of cross-community calls (inlining, duplication, both bad practice).
3. **Replacement changes the meaning of the axis.** Trends in `.archy/history.jsonl` would break in a way that's not just "shifted by a constant" but "now measuring something different." The OECD versioning machinery handles axis additions cleanly; it handles axis redefinitions less cleanly.

## Why a parallel diagnostic (Path B) is the right shape

The value of the weighted signal lies in the *gap* between it and the unweighted signal, not in either value alone. Replacing one with the other destroys the gap. Treating the weighted value as a separate score axis loses the comparative-signal property and is subject to the same directionality objection.

A parallel diagnostic captures every interpretive use case listed above while:

- not changing the score formula,
- not breaking trend continuity,
- not requiring users to re-baseline,
- giving users and agents both numbers to read and judge contextually.

## Implementation summary

Code changes are small. The full diff is in this PR.

- `src/archy/score.py`: new `compute_modularity_weighted()` (matches `compute_modularity`'s tuple shape for drop-in interchangeability); two new fields on `ScoreInputs` (`raw_modularity_weighted`, `modularity_weighted_community_count`). `compute_score` calls the new function; the `overall` formula is unchanged.
- `src/archy/cli.py`: new helper `_call_weighted_modularity_prose()` that maps the gap to one of the three direction phrases; new line in `_score_to_text` immediately following the unweighted modularity line. `_score_to_dict` exposes the two new input fields.
- `tests/test_score.py`: four new tests covering the fallback (no call counts matches unweighted), input-graph immutability, weight-attribute read-through, and the vacuous trivial-graph case.
- `bench/call_weighted_modularity.py`: the experiment script. Keep for future re-runs when the bench changes.
- `bench/call_weighted_modularity_results.md`: captured raw numbers as of 2026-05-16.

No new MCP wire shape: `archy_score`'s `ScoreInputs` is the same Pydantic model and the new fields are picked up automatically as defaulted ints/floats.

## What we'll watch for

Decision principle: if the new diagnostic field is not referenced in any user-facing artifact (bug reports, blog posts, third-party tooling) over the next ~3 releases, that's evidence the signal isn't useful enough to keep. Plan to remove it if so rather than letting unused diagnostics accumulate.

## What this analysis does not settle

- The 10-expert ranking study from `AXIS_REVIEW.md` remains the cleanest way to resolve direction-contested signals. Out of scope here.
- Type-hint coverage as a candidate 6th axis (`AXIS_REVIEW.md`'s stronger recommendation) is unaffected by this analysis. The two paths run in parallel.
- The equality-axis redesign (`gini(per_function_cc)` replacing `gini(out_degree)`) is also unaffected.
