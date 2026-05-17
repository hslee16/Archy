# Benchmark results

Output of `uv run --with networkx --with pyyaml python bench/run.py`.
SHAs pinned in `bench/projects.yaml`. Captured 2026-05-16.

## Score table

| name | sha | modules | edges | overall | modularity | acyclicity | depth | equality | complexity |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pygments | `6fe2c31` | 342 | 834 | 0.663 | 0.565 | 1.000 | 0.500 | 0.676 | 0.668 |
| boto3 | `81a86c9` | 39 | 71 | 0.653 | 0.689 | 0.897 | 0.533 | 0.417 | 0.861 |
| numpy | `0a1ed72` | 424 | 1342 | 0.647 | 0.596 | 0.745 | 0.571 | 0.521 | 0.856 |
| mkdocs | `2862536` | 61 | 177 | 0.639 | 0.520 | 0.787 | 0.615 | 0.469 | 0.903 |
| starlette | `7793b92` | 34 | 114 | 0.613 | 0.458 | 0.588 | 0.727 | 0.547 | 0.811 |
| anyio | `bcb2db6` | 42 | 158 | 0.607 | 0.499 | 0.643 | 0.615 | 0.480 | 0.872 |
| scrapy | `5223dbe` | 172 | 858 | 0.603 | 0.521 | 0.640 | 0.533 | 0.552 | 0.814 |
| setuptools | `84ed591` | 317 | 592 | 0.586 | 0.766 | 0.931 | 0.348 | 0.367 | 0.762 |
| botocore | `2b64927` | 76 | 257 | 0.581 | 0.563 | 0.934 | 0.348 | 0.439 | 0.823 |
| pytest | `856da14` | 69 | 374 | 0.568 | 0.478 | 0.710 | 0.471 | 0.490 | 0.757 |
| archy | `v0.22.0` | 19 | 43 | 0.564 | 0.512 | 1.000 | 0.667 | 0.273 | 0.615 |
| datasette | `aa84fe0` | 59 | 180 | 0.557 | 0.534 | 0.831 | 0.471 | 0.442 | 0.578 |
| fastapi | `e89a37e` | 48 | 114 | 0.549 | 0.522 | 0.771 | 0.615 | 0.300 | 0.671 |
| sqlalchemy | `1e1c008` | 255 | 2550 | 0.546 | 0.571 | 0.388 | 0.471 | 0.568 | 0.819 |
| requests | `b684dcb` | 19 | 73 | 0.545 | 0.429 | 0.579 | 0.571 | 0.469 | 0.722 |
| rich | `46cebbb` | 100 | 421 | 0.544 | 0.524 | 0.450 | 0.667 | 0.430 | 0.705 |
| pydantic | `5c63f86` | 104 | 496 | 0.541 | 0.636 | 0.385 | 0.615 | 0.459 | 0.673 |
| django | `4d455ae` | 902 | 3274 | 0.521 | 0.640 | 0.754 | 0.267 | 0.399 | 0.746 |
| mypy | `e53693b` | 195 | 1105 | 0.521 | 0.571 | 0.815 | 0.286 | 0.464 | 0.620 |
| scikit-learn | `13f20d7` | 638 | 3866 | 0.518 | 0.525 | 0.824 | 0.222 | 0.477 | 0.810 |
| flask | `7374c85` | 24 | 94 | 0.517 | 0.484 | 0.208 | 0.800 | 0.569 | 0.802 |
| dagster | `8e7f318` | 801 | 6273 | 0.515 | 0.575 | 0.400 | 0.471 | 0.416 | 0.803 |
| httpx | `b5addb6` | 23 | 87 | 0.515 | 0.482 | 0.261 | 0.667 | 0.550 | 0.782 |
| click | `fc6c7c4` | 17 | 60 | 0.511 | 0.451 | 0.235 | 0.800 | 0.575 | 0.717 |
| ansible | `b7c0900` | 581 | 2145 | 0.495 | 0.614 | 0.769 | 0.286 | 0.383 | 0.573 |
| aiohttp | `e8f4371` | 52 | 312 | 0.494 | 0.530 | 0.173 | 0.727 | 0.563 | 0.785 |
| msgspec | `3b2543b` | 10 | 20 | 0.397 | 0.427 | 0.100 | 0.889 | 0.570 | 0.458 |

