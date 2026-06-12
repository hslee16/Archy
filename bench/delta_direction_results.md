# Score-delta direction validation (issue #178)

Each row injects exactly one 2-cycle and asserts `acyclicity` strictly drops with `cycles.added == 1`, then reports the `overall` delta so per-edge dilution at scale stays visible. The break direction is asserted separately (see below) on a graph built natively with a cycle, since `compute_diff` is antisymmetric and re-diffing an inject pair backwards would pass by construction.

| graph | modules | edges | acyclicity delta | overall delta | overall/acy survival |
|---|--:|--:|--:|--:|--:|
| synthetic-5000 | 5000 | 9997 | -0.000400 | -0.000005 | 0.013 |
| synthetic-1500 | 1500 | 2997 | -0.001333 | -0.000023 | 0.017 |
| fastapi | 1118 | 1915 | -0.001789 | -0.000042 | 0.024 |
| scrapy | 439 | 1920 | -0.004556 | -0.000406 | 0.089 |
| pydantic | 402 | 1119 | -0.004975 | -0.000513 | 0.103 |
| synthetic-300 | 300 | 597 | -0.006667 | -0.001596 | 0.239 |
| rich | 213 | 733 | -0.009390 | -0.001083 | 0.115 |
| datasette | 143 | 415 | -0.013986 | -0.001820 | 0.130 |
| starlette | 67 | 314 | -0.029851 | -0.003550 | 0.119 |
| mkdocs | 65 | 179 | -0.030769 | -0.004491 | 0.146 |
| httpx | 60 | 122 | -0.033333 | -0.004621 | 0.139 |
| flask | 54 | 145 | -0.037037 | -0.006136 | 0.166 |
| requests | 37 | 110 | -0.054054 | -0.007709 | 0.143 |
| click | 22 | 66 | -0.090909 | -0.017830 | 0.196 |

The `overall/acy survival` column is the fraction of the one-edge acyclicity regression that reaches `overall`. It is small across all 14 graphs: from 24% (synthetic-300, 300 modules) down to 1.3% (synthetic-5000, 5000 modules). The three largest graphs (synthetic-5000, synthetic-1500, fastapi) carry the least (1.3%, 1.7%, 2.4%), so a single back-edge moves `overall` proportionally less as the graph grows.
The relationship is not monotonic: survival also depends on how the edge perturbs equality/modularity, not size alone. For example click (22 modules, 19.6%) is more diluted than the larger synthetic-300 (300 modules, 23.9%).
This is the pinned decision for #178: `overall` is a five-axis geometric mean, so a single back-edge's contribution to it is a small, structure-dependent fraction of the acyclicity magnitude and can be swamped (or sign-flipped) by simultaneous moves in modularity/equality/depth. `overall`'s per-edge sign is therefore NOT a reliable regression signal; `acyclicity` (asserted strictly negative above) and `cycles.added` are, which is why archy_diff surfaces them independently of `overall`. The dilution is intended, not a bug.

## Break direction (separate native-cycle graph)
- 200-node graph built with a 2-cycle on `m100 <-> m101`, then broken: 1 resolved, 0 added, acyclicity rises.

## Layer-violation direction (synthetic, forbid l0->l1)
- added edge flagged: 1 violation (`l0->l1`, `L0.m0->L1.m0`)
- removed edge: 1 resolved, 0 added

> The asserted signals (acyclicity sign, cycles.added/resolved counts) are exact and environment-independent. The `overall` / ratio columns depend on networkx's community detection and may shift across networkx versions; they are measured, not asserted.

