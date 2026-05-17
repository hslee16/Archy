# Score-shape redesign empirics

Output of `uv run --with networkx --with pyyaml python bench/score_redesign.py evaluate`.
28 projects (27-project bench + governingdocs/backend). Captured 2026-05-17.

## Per-acyclicity-candidate Pearson correlation matrices

Each row is a candidate acyclicity formulation; remaining four axes
are unchanged. The two OECD-relevant pairs (acyclicity ↔ depth,
modularity ↔ depth) are highlighted; any pair with |r| ≥ 0.5 is
a 'moderate' coupling per the OECD handbook commentary.

### baseline_tangle

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.479 |
| modularity ↔ depth ** | -0.581 ** |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth ** | -0.641 ** |
| acyclicity ↔ equality ** | -0.519 ** |
| acyclicity ↔ complexity | -0.113 |
| depth ↔ equality | +0.345 |
| depth ↔ complexity | -0.027 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **-0.641**
- modularity ↔ depth: **-0.581**
- pairs at |r| ≥ 0.5: **3/10**
- max |r|: **0.641**

### largest_scc

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.498 |
| modularity ↔ depth ** | -0.581 ** |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth ** | -0.700 ** |
| acyclicity ↔ equality ** | -0.514 ** |
| acyclicity ↔ complexity | -0.112 |
| depth ↔ equality | +0.345 |
| depth ↔ complexity | -0.027 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **-0.700**
- modularity ↔ depth: **-0.581**
- pairs at |r| ≥ 0.5: **3/10**
- max |r|: **0.700**

### modular_tangle

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.474 |
| modularity ↔ depth ** | -0.581 ** |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth ** | -0.683 ** |
| acyclicity ↔ equality | -0.487 |
| acyclicity ↔ complexity | -0.134 |
| depth ↔ equality | +0.345 |
| depth ↔ complexity | -0.027 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **-0.683**
- modularity ↔ depth: **-0.581**
- pairs at |r| ≥ 0.5: **2/10**
- max |r|: **0.683**

### feedback_edges

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.268 |
| modularity ↔ depth ** | -0.581 ** |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth ** | -0.517 ** |
| acyclicity ↔ equality | -0.387 |
| acyclicity ↔ complexity | -0.159 |
| depth ↔ equality | +0.345 |
| depth ↔ complexity | -0.027 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **-0.517**
- modularity ↔ depth: **-0.581**
- pairs at |r| ≥ 0.5: **2/10**
- max |r|: **0.581**

### feedback_x_tangle

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.413 |
| modularity ↔ depth ** | -0.581 ** |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth ** | -0.618 ** |
| acyclicity ↔ equality | -0.483 |
| acyclicity ↔ complexity | -0.116 |
| depth ↔ equality | +0.345 |
| depth ↔ complexity | -0.027 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **-0.618**
- modularity ↔ depth: **-0.581**
- pairs at |r| ≥ 0.5: **2/10**
- max |r|: **0.618**

### log_cycle_count

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | -0.116 |
| modularity ↔ depth ** | -0.581 ** |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth | +0.411 |
| acyclicity ↔ equality | -0.032 |
| acyclicity ↔ complexity | -0.367 |
| depth ↔ equality | +0.345 |
| depth ↔ complexity | -0.027 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **+0.411**
- modularity ↔ depth: **-0.581**
- pairs at |r| ≥ 0.5: **1/10**
- max |r|: **0.581**

### sentrux_legacy

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | -0.133 |
| modularity ↔ depth ** | -0.581 ** |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth | +0.435 |
| acyclicity ↔ equality | -0.019 |
| acyclicity ↔ complexity | -0.346 |
| depth ↔ equality | +0.345 |
| depth ↔ complexity | -0.027 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **+0.435**
- modularity ↔ depth: **-0.581**
- pairs at |r| ≥ 0.5: **1/10**
- max |r|: **0.581**

## Acyclicity candidate summary

| candidate | acyc↔depth | mod↔depth | max \|r\| |
| --- | ---: | ---: | ---: |
| baseline_tangle | -0.641 | -0.581 | 0.641 |
| largest_scc | -0.700 | -0.581 | 0.700 |
| modular_tangle | -0.683 | -0.581 | 0.683 |
| feedback_edges | -0.517 | -0.581 | 0.581 |
| feedback_x_tangle | -0.618 | -0.581 | 0.618 |
| log_cycle_count | +0.411 | -0.581 | 0.581 |
| sentrux_legacy | +0.435 | -0.581 | 0.581 |

## Per-depth-candidate Pearson correlation matrices

