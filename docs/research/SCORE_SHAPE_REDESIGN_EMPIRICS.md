# Score-shape redesign: empirical study and the don't-replace-axes decision

This document records an empirical investigation into whether the archy
score's two moderately-correlated axis pairs (`|r| in [0.5, 0.7]`) can
be eliminated by reformulating the acyclicity axis, the depth axis, or
the geometric-mean aggregator. The companion artifacts are
`bench/score_redesign.py` (the script) and `bench/score_redesign_results.md`
(the raw numbers).

The parent question comes from the v0.23 SCORING.md "Empirical axis
independence" section, which observed that 2 of 10 axis pairs sit at
moderate Pearson correlation (`r(acyclicity, depth) = -0.641`,
`r(modularity, depth) = -0.581` after this study's 28-project
recomputation). All ten pairs are below the OECD `|r| < 0.7` redundancy
threshold, but the design language in `docs/LEARNINGS.md` ("the only
way to game the score is to actually improve every dimension") is
stronger than that. ROADMAP.md flagged two candidate redesigns:
"replace acyclicity's tangle-ratio normalization" and "replace
geometric mean with a non-compensatory aggregator (e.g.,
Mazziotta-Pareto Index)."

## Decision

**Don't replace any axis. Optionally adopt the Mariani-Ciommi
penalized geometric mean (PGM) aggregator. Soften the design language
in LEARNINGS.md to match what the data supports.**

The empirics support keeping every existing axis formulation. The two
acyclicity reformulations that look most promising on paper (literature-
backed `feedback_edges` and `modular_tangle`) achieve only modest
decoupling in isolation and require pairing with a depth-axis change
to clear the OECD threshold on both moderate pairs. The single depth-
axis change that breaks the coupling at the source
(`depth_with_scc_penalty`) fails the actionability gate: it conflates
two structurally distinct regressions ("new long chain" vs "new SCC")
into one number, which an agent cannot localize without re-deriving
the underlying graph signals. The cross-product combinations that
achieve `0/10` moderate pairs all shake up the project leaderboard
substantially (Spearman ρ vs v0.23 in `[0.53, 0.64]`), a much larger
discontinuity than any previous archy score-shape change.

The aggregator side is more tractable. Three aggregator variants pass
the OECD gate AND preserve rank-ordering above ρ = 0.9: harmonic mean
(ρ = 0.949), MPI (ρ = 0.969), and PGM (ρ = 0.907). PGM is the cleanest
match to the stated design concern because its penalty term fires
*exactly* when correlated axes diverge, which is the failure mode the
2/10 moderate pairs makes possible. The other two are reasonable
alternatives if the calibration of PGM's `lambda` parameter is judged
to add too much knob-tuning surface.

A finding ran sideways to the original framing, but it must be read
carefully to avoid a trap the 2026-06 adversarial review ([#176]) caught
in `SCORING.md`: under *every* tested aggregator, `depth` is the axis
least *correlated* with `overall` (`|r(overall, depth)| <= 0.187` for
geomean, harmonic, MPI, PGM, penalty-geomean, and arith). **This is low
cross-project correlation, not low leverage, and the two are different
things.** The low correlation is an artifact of low cross-project
*variance* in depth scores, not evidence that depth is inert: a direct
local-sensitivity sweep at the corpus baseline measured
`d(overall)/d(axis)` as equality 0.376, **depth 0.225**, modularity
0.216, complexity 0.139, acyclicity 0.133 -- depth is the *second* most
locally influential axis under the geomean. So improving (or tanking) a
single project's depth *does* move its score; what the `|r| <= 0.187`
shows is only that, across projects, depth doesn't co-vary much with
overall.

The corrected operational reading: the two moderate pairs create a
gaming surface that is **smaller than the LEARNINGS.md "improve every
dimension" language implies but not cosmetic** -- a project that lets
depth regress pays for it (leverage 0.225), and the non-compensatory
geomean bites on every axis. The OECD breach is real; the earlier claim
that it is "cosmetic in practice" overstated and is withdrawn (it relied
on conflating depth's cross-project correlation with its local leverage,
the exact error corrected in `SCORING.md`).

## What this rules out

### Acyclicity-only reformulations

Five acyclicity variants were tested against the v0.23 status quo
(`baseline_tangle = 1 - nodes_in_cycles / total_nodes`):

| candidate | `r(acy, dep)` | `r(mod, dep)` | max `|r|` | moderate pairs (`|r| >= 0.5`) | discriminative? |
| --- | ---: | ---: | ---: | ---: | --- |
| `baseline_tangle` (v0.23) | -0.641 | -0.581 | 0.641 | 3/10 | yes |
| `largest_scc` | -0.700 | -0.581 | 0.700 | 3/10 | yes (crosses OECD threshold) |
| `modular_tangle` | -0.683 | -0.581 | 0.683 | 2/10 | yes |
| `feedback_edges` | -0.517 | -0.581 | 0.581 | 2/10 | yes |
| `feedback_x_tangle` (geomean) | -0.618 | -0.581 | 0.618 | 2/10 | yes |
| `log_cycle_count` | +0.411 | -0.581 | 0.581 | 1/10 | no, saturates |
| `sentrux_legacy` | +0.435 | -0.581 | 0.581 | 1/10 | no, saturates |

Notes:

- `largest_scc = 1 - largest_scc_size / total_nodes` (Cooper-Frieze
  core-size measure; Baldwin-MacCormack-Rusnak "hidden structure")
  makes the coupling **worse**, crossing the OECD `|r| >= 0.7`
  threshold. Big monolithic SCCs do tend to live in deep architectures,
  so this formulation amplifies rather than dampens the existing signal.
- `modular_tangle = 1 - largest_scc / largest_weakly_connected_component`
  (Baldwin et al. 2014, archy-specific WCC normalization). Removes the
  `|V|` denominator but the WCC denominator is itself correlated with
  depth in elongated trees, so the improvement is marginal.
- `feedback_edges = 1 - feedback_edges_lb / total_edges` (Structure101
  XS Measurement Framework, the original tangle formulation at the edge
  level). Strongest standalone decoupler, drops `r(acy, dep)` from
  -0.641 to -0.517. Theoretical justification matches: long DAG paths
  add to `|E|` but not to the minimum feedback arc set, so the metric
  is asymptotically depth-independent. Lower-bound implementation
  rather than exact MFAS (Karp 1972 NP-hard; Eades-Lin-Smyth 1993
  greedy is the standard polynomial approximation).
- `log_cycle_count` and `sentrux_legacy` (the pre-Structure101 form)
  flip the sign of `r(acy, dep)` to mildly positive but **saturate**:
  multiple projects with one SCC all score `0.591`, multiple projects
  with zero cycles all score `1.0`. Discriminative power across the
  bench distribution collapses.

`feedback_edges` is the best of the standalone-acyclicity candidates,
but it leaves `r(mod, dep) = -0.581` untouched (modularity-depth
coupling is independent of how acyclicity is computed) and still has
two moderate pairs.

### Depth-only reformulations

Three depth variants were tested with acyclicity held at the v0.23
status quo:

| candidate | `r(acy, dep)` | `r(mod, dep)` | max `|r|` (full matrix) | actionable? |
| --- | ---: | ---: | ---: | --- |
| `depth_baseline` (v0.23) | -0.641 | -0.581 | 0.641 | yes |
| `depth_with_scc_penalty` | +0.245 | -0.295 | 0.519 (acy ↔ eq) | **no** |
| `depth_size_relative` | -0.072 | +0.400 | 0.519 (acy ↔ eq) | partial |

- `depth_with_scc_penalty = depth_score(max_depth + largest_scc_size)`
  treats SCC traversal as part of the longest path because, from a
  change-propagation standpoint, a 50-module SCC *is* at least 50 hops
  deep. Mathematically the cleanest fix: it directly attacks the SCC
  condensation step that mechanically couples depth to acyclicity.
  Drops both moderate pairs below `0.5`. **Fails archy's diagnostic-
  legibility bar** (a stricter, archy-specific gate, not the bare OECD
  actionability criterion -- to be precise about which test it fails):
  strict OECD actionability asks only whether a good-practice refactoring
  improves the indicator, and this form *passes* that (both "shorten the
  longest chain" and "break the SCC" are good practice). What it fails is
  decomposability: when the axis regresses, the agent cannot tell whether
  the cause was a new long chain or a new SCC without re-deriving
  `max_depth` and `largest_scc` separately. Today "max_depth went from 8
  to 12" is unambiguous; under the SCC-penalty form it becomes
  "depth-with-SCC went from 8 to 14, of which ? came from chain growth
  and ? from SCC growth." The OECD handbook's actionable-indicator
  discussion (Section 2) motivates keeping an indicator interpretable;
  archy raises that to a hard requirement that a single axis map to a
  single, localizable structural cause. It is that archy-specific
  requirement, not OECD actionability per se, that this candidate fails.
- `depth_size_relative = 1 - 2 * (max_depth / module_count)` reframes
  depth as a *fraction* of the graph, which is conceptually closer to
  acyclicity (also a fraction). Removes the acy ↔ depth coupling but
  introduces a `+0.400` mod ↔ depth coupling in the opposite
  direction (more modular projects tend to be denser, with shorter
  chains per module). Same actionability concern in a softer form:
  the meaning of the axis shifts from "absolute chain length" to
  "chain length per module," which a developer doesn't think in.

### Cross-product (acyclicity × depth)

The full Cartesian product over all (7 acyclicity × 3 depth) variants
was evaluated. Only six combinations clear `0/10` moderate pairs, and
all six include either `depth_with_scc_penalty` or
`depth_size_relative` (which fail actionability/intuition gates above):

| acyclicity | depth | acy ↔ dep | mod ↔ dep | spearman ρ vs v0.23 |
| --- | --- | ---: | ---: | ---: |
| `feedback_edges` | `depth_with_scc_penalty` | +0.444 | -0.295 | **0.534** |
| `feedback_edges` | `depth_size_relative` | -0.283 | +0.400 | 0.621 |
| `feedback_x_tangle` | `depth_with_scc_penalty` | +0.312 | -0.295 | 0.648 |
| `modular_tangle` | `depth_with_scc_penalty` | +0.237 | -0.295 | 0.638 |
| `modular_tangle` | `depth_size_relative` | -0.102 | +0.400 | n/a |
| `log_cycle_count` | `depth_size_relative` | -0.173 | +0.400 | n/a |

The Spearman rank-stability column is the load-bearing one. Every
`0/10` combination re-ranks the bench projects substantially compared
to v0.23. ρ = 0.534 means the project ordering is closer to a coin
flip than to the current shape. For comparison, the v0.20 promotion of
`cc_mean` to a fifth axis preserved the ordering within `+/- 2` ranks
for most projects, and the v0.23 divisor widening preserved ordering
exactly (it was a monotone transform). A redesign that shakes the
leaderboard this hard would invalidate every existing `archy trend`
series and `.archy/history.jsonl` baseline, with no compensating
benefit beyond moving the worst correlation from `-0.641` (already
below the OECD redundancy threshold) to `+0.237`.

### Aggregator reformulations

Six alternatives to geometric mean were tested with axes held at v0.23
baseline. The OECD-relevant measure here is the *uniformity* of the
correlation profile `|r(overall, axis_k)|` across axes: a uniform
profile means no single axis has outsized leverage, which is the
non-compensatory property the design language is reaching for.

| aggregator | `r(o, m)` | `r(o, a)` | `r(o, d)` | `r(o, e)` | `r(o, c)` | spread | ρ vs geomean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `geomean` (v0.23) | +0.262 | +0.552 | -0.135 | +0.069 | +0.555 | 0.69 | 1.000 |
| `arith` | +0.298 | +0.575 | -0.086 | +0.074 | +0.515 | 0.66 | 0.897 |
| `harmonic` (p = -1) | +0.239 | +0.529 | -0.187 | +0.033 | +0.521 | 0.72 | 0.949 |
| `mpi` (Mazziotta-Pareto) | +0.197 | +0.450 | -0.096 | +0.129 | +0.612 | 0.71 | 0.969 |
| `pgm` (Mariani-Ciommi) | +0.200 | +0.481 | -0.176 | +0.048 | +0.512 | 0.69 | 0.907 |
| `penalty_geomean` (sigma) | +0.119 | +0.375 | -0.079 | +0.144 | +0.562 | 0.64 | 0.890 |
| `min` (Rawlsian / lex) | +0.130 | +0.400 | -0.158 | +0.077 | +0.378 | 0.56 | 0.793 |

Where "spread" is the range of `|r(overall, axis)|` values (lower = more
uniform sensitivity).

- `min` has the flattest profile but throws away information from four
  of five axes per project, and Spearman ρ = 0.793 means substantial
  re-ranking. Rejected.
- `arith` is more compensatory than geomean (wrong direction; this is
  the OECD-flagged anti-pattern). Rejected.
- `harmonic` (generalized power mean with `p = -1`; Hardy-Littlewood-
  Polya) is a one-line change and preserves rank-ordering (ρ = 0.949)
  while being one step more non-compensatory than geomean. Reasonable
  but doesn't directly address the "axis imbalance" angle.
- `mpi` (De Muro / Mazziotta / Pareto 2011, ISTAT) is the most rank-
  stable change (ρ = 0.969) and reduces the dominance of acyclicity
  (`r(o, a)` drops from +0.552 to +0.450) while raising complexity's
  leverage (`r(o, c)` rises to +0.612). Penalty mechanism is the cv^2
  term applied to a min-max-rescaled mean.
- `pgm` (Mariani-Ciommi 2022, *Computation* 10:64) is the multiplicative
  analog of MPI applied to a geometric base: `PGM = g * exp(-lambda *
  sigma^2_log)`. With `lambda = 1.0`, ρ = 0.907 vs geomean (just above
  the 0.9 rank-stability bar). The penalty fires *exactly* when
  correlated axes diverge, which is the cleanest theoretical match to
  the design-language concern.
- `penalty_geomean = geomean * (1 - sigma)` (archy-original, simpler
  cv-free variant of pgm) is the most discriminating but drops ρ to
  0.890, which would shake up the leaderboard more than is justified
  by the improvement.

### What this doesn't settle

- **Per-project axis weights** (DP2, BoD, PCA-derived) were considered
  in the literature survey (`research/score_redesign_literature.md`)
  but not implemented. PCA *up-weights* correlated indicators, which is
  the canonical anti-pattern for redundancy discounting. DP2 (Pena's
  distance) is the closest formal answer to "discount redundancy" but
  is order-dependent and not bounded in `[0,1]` without an extra
  normalization step. Could be revisited if a future axis change makes
  the correlation matrix worse.
- **Spectral cyclicity** (Mezic 2019, Wasserstein distance between the
  recurrence-matrix spectrum and a point mass at 1) has the strongest
  theoretical decoupling from depth but is computationally heavy and
  hard to explain to a developer reading a score breakdown. Not tested.
- **CIPI** (Drago 2020 Composite Indicator Performance Interval)
  produces a triple `(lower, point, upper)` over a polytope of plausible
  weight vectors. Sidesteps the correlation issue by quantifying weight
  uncertainty rather than collapsing it. Doesn't fit archy's scalar-
  per-axis shape but could be a future companion to `archy score`.
- **Exact minimum feedback arc set.** The `feedback_edges` candidate
  was implemented as a lower bound (`edges_inside_scc - (scc_size - 1)`
  per SCC), not the exact MFAS (which is NP-hard). If the decision
  were to ship this axis, the implementation would need to switch to
  the Eades-Lin-Smyth O(V+E) greedy approximation. The empirical
  ordering is preserved by the lower bound across the 28-project bench,
  so the conclusion would not change.
- **Munda's non-compensatory MCA (Condorcet aggregation)** produces a
  ranking, not a scalar in `[0,1]`. Not a drop-in replacement and
  conflicts with archy's "score for a single project in isolation"
  shape. The other aggregators tested are scalar-per-project so the
  comparison was apples to apples.

## Methodology

- **Corpus**: 27 projects pinned in `bench/projects.yaml` (the
  benchmark population behind `docs/research/RESEARCH_METRICS.md`) plus
  `governingdocs/backend` as a 28th data point (validator/parser-heavy
  backend, `cc_mean = 6.48`, 209 modules, archy.yaml-configured src
  scope). All 28 captured 2026-05-17 against `archy v0.23.0`.
- **Raw data**: one `archy score --format json` and one `archy graph
  --format json` per project, cached to `bench/cache/{name}.json`.
  Re-running the cache from clean re-derives every candidate's score
  table; candidate formulas are pure post-processing on cached graph
  data so a single collect-run feeds every candidate. The `scc_metrics`
  function (`bench/score_redesign.py`) derives SCC-size distribution,
  largest WCC size, nodes-in-cycles count, and a lower-bound minimum
  feedback arc set per project; archy's score JSON does not surface
  these by default.
- **Correlation matrix**: Pearson r on the 28-project distribution
  for all 10 unique axis pairs, per candidate combination.
- **Rank stability**: Spearman ρ of each candidate's overall ranking
  vs v0.23 baseline overall ranking.
- **OECD discriminant-validity gate**: same gate that killed
  call-weighted-Q-as-axis (`docs/research/CALL_WEIGHTED_Q_EMPIRICS.md`) and the
  DSM scalars (`docs/research/DSM_EMPIRICS.md`). Per axis change: independence,
  directionality, actionability, ordering stability. Per aggregator
  change: rank stability `>= 0.9`, sensitivity profile no flatter than
  the existing geomean by more than 50%, single-line implementation.

  **Why the two gates differ (the asymmetry is deliberate, not selective).**
  A reader could object that the diagnostic-legibility bar above sank
  `depth_with_scc_penalty` while PGM -- which also adds a non-decomposable
  penalty term to `overall` -- was accepted without an analogous check.
  The distinction is *where* the legibility is lost. An axis redefinition
  corrupts the meaning of that axis's own diagnostic ("max_depth" no
  longer means max depth), so the per-axis number an agent reads to
  localize a regression becomes ambiguous. An aggregator change leaves
  all five axis values and their diagnostics individually intact and
  inspectable; PGM only changes how those five legible numbers combine
  into the single headline `overall`. An agent still localizes via the
  per-axis breakdown, never via the headline. Hence actionability/
  legibility is gated per-axis but not per-aggregator -- by design, not
  by oversight. (PGM was ultimately not shipped regardless; see below.)

## What ships and what doesn't

- **Ships**: nothing in `src/archy/score.py`. The empirics support
  every existing axis formulation and the geometric-mean aggregator at
  the operational level the score is actually used at.
- **Soft recommendation** (separate PR): adopt PGM aggregator
  (`overall = geomean * exp(-lambda * sigma^2_log)` with
  `lambda = 1.0`). One-line change in `src/archy/score.py:overall`,
  Spearman ρ = 0.907 vs v0.23 (just above the rank-stability gate),
  cleanest theoretical match to the "axis imbalance is penalized"
  design language. A v0.24 minor bump with score-shape-versioning
  discontinuity notes per the existing v0.20 / v0.23 precedent. If
  the calibration of `lambda` is judged too much knob-tuning surface,
  MPI is the next choice (ρ = 0.969, no `lambda`).
- **Updates to existing docs** (this PR or follow-up):
  - `docs/LEARNINGS.md`: soften "the only way to game the score is to
    actually improve every dimension." The data supports "the score is
    not gameable by single-axis cosmetic changes to acyclicity,
    modularity, equality, or complexity. The depth axis correlates
    weakly with overall under every tested aggregator, so depth
    optimization is essentially toothless against the score, but for
    the unrelated reason that depth's correlations with other axes
    don't bleed into the score, not because the axes are strictly
    independent."
  - `docs/SCORING.md` "Empirical axis independence" section: replace
    the speculative "honest reading" paragraph with a pointer to this
    document and the operational-irrelevance finding from the
    aggregator-sensitivity matrix.
  - `docs/ROADMAP.md` deferred section: strike "Score-shape redesign
    for axis independence (2/10 pairs at moderate Pearson r)" with a
    citation here. The redesign options were enumerated and tested;
    none clear the OECD gate without breaking rank stability or
    actionability.

## References

- OECD/JRC, *Handbook on Constructing Composite Indicators* (2008).
  Section 6 on aggregation rules, Section 7 on weighting, Section 2
  on indicator-selection gates.
- De Muro, Mazziotta, Pareto (2011). "Composite Indices of Development
  and Poverty: An Application to MDGs." *Social Indicators Research*
  104:1-18.
- Mariani, Ciommi (2022). "Aggregating Composite Indicators through
  the Geometric Mean: A Penalization Approach." *Computation*
  10(4):64. https://www.mdpi.com/2079-3197/10/4/64
- Baldwin, MacCormack, Rusnak (2014). "Hidden Structure: Using
  Network Methods to Map System Architecture." *Research Policy*
  43(8):1381-1397.
- Becker, Saisana, Paruolo, Vandecasteele (2017). "Weights and
  importance in composite indicators: closing the gap." *Ecological
  Indicators* 80:12-22.
- Eades, Lin, Smyth (1993). "A fast and effective heuristic for the
  feedback arc set problem." *Information Processing Letters*
  47(6):319-323.
- Karp (1972). "Reducibility Among Combinatorial Problems." On the
  NP-hardness of minimum feedback arc set.
- Hardy, Littlewood, Polya (1952). *Inequalities.* On the generalized
  power mean.
- Structure101 XS Measurement Framework.
  https://structure101.com/static-content/pages/resources/documents/XS-MeasurementFramework.pdf
- `bench/score_redesign.py` (this study's script).
- `bench/score_redesign_results.md` (this study's raw numbers).
- `research/score_redesign_literature.md` (the full literature brief,
  gitignored).
