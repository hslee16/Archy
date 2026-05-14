# Benchmark results

Output of `uv run --with networkx --with pyyaml python bench/run.py`.
SHAs pinned in `bench/projects.yaml`. Captured 2026-05-14.

## Score table

| name | sha | modules | edges | overall | modularity | acyclicity | depth | equality |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pygments | `6fe2c31` | 342 | 834 | 0.661 | 0.565 | 1.000 | 0.500 | 0.676 |
| boto3 | `81a86c9` | 39 | 71 | 0.609 | 0.689 | 0.897 | 0.533 | 0.417 |
| numpy | `0a1ed72` | 424 | 1342 | 0.603 | 0.596 | 0.745 | 0.571 | 0.521 |
| mkdocs | `2862536` | 61 | 177 | 0.586 | 0.520 | 0.787 | 0.615 | 0.469 |
| starlette | `7793b92` | 34 | 114 | 0.572 | 0.458 | 0.588 | 0.727 | 0.547 |
| archy | `v0.13.1` | 14 | 30 | 0.561 | 0.471 | 1.000 | 0.667 | 0.314 |
| scrapy | `5223dbe` | 172 | 858 | 0.560 | 0.521 | 0.640 | 0.533 | 0.552 |
| anyio | `bcb2db6` | 42 | 158 | 0.555 | 0.499 | 0.643 | 0.615 | 0.480 |
| datasette | `aa84fe0` | 59 | 180 | 0.551 | 0.534 | 0.831 | 0.471 | 0.442 |
| setuptools | `84ed591` | 317 | 592 | 0.549 | 0.766 | 0.931 | 0.348 | 0.367 |
| botocore | `2b64927` | 76 | 257 | 0.533 | 0.563 | 0.934 | 0.348 | 0.439 |
| pytest | `856da14` | 69 | 374 | 0.529 | 0.478 | 0.710 | 0.471 | 0.490 |
| fastapi | `e89a37e` | 48 | 114 | 0.522 | 0.522 | 0.771 | 0.615 | 0.300 |
| pydantic | `5c63f86` | 104 | 496 | 0.513 | 0.636 | 0.385 | 0.615 | 0.459 |
| rich | `46cebbb` | 100 | 421 | 0.510 | 0.524 | 0.450 | 0.667 | 0.430 |
| requests | `b684dcb` | 19 | 73 | 0.508 | 0.429 | 0.579 | 0.571 | 0.469 |
| mypy | `e53693b` | 195 | 1105 | 0.498 | 0.571 | 0.815 | 0.286 | 0.464 |
| sqlalchemy | `1e1c008` | 255 | 2550 | 0.493 | 0.571 | 0.388 | 0.471 | 0.568 |
| ansible | `b7c0900` | 581 | 2145 | 0.477 | 0.614 | 0.769 | 0.286 | 0.383 |
| django | `4d455ae` | 902 | 3274 | 0.476 | 0.640 | 0.754 | 0.267 | 0.399 |
| click | `fc6c7c4` | 17 | 60 | 0.470 | 0.451 | 0.235 | 0.800 | 0.575 |
| httpx | `b5addb6` | 23 | 87 | 0.463 | 0.482 | 0.261 | 0.667 | 0.550 |
| flask | `7374c85` | 24 | 94 | 0.463 | 0.484 | 0.208 | 0.800 | 0.569 |
| scikit-learn | `13f20d7` | 638 | 3866 | 0.463 | 0.525 | 0.824 | 0.222 | 0.477 |
| dagster | `8e7f318` | 801 | 6273 | 0.461 | 0.575 | 0.400 | 0.471 | 0.416 |
| aiohttp | `e8f4371` | 52 | 312 | 0.440 | 0.530 | 0.173 | 0.727 | 0.563 |
| msgspec | `3b2543b` | 10 | 20 | 0.383 | 0.427 | 0.100 | 0.889 | 0.570 |

## Pairwise Pearson correlations

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.423 |
| modularity ↔ depth | -0.587 |
| modularity ↔ equality | -0.359 |
| acyclicity ↔ depth | -0.651 |
| acyclicity ↔ equality | -0.471 |
| depth ↔ equality | +0.373 |

## Call-graph diagnostics

| project | sha | modules | edges | call_edges | total_calls | calls/edge |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| pygments | `6fe2c31` | 342 | 834 | 331 | 5926 | 17.90 |
| boto3 | `81a86c9` | 39 | 71 | 57 | 142 | 2.49 |
| numpy | `0a1ed72` | 424 | 1342 | 988 | 52044 | 52.68 |
| mkdocs | `2862536` | 61 | 177 | 119 | 1425 | 11.97 |
| starlette | `7793b92` | 34 | 114 | 60 | 116 | 1.93 |
| archy | `v0.13.1` | 14 | 30 | 27 | 74 | 2.74 |
| scrapy | `5223dbe` | 172 | 858 | 354 | 762 | 2.15 |
| anyio | `bcb2db6` | 42 | 158 | 78 | 306 | 3.92 |
| datasette | `aa84fe0` | 59 | 180 | 111 | 672 | 6.05 |
| setuptools | `84ed591` | 317 | 592 | 400 | 1520 | 3.80 |
| botocore | `2b64927` | 76 | 257 | 207 | 714 | 3.45 |
| pytest | `856da14` | 69 | 374 | 193 | 549 | 2.84 |
| fastapi | `e89a37e` | 48 | 114 | 51 | 272 | 5.33 |
| pydantic | `5c63f86` | 104 | 496 | 264 | 959 | 3.63 |
| rich | `46cebbb` | 100 | 421 | 322 | 886 | 2.75 |
| requests | `b684dcb` | 19 | 73 | 41 | 174 | 4.24 |
| mypy | `e53693b` | 195 | 1105 | 716 | 6872 | 9.60 |
| sqlalchemy | `1e1c008` | 255 | 2550 | 1085 | 7970 | 7.35 |
| ansible | `b7c0900` | 581 | 2145 | 1395 | 4448 | 3.19 |
| django | `4d455ae` | 902 | 3274 | 1919 | 5969 | 3.11 |
| click | `fc6c7c4` | 17 | 60 | 38 | 167 | 4.39 |
| httpx | `b5addb6` | 23 | 87 | 36 | 155 | 4.31 |
| flask | `7374c85` | 24 | 94 | 36 | 88 | 2.44 |
| scikit-learn | `13f20d7` | 638 | 3866 | 3083 | 25869 | 8.39 |
| dagster | `8e7f318` | 801 | 6273 | 2872 | 10540 | 3.67 |
| aiohttp | `e8f4371` | 52 | 312 | 107 | 403 | 3.77 |
| msgspec | `3b2543b` | 10 | 20 | 9 | 24 | 2.67 |

## Call-density orthogonality to existing axes

Pearson correlation of `calls_per_edge` against each axis + propagation cost.
Values below `|r| = 0.7` are below the OECD redundancy threshold.

| signal | r vs calls/edge |
| --- | ---: |
| modularity | +0.148 |
| acyclicity | +0.208 |
| depth | -0.062 |
| equality | +0.212 |
| propagation_cost | -0.229 |