Each row is a candidate depth formulation; acyclicity is held at the
status-quo (baseline_tangle). Tests whether the modularity↔depth
and acyclicity↔depth pairs respond to depth-side reformulations.

### depth_baseline

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.479 |
| modularity ↔ depth ** | -0.581 ** |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth ** | -0.641 ** |
| acyclicity ↔ equality ** | -0.519 ** |
| acyclicity ↔ complexity | -0.113 |
| depth ↔ equality | +0.345 |
| depth ↔ complexity | -0.027 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **-0.641**
- modularity ↔ depth: **-0.581**
- max |r|: **0.641**

### depth_with_scc_penalty

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.479 |
| modularity ↔ depth | -0.295 |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth | +0.245 |
| acyclicity ↔ equality ** | -0.519 ** |
| acyclicity ↔ complexity | -0.113 |
| depth ↔ equality | -0.122 |
| depth ↔ complexity | -0.394 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **+0.245**
- modularity ↔ depth: **-0.295**
- max |r|: **0.519**

### depth_size_relative

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.479 |
| modularity ↔ depth | +0.400 |
| modularity ↔ equality | -0.409 |
| modularity ↔ complexity | +0.002 |
| acyclicity ↔ depth | -0.072 |
| acyclicity ↔ equality ** | -0.519 ** |
| acyclicity ↔ complexity | -0.113 |
| depth ↔ equality | +0.152 |
| depth ↔ complexity | -0.020 |
| equality ↔ complexity | +0.303 |

- acyclicity ↔ depth: **-0.072**
- modularity ↔ depth: **+0.400**
- max |r|: **0.519**

## Depth candidate summary

| candidate | acyc↔depth | mod↔depth | max \|r\| |
| --- | ---: | ---: | ---: |
| depth_baseline | -0.641 | -0.581 | 0.641 |
| depth_with_scc_penalty | +0.245 | -0.295 | 0.519 |
| depth_size_relative | -0.072 | +0.400 | 0.519 |

## Cross-product: best acyclicity × best depth

If a candidate acyclicity AND a candidate depth both reduce |r|,
the combination should compound. This table is the full Cartesian
product over (acyclicity-candidate × depth-candidate), reporting
only the two OECD-relevant pairs.

| acyclicity | depth | acyc↔depth | mod↔depth | moderate pairs (\|r\| ≥ 0.5) |
| --- | --- | ---: | ---: | ---: |
| baseline_tangle | depth_baseline | -0.641 | -0.581 | 3/10 |
| baseline_tangle | depth_with_scc_penalty | +0.245 | -0.295 | 1/10 |
| baseline_tangle | depth_size_relative | -0.072 | +0.400 | 1/10 |
| largest_scc | depth_baseline | -0.700 | -0.581 | 3/10 |
| largest_scc | depth_with_scc_penalty | +0.204 | -0.295 | 1/10 |
| largest_scc | depth_size_relative | -0.073 | +0.400 | 1/10 |
| modular_tangle | depth_baseline | -0.683 | -0.581 | 2/10 |
| modular_tangle | depth_with_scc_penalty | +0.237 | -0.295 | 0/10 |
| modular_tangle | depth_size_relative | -0.102 | +0.400 | 0/10 |
| feedback_edges | depth_baseline | -0.517 | -0.581 | 2/10 |
| feedback_edges | depth_with_scc_penalty | +0.444 | -0.295 | 0/10 |
| feedback_edges | depth_size_relative | -0.283 | +0.400 | 0/10 |
| feedback_x_tangle | depth_baseline | -0.618 | -0.581 | 2/10 |
| feedback_x_tangle | depth_with_scc_penalty | +0.312 | -0.295 | 0/10 |
| feedback_x_tangle | depth_size_relative | -0.153 | +0.400 | 0/10 |
| log_cycle_count | depth_baseline | +0.411 | -0.581 | 1/10 |
| log_cycle_count | depth_with_scc_penalty | +0.762 | -0.295 | 1/10 |
| log_cycle_count | depth_size_relative | -0.173 | +0.400 | 0/10 |
| sentrux_legacy | depth_baseline | +0.435 | -0.581 | 1/10 |
| sentrux_legacy | depth_with_scc_penalty | +0.762 | -0.295 | 1/10 |
| sentrux_legacy | depth_size_relative | -0.185 | +0.400 | 0/10 |

## Aggregator sensitivity (axes held at v0.23 baseline)

For each aggregator, overall scores under the **status-quo acyclicity** axis.
The Pearson correlation of `overall` against each axis is shown; lower
|r| means the aggregator depends less mechanically on that single axis.

