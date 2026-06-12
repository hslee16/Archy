# greedy vs louvain partitioner (issue #190)

Seeded louvain (`seed=0`) vs greedy modularity on every corpus repo and synthetic graph. `mod` is the normalized score sub-metric `(Q + 0.5) / 1.5`; `score delta` is what the score's modularity axis would move by if the SCORE path switched (the advisory DSM path can switch without touching the score).

| graph | modules | greedy Q | louvain Q | greedy mod | louvain mod | score delta | greedy/louvain comms | louvain x faster | louvain repeat-stable | louvain order-stable | greedy order-stable |
|---|--:|--:|--:|--:|--:|--:|--:|--:|:--:|:--:|:--:|
| synthetic-5000 | 5000 | 0.7491 | 0.9647 | 0.8327 | 0.9764 | +0.1437 | 4/58 | 0.7x | yes | NO | yes |
| synthetic-1500 | 1500 | 0.7470 | 0.9366 | 0.8313 | 0.9577 | +0.1264 | 4/32 | 1.7x | yes | NO | yes |
| fastapi | 1118 | 0.3786 | 0.4091 | 0.5857 | 0.6061 | +0.0204 | 267/267 | 30.3x | yes | NO | yes |
| scrapy | 439 | 0.3109 | 0.3612 | 0.5406 | 0.5741 | +0.0335 | 39/42 | 8.3x | yes | NO | yes |
| pydantic | 402 | 0.4604 | 0.4504 | 0.6402 | 0.6336 | -0.0067 | 83/81 | 5.6x | yes | NO | yes |
| synthetic-300 | 300 | 0.7539 | 0.8608 | 0.8359 | 0.9072 | +0.0713 | 5/16 | 0.5x | yes | NO | yes |
| rich | 213 | 0.3188 | 0.3184 | 0.5459 | 0.5456 | -0.0003 | 13/14 | 5.3x | yes | NO | yes |
| datasette | 143 | 0.3303 | 0.3301 | 0.5535 | 0.5534 | -0.0001 | 13/14 | 3.4x | yes | NO | yes |
| starlette | 67 | 0.1811 | 0.1802 | 0.4541 | 0.4535 | -0.0006 | 8/10 | 2.3x | yes | NO | yes |
| mkdocs | 65 | 0.2775 | 0.2739 | 0.5183 | 0.5159 | -0.0024 | 18/18 | 1.8x | yes | NO | yes |
| httpx | 60 | 0.3702 | 0.3710 | 0.5801 | 0.5807 | +0.0005 | 7/7 | 4.1x | yes | NO | yes |
| flask | 54 | 0.3353 | 0.3488 | 0.5569 | 0.5659 | +0.0090 | 11/10 | 2.0x | yes | yes | yes |
| requests | 37 | 0.2370 | 0.2345 | 0.4913 | 0.4897 | -0.0017 | 7/8 | 1.6x | yes | NO | yes |
| click | 22 | 0.2367 | 0.2466 | 0.4911 | 0.4977 | +0.0066 | 6/6 | 1.2x | yes | yes | yes |

Louvain Q vs greedy Q: from -0.0100 to +0.2156 (mean +0.0428); louvain wins on 8/14 graphs. On the real corpus alone the gain is marginal (-0.0100..+0.0502, mean +0.0079) -- the large gains are confined to the regular synthetic graphs greedy handles poorly, not real codebases.
If the SCORE path switched, the modularity sub-metric would move by -0.0067..+0.1437 per project, breaking trend continuity and the deliberate sentrux comparability unless renormalized (see #192).
Determinism under `seed=0`: louvain is repeat-stable on all graphs but insertion-order-stable on only 2/14 -- its partition (and any number derived from it) changes with parse order. Greedy is insertion-order invariant on all graphs, measured here, not just cited.

## Decision
Keep greedy for both paths. The score path stays greedy because a louvain switch buys a marginal real-corpus Q gain while introducing parse-order non-determinism and breaking score comparability. The advisory DSM path also stays greedy: on real repos louvain's Q and community counts barely differ, and its insertion-order instability would make DSM blocks (and `diff_dsm`) non-reproducible across environments, regressing the stable-layout property the grouping is built for. Leiden's well-connectedness guarantee could change this calculus but needs the `leidenalg`/`igraph` C dependency and is out of scope for this experiment.

