# DSM-derived signals vs the existing score axes

Bench: 27 projects pinned in `bench/projects.yaml`. Captured locally.

Internal-only subgraph. Ordering: SCC-condensed topological, alphabetical inside SCCs.

## Per-project DSM signals

| project | modules | edges | feedback | bandwidth | block_comm | block_layer |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| msgspec | 64 | 31 | 0.333 | 0.261 | 1.000 | 0.581 |
| click | 22 | 65 | 0.231 | 0.242 | 0.554 | 0.569 |
| flask | 54 | 145 | 0.221 | 0.206 | 0.572 | 0.510 |
| rich | 213 | 733 | 0.186 | 0.279 | 0.528 | 0.397 |
| aiohttp | 163 | 688 | 0.185 | 0.274 | 0.613 | 0.359 |
| numpy | 493 | 1453 | 0.181 | 0.330 | 0.659 | 0.326 |
| sqlalchemy | 668 | 2574 | 0.171 | 0.171 | 0.681 | 0.390 |
| pydantic | 402 | 1119 | 0.171 | 0.209 | 0.776 | 0.330 |
| mkdocs | 65 | 179 | 0.128 | 0.305 | 0.575 | 0.246 |
| requests | 37 | 110 | 0.118 | 0.237 | 0.573 | 0.255 |
| pytest | 247 | 788 | 0.115 | 0.234 | 0.537 | 0.212 |
| dagster | 5690 | 24023 | 0.109 | 0.299 | 0.716 | 0.211 |
| httpx | 60 | 122 | 0.107 | 0.185 | 0.787 | 0.328 |
| anyio | 72 | 277 | 0.094 | 0.353 | 0.502 | 0.170 |
| ansible | 1791 | 4285 | 0.087 | 0.277 | 0.849 | 0.160 |
| starlette | 67 | 313 | 0.077 | 0.400 | 0.383 | 0.166 |
| scrapy | 436 | 1913 | 0.057 | 0.336 | 0.526 | 0.141 |
| django | 2904 | 9526 | 0.036 | 0.351 | 0.665 | 0.085 |
| mypy | 437 | 2010 | 0.034 | 0.237 | 0.845 | 0.078 |
| setuptools | 327 | 597 | 0.030 | 0.301 | 0.905 | 0.074 |
| datasette | 129 | 364 | 0.025 | 0.363 | 0.536 | 0.052 |
| scikit-learn | 997 | 5462 | 0.019 | 0.333 | 0.538 | 0.049 |
| boto3 | 104 | 228 | 0.018 | 0.317 | 0.689 | 0.022 |
| fastapi | 1119 | 1915 | 0.005 | 0.474 | 0.708 | 0.011 |
| botocore | 290 | 933 | 0.004 | 0.310 | 0.605 | 0.006 |
| pygments | 403 | 951 | 0.000 | 0.476 | 0.708 | 0.000 |
| archy | 40 | 69 | 0.000 | 0.282 | 0.536 | 0.000 |

Legend:
- `feedback`: above-diagonal edge fraction in topo order (0 = pure DAG layering).
- `bandwidth`: mean `|i-j|/N` across edges (low = local dependencies).
- `block_comm`: edges inside Newman-community blocks.
- `block_layer`: edges inside depth-bucketed layer blocks.

## Pearson r of each DSM signal against existing axes + propagation_cost

Values with `|r| < 0.7` are below the OECD redundancy threshold (distinct signal).

| signal | vs modularity | vs acyclicity | vs depth | vs equality | vs complexity | vs propagation_cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| feedback | -0.072 | -0.688 | +0.605 | -0.561 | +0.149 | +0.428 |
| bandwidth | -0.065 | +0.579 | -0.083 | +0.603 | +0.055 | -0.350 |
| block_comm | +0.716 | +0.175 | -0.156 | +0.122 | -0.203 | -0.501 |
| block_layer | -0.094 | -0.771 | +0.584 | -0.606 | +0.088 | +0.548 |

## Pairwise Pearson r among the four DSM signals

| pair | r |
| --- | ---: |
| feedback ↔ bandwidth | -0.548 |
| feedback ↔ block_comm | +0.163 |
| feedback ↔ block_layer | +0.974 |
| bandwidth ↔ block_comm | -0.213 |
| bandwidth ↔ block_layer | -0.610 |
| block_comm ↔ block_layer | +0.104 |