| aggregator | r(overall, mod) | r(overall, acy) | r(overall, dep) | r(overall, equ) | r(overall, com) |
| --- | ---: | ---: | ---: | ---: | ---: |
| geomean | +0.262 | +0.552 | -0.135 | +0.069 | +0.555 |
| arith | +0.298 | +0.575 | -0.086 | +0.074 | +0.515 |
| min | +0.130 | +0.400 | -0.158 | +0.077 | +0.378 |
| harmonic | +0.239 | +0.529 | -0.187 | +0.033 | +0.521 |
| mpi | +0.197 | +0.450 | -0.096 | +0.129 | +0.612 |
| pgm | +0.200 | +0.481 | -0.176 | +0.048 | +0.512 |
| penalty_geomean | +0.119 | +0.375 | -0.079 | +0.144 | +0.562 |

Interpretation: the closer all five r-values are to one another, the
more even the aggregator's sensitivity to each axis. A geomean variant
with strongly non-uniform correlations is implicitly weighting some
axes more than others, which is the failure mode MPI / penalty-geomean
are trying to prevent.

## Aggregator score tables (status-quo axes)

### geomean

| project | overall |
| --- | ---: |
| pygments | 0.663 |
| boto3 | 0.653 |
| numpy | 0.647 |
| mkdocs | 0.639 |
| starlette | 0.613 |
| anyio | 0.607 |
| scrapy | 0.603 |
| setuptools | 0.586 |
| botocore | 0.581 |
| pytest | 0.568 |
| archy | 0.564 |
| datasette | 0.557 |
| fastapi | 0.549 |
| sqlalchemy | 0.546 |
| requests | 0.545 |
| rich | 0.544 |
| pydantic | 0.541 |
| django | 0.521 |
| mypy | 0.521 |
| scikit-learn | 0.518 |
| flask | 0.517 |
| dagster | 0.515 |
| httpx | 0.515 |
| click | 0.511 |
| governingdocs | 0.501 |
| ansible | 0.495 |
| aiohttp | 0.494 |
| msgspec | 0.397 |

### arith

| project | overall |
| --- | ---: |
| pygments | 0.682 |
| boto3 | 0.680 |
| mkdocs | 0.659 |
| numpy | 0.658 |
| setuptools | 0.635 |
| starlette | 0.626 |
| anyio | 0.622 |
| botocore | 0.621 |
| archy | 0.613 |
| scrapy | 0.612 |
| pytest | 0.581 |
| fastapi | 0.576 |
| flask | 0.573 |
| scikit-learn | 0.572 |
| datasette | 0.571 |
| sqlalchemy | 0.563 |
| django | 0.561 |
| aiohttp | 0.556 |
| click | 0.556 |
| rich | 0.555 |
| requests | 0.554 |
| pydantic | 0.554 |
| governingdocs | 0.551 |
| mypy | 0.551 |
| httpx | 0.548 |
| dagster | 0.533 |
| ansible | 0.525 |
| msgspec | 0.489 |

### min

| project | overall |
| --- | ---: |
| scrapy | 0.521 |
| numpy | 0.521 |
| pygments | 0.500 |
| anyio | 0.480 |
| pytest | 0.471 |
| mkdocs | 0.469 |
| starlette | 0.458 |
| datasette | 0.442 |
| rich | 0.430 |
| requests | 0.429 |
| boto3 | 0.417 |
| dagster | 0.400 |
| sqlalchemy | 0.388 |
| pydantic | 0.385 |
| botocore | 0.348 |
| setuptools | 0.348 |
| governingdocs | 0.316 |
| fastapi | 0.300 |
| ansible | 0.286 |
| mypy | 0.286 |
| archy | 0.273 |
| django | 0.267 |
| httpx | 0.261 |
| click | 0.235 |
| scikit-learn | 0.222 |
| flask | 0.208 |
| aiohttp | 0.173 |
| msgspec | 0.100 |

### harmonic

| project | overall |
| --- | ---: |
| pygments | 0.646 |
| numpy | 0.636 |
| boto3 | 0.625 |
| mkdocs | 0.621 |
| starlette | 0.601 |
| scrapy | 0.595 |
| anyio | 0.594 |
| pytest | 0.556 |
| datasette | 0.544 |
| botocore | 0.543 |
| setuptools | 0.538 |
| requests | 0.536 |
| rich | 0.533 |
| sqlalchemy | 0.530 |
| pydantic | 0.529 |
| fastapi | 0.518 |
| archy | 0.513 |
| dagster | 0.499 |
| mypy | 0.488 |
| django | 0.477 |
| httpx | 0.476 |
| ansible | 0.464 |
| click | 0.461 |
| governingdocs | 0.460 |
| scikit-learn | 0.457 |
| flask | 0.450 |
| aiohttp | 0.414 |
| msgspec | 0.287 |

