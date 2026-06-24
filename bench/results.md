# Benchmark results

Output of `uv run --with networkx --with pyyaml python bench/run.py`.
SHAs pinned in `bench/projects.yaml`. Captured 2026-06-24.

## Score table

| name | sha | modules | edges | overall | modularity | acyclicity | depth | equality | complexity |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pygments | `74fdf56` | 342 | 834 | 0.663 | 0.565 | 1.000 | 0.500 | 0.676 | 0.668 |
| boto3 | `447ab8f` | 39 | 71 | 0.653 | 0.689 | 0.897 | 0.533 | 0.417 | 0.861 |
| numpy | `7dfe67e` | 425 | 1361 | 0.648 | 0.595 | 0.744 | 0.571 | 0.527 | 0.857 |
| mkdocs | `2862536` | 61 | 177 | 0.639 | 0.520 | 0.787 | 0.615 | 0.469 | 0.903 |
| home-assistant | `ff87c54` | 9647 | 54340 | 0.628 | 0.576 | 0.909 | 0.364 | 0.637 | 0.803 |
| starlette | `de970d7` | 34 | 119 | 0.610 | 0.460 | 0.588 | 0.667 | 0.581 | 0.808 |
| anyio | `1dbc3b6` | 43 | 163 | 0.607 | 0.494 | 0.651 | 0.615 | 0.480 | 0.864 |
| scrapy | `fada8be` | 174 | 877 | 0.606 | 0.529 | 0.638 | 0.533 | 0.556 | 0.813 |
| setuptools | `84ed591` | 317 | 592 | 0.586 | 0.766 | 0.931 | 0.348 | 0.367 | 0.762 |
| botocore | `dbfa9d0` | 76 | 257 | 0.581 | 0.563 | 0.934 | 0.348 | 0.439 | 0.823 |
| pytest | `070e35b` | 77 | 401 | 0.574 | 0.494 | 0.740 | 0.471 | 0.477 | 0.757 |
| archy | `v0.22.0` | 19 | 43 | 0.564 | 0.512 | 1.000 | 0.667 | 0.273 | 0.615 |
| fastapi | `c61384e` | 48 | 114 | 0.551 | 0.522 | 0.771 | 0.615 | 0.300 | 0.684 |
| datasette | `dfd5b95` | 72 | 238 | 0.546 | 0.530 | 0.806 | 0.471 | 0.429 | 0.566 |
| sqlalchemy | `ddf3b65` | 255 | 2553 | 0.546 | 0.571 | 0.388 | 0.471 | 0.568 | 0.819 |
| requests | `d64b9ad` | 19 | 73 | 0.545 | 0.429 | 0.579 | 0.571 | 0.469 | 0.722 |
| rich | `46cebbb` | 100 | 421 | 0.544 | 0.524 | 0.450 | 0.667 | 0.430 | 0.705 |
| pydantic | `8dbb2a1` | 104 | 496 | 0.541 | 0.636 | 0.385 | 0.615 | 0.459 | 0.671 |
| django | `189c2d2` | 907 | 3321 | 0.521 | 0.643 | 0.752 | 0.267 | 0.400 | 0.744 |
| mypy | `74ecdd8` | 195 | 1106 | 0.520 | 0.571 | 0.815 | 0.286 | 0.464 | 0.616 |
| scikit-learn | `8fac97f` | 653 | 3912 | 0.518 | 0.526 | 0.828 | 0.222 | 0.476 | 0.810 |
| flask | `36e4a82` | 24 | 94 | 0.517 | 0.484 | 0.208 | 0.800 | 0.569 | 0.802 |
| dagster | `c8a8460` | 805 | 6290 | 0.515 | 0.576 | 0.401 | 0.471 | 0.415 | 0.802 |
| httpx | `b5addb6` | 23 | 87 | 0.515 | 0.482 | 0.261 | 0.667 | 0.550 | 0.782 |
| click | `8a1b1a3` | 17 | 61 | 0.506 | 0.457 | 0.235 | 0.727 | 0.595 | 0.710 |
| aiohttp | `7b0b013` | 52 | 314 | 0.495 | 0.531 | 0.173 | 0.727 | 0.567 | 0.782 |
| ansible | `b475463` | 581 | 2145 | 0.495 | 0.614 | 0.769 | 0.286 | 0.383 | 0.572 |
| pytorch | `4ece0fc` | 2325 | 13693 | 0.474 | 0.681 | 0.415 | 0.286 | 0.430 | 0.690 |
| msgspec | `54a7c2f` | 10 | 20 | 0.394 | 0.427 | 0.100 | 0.889 | 0.570 | 0.439 |

## Pairwise Pearson correlations

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.382 |
| modularity ↔ depth | -0.611 |
| modularity ↔ equality | -0.353 |
| modularity ↔ complexity | +0.126 |
| acyclicity ↔ depth | -0.590 |
| acyclicity ↔ equality | -0.366 |
| acyclicity ↔ complexity | +0.116 |
| depth ↔ equality | +0.278 |
| depth ↔ complexity | -0.084 |
| equality ↔ complexity | +0.182 |