## Pairwise Pearson correlations

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.456 |
| modularity ↔ depth | -0.581 |
| modularity ↔ equality | -0.380 |
| modularity ↔ complexity | +0.139 |
| acyclicity ↔ depth | -0.651 |
| acyclicity ↔ equality | -0.479 |
| acyclicity ↔ complexity | +0.059 |
| depth ↔ equality | +0.347 |
| depth ↔ complexity | -0.073 |
| equality ↔ complexity | +0.154 |

## Call-graph diagnostics

| project | sha | modules | edges | call_edges | total_calls | calls/edge |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| pygments | `6fe2c31` | 342 | 834 | 331 | 5926 | 17.90 |
| boto3 | `81a86c9` | 39 | 71 | 57 | 142 | 2.49 |
| numpy | `0a1ed72` | 424 | 1342 | 988 | 52044 | 52.68 |
| mkdocs | `2862536` | 61 | 177 | 119 | 1425 | 11.97 |
| starlette | `7793b92` | 34 | 114 | 60 | 116 | 1.93 |
| anyio | `bcb2db6` | 42 | 158 | 78 | 306 | 3.92 |
| scrapy | `5223dbe` | 172 | 858 | 354 | 762 | 2.15 |
| setuptools | `84ed591` | 317 | 592 | 400 | 1520 | 3.80 |
| botocore | `2b64927` | 76 | 257 | 207 | 714 | 3.45 |
| pytest | `856da14` | 69 | 374 | 193 | 549 | 2.84 |
| archy | `v0.22.0` | 19 | 43 | 39 | 99 | 2.54 |
| datasette | `aa84fe0` | 59 | 180 | 111 | 672 | 6.05 |
| fastapi | `e89a37e` | 48 | 114 | 51 | 272 | 5.33 |
| sqlalchemy | `1e1c008` | 255 | 2550 | 1085 | 7970 | 7.35 |
| requests | `b684dcb` | 19 | 73 | 41 | 174 | 4.24 |
| rich | `46cebbb` | 100 | 421 | 322 | 886 | 2.75 |
| pydantic | `5c63f86` | 104 | 496 | 264 | 959 | 3.63 |
| django | `4d455ae` | 902 | 3274 | 1919 | 5969 | 3.11 |
| mypy | `e53693b` | 195 | 1105 | 716 | 6872 | 9.60 |
| scikit-learn | `13f20d7` | 638 | 3866 | 3083 | 25869 | 8.39 |
| flask | `7374c85` | 24 | 94 | 36 | 88 | 2.44 |
| dagster | `8e7f318` | 801 | 6273 | 2872 | 10540 | 3.67 |
| httpx | `b5addb6` | 23 | 87 | 36 | 155 | 4.31 |
| click | `fc6c7c4` | 17 | 60 | 38 | 167 | 4.39 |
| ansible | `b7c0900` | 581 | 2145 | 1395 | 4448 | 3.19 |
| aiohttp | `e8f4371` | 52 | 312 | 107 | 403 | 3.77 |
| msgspec | `3b2543b` | 10 | 20 | 9 | 24 | 2.67 |

## Call-density orthogonality to existing axes

Pearson correlation of `calls_per_edge` against each axis + propagation cost.
Values below `|r| = 0.7` are below the OECD redundancy threshold.

| signal | r vs calls/edge |
| --- | ---: |
| modularity | +0.143 |
| acyclicity | +0.207 |
| depth | -0.062 |
| equality | +0.213 |
| complexity | +0.201 |
| propagation_cost | -0.224 |

## Cyclomatic complexity diagnostics

