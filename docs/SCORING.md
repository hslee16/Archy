# Scoring

`archy score` reduces an import graph to a single number in `[0, 1]` by
combining four sub-metrics that each capture an independent structural
property of a directed graph: **modularity**, **acyclicity**, **depth**,
and **equality**. The four are aggregated by geometric mean.

This document explains what each sub-metric measures, the exact formula
archy uses, why that formula was chosen, and how to read the output. The
implementation lives in [`src/archy/score.py`](../src/archy/score.py).

The model - four-of-five sub-metrics plus geometric-mean aggregation -
follows sentrux's [`quality-signal-design.md`][sentrux-design]. archy
defers sentrux's fifth metric (redundancy); see
[Deferred metrics](#deferred-metrics) below.

## Design goals

1. **Every axis must be independent.** Two graphs can have identical
   modularity but very different depth; identical depth but very
   different fan-out concentration. A single number that conflates them
   is uninformative. The four sub-metrics are chosen so that improving
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

### Acyclicity

> **What it measures:** whether the graph is a DAG.

archy detects cycles via NetworkX's strongly-connected-components
machinery, which uses [Tarjan's SCC algorithm][tarjan-scc] (linear
time, single DFS). Any SCC of size ≥ 2 is, by definition, a cycle:
every node in an SCC reaches every other node, so any pair forms a
loop.[^scc-cycle] archy counts those SCCs.

```
acyclicity = 1 / (1 + cycle_count)
```

This is a sigmoid in `(0, 1]`: zero cycles gives 1.0, one cycle gives
0.5, two gives 0.33, etc. The penalty is steep on purpose - cycles
make build order undefined and change propagation unpredictable, so
even one cycle should visibly dent the score.

**What moves it:**

- **Up:** breaking a cycle, usually by extracting a shared interface
  module that both cyclic participants depend on instead of each
  other.
- **Down:** introducing any edge that closes a loop.

**Caveats:**

- archy counts SCCs, not elementary cycles. One SCC containing five
  modules is one cycle in this score, even though it may contain many
  elementary cycles. This is intentional: an SCC is the unit you have
  to break to restore acyclicity.

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
  CC requires AST-level analysis that archy plans to add but has not
  shipped (see [`docs/FUTURE.md`](FUTURE.md)).
- For very small graphs (< ~10 modules) Gini is noisy and a single
  utility module can swing the score significantly.

## Aggregation

```
overall = (modularity * acyclicity * depth * equality) ^ (1/4)
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
sub-metric at 0.2 caps the overall around 0.67 even if the other three
are perfect.

This is the property that makes the score hard to game. Improving
`overall` requires improving every axis, and adding cosmetic edges to
boost one sub-metric will tend to degrade another (e.g., bridging
communities to flatten depth lowers modularity).

### Empirical axis independence

The geometric-mean argument assumes the four axes are independent.
The OECD Handbook on Constructing Composite Indicators recommends
testing this empirically: pairwise correlation between sub-indicators
above ~`|r| = 0.7` is treated as a "symptom of double counting."[^oecd-handbook]

Pairwise Pearson correlations on the 9-library benchmark plus archy
itself (10 projects, fresh HEADs at 2026-05-10):

| Pair                    |    `r` |
| ----------------------- | -----: |
| modularity ↔ acyclicity | +0.117 |
| modularity ↔ depth      | -0.198 |
| modularity ↔ equality   | -0.441 |
| acyclicity ↔ depth      | +0.058 |
| acyclicity ↔ equality   | -0.063 |
| depth ↔ equality        | +0.526 |

Five of six pairs are below `|r| = 0.5`, comfortably under the OECD
redundancy threshold. The exception is **depth ↔ equality at
`r = +0.526`**, a moderate positive correlation. Recall that both
archy axes are inverted so that 1.0 is best: a low `depth` score
means a long chain, and a low `equality` score means concentrated
fan-out. So `r = +0.526` says: in this benchmark, graphs with longer
chains also tend to have more concentrated fan-out, and graphs with
shorter chains tend to have more even fan-out. The two axes are
*not* strictly independent - they're moderately coupled in real
Python code.

This doesn't break the geometric-mean argument (the OECD threshold
for double-counting is `|r| > 0.7`), but it's a real empirical
finding worth honest disclosure. Two axes at `r ≈ 0.5` partially
overlap, which means improving one nudges the other in the same
direction, which means the score is slightly easier to move via
single-axis optimization than the design language in
[`docs/LEARNINGS.md`](LEARNINGS.md) implies. Anyone using the
breakdown to localize regressions should keep this coupling in mind:
a `depth` regression with no `equality` movement is a stronger
signal than the same `depth` regression alongside an `equality`
regression.

## Interpreting a score

There is no universal "good architecture score." The systematic-
mapping literature on software-metric thresholds is explicit that
thresholds must be derived empirically from a benchmark population
rather than asserted from intuition.[^thresholds-empirical] The bands
below are derived from archy's own benchmark - nine widely-used Python
libraries (pydantic, fastapi, flask, pytest, requests, click, rich,
httpx, starlette) plus archy on archy, re-run 2026-05-10 against
fresh HEADs:

| Project   | SHA       | Overall | Modularity | Acyclicity | Depth | Equality |
| --------- | --------- | ------: | ---------: | ---------: | ----: | -------: |
| archy     | `02ce8f3` |   0.620 |      0.553 |      1.000 | 0.615 |    0.433 |
| flask     | `7374c85` |   0.576 |      0.484 |      0.500 | 0.800 |    0.569 |
| starlette | `7793b92` |   0.549 |      0.458 |      0.500 | 0.727 |    0.547 |
| click     | `fc6c7c4` |   0.513 |      0.451 |      0.333 | 0.800 |    0.575 |
| pydantic  | `bd8e63e` |   0.495 |      0.636 |      0.333 | 0.615 |    0.459 |
| httpx     | `b5addb6` |   0.493 |      0.482 |      0.333 | 0.667 |    0.550 |
| requests  | `e8d2c01` |   0.490 |      0.429 |      0.500 | 0.571 |    0.469 |
| rich      | `46cebbb` |   0.473 |      0.524 |      0.333 | 0.667 |    0.431 |
| fastapi   | `622b635` |   0.423 |      0.522 |      0.333 | 0.615 |    0.300 |
| pytest    | `09f969f` |   0.408 |      0.479 |      0.250 | 0.471 |    0.490 |

Bands derived from this distribution:

| Overall     | What's typically true at this score                                                                                      | Examples from the benchmark                |
| ----------- | ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------ |
| `≥ 0.60`    | Zero or one cycle, distributed fan-out. Hard to reach without deliberate architectural discipline.                       | archy (0.620)                              |
| `0.50–0.60` | Strong on three axes; usually one weak axis (typically acyclicity).                                                      | flask (0.576), starlette (0.549), click (0.513) |
| `0.40–0.50` | "Typical mature library." One or two cycles plus some fan-out concentration. Most production libraries land here.        | pydantic, httpx, requests, rich, fastapi, pytest (0.41–0.50) |
| `0.30–0.40` | At least one axis collapsing - 3+ cycles, severe god-module, or a 12+ deep chain.                                        | None in the benchmark.                     |
| `< 0.30`    | Multiple axes weak simultaneously. Worth investigating before adding features.                                           | None in the benchmark.                     |

Two things to note when reading these bands:

1. **Acyclicity dominates variance** in real-world Python code. From
   the benchmark, eight of nine libraries lose 0.5 or more on this
   axis to a single SCC. Adding one cycle drops acyclicity from 1.0 to
   0.5, which through the geometric mean caps the overall around
   `0.84 × (other axes)^(3/4)`. This is a deliberate design choice -
   cycles are the highest-signal pathology - but it means cross-
   project comparisons should always look at the breakdown, not just
   the overall.
2. **Modularity has its own well-established literature band.** Newman
   (2006) and follow-up work on real-world networks consistently find
   that raw `Q ∈ [0.3, 0.7]` indicates strong community
   structure.[^modularity-band] After archy's `(Q + 0.5) / 1.5`
   normalization that maps to a normalized modularity of
   **0.53–0.80**. Scores in that band are healthy; below ~0.50
   suggests the graph has no clear community structure, above ~0.80
   is unusual outside very small or already-decomposed codebases.

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
[`docs/RESEARCH_METRICS.md`](RESEARCH_METRICS.md) §12: vulture 2.16
was run on 14 popular Python projects in 2026-05; default-confidence
findings ranged from 32 (click) to 2,795 (sqlalchemy), and 15 random
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
widen from 1/4 to 1/5 and absolute scores will shift downward;
trends within a single project will remain comparable via
`archy score --record`, but cross-version comparisons will need a
note. The same caveat applies to any future axis (e.g., a NCCD or
type-hint-coverage axis) - a published scoring model that adds
indicators over time has to either pin a baseline or accept that
absolute scores aren't comparable across versions, per the OECD
Handbook's guidance.[^oecd-handbook]

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