### mpi

| project | overall |
| --- | ---: |
| pygments | 0.638 |
| numpy | 0.635 |
| boto3 | 0.629 |
| mkdocs | 0.618 |
| starlette | 0.600 |
| scrapy | 0.593 |
| anyio | 0.590 |
| pytest | 0.554 |
| setuptools | 0.548 |
| botocore | 0.541 |
| datasette | 0.538 |
| requests | 0.536 |
| rich | 0.533 |
| fastapi | 0.532 |
| pydantic | 0.531 |
| sqlalchemy | 0.526 |
| archy | 0.523 |
| mypy | 0.496 |
| django | 0.493 |
| httpx | 0.492 |
| dagster | 0.492 |
| flask | 0.487 |
| click | 0.484 |
| scikit-learn | 0.483 |
| aiohttp | 0.473 |
| ansible | 0.469 |
| governingdocs | 0.437 |
| msgspec | 0.357 |

### pgm

| project | overall |
| --- | ---: |
| pygments | 0.627 |
| numpy | 0.626 |
| mkdocs | 0.602 |
| boto3 | 0.600 |
| starlette | 0.588 |
| scrapy | 0.587 |
| anyio | 0.580 |
| pytest | 0.544 |
| datasette | 0.530 |
| requests | 0.528 |
| rich | 0.523 |
| pydantic | 0.517 |
| sqlalchemy | 0.514 |
| botocore | 0.506 |
| setuptools | 0.495 |
| fastapi | 0.493 |
| dagster | 0.482 |
| archy | 0.472 |
| mypy | 0.461 |
| httpx | 0.446 |
| django | 0.441 |
| ansible | 0.436 |
| click | 0.423 |
| governingdocs | 0.417 |
| scikit-learn | 0.412 |
| flask | 0.405 |
| aiohttp | 0.367 |
| msgspec | 0.231 |

### penalty_geomean

| project | overall |
| --- | ---: |
| numpy | 0.567 |
| pygments | 0.549 |
| scrapy | 0.538 |
| starlette | 0.536 |
| mkdocs | 0.535 |
| boto3 | 0.532 |
| anyio | 0.522 |
| pytest | 0.497 |
| requests | 0.490 |
| rich | 0.483 |
| pydantic | 0.481 |
| datasette | 0.480 |
| sqlalchemy | 0.467 |
| fastapi | 0.461 |
| botocore | 0.451 |
| setuptools | 0.449 |
| dagster | 0.438 |
| archy | 0.431 |
| mypy | 0.430 |
| httpx | 0.424 |
| django | 0.419 |
| ansible | 0.410 |
| click | 0.409 |
| flask | 0.402 |
| scikit-learn | 0.401 |
| aiohttp | 0.389 |
| governingdocs | 0.376 |
| msgspec | 0.296 |

## Rank stability of winning axis combinations

Spearman ρ of each candidate axis-combination's overall (geomean)
against the v0.23 baseline. ρ near 1 means projects re-rank little;
ρ < 0.9 means the leaderboard would visibly shake up.

| acyclicity | depth | spearman ρ vs v0.23 |
| --- | --- | ---: |
| baseline_tangle | depth_baseline | +1.000 |
| feedback_edges | depth_baseline | +0.691 |
| modular_tangle | depth_baseline | +0.885 |
| baseline_tangle | depth_with_scc_penalty | +0.693 |
| feedback_edges | depth_with_scc_penalty | +0.534 |
| modular_tangle | depth_with_scc_penalty | +0.638 |
| feedback_x_tangle | depth_with_scc_penalty | +0.648 |
| baseline_tangle | depth_size_relative | +0.685 |
| feedback_edges | depth_size_relative | +0.621 |

## Rank stability under aggregator changes

Spearman ρ between aggregator overall-rankings. ρ near 1 means the
aggregator change re-orders the projects very little; ρ < 0.9 means
the new aggregator would visibly shake up the leaderboard.

| pair | spearman ρ |
| --- | ---: |
| geomean ↔ arith | +0.897 |
| geomean ↔ min | +0.793 |
| geomean ↔ harmonic | +0.949 |
| geomean ↔ mpi | +0.969 |
| geomean ↔ pgm | +0.907 |
| geomean ↔ penalty_geomean | +0.890 |
| arith ↔ min | +0.543 |
| arith ↔ harmonic | +0.754 |
| arith ↔ mpi | +0.817 |
| arith ↔ pgm | +0.679 |
| arith ↔ penalty_geomean | +0.661 |
| min ↔ harmonic | +0.914 |
| min ↔ mpi | +0.855 |
| min ↔ pgm | +0.938 |
| min ↔ penalty_geomean | +0.929 |
| harmonic ↔ mpi | +0.980 |
| harmonic ↔ pgm | +0.985 |
| harmonic ↔ penalty_geomean | +0.962 |
| mpi ↔ pgm | +0.957 |
| mpi ↔ penalty_geomean | +0.950 |
| pgm ↔ penalty_geomean | +0.986 |