| project | sha | functions | cc_mean | cc_max |
| --- | --- | ---: | ---: | ---: |
| pygments | `6fe2c31` | 936 | 3.66 | 98 |
| boto3 | `81a86c9` | 375 | 2.11 | 12 |
| numpy | `0a1ed72` | 11,283 | 2.15 | 181 |
| mkdocs | `2862536` | 1,277 | 1.77 | 29 |
| starlette | `7793b92` | 498 | 2.51 | 17 |
| anyio | `bcb2db6` | 1,051 | 2.03 | 20 |
| scrapy | `5223dbe` | 1,715 | 2.49 | 19 |
| setuptools | `84ed591` | 3,811 | 2.91 | 340 |
| botocore | `2b64927` | 2,296 | 2.42 | 25 |
| pytest | `856da14` | 2,010 | 2.94 | 37 |
| archy | `v0.22.0` | 206 | 4.08 | 18 |
| datasette | `aa84fe0` | 798 | 4.37 | 98 |
| fastapi | `e89a37e` | 296 | 3.63 | 41 |
| sqlalchemy | `1e1c008` | 11,480 | 2.45 | 73 |
| requests | `b684dcb` | 267 | 3.22 | 21 |
| rich | `46cebbb` | 912 | 3.36 | 49 |
| pydantic | `5c63f86` | 1,864 | 3.62 | 77 |
| django | `4d455ae` | 9,561 | 3.04 | 94 |
| mypy | `e53693b` | 6,485 | 4.04 | 79 |
| scikit-learn | `13f20d7` | 10,841 | 2.52 | 75 |
| flask | `7374c85` | 388 | 2.59 | 23 |
| dagster | `8e7f318` | 10,381 | 2.58 | 96 |
| httpx | `b5addb6` | 446 | 2.75 | 46 |
| click | `fc6c7c4` | 544 | 3.26 | 48 |
| ansible | `b7c0900` | 4,925 | 4.42 | 127 |
| aiohttp | `e8f4371` | 1,497 | 2.72 | 91 |
| msgspec | `3b2543b` | 63 | 5.33 | 86 |

## CC orthogonality to existing axes

Pearson correlation of `cc_mean` against each axis + the two prior diagnostics.
Values below `|r| = 0.7` are below the OECD redundancy threshold.

| signal | r vs cc_mean |
| --- | ---: |
| modularity | -0.139 |
| acyclicity | -0.059 |
| depth | +0.073 |
| equality | -0.154 |
| propagation_cost | +0.094 |
| calls_per_edge | -0.201 |

## Call-weighted modularity diagnostic (v0.21)

Per-project unweighted vs call-weighted raw Newman Q. The gap (weighted - unweighted) is the load-bearing signal; see `docs/CALL_WEIGHTED_Q_EMPIRICS.md`.

| project | sha | unweighted Q | weighted Q | gap |
| --- | --- | ---: | ---: | ---: |
| pygments | `6fe2c31` | +0.348 | +0.130 | -0.218 |
| boto3 | `81a86c9` | +0.533 | +0.560 | +0.027 |
| numpy | `0a1ed72` | +0.394 | +0.324 | -0.071 |
| mkdocs | `2862536` | +0.280 | +0.518 | +0.238 |
| starlette | `7793b92` | +0.186 | +0.227 | +0.041 |
| anyio | `bcb2db6` | +0.249 | +0.399 | +0.151 |
| scrapy | `5223dbe` | +0.282 | +0.365 | +0.083 |
| setuptools | `84ed591` | +0.648 | +0.743 | +0.095 |
| botocore | `2b64927` | +0.345 | +0.304 | -0.041 |
| pytest | `856da14` | +0.217 | +0.336 | +0.119 |
| archy | `v0.22.0` | +0.268 | +0.209 | -0.059 |
| datasette | `aa84fe0` | +0.301 | +0.284 | -0.017 |
| fastapi | `e89a37e` | +0.283 | +0.414 | +0.131 |
| sqlalchemy | `1e1c008` | +0.357 | +0.538 | +0.181 |
| requests | `b684dcb` | +0.144 | +0.154 | +0.010 |
| rich | `46cebbb` | +0.285 | +0.405 | +0.120 |
| pydantic | `5c63f86` | +0.454 | +0.498 | +0.044 |
| django | `4d455ae` | +0.460 | +0.547 | +0.087 |
| mypy | `e53693b` | +0.356 | +0.414 | +0.058 |
| scikit-learn | `13f20d7` | +0.287 | +0.453 | +0.166 |
| flask | `7374c85` | +0.226 | +0.218 | -0.008 |
| dagster | `8e7f318` | +0.362 | +0.394 | +0.032 |
| httpx | `b5addb6` | +0.223 | +0.312 | +0.089 |
| click | `fc6c7c4` | +0.177 | +0.236 | +0.059 |
| ansible | `b7c0900` | +0.422 | +0.432 | +0.010 |
| aiohttp | `e8f4371` | +0.295 | +0.406 | +0.111 |
| msgspec | `3b2543b` | +0.140 | +0.201 | +0.061 |

Pearson correlation of normalized weighted Q against the existing axes.
Lower absolute values indicate stronger orthogonality.

| signal | r vs weighted Q (normalized) |
| --- | ---: |
| modularity | +0.767 |
| acyclicity | +0.198 |
| depth | -0.486 |
| equality | -0.406 |
| complexity | +0.319 |