## Call-graph diagnostics

`coverage` = call_edges / import_edges: the fraction of import edges that carry at least one resolved call. Static call resolution is partial (dynamic dispatch, decorators, and re-exports are not followed), so the call-graph diagnostics below are computed on this fraction, not the whole import graph.

| project | sha | modules | edges | call_edges | coverage | total_calls | calls/edge |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| pygments | `74fdf56` | 342 | 834 | 331 | 39.7% | 5926 | 17.90 |
| boto3 | `447ab8f` | 39 | 71 | 57 | 80.3% | 142 | 2.49 |
| numpy | `7dfe67e` | 425 | 1361 | 1001 | 73.5% | 52443 | 52.39 |
| mkdocs | `2862536` | 61 | 177 | 119 | 67.2% | 1425 | 11.97 |
| home-assistant | `ff87c54` | 9647 | 54340 | 16188 | 29.8% | 37086 | 2.29 |
| starlette | `de970d7` | 34 | 119 | 61 | 51.3% | 118 | 1.93 |
| anyio | `1dbc3b6` | 43 | 163 | 83 | 50.9% | 347 | 4.18 |
| scrapy | `fada8be` | 174 | 877 | 357 | 40.7% | 752 | 2.11 |
| setuptools | `84ed591` | 317 | 592 | 400 | 67.6% | 1520 | 3.80 |
| botocore | `dbfa9d0` | 76 | 257 | 207 | 80.5% | 714 | 3.45 |
| pytest | `070e35b` | 77 | 401 | 207 | 51.6% | 597 | 2.88 |
| archy | `v0.22.0` | 19 | 43 | 39 | 90.7% | 99 | 2.54 |
| fastapi | `c61384e` | 48 | 114 | 52 | 45.6% | 292 | 5.62 |
| datasette | `dfd5b95` | 72 | 238 | 156 | 65.5% | 1003 | 6.43 |
| sqlalchemy | `ddf3b65` | 255 | 2553 | 1088 | 42.6% | 8031 | 7.38 |
| requests | `d64b9ad` | 19 | 73 | 42 | 57.5% | 177 | 4.21 |
| rich | `46cebbb` | 100 | 421 | 322 | 76.5% | 886 | 2.75 |
| pydantic | `8dbb2a1` | 104 | 496 | 264 | 53.2% | 959 | 3.63 |
| django | `189c2d2` | 907 | 3321 | 1946 | 58.6% | 6049 | 3.11 |
| mypy | `74ecdd8` | 195 | 1106 | 716 | 64.7% | 6908 | 9.65 |
| scikit-learn | `8fac97f` | 653 | 3912 | 3123 | 79.8% | 26313 | 8.43 |
| flask | `36e4a82` | 24 | 94 | 36 | 38.3% | 88 | 2.44 |
| dagster | `c8a8460` | 805 | 6290 | 2882 | 45.8% | 10590 | 3.67 |
| httpx | `b5addb6` | 23 | 87 | 36 | 41.4% | 155 | 4.31 |
| click | `8a1b1a3` | 17 | 61 | 39 | 63.9% | 175 | 4.49 |
| aiohttp | `7b0b013` | 52 | 314 | 110 | 35.0% | 414 | 3.76 |
| ansible | `b475463` | 581 | 2145 | 1395 | 65.0% | 4448 | 3.19 |
| pytorch | `4ece0fc` | 2325 | 13693 | 8756 | 63.9% | 74277 | 8.48 |
| msgspec | `54a7c2f` | 10 | 20 | 9 | 45.0% | 24 | 2.67 |

## Call-density orthogonality to existing axes

Pearson correlation of `calls_per_edge` against each axis + propagation cost.
Values below `|r| = 0.7` are below the OECD redundancy threshold.

| signal | r vs calls/edge |
| --- | ---: |
| modularity | +0.132 |
| acyclicity | +0.177 |
| depth | -0.045 |
| equality | +0.165 |
| complexity | +0.184 |
| propagation_cost | -0.189 |

## Cyclomatic complexity diagnostics