## Per-project axis dump (debugging)

| project | mod | acy_baseline | acy_largest | acy_feedback | acy_log | acy_legacy | depth | equality | complexity |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| aiohttp | 0.530 | 0.173 | 0.173 | 0.343 | 0.591 | 0.500 | 0.727 | 0.563 | 0.785 |
| ansible | 0.614 | 0.769 | 0.819 | 0.750 | 0.339 | 0.143 | 0.286 | 0.383 | 0.573 |
| anyio | 0.499 | 0.643 | 0.643 | 0.791 | 0.591 | 0.500 | 0.615 | 0.480 | 0.872 |
| archy | 0.512 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.667 | 0.273 | 0.615 |
| boto3 | 0.689 | 0.897 | 0.897 | 0.972 | 0.591 | 0.500 | 0.533 | 0.417 | 0.861 |
| botocore | 0.563 | 0.934 | 0.961 | 0.988 | 0.477 | 0.333 | 0.348 | 0.439 | 0.823 |
| click | 0.451 | 0.235 | 0.353 | 0.567 | 0.477 | 0.333 | 0.800 | 0.575 | 0.717 |
| dagster | 0.575 | 0.400 | 0.408 | 0.313 | 0.477 | 0.333 | 0.471 | 0.416 | 0.803 |
| datasette | 0.534 | 0.831 | 0.881 | 0.939 | 0.477 | 0.333 | 0.471 | 0.442 | 0.578 |
| django | 0.640 | 0.754 | 0.826 | 0.817 | 0.261 | 0.059 | 0.267 | 0.399 | 0.746 |
| fastapi | 0.522 | 0.771 | 0.854 | 0.895 | 0.477 | 0.333 | 0.615 | 0.300 | 0.671 |
| flask | 0.484 | 0.208 | 0.208 | 0.426 | 0.591 | 0.500 | 0.800 | 0.569 | 0.802 |
| governingdocs | 0.615 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.500 | 0.326 | 0.316 |
| httpx | 0.482 | 0.261 | 0.522 | 0.713 | 0.477 | 0.333 | 0.667 | 0.550 | 0.782 |
| mkdocs | 0.520 | 0.787 | 0.787 | 0.819 | 0.591 | 0.500 | 0.615 | 0.469 | 0.903 |
| msgspec | 0.427 | 0.100 | 0.100 | 0.500 | 0.591 | 0.500 | 0.889 | 0.570 | 0.458 |
| mypy | 0.571 | 0.815 | 0.913 | 0.938 | 0.358 | 0.167 | 0.286 | 0.464 | 0.620 |
| numpy | 0.596 | 0.745 | 0.745 | 0.727 | 0.591 | 0.500 | 0.571 | 0.521 | 0.856 |
| pydantic | 0.636 | 0.385 | 0.558 | 0.383 | 0.477 | 0.333 | 0.615 | 0.459 | 0.673 |
| pygments | 0.565 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.500 | 0.676 | 0.668 |
| pytest | 0.478 | 0.710 | 0.739 | 0.842 | 0.477 | 0.333 | 0.471 | 0.490 | 0.757 |
| requests | 0.429 | 0.579 | 0.579 | 0.712 | 0.591 | 0.500 | 0.571 | 0.469 | 0.722 |
| rich | 0.524 | 0.450 | 0.470 | 0.435 | 0.477 | 0.333 | 0.667 | 0.430 | 0.705 |
| scikit-learn | 0.525 | 0.824 | 0.940 | 0.956 | 0.257 | 0.056 | 0.222 | 0.477 | 0.810 |
| scrapy | 0.521 | 0.640 | 0.663 | 0.755 | 0.419 | 0.250 | 0.533 | 0.552 | 0.814 |
| setuptools | 0.766 | 0.931 | 0.943 | 0.958 | 0.419 | 0.250 | 0.348 | 0.367 | 0.762 |
| sqlalchemy | 0.571 | 0.388 | 0.537 | 0.460 | 0.339 | 0.143 | 0.471 | 0.568 | 0.819 |
| starlette | 0.458 | 0.588 | 0.588 | 0.658 | 0.591 | 0.500 | 0.727 | 0.547 | 0.811 |

