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

Pairwise Pearson correlations on a 27-project benchmark spanning
small CLI tools to very large frameworks (msgspec at 10 modules,
django at 902, dagster at 801), captured 2026-05-13 against pinned
SHAs (see [`bench/projects.yaml`](../bench/projects.yaml)):

| Pair                    |    `r` |
| ----------------------- | -----: |
| modularity ↔ acyclicity | +0.423 |
| modularity ↔ depth      | -0.576 |
| modularity ↔ equality   | -0.344 |
| acyclicity ↔ depth      | -0.653 |
| acyclicity ↔ equality   | -0.453 |
| depth ↔ equality        | +0.359 |

All six pairs are below `|r| = 0.7`, the OECD-conventional threshold
for treating sub-indicators as redundant. **Two of six sit at
"moderate" coupling (`|r| ∈ [0.5, 0.7]`)**, down from four at the
23-project sample: adding boto3, botocore, pygments, and setuptools
(several with very high acyclicity but middling equality) regressed
the acyclicity↔equality coupling out of the moderate band. Concretely:

- **acyclicity ↔ depth at `-0.653`**: codebases with low acyclicity
  also tend to have low depth scores (longer chains). A graph that's
  mostly inside a few SCCs has fewer free DAG hops to extend, but the
  SCC condensation it does have tends to be deep when the tangled
  mass dominates.
- **modularity ↔ depth at `-0.576`**: deeper graphs tend to have
  higher modularity. Plausible: in a deep DAG, communities form
  along the chain naturally, so longer chains give Newman's Q more
  structure to find.
- **acyclicity ↔ equality at `-0.453`**: regressed from `-0.652` at
  n=23. The four projects added in the n=27 refresh break the "hub
  modules in large SCC pull both axes down" pattern -
  boto3/botocore/pygments/setuptools are largely acyclic but still
  have concentrated fan-out, so the coupling looks weaker once
  they're in the sample.
- **modularity ↔ acyclicity at `+0.423`**: strengthened from
  `+0.257` at n=23. The wide-and-shallow plugin shapes (pygments,
  setuptools) score high on both, pulling the correlation up.
- **depth ↔ equality at `+0.359`**: weak and stable across sample
  expansions.

