# greedy vs louvain partitioner (issue #190)

Seeded louvain (`seed=0`) vs greedy modularity on every corpus repo and synthetic graph, evaluating both call sites: the score sub-metric (`score.py:compute_modularity`, gated, must be reproducible) and the advisory DSM grouping (`dsm.py:_group_by_community`, where block cohesion is the product).

| graph | modules | greedy Q | louvain Q | score delta | greedy cov | louvain cov | greedy/louvain comms | louvain x faster | louvain order-stable | greedy order-stable |
|---|--:|--:|--:|--:|--:|--:|--:|--:|:--:|:--:|
| synthetic-5000 | 5000 | 0.7491 | 0.9647 | +0.1437 | 0.999 | 0.983 | 4/58 | 0.7x | NO | yes |
| synthetic-1500 | 1500 | 0.7470 | 0.9366 | +0.1264 | 0.997 | 0.969 | 4/32 | 1.7x | NO | yes |
| fastapi | 1118 | 0.3786 | 0.4091 | +0.0204 | 0.708 | 0.617 | 267/267 | 29.9x | NO | yes |
| scrapy | 439 | 0.3109 | 0.3612 | +0.0335 | 0.541 | 0.503 | 39/42 | 8.3x | NO | yes |
| pydantic | 402 | 0.4604 | 0.4504 | -0.0067 | 0.776 | 0.648 | 83/81 | 5.8x | NO | yes |
| synthetic-300 | 300 | 0.7539 | 0.8608 | +0.0713 | 0.980 | 0.925 | 5/16 | 0.5x | NO | yes |
| rich | 213 | 0.3188 | 0.3184 | -0.0003 | 0.528 | 0.476 | 13/14 | 5.2x | NO | yes |
| datasette | 143 | 0.3303 | 0.3301 | -0.0001 | 0.605 | 0.518 | 13/14 | 3.4x | NO | yes |
| starlette | 67 | 0.1811 | 0.1802 | -0.0006 | 0.414 | 0.385 | 8/10 | 2.2x | NO | yes |
| mkdocs | 65 | 0.2775 | 0.2739 | -0.0024 | 0.575 | 0.542 | 18/18 | 1.8x | NO | yes |
| httpx | 60 | 0.3702 | 0.3710 | +0.0005 | 0.787 | 0.754 | 7/7 | 4.1x | NO | yes |
| flask | 54 | 0.3353 | 0.3488 | +0.0090 | 0.572 | 0.559 | 11/10 | 2.0x | yes | yes |
| requests | 37 | 0.2370 | 0.2345 | -0.0017 | 0.573 | 0.518 | 7/8 | 1.6x | NO | yes |
| click | 22 | 0.2367 | 0.2466 | +0.0066 | 0.545 | 0.515 | 6/6 | 1.3x | yes | yes |

`cov` is DSM block cohesion: the fraction of import edges that stay inside a community (higher = tighter blocks). `score delta` is the move to the score's modularity sub-metric if the SCORE path switched. louvain repeat-stable under `seed=0` on every graph (so it is omitted from the table); order-stable and greedy order-stable are checked across three deterministic node reorderings.

Louvain Q vs greedy Q: from -0.0100 to +0.2156 (mean +0.0428); louvain wins on 8/14 graphs. On the real corpus alone the gain is marginal (-0.0100..+0.0502, mean +0.0079) -- the large gains are confined to the regular synthetic graphs greedy handles poorly, not real codebases.
If the SCORE path switched, the modularity sub-metric would move by -0.0067..+0.1437 per project, breaking trend continuity and the deliberate sentrux comparability unless renormalized (see #192).
DSM block cohesion (the advisory path's product): on the real corpus louvain's coverage vs greedy's ranges -0.128..-0.014 (mean -0.053); louvain has tighter blocks on 0/11 repos. Coverage can favor coarser partitions, but the two produce comparable community counts on the real repos (e.g. fastapi 267/267, pydantic 83/81, rich 13/14), so this is not a granularity artifact: at similar block counts louvain's blocks are simply less cohesive. The swap does not buy more cohesive DSM blocks on real code, which was its entire rationale.
Determinism across three node reorderings under `seed=0`: louvain is order-stable on only 2/14 graphs (repeat-stable on all -- the instability is parse-order, not run-to-run), so its partition changes with parse order. Greedy is order-stable on all graphs, measured here, not cited.

## Decision
Keep greedy for both paths. The SCORE path stays greedy because a louvain switch buys a marginal real-corpus Q gain (mean +0.008) while making the score parse-order-dependent and breaking trend/sentrux comparability.
The ADVISORY DSM path also stays greedy, on the evidence the ticket asked for: louvain's block cohesion (coverage) is no better on real repos (above), and its partition MEMBERSHIP changes with parse order, so the DSM blocks an agent reads would differ run to run on the same code. Note this is a visual-consistency cost, NOT a `diff_dsm` correctness regression: `diff_dsm` is name-keyed and explicitly reorder-robust (its added/removed/weight-changed sets are invariant to community grouping), so that earlier worry does not apply. Greedy's own layout is stable in block MEMBERSHIP; only the Community-N label of equal-size blocks can reorder, a minor pre-existing tiebreak detail, not membership churn. Leiden's well-connectedness guarantee could change this calculus but needs the `leidenalg`/`igraph` C dependency and is out of scope for this experiment.

