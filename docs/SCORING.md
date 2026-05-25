# Scoring

`archy score` reduces an import graph to a single number in `[0, 1]` by
combining five sub-metrics that each capture an independent structural
property of the codebase: **modularity**, **acyclicity**, **depth**,
**equality**, and **complexity**. The five are aggregated by geometric mean.

This document explains what each sub-metric measures, the exact formula
archy uses, why that formula was chosen, and how to read the output. The
implementation lives in [`src/archy/score.py`](../src/archy/score.py).

The first four sub-metrics plus the geometric-mean aggregator follow
sentrux's [`quality-signal-design.md`][sentrux-design]. The fifth axis
(complexity) was promoted from a v0.17 diagnostic to a score axis in
v0.20 after the then-27-project benchmark showed it is the most
orthogonal signal archy has ever measured against the existing four
(max `|r| = 0.197` at the time; `|r| = 0.159` on the current
28-project bench after pytorch was added); the validation evidence
lives in
[`docs/research/RESEARCH_METRICS.md` section 17](research/RESEARCH_METRICS.md).
archy defers sentrux's redundancy axis; see
[Deferred metrics](#deferred-metrics) below.

> **v0.20 score-shape change.** Adding a fifth axis means absolute
> `overall` scores shift slightly on every project. The four pre-v0.20
> sub-axis values are unchanged. Existing `.archy/history.jsonl`
> trends will show a one-step discontinuity at the v0.20 upgrade
> commit; record a fresh baseline (`archy score --record`) after
> upgrading to anchor the new series. See
> [Score-shape versioning](#score-shape-versioning).

## Design goals

1. **Every axis must be independent.** Two graphs can have identical
   modularity but very different depth; identical depth but very
   different fan-out concentration. A single number that conflates them
   is uninformative. The five sub-metrics are chosen so that improving
   one does not mechanically improve the others.
2. **The aggregate must be hard to game.** A weak score on any axis
   should pull the overall down sharply. Improving the overall should
   require improving every axis. This is what motivates geometric mean
   over arithmetic mean (see [Aggregation](#aggregation)).
3. **Computation must be static.** archy never runs the code under
   analysis. Every sub-metric is derivable from the import graph alone.

## Sub-metrics

Each sub-metric is normalized into `[0, 1]` so that 1.0 is best. The
raw, un-normalized value is preserved on `Score.inputs` for diagnostics.

### Modularity

> **What it measures:** how cleanly the graph decomposes into
> communities of modules that depend on each other more than on
> outsiders.

archy partitions the graph using NetworkX's
[`greedy_modularity_communities`][nx-greedy], which implements the
[Clauset–Newman–Moore][cnm-paper] greedy agglomeration: start with each
node in its own community and repeatedly merge the pair whose merger
most increases Newman's Q. archy then evaluates Newman's Q on that
partition.

For directed graphs, Newman's Q is:

```
Q = (1 / m) * Σ_ij [ A_ij - (k_out_i * k_in_j / m) ] * δ(c_i, c_j)
```

where `m` is the edge count, `A_ij` is the adjacency matrix, `k_out_i`
and `k_in_j` are the out- and in-degrees, and `δ(c_i, c_j) = 1` iff
nodes `i` and `j` are in the same community. Q compares the observed
intra-community edge density to what a random graph with the same
degree sequence would produce; values significantly above zero indicate
real community structure. The canonical theoretical range is
`[-1/2, 1]`; positive values are typical for real networks.[^q-range]

archy normalizes:

```
modularity = clamp01((Q + 0.5) / 1.5)
```

This maps the canonical `[-0.5, 1.0]` range onto `[0, 1]`.

**What moves it:**

- **Up:** splitting a high-traffic module so its dependents cluster
  with their actual collaborators rather than all funnelling through
  one node.
- **Down:** adding a cross-cutting import that bridges two otherwise
  separate communities (e.g., a utility module pulled in everywhere).

**Caveats:**

- Greedy modularity maximization is a heuristic; it does not always
  find the global maximum. For archy's typical input sizes (single-
  digit thousands of modules) the heuristic is stable enough that
  Q changes track real structural changes, not solver noise.
- Modularity has a known [resolution limit][resolution-limit]: it
  cannot detect communities smaller than roughly `√m`. For very small
  codebases this metric saturates at 1.0 and stops being informative.

**Call-weighted Q diagnostic.** The same algorithm is rerun on a graph
whose edges are weighted by `call_count` (with weight=1 fallback for
import-only edges), producing a second raw Q value reported alongside
the unweighted one on `archy score`. The *gap* between unweighted and
weighted Q is the load-bearing signal:

- gap `> +0.05`: calls amplify community structure (the package layout
  matches how the code actually runs).
- gap `< -0.05`: calls cross community boundaries (the package layout
  is aspirational; runtime usage doesn't respect it).
- gap roughly zero: calls track the community structure.

This is a diagnostic, not a score axis. The empirical analysis and the
rejected "ship as axis replacement" alternative are in
[`CALL_WEIGHTED_Q_EMPIRICS.md`](research/CALL_WEIGHTED_Q_EMPIRICS.md). The gap
is the unique signal among archy's outputs that compares two views of
the same codebase (import structure vs call structure) rather than
looking at one view in isolation.

### Acyclicity

> **What it measures:** what fraction of the codebase sits inside a
> cycle.

archy detects cycles via NetworkX's strongly-connected-components
machinery, which uses [Tarjan's SCC algorithm][tarjan-scc] (linear
time, single DFS). Any SCC of size ≥ 2 is, by definition, a cycle:
every node in an SCC reaches every other node, so any pair forms a
loop.[^scc-cycle] A self-looped singleton (a module that imports
itself) is also counted as a cycle.

```
tangle_ratio = nodes_in_cycles / total_nodes
acyclicity   = 1 - tangle_ratio
```

This is the [Structure101 "Tangle"][structure101-xs] formulation:
the metric reads as a *fraction of code in cyclic regions*, not a
count of cycles. It has the property that a small isolated cycle in a
50-module codebase reads very differently from the same cycle in a
5-module codebase, which the older `1 / (1 + cycle_count)` form did
not capture (it gave 0.5 either way).

**What moves it:**

- **Up:** breaking a cycle, usually by extracting a shared interface
  module that both cyclic participants depend on instead of each
  other; or growing the codebase with cycle-free additions, which
  shrinks the existing tangle's share.
- **Down:** introducing any edge that closes a loop, or pulling more
  modules into an existing SCC (a 3-node SCC becoming 5 nodes is a
  bigger tangle).

**Caveats:**

- archy counts SCCs, not elementary cycles. One SCC containing five
  modules counts as one cycle for `cycle_count`, but those five
  modules contribute 5 to the tangle ratio's numerator, so larger
  SCCs do dent the score more.
- The metric is sensitive to graph size by construction. This is the
  intended behavior, but it does mean the same architectural pattern
  scores differently in a small codebase than a large one - see the
  empirical correlation results below.
- The diagnostic `cycle_count` is preserved on `Score.inputs` for
  backwards compatibility and as a quick "how many independent
  groups need fixing" stat.

### Depth

> **What it measures:** the length of the longest dependency chain.

archy first builds the [condensation][dag-condensation] of the import
graph - every SCC collapses to a single super-node - then computes the
longest path in the resulting DAG via NetworkX's
[`dag_longest_path_length`][nx-dag-longest], a topological-order DP
that runs in linear time.[^longest-path]

```
depth = 1 / (1 + max_depth / 8)
```

The 8-module midpoint is a tunable taste choice inherited from
sentrux: a chain of 8 modules gives a depth score of 0.5. Below ~4 the
metric saturates near 1.0; above ~16 it asymptotes to 0.

**What moves it:**

- **Up:** flattening a chain (`A → B → C → D` becoming `A → B`,
  `A → C`, `A → D` if the intermediates were just pass-throughs).
- **Down:** introducing layered indirection where a direct dependency
  would have done.

**Caveats:**

- Depth is independent of modularity by construction: a graph can
  have perfect community structure and a 50-deep chain, or a flat
  graph with no community structure at all. That's the point - these
  axes catch different pathologies.
- Condensing first means a single giant SCC reads as `depth = 0` (one
  node, no edges) even though it's pathological. Acyclicity already
  penalizes that case, so we don't double-count.

### Equality

> **What it measures:** how evenly fan-out is distributed across
> modules. A "god module" with very high out-degree relative to its
> peers drags this down.

archy computes the [Gini coefficient][gini-wiki] of the out-degree
distribution. Gini is the standard tool for inequality measurement and
ranges from 0 (every module has identical out-degree) to approaching 1
(one module emits all edges).[^gini-bounds]

Using the sorted formula archy implements:

```
G = Σ_{i=1..n} (2i - n - 1) * x_i / (n * Σ x_i)        # x sorted ascending
equality = 1 - G
```

This is the standard Brown / sorted-rank Gini formula and is
equivalent to `1 - 2 * (area under the Lorenz curve)`.[^gini-formula]

**What moves it:**

- **Up:** splitting a high-fan-out module so its outgoing edges
  distribute across smaller modules with focused responsibilities.
- **Down:** adding more imports to an already-fat module ("just one
  more thing in `utils.py`").

**Caveats:**

- This is currently a **proxy** for the metric we actually want. The
  long-term target is `gini(per_function_cyclomatic_complexity)` -
  inequality across function complexity, not module fan-out. Module-
  level Gini is computable from the import graph alone; per-function
  CC requires AST-level analysis. archy ships per-function CC as of
  v0.17 (and uses the project-wide `cc_mean` as the basis for the
  Complexity axis below); swapping the equality axis to use
  `gini(per_function_cc)` is still on the roadmap (see
  [`docs/ROADMAP.md`](ROADMAP.md)).
- For very small graphs (< ~10 modules) Gini is noisy and a single
  utility module can swing the score significantly.

### Complexity

> **What it measures:** how branch-heavy the average function is. A
> codebase whose typical function carries lots of conditional logic
> drags this down.

archy computes per-function McCabe cyclomatic complexity over the
tree-sitter AST (see [`src/archy/complexity.py`](../src/archy/complexity.py))
and aggregates to a project-wide mean (`cc_mean`). The mean is then
mapped linearly to `[0, 1]` with the floor at the theoretical minimum
(every function has one branch-free path) and the ceiling at nine:

```
complexity = 1 - clamp((cc_mean - 1) / 8, 0, 1)
```

Anchor points from the 28-project benchmark
([`RESEARCH_METRICS.md` section 17](research/RESEARCH_METRICS.md)):

- mkdocs at `cc_mean = 1.77` -> `complexity = 0.904`
- archy at `cc_mean = 3.73` -> `complexity = 0.659`
- msgspec at `cc_mean = 5.33` -> `complexity = 0.459`

A graph with zero functions (a project of only empty `__init__.py`
files) returns `1.0` vacuously, matching the convention the other axes
use for empty inputs. The same `1.0` floor applies to projects with
fewer than 20 functions: `cc_mean` is statistically unstable on tiny
inputs and one branchy dispatcher can pull the mean well above 4 even
when surrounding code is healthy.

**Why a linear floor-to-nine mapping.** The bench distribution sits in
`[1.77, 5.33]`. Linearly mapping `[1, 9]` to `[1, 0]` spreads that
entire range across roughly `[0.90, 0.46]`, which gives the axis room
to discriminate without bottoming out at 0 for any real Python project.
A floor of 1 (rather than 0) is what McCabe's definition implies: even
a function with no branches has cyclomatic complexity 1.

The divisor was widened from `/5` (v0.20) to `/8` (v0.23) after the
original calibration drove the geomean to 0.000 on realistic backends
whose `cc_mean` lands in `[6, 9)` (validator-heavy or parser-heavy
codebases). Under the old slope, a single axis at 0 zeroed the entire
score regardless of how strong the other four axes were; the wider
divisor keeps the bottom band discriminative while preserving the
ordering of the bench distribution.

**Why mean and not max.** Project-wide `cc_max` is dominated by
single dispatcher / parser functions and does not correlate with the
overall coding style: setuptools shows `cc_max = 340` (one extreme
function) alongside `cc_mean = 2.91` (typical restraint elsewhere).
The mean is the stable signal; `cc_max` is preserved as a diagnostic
on `Score.inputs`. The [`archy hotspots`](research/RESEARCH_METRICS.md)
refactor-priority ranking uses `cc_sum` (total CC per file) rather
than `cc_max`, because a file with twenty branchy functions is a
bigger refactoring target than one with a single high-CC dispatcher
(see `src/archy/hotspots.py` module docstring).

**What moves it:**

- **Up:** reducing the branching in the typical function (extracting
  guard clauses, replacing nested conditionals with dispatch, breaking
  multi-purpose functions into single-purpose ones).
- **Down:** dropping a new highly-branched function into the codebase,
  or growing existing functions with additional conditional paths.

**Caveats:**

- `assert` is not counted (matches radon-default; `assert` compiles
  out at `-O`).
- `try` / `else` / `finally` / `with` / `async with` are not branches
  (matches radon).
- **Cognitive complexity** (Sonar / Campbell 2017) is *not* what this
  axis measures; cognitive complexity needs nesting-depth bookkeeping
  the single-pass walker doesn't track. McCabe was chosen because the
  walker is one pass over the existing AST and the metric is
  well-defined.
- Lambda expressions don't get their own row but their internal
  branches count toward the containing function (consistent with
  radon, inconsistent with pyan).

The full implementation surface (the walker, the per-module
aggregates, the bench distribution) lives in
[`docs/research/RESEARCH_METRICS.md` section 17](research/RESEARCH_METRICS.md).

## Aggregation

```
overall = (modularity * acyclicity * depth * equality * complexity) ^ (1/5)
```

Geometric mean, not arithmetic. The reason is the
[Nash][nash-bargaining] / Cobb–Douglas characterization: the geometric
mean is, up to monotone transforms, the unique aggregator that is
simultaneously *Pareto-optimal*, *symmetric*, and *independent of
irrelevant alternatives* - i.e., independent across the indicators
being aggregated.[^geomean-axioms] More practically, geometric mean is
**non-compensatory**: a low value on one axis cannot be hidden by
piling up high values on the others, the way it can with arithmetic
mean.[^non-compensatory] If any sub-metric is 0, the overall is 0; a
sub-metric at 0.2 caps the overall around 0.72 even if the other four
are perfect.

This is the property that makes the score hard to game. Improving
`overall` requires improving every axis, and adding cosmetic edges to
boost one sub-metric will tend to degrade another (e.g., bridging
communities to flatten depth lowers modularity).

### Score-shape versioning

The score is intentionally not a stable absolute number across archy
versions. Every axis addition (and every normalization change) shifts
`overall` for every project. The OECD Handbook recommends being explicit
about this: "Composite indicators should be revised over time as
information improves, but the discontinuity should be flagged so trends
remain interpretable."[^oecd-handbook] Adding the Complexity axis in
v0.20 is one such discontinuity:

- The four pre-v0.20 sub-axis values are unchanged.
- `overall` shifts by a multiplicative factor of `complexity ^ (1/5) /
  (existing_geomean ^ (1/4)) * existing_geomean`: in practice, for
  projects whose complexity score is close to their old `overall`, the
  number barely moves; for projects with unusually high or low
  complexity, the number shifts more.
- `.archy/history.jsonl` rows written by archy < 0.20 are still
  readable; their `complexity` field reads as `null` and the trend
  table renders `-` for that column. Record a fresh baseline after
  upgrading to anchor the new five-axis series:

  ```bash
  archy score . --record
  ```

Widening the Complexity divisor in v0.23 (`/5` -> `/8`) is the second
discontinuity:

- The four non-complexity sub-axes are unchanged.
- `complexity` scores lift uniformly. Bench-wide range shifted from
  [0.133, 0.846] under `/5` to [0.458, 0.903] under `/8`; ordering
  preserved.
- `overall` lifts by a multiplicative factor of
  `(complexity_new / complexity_old) ^ (1/5)`. Bench-wide that's
  +0.04 to +0.09; the largest absolute gainer is msgspec
  (0.310 -> 0.397). The trend table renders both eras side by side
  without re-baselining since the historical `complexity` field is
  still meaningful in isolation; record a fresh baseline if you want
  the new-era number to anchor future comparisons.
- The motivation was practical: real-world repos with `cc_mean >= 6`
  (validator-heavy or parser-heavy backends) were zeroing the entire
  geomean on a single axis under `/5`, masking otherwise-healthy
  structure. `/8` keeps the ordering of the bench distribution while
  extending the discriminative range out to `cc_mean = 9`.

The same precedent will apply to any future axis addition or
normalization change. See [`docs/ROADMAP.md`](ROADMAP.md) for the
next candidates.

### Empirical axis independence

The geometric-mean argument assumes the five axes are independent.
The OECD Handbook on Constructing Composite Indicators recommends
testing this empirically: pairwise correlation between sub-indicators
above ~`|r| = 0.7` is treated as a "symptom of double counting."[^oecd-handbook]

Pairwise Pearson correlations on the 28-project benchmark spanning
small CLI tools to very large frameworks (msgspec at 10 modules,
django at 902, dagster at 801, pytorch at 2,252), captured 2026-05-18
against pinned SHAs (see [`bench/projects.yaml`](../bench/projects.yaml)):

| Pair                     |    `r` |
| ------------------------ | -----: |
| modularity ↔ acyclicity  | +0.383 |
| modularity ↔ depth       | -0.617 |
| modularity ↔ equality    | -0.389 |
| modularity ↔ complexity  | +0.110 |
| acyclicity ↔ depth       | -0.581 |
| acyclicity ↔ equality    | -0.458 |
| acyclicity ↔ complexity  | +0.068 |
| depth ↔ equality         | +0.359 |
| depth ↔ complexity       | -0.052 |
| equality ↔ complexity    | +0.159 |

All ten pairs are below `|r| = 0.7`, the OECD-conventional threshold
for treating sub-indicators as redundant. **Two of ten sit at
"moderate" coupling (`|r| ∈ [0.5, 0.7]`)**, both involving the
original four axes (modularity↔depth and acyclicity↔depth). The four
pairs involving `complexity` are all weakly coupled (`|r| ≤ 0.14`),
the empirical evidence behind its v0.20 promotion from diagnostic to
a fifth axis.

Concretely:

- **modularity ↔ depth at `-0.617`**: deeper graphs tend to have
  higher modularity. Plausible: in a deep DAG, communities form
  along the chain naturally, so longer chains give Newman's Q more
  structure to find. Tightened slightly with pytorch's addition
  (modularity 0.680 / depth 0.286), now the dominant moderate pair.
- **acyclicity ↔ depth at `-0.581`**: codebases with low acyclicity
  also tend to have low depth scores (longer chains). A graph that's
  mostly inside a few SCCs has fewer free DAG hops to extend, but the
  SCC condensation it does have tends to be deep when the tangled
  mass dominates. Eased from `-0.651` after pytorch (acyclicity 0.423
  with depth 0.286 lands off-trend for this pair).
- **acyclicity ↔ equality at `-0.458`**: stable since the prior
  bench. Tangled hub modules pull both axes down, but the effect is
  not strong enough to cross into the moderate band.
- **modularity ↔ acyclicity at `+0.383`**: the wide-and-shallow plugin
  shapes (pygments, setuptools) score high on both. Loosened from
  `+0.454` after pytorch (high modularity, mid acyclicity).
- **depth ↔ equality at `+0.359`**: weak and stable across sample
  expansions.
- **All four complexity pairs at `|r| ≤ 0.16`**: empirically the most
  orthogonal axis archy has. Branchiness within functions does not
  correlate with the import-graph shape one way or the other; this is
  what makes the geometric mean's non-compensatory property bite
  hardest on the complexity axis.

These don't break the geometric-mean argument - none cross the OECD
threshold - but the design language in
[`docs/LEARNINGS.md`](LEARNINGS.md) ("the only way to game the score
is to actually improve every dimension") was stronger than the data
supports. The empirical study in
[`docs/research/SCORE_SHAPE_REDESIGN_EMPIRICS.md`](research/SCORE_SHAPE_REDESIGN_EMPIRICS.md)
(2026-05) tested seven acyclicity reformulations, three depth
reformulations, and six aggregator alternatives against the 28-project
bench and concluded against any axis change: the candidate
combinations that reach `0/10` moderate pairs all require a depth-axis
redesign that conflates "new long chain" with "new SCC" (fails the
OECD actionability gate), and every such combination shakes the
project leaderboard substantially (Spearman ρ vs v0.23 in
`[0.53, 0.64]`). The honest reading the empirics support is twofold:
(1) the two moderate pairs both involve `depth`, and under every
tested aggregator `depth` correlates only weakly with `overall`
(`|r| ≤ 0.187`), so the OECD breach is operationally cosmetic - depth
optimization is essentially toothless against the score for the
unrelated reason that depth barely moves the score; (2) the other
four axes (modularity, acyclicity, equality, complexity) all have
non-trivial leverage on `overall` and the geometric mean's
non-compensatory property does bite on them. Complexity remains the
most orthogonal axis (max `|r| ≤ 0.14` against any other axis), so
moving it does not mechanically move anything else.

The flip side is that this couples the *diagnostic* signal usefully:
a regression in just one axis is a stronger localized signal than a
regression that smears across moderately-coupled axes. When using
the breakdown to localize a drop, a single-axis movement is the
sharpest pointer.

## Interpreting a score

There is no universal "good architecture score." The systematic-
mapping literature on software-metric thresholds is explicit that
thresholds must be derived empirically from a benchmark population
rather than asserted from intuition.[^thresholds-empirical] The bands
below are derived from archy's 28-project benchmark spanning small
CLI tools (click, msgspec) to very large frameworks (pytorch at
2,252 modules, django, numpy, sqlalchemy, dagster), with diversity
across web / async / scientific / ML
/ ORM / plugin-host / devops / workflow-orchestration / build-tooling
/ syntax-highlighting / generated-SDK domains. Pinned SHAs in
[`bench/projects.yaml`](../bench/projects.yaml); raw output in
[`bench/results.md`](../bench/results.md). Captured 2026-05-18
against archy v0.24.0 (added pytorch to widen the size envelope to
2,252 modules; see [Score-shape versioning](#score-shape-versioning)):

| Project       | SHA       | Modules | Edges | Overall | Modularity | Acyclicity | Depth | Equality | Complexity |
| ------------- | --------- | ------: | ----: | ------: | ---------: | ---------: | ----: | -------: | ---------: |
| pygments      | `6fe2c31` |     342 |   834 |   0.663 |      0.565 |      1.000 | 0.500 |    0.676 |      0.668 |
| boto3         | `81a86c9` |      39 |    71 |   0.653 |      0.689 |      0.897 | 0.533 |    0.417 |      0.861 |
| numpy         | `0a1ed72` |     424 |  1342 |   0.647 |      0.596 |      0.745 | 0.571 |    0.521 |      0.856 |
| mkdocs        | `2862536` |      61 |   177 |   0.639 |      0.520 |      0.787 | 0.615 |    0.469 |      0.903 |
| starlette     | `7793b92` |      34 |   114 |   0.613 |      0.458 |      0.588 | 0.727 |    0.547 |      0.811 |
| anyio         | `bcb2db6` |      42 |   158 |   0.607 |      0.499 |      0.643 | 0.615 |    0.480 |      0.872 |
| scrapy        | `5223dbe` |     172 |   858 |   0.603 |      0.521 |      0.640 | 0.533 |    0.552 |      0.814 |
| setuptools    | `84ed591` |     317 |   592 |   0.586 |      0.766 |      0.931 | 0.348 |    0.367 |      0.762 |
| botocore      | `2b64927` |      76 |   257 |   0.581 |      0.563 |      0.934 | 0.348 |    0.439 |      0.823 |
| pytest        | `856da14` |      69 |   374 |   0.568 |      0.478 |      0.710 | 0.471 |    0.490 |      0.757 |
| archy         | `v0.22.0` |      19 |    43 |   0.564 |      0.512 |      1.000 | 0.667 |    0.273 |      0.615 |
| datasette     | `aa84fe0` |      59 |   180 |   0.557 |      0.534 |      0.831 | 0.471 |    0.442 |      0.578 |
| fastapi       | `e89a37e` |      48 |   114 |   0.549 |      0.522 |      0.771 | 0.615 |    0.300 |      0.671 |
| sqlalchemy    | `1e1c008` |     255 |  2550 |   0.546 |      0.571 |      0.388 | 0.471 |    0.568 |      0.819 |
| requests      | `b684dcb` |      19 |    73 |   0.545 |      0.429 |      0.579 | 0.571 |    0.469 |      0.722 |
| rich          | `46cebbb` |     100 |   421 |   0.544 |      0.524 |      0.450 | 0.667 |    0.430 |      0.705 |
| pydantic      | `5c63f86` |     104 |   496 |   0.541 |      0.636 |      0.385 | 0.615 |    0.459 |      0.673 |
| django        | `4d455ae` |     902 |  3274 |   0.521 |      0.640 |      0.754 | 0.267 |    0.399 |      0.746 |
| mypy          | `e53693b` |     195 |  1105 |   0.521 |      0.571 |      0.815 | 0.286 |    0.464 |      0.620 |
| scikit-learn  | `13f20d7` |     638 |  3866 |   0.518 |      0.525 |      0.824 | 0.222 |    0.477 |      0.810 |
| flask         | `7374c85` |      24 |    94 |   0.517 |      0.484 |      0.208 | 0.800 |    0.569 |      0.802 |
| dagster       | `8e7f318` |     801 |  6273 |   0.515 |      0.575 |      0.400 | 0.471 |    0.416 |      0.803 |
| httpx         | `b5addb6` |      23 |    87 |   0.515 |      0.482 |      0.261 | 0.667 |    0.550 |      0.782 |
| click         | `fc6c7c4` |      17 |    60 |   0.511 |      0.451 |      0.235 | 0.800 |    0.575 |      0.717 |
| ansible       | `b7c0900` |     581 |  2145 |   0.495 |      0.614 |      0.769 | 0.286 |    0.383 |      0.573 |
| aiohttp       | `e8f4371` |      52 |   312 |   0.494 |      0.530 |      0.173 | 0.727 |    0.563 |      0.785 |
| pytorch       | `9a8a62c` |    2252 | 13238 |   0.478 |      0.680 |      0.423 | 0.286 |    0.431 |      0.703 |
| msgspec       | `3b2543b` |      10 |    20 |   0.397 |      0.427 |      0.100 | 0.889 |    0.570 |      0.458 |

Bands derived from this distribution (median 0.544, IQR ~0.517-0.581):

| Overall     | What's typically true at this score                                                                                                                | Examples from the benchmark                                       |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `≥ 0.60`    | Top tier. Usually a deliberate architectural pattern combined with restrained per-function complexity: wide-and-shallow lexer registry over a small core (pygments), hand-written facade over an auto-generated SDK (boto3), layered scientific code with disciplined small functions (numpy), pluggy-driven plugin host with short callbacks (mkdocs), structured async primitives (anyio). | pygments, boto3, numpy, mkdocs, starlette, anyio, scrapy |
| `0.55–0.60` | Healthy. Mature Python libraries that ace one or two axes. setuptools tops modularity (0.766) but middling depth (0.348) and equality (0.367) keep it mid-band; archy is acyclic (1.000) with low equality (0.273). | setuptools, botocore, pytest, archy, datasette |
| `0.50–0.55` | "Typical." The bulk of mature Python libraries. Usually two weak axes that drag the geomean down: large frameworks tend to land here because depth and equality both suffer from "many modules, one or two hubs." | fastapi, sqlalchemy, requests, rich, pydantic, django, mypy, scikit-learn, flask, dagster, httpx, click |
| `0.45–0.50` | At least one axis severely weak. Examine the breakdown. ansible at 0.495 is dragged by low depth (0.286) and equality (0.383); aiohttp at 0.494 by acyclicity (0.173); pytorch at 0.478, despite the highest modularity in the bench after setuptools (0.680), is dragged by depth (0.286) and acyclicity (0.423) since its 2,252 modules and 13,238 edges produce both a flat condensation and a few large SCCs. | ansible, aiohttp, pytorch |
| `< 0.40`    | Multiple axes weak, or one axis below 0.15. Worth investigating before adding features. msgspec sits at 0.397 in v0.23: acyclicity 0.100 and complexity 0.458 still bite (the 10-module SCC and the 5.33 cc_mean are two independent symptoms of the same compact-but-dense codebase). | msgspec |

Two things to note when reading these bands:

1. **Acyclicity is the highest-variance axis** across the benchmark,
   spanning 0.10 (msgspec, where most of the 10-module surface is in
   one SCC) to 1.0 (archy, no cycles at all). Whether a project lands
   in the "healthy" or "typical" band is largely determined by how
   concentrated its cycles are: datasette at 0.881 sits high because
   its cycles cover only a small fraction of its 59 modules; flask at
   0.208 falls into the lower band because a large share of its 24
   modules are in cycles. The metric correctly penalizes
   tangle-relative-to-size rather than raw cycle count.
2. **Modularity has its own well-established literature band.** Newman
   (2006) and follow-up work on real-world networks consistently find
   that raw `Q ∈ [0.3, 0.7]` indicates strong community
   structure.[^modularity-band] After archy's `(Q + 0.5) / 1.5`
   normalization that maps to a normalized modularity of
   **0.53–0.80**. Scores in that band are healthy; below ~0.50
   suggests the graph has no clear community structure, above ~0.80
   is unusual outside very small or already-decomposed codebases.
3. **The top scorers are shape-driven, not size-driven.** pygments
   (0.663, the v0.23 benchmark high) and setuptools (0.766 modularity,
   the benchmark high on that axis) both score the way they do because
   of their *layout*, not their quality per se. pygments is a
   wide-and-shallow registry: a small core (`pygments.lexer`,
   `pygments.formatter`, `pygments.token`) and ~300 nearly-independent
   lexer modules that import from the core but rarely from each other.
   That structure is acyclic by construction (acyclicity 1.000),
   spreads fan-out evenly (equality 0.676, the benchmark high), and
   produces tight communities (one per language family). setuptools
   gets a similar boost from its `command/` plugin directory plus
   vendored distutils: each command module is a near-leaf, so
   modularity is high but depth (0.348) and equality (0.367) suffer
   because the few core modules carry disproportionate fan-in. The
   takeaway: scores in the `0.55+` band often reflect a deliberate
   plugin/registry pattern, and "improving" a non-plugin codebase by
   chasing top-band numbers is the wrong inference.
4. **Complexity is a meaningful but no-longer-dominant axis.** Under
   the v0.23 `/8` divisor it spans 0.458 (msgspec) to 0.903 (mkdocs),
   a 0.45 spread - narrower than the v0.20-v0.22 `/5` range
   (0.133-0.845) but still wider than depth or equality. The widened
   divisor was a deliberate response to real-world repos whose
   `cc_mean` lands in `[6, 9)` zeroing the entire geomean under the
   old slope; see [Score-shape versioning](#score-shape-versioning).
   Complexity is still doing work the other four axes were not:
   pygments at `cc_mean = 3.66` carries a 0.668 complexity score
   under the new slope, well below its near-perfect acyclicity and
   equality, and that gap is the signal the axis was promoted to
   capture. Any project where the existing four look good but
   complexity is the weakest is the kind of "compact but branchy"
   codebase the v0.20 promotion was designed to surface.
5. **Auto-generated SDKs score well on archy's axes for trivial
   reasons.** boto3 (0.653) and botocore (0.581) both rank above
   average. The bulk of botocore is JSON-driven at runtime, so the
   *static* Python surface archy sees is small and orderly: a handful
   of generic client/session/serializer modules. boto3 is even more
   extreme - 39 modules of hand-written facade over botocore. archy's
   import graph correctly says "this Python source is well-organized,"
   but that's a property of the code-generation strategy, not a
   judgment to generalize to non-generated codebases.

When reading the breakdown, look at the **lowest** sub-metric first -
geometric mean means that's the one bottlenecking the overall. The
`Score.inputs` payload exposes the raw, un-normalized values
(`raw_modularity`, `cycle_count`, `max_depth`, `raw_gini`,
`community_count`) so you can ground the normalized number in the
actual graph property.

The most useful comparison is almost always **a project against
itself over time**, not against these bands or other projects. That's
what `archy score --record` and `archy trend` are for.

## Deferred metrics

sentrux ships a fifth sub-metric, **redundancy**: dead functions plus
duplicate functions over total functions. archy intentionally omits
it.

The empirical case for omission is documented in
[`docs/research/RESEARCH_METRICS.md`](research/RESEARCH_METRICS.md) §12: vulture 2.16
was run on 27 popular Python projects in 2026-05; default-confidence
findings ranged from 10 (msgspec) to 2,017 (django), and 15 random
findings spot-checked on FastAPI, pytest, and Django were all
(15/15) false positives - driven by Python idioms like Pydantic
validators, pytest fixtures, decorator-registered route handlers,
and Django's `global_settings.py` string-lookup pattern. Static
dead-code detection in Python produces too much noise to fold into a
quality score without inverting the signal.

Duplicate-function detection (AST-shape hashing) has lower
empirical FP rates and remains a candidate, but is similarly
deferred until evidence on real codebases is available.

If `archy redundancy` does ship, the geometric mean exponent will
widen from 1/5 to 1/6 and absolute scores will shift downward;
trends within a single project will remain comparable via
`archy score --record`, but cross-version comparisons will need a
note. The same caveat applies to any future axis (e.g., a NCCD or
type-hint-coverage axis) - a published scoring model that adds
indicators over time has to either pin a baseline or accept that
absolute scores aren't comparable across versions, per the OECD
Handbook's guidance.[^oecd-handbook] The v0.20 promotion of `cc_mean`
to the `complexity` axis is the first time archy has exercised this
versioning discipline; see [Score-shape versioning](#score-shape-versioning).

**Call edges (v0.16.0, diagnostic).** Call-graph extraction shipped
as a *diagnostic only*: per-edge `kinds`, `call_lines`, `call_count`
attributes plus `inputs.total_calls` / `inputs.calls_per_edge` on
`archy score`'s output. Not folded into the geometric mean. The
28-project benchmark shows `calls_per_edge` is orthogonal to every
existing axis (max `|r| = 0.229` against modularity, acyclicity,
depth, equality, propagation_cost). The original plan was to promote
it to a score axis at a deliberate version boundary; that plan was
reviewed after the v0.20 cc_mean promotion and **rejected** on
directionality, actionability, and discriminant-validity grounds (see
[`AXIS_REVIEW.md`](research/AXIS_REVIEW.md)). The call data continues to be
useful as a diagnostic on `archy score`'s output, as a refinement
candidate for the modularity axis (call-weighted Newman Q), and as
agent navigation data via the `archy_graph_*` MCP tools. Detailed
empirics in [`docs/research/RESEARCH_METRICS.md` §16](research/RESEARCH_METRICS.md).

**Cyclomatic complexity (v0.17.0 diagnostic, v0.20.0 promoted).**
Per-function McCabe CC shipped in v0.17.0 as a *diagnostic only*:
per-module `function_count` / `cc_sum` / `cc_max` / `cc_mean` on every
internal graph node, plus project-wide aggregates on `archy score`'s
`inputs`. Not deferred any longer: in v0.20.0 `cc_mean` was promoted
to the fifth score axis as `complexity = 1 - clamp((cc_mean - 1) / 5,
0, 1)`; the divisor was widened from `/5` to `/8` in v0.23.0 after
the original calibration zeroed the geomean on validator/parser-heavy
backends whose `cc_mean` sits in `[6, 9)`. See the
[Complexity](#complexity) sub-metric section above for
the formula, anchor points, and what-moves-it discussion; the
diagnostic surface (per-module aggregates, `cc_max` for hotspots) is
unchanged. The Gini-of-CC redesign of the equality axis remains
on the roadmap as a separate question; the v0.20 promotion was
additive rather than a replacement so the equality redesign can land
later without undoing this work. Detailed empirics in
[`docs/research/RESEARCH_METRICS.md` §17](research/RESEARCH_METRICS.md).

## References

- Sentrux quality-signal design (the model archy follows): [sentrux/docs/quality-signal-design.md][sentrux-design]
- Newman, M. E. J. *Modularity and community structure in networks.* PNAS, 2006. See also [Wikipedia: Modularity (networks)][modularity-wiki].
- Clauset, A., Newman, M. E. J., Moore, C. *Finding community structure in very large networks.* Phys. Rev. E, 2004. - The greedy algorithm `greedy_modularity_communities` implements. [Paper PDF][cnm-paper-pdf].
- Tarjan, R. E. *Depth-first search and linear graph algorithms.* SIAM J. Computing, 1972. See also [Wikipedia: Tarjan's SCC algorithm][tarjan-scc].
- Gini, C. (1912). Gini coefficient overview: [Wikipedia: Gini coefficient][gini-wiki].
- Nash, J. F. *The Bargaining Problem.* Econometrica, 1950. - Geometric-mean axiomatization. See also [Mazziotta–Pareto on non-compensatory composite indices][mazziotta-pareto] for the practical case.
- NetworkX docs: [`greedy_modularity_communities`][nx-greedy], [`dag_longest_path_length`][nx-dag-longest], [`condensation`][nx-condensation].

[sentrux-design]: https://github.com/sentrux/sentrux/blob/main/docs/quality-signal-design.md
[nx-greedy]: https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.community.modularity_max.greedy_modularity_communities.html
[nx-dag-longest]: https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.dag.dag_longest_path.html
[nx-condensation]: https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.components.condensation.html
[cnm-paper]: https://arxiv.org/abs/cond-mat/0408187
[cnm-paper-pdf]: https://arxiv.org/pdf/cond-mat/0408187
[modularity-wiki]: https://en.wikipedia.org/wiki/Modularity_(networks)
[tarjan-scc]: https://en.wikipedia.org/wiki/Tarjan%27s_strongly_connected_components_algorithm
[dag-condensation]: https://en.wikipedia.org/wiki/Strongly_connected_component#Definitions
[gini-wiki]: https://en.wikipedia.org/wiki/Gini_coefficient
[nash-bargaining]: https://en.wikipedia.org/wiki/Nash_bargaining_game
[mazziotta-pareto]: https://www.istat.it/en/files/2013/12/Rivista2013_Mazziotta_Pareto.pdf
[resolution-limit]: https://en.wikipedia.org/wiki/Modularity_(networks)#Resolution_limit
[structure101-xs]: https://structure101.com/static-content/pages/resources/documents/XS-MeasurementFramework.pdf

[^q-range]: Newman's Q is bounded above by 1 and below by `-1/2` for any partition; positive values indicate intra-community density above the random-graph null model. See [Modularity (networks)][modularity-wiki].
[^scc-cycle]: An SCC of size ≥ 2 is a cycle by definition: every vertex reaches every other, so any pair forms a directed loop. Tarjan's algorithm finds all SCCs in `O(V + E)`.
[^longest-path]: Longest path is NP-hard on general graphs but linear on DAGs via topological-order DP. archy condenses first, so the DP is always on a DAG.
[^gini-bounds]: Gini ranges over `[0, 1)` for non-degenerate finite distributions: 0 is perfect equality, and the upper bound approaches 1 as concentration approaches a single recipient.
[^gini-formula]: This is the standard "Brown" or sorted-rank formula, equivalent to `1 - 2 * AUC(Lorenz)` to the precision of trapezoidal integration.
[^geomean-axioms]: For composite-index theory specifically, see Mazziotta & Pareto, *Methods for Constructing Composite Indices*, ISTAT, 2013, which formalizes why geometric-mean variants are the standard non-compensatory aggregator.
[^non-compensatory]: "Non-compensatory" means a deficit on one indicator cannot be fully offset by surplus on another - the property that prevents gaming a single dimension. Arithmetic mean is fully compensatory; geometric mean is partially non-compensatory; the lexicographic minimum is fully non-compensatory.
[^thresholds-empirical]: See *Techniques for Calculating Software Product Metrics Threshold Values: A Systematic Mapping Study*, [Applied Sciences, 2021](https://www.mdpi.com/2076-3417/11/23/11377). The literature consensus is that universal thresholds across projects are unreliable; thresholds derived from a benchmark population (e.g., Mori et al., >3000 systems) outperform expert-asserted ones in fault detection.
[^modularity-band]: Newman, *Modularity and community structure in networks*, [PNAS 2006](https://www.pnas.org/doi/10.1073/pnas.0601602103). The `Q ∈ [0.3, 0.7]` band has been replicated across biological networks (e.g., E. coli transcription `Qm = 0.54`, C. elegans synaptic network `Qm = 0.54`, human signal-transduction `Qm = 0.58`).
[^oecd-handbook]: OECD / JRC, [*Handbook on Constructing Composite Indicators: Methodology and User Guide*](https://www.oecd.org/content/dam/oecd/en/publications/reports/2008/08/handbook-on-constructing-composite-indicators-methodology-and-user-guide_g1gh9301/9789264043466-en.pdf), 2008. Recommends pairwise correlation analysis as a redundancy / double-counting check on sub-indicators; the `|r| > 0.7` rule of thumb is widely used in this literature. Also covers the geometric-mean rationale and the comparability-over-time caveats when adding indicators to an existing index.