| project | sha | functions | cc_mean | cc_max |
| --- | --- | ---: | ---: | ---: |
| pygments | `74fdf56` | 936 | 3.66 | 98 |
| boto3 | `447ab8f` | 375 | 2.11 | 12 |
| numpy | `7dfe67e` | 11,368 | 2.15 | 181 |
| mkdocs | `2862536` | 1,277 | 1.77 | 29 |
| home-assistant | `ff87c54` | 57,950 | 2.57 | 95 |
| starlette | `de970d7` | 498 | 2.54 | 17 |
| anyio | `1dbc3b6` | 1,098 | 2.09 | 20 |
| scrapy | `fada8be` | 1,741 | 2.50 | 21 |
| setuptools | `84ed591` | 3,811 | 2.91 | 340 |
| botocore | `dbfa9d0` | 2,297 | 2.42 | 25 |
| pytest | `070e35b` | 2,029 | 2.95 | 37 |
| archy | `v0.22.0` | 206 | 4.08 | 18 |
| fastapi | `c61384e` | 337 | 3.53 | 41 |
| datasette | `dfd5b95` | 991 | 4.47 | 90 |
| sqlalchemy | `ddf3b65` | 11,518 | 2.45 | 73 |
| requests | `d64b9ad` | 268 | 3.23 | 21 |
| rich | `46cebbb` | 912 | 3.36 | 49 |
| pydantic | `8dbb2a1` | 1,861 | 3.63 | 77 |
| django | `189c2d2` | 9,604 | 3.04 | 94 |
| mypy | `74ecdd8` | 6,509 | 4.07 | 85 |
| scikit-learn | `8fac97f` | 11,135 | 2.52 | 73 |
| flask | `36e4a82` | 388 | 2.59 | 23 |
| dagster | `c8a8460` | 10,461 | 2.58 | 96 |
| httpx | `b5addb6` | 446 | 2.75 | 46 |
| click | `8a1b1a3` | 553 | 3.32 | 47 |
| aiohttp | `7b0b013` | 1,507 | 2.74 | 83 |
| ansible | `b475463` | 4,924 | 4.42 | 127 |
| pytorch | `4ece0fc` | 47,713 | 3.48 | 214 |
| msgspec | `54a7c2f` | 61 | 5.49 | 86 |

## CC orthogonality to existing axes

Pearson correlation of `cc_mean` against each axis + the two prior diagnostics.
Values below `|r| = 0.7` are below the OECD redundancy threshold.

| signal | r vs cc_mean |
| --- | ---: |
| modularity | -0.126 |
| acyclicity | -0.116 |
| depth | +0.084 |
| equality | -0.182 |
| propagation_cost | +0.148 |
| calls_per_edge | -0.184 |

## Call-weighted modularity diagnostic (v0.21)

Per-project unweighted vs call-weighted raw Newman Q. The gap (weighted - unweighted) is the load-bearing signal; see `docs/research/CALL_WEIGHTED_Q_EMPIRICS.md`.

| project | sha | unweighted Q | weighted Q | gap |
| --- | --- | ---: | ---: | ---: |
| pygments | `74fdf56` | +0.348 | +0.130 | -0.218 |
| boto3 | `447ab8f` | +0.533 | +0.560 | +0.027 |
| numpy | `7dfe67e` | +0.393 | +0.323 | -0.070 |
| mkdocs | `2862536` | +0.280 | +0.518 | +0.238 |
| home-assistant | `ff87c54` | +0.364 | +0.451 | +0.087 |
| starlette | `de970d7` | +0.190 | +0.218 | +0.028 |
| anyio | `1dbc3b6` | +0.240 | +0.441 | +0.200 |
| scrapy | `fada8be` | +0.293 | +0.363 | +0.069 |
| setuptools | `84ed591` | +0.648 | +0.743 | +0.095 |
| botocore | `dbfa9d0` | +0.345 | +0.304 | -0.041 |
| pytest | `070e35b` | +0.241 | +0.368 | +0.127 |
| archy | `v0.22.0` | +0.268 | +0.209 | -0.059 |
| fastapi | `c61384e` | +0.283 | +0.414 | +0.130 |
| datasette | `dfd5b95` | +0.295 | +0.330 | +0.036 |
| sqlalchemy | `ddf3b65` | +0.357 | +0.541 | +0.184 |
| requests | `d64b9ad` | +0.144 | +0.150 | +0.006 |
| rich | `46cebbb` | +0.285 | +0.405 | +0.120 |
| pydantic | `8dbb2a1` | +0.454 | +0.498 | +0.044 |
| django | `189c2d2` | +0.464 | +0.547 | +0.082 |
| mypy | `74ecdd8` | +0.356 | +0.416 | +0.060 |
| scikit-learn | `8fac97f` | +0.289 | +0.457 | +0.168 |
| flask | `36e4a82` | +0.226 | +0.218 | -0.008 |
| dagster | `c8a8460` | +0.364 | +0.395 | +0.031 |
| httpx | `b5addb6` | +0.223 | +0.312 | +0.089 |
| click | `8a1b1a3` | +0.185 | +0.263 | +0.078 |
| aiohttp | `7b0b013` | +0.296 | +0.403 | +0.106 |
| ansible | `b475463` | +0.422 | +0.432 | +0.010 |
| pytorch | `4ece0fc` | +0.522 | +0.693 | +0.171 |
| msgspec | `54a7c2f` | +0.140 | +0.201 | +0.061 |

Pearson correlation of normalized weighted Q against the existing axes.
Lower absolute values indicate stronger orthogonality.

| signal | r vs weighted Q (normalized) |
| --- | ---: |
| modularity | +0.784 |
| acyclicity | +0.142 |
| depth | -0.534 |
| equality | -0.371 |
| complexity | +0.264 |

## Axis-independence gate

**PASS**: all 10 axis pairs are below the OECD redundancy threshold `|r| = 0.7`.

Moderate coupling (`0.5 <= |r| <= 0.7`), acceptable but watched:
- `modularity ↔ depth`: `-0.611`
- `acyclicity ↔ depth`: `-0.590`