These don't break the geometric-mean argument - none cross the OECD
threshold - but the design language in
[`docs/LEARNINGS.md`](LEARNINGS.md) ("the only way to game the score
is to actually improve every dimension") is stronger than the data
supports. The honest reading is: improving any axis tends to nudge
its moderately-coupled neighbors in the same direction, which means
the geometric mean is slightly easier to move via single-axis
optimization than a strict-independence design would predict.

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
below are derived from archy's 27-project benchmark spanning small
CLI tools (click, msgspec) to very large frameworks (django, numpy,
sqlalchemy, dagster), with diversity across web / async / scientific
/ ORM / plugin-host / devops / workflow-orchestration / build-tooling
/ syntax-highlighting / generated-SDK domains. Pinned SHAs in
[`bench/projects.yaml`](../bench/projects.yaml); raw output in
[`bench/results.md`](../bench/results.md). Captured 2026-05-13:

| Project       | SHA       | Modules | Edges | Overall | Modularity | Acyclicity | Depth | Equality |
| ------------- | --------- | ------: | ----: | ------: | ---------: | ---------: | ----: | -------: |
| pygments      | `6fe2c31` |     342 |   834 |   0.661 |      0.565 |      1.000 | 0.500 |    0.676 |
| numpy         | `0a1ed72` |     424 |  1192 |   0.611 |      0.609 |      0.745 | 0.571 |    0.539 |
| boto3         | `81a86c9` |      39 |    71 |   0.609 |      0.689 |      0.897 | 0.533 |    0.417 |
| mkdocs        | `2862536` |      61 |   175 |   0.589 |      0.526 |      0.787 | 0.615 |    0.472 |
| starlette     | `7793b92` |      34 |   114 |   0.572 |      0.458 |      0.588 | 0.727 |    0.547 |
| archy         | `v0.13.1` |      14 |    30 |   0.561 |      0.471 |      1.000 | 0.667 |    0.314 |
| scrapy        | `5223dbe` |     172 |   858 |   0.560 |      0.521 |      0.640 | 0.533 |    0.552 |
| datasette     | `aa84fe0` |      59 |   172 |   0.555 |      0.551 |      0.881 | 0.444 |    0.441 |
| anyio         | `bcb2db6` |      42 |   158 |   0.555 |      0.499 |      0.643 | 0.615 |    0.480 |
| setuptools    | `84ed591` |     317 |   592 |   0.549 |      0.766 |      0.931 | 0.348 |    0.367 |
| botocore      | `2b64927` |      76 |   255 |   0.534 |      0.566 |      0.934 | 0.348 |    0.443 |
| pytest        | `856da14` |      69 |   373 |   0.529 |      0.478 |      0.710 | 0.471 |    0.491 |
| fastapi       | `e89a37e` |      48 |   114 |   0.522 |      0.522 |      0.771 | 0.615 |    0.300 |
| pydantic      | `5c63f86` |     104 |   496 |   0.513 |      0.636 |      0.385 | 0.615 |    0.459 |
| rich          | `46cebbb` |     100 |   420 |   0.510 |      0.524 |      0.450 | 0.667 |    0.431 |
| requests      | `b684dcb` |      19 |    73 |   0.508 |      0.429 |      0.579 | 0.571 |    0.469 |
| mypy          | `e53693b` |     195 |  1104 |   0.499 |      0.571 |      0.815 | 0.286 |    0.465 |
| sqlalchemy    | `1e1c008` |     255 |  2536 |   0.492 |      0.565 |      0.388 | 0.471 |    0.568 |
| ansible       | `b7c0900` |     581 |  2144 |   0.477 |      0.614 |      0.769 | 0.286 |    0.383 |
| django        | `4d455ae` |     902 |  3234 |   0.477 |      0.641 |      0.754 | 0.267 |    0.401 |
| click         | `fc6c7c4` |      17 |    60 |   0.470 |      0.451 |      0.235 | 0.800 |    0.575 |
| httpx         | `b5addb6` |      23 |    87 |   0.463 |      0.482 |      0.261 | 0.667 |    0.550 |
| flask         | `7374c85` |      24 |    94 |   0.463 |      0.484 |      0.208 | 0.800 |    0.569 |
| dagster       | `8e7f318` |     801 |  6255 |   0.461 |      0.578 |      0.400 | 0.471 |    0.416 |
| scikit-learn  | `13f20d7` |     638 |  3857 |   0.459 |      0.523 |      0.826 | 0.216 |    0.476 |
| aiohttp       | `e8f4371` |      52 |   312 |   0.440 |      0.530 |      0.173 | 0.727 |    0.563 |
| msgspec       | `3b2543b` |      10 |    19 |   0.384 |      0.440 |      0.100 | 0.889 |    0.553 |

Bands derived from this distribution (median 0.513, IQR roughly
0.47–0.55):

| Overall     | What's typically true at this score                                                                                                                | Examples from the benchmark                                       |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `≥ 0.58`    | Top tier. Usually a deliberate architectural pattern: wide-and-shallow lexer registry over a small core (pygments), layered scientific code (numpy), thin hand-written layer over an auto-generated SDK surface (boto3), pluggy-driven decomposition (mkdocs). | pygments, numpy, boto3, mkdocs                                    |
| `0.50–0.58` | Healthy. The bulk of mature Python libraries. Usually one weak axis, often acyclicity below 0.5. setuptools is interesting here: highest modularity in the benchmark (0.766) and very high acyclicity (0.931), but middling depth (0.348) and weak equality (0.367) keep it mid-band. | starlette, scrapy, datasette, anyio, setuptools, archy, botocore, pytest, fastapi, pydantic, rich, requests |
| `0.45–0.50` | "Typical." Often one of: many small cycles (high tangle), or a long chain pulling depth low. Most very-large frameworks land here. dagster is a notable exception within the band: high modularity (0.577) and the highest edge density in the benchmark (7.8 edges/module), but acyclicity 0.400 and equality 0.416 pull it down. | mypy, sqlalchemy, ansible, django, click, httpx, flask, dagster, scikit-learn |
| `0.40–0.45` | At least one axis severely weak. Examine the breakdown.                                                                                            | aiohttp                                                           |
| `< 0.40`    | Multiple axes weak, or one axis below 0.15. Worth investigating before adding features.                                                            | msgspec (acyclicity 0.10 dominates a 10-module surface)           |

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
   (0.661, the benchmark high) and setuptools (0.766 modularity, the
   benchmark high on that axis) both score the way they do because of
   their *layout*, not their quality per se. pygments is a wide-and-
   shallow registry: a small core (`pygments.lexer`,
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
   chasing pygments-like numbers is the wrong inference.
4. **Auto-generated SDKs score well on archy's axes for trivial
   reasons.** boto3 (0.609) and botocore (0.534) both rank above
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
[`docs/RESEARCH_METRICS.md`](RESEARCH_METRICS.md) §12: vulture 2.16
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
widen from 1/4 to 1/5 and absolute scores will shift downward;
trends within a single project will remain comparable via
`archy score --record`, but cross-version comparisons will need a
note. The same caveat applies to any future axis (e.g., a NCCD or
type-hint-coverage axis) - a published scoring model that adds
indicators over time has to either pin a baseline or accept that
absolute scores aren't comparable across versions, per the OECD
Handbook's guidance.[^oecd-handbook]

**Call edges (v0.16.0).** Call-graph extraction shipped as a
*diagnostic only*: per-edge `kinds`, `call_lines`, `call_count`
attributes plus `inputs.total_calls` / `inputs.calls_per_edge` on
`archy score`'s output. Not folded into the geometric mean. The
27-project benchmark shows `calls_per_edge` is highly orthogonal to
every existing axis (max `|r| = 0.229` against modularity,
acyclicity, depth, equality, propagation_cost), so the signal earns
a follow-up promotion. The shape of that promotion - weighted Newman
Q replacing the unweighted modularity computation, or a new fifth
axis based on call concentration - is unresolved and deferred to a
deliberate version boundary along with a SCORING.md band refresh.
Detailed empirics in
[`docs/RESEARCH_METRICS.md` §16](RESEARCH_METRICS.md).

**Cyclomatic complexity (v0.17.0).** Per-function McCabe CC ships as
a *diagnostic only*: per-module `function_count` / `cc_sum` / `cc_max`
/ `cc_mean` on every internal graph node, plus project-wide aggregates
on `archy score`'s `inputs`. Not folded into the geometric mean. The
27-project benchmark shows `cc_mean` is the most orthogonal signal
archy has ever measured: max `|r| = 0.197` against modularity,
acyclicity, depth, equality, propagation_cost, and calls_per_edge.
This makes CC the strongest candidate to date for score-axis
promotion - either as a new 5th axis (`1 - normalized_cc_mean`) or
as a redesign of the equality axis to use `gini(per_function_cc)`
instead of `gini(out_degree)`, the long-term target the Equality
section above already flags. Detailed empirics in
[`docs/RESEARCH_METRICS.md` §17](RESEARCH_METRICS.md).

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
