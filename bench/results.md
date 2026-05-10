# Benchmark results

Output of `uv run --with networkx --with pyyaml python bench/run.py --vulture`.
SHAs pinned in `bench/projects.yaml`. Captured 2026-05-10.

## Score table

| name | sha | modules | edges | overall | modularity | acyclicity | depth | equality |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| numpy | `3bea241` | 424 | 1191 | 0.611 | 0.609 | 0.745 | 0.571 | 0.538 |
| mkdocs | `2862536` | 61 | 175 | 0.589 | 0.526 | 0.787 | 0.615 | 0.472 |
| starlette | `7793b92` | 34 | 114 | 0.572 | 0.458 | 0.588 | 0.727 | 0.547 |
| scrapy | `5223dbe` | 172 | 858 | 0.560 | 0.521 | 0.640 | 0.533 | 0.552 |
| datasette | `aa84fe0` | 59 | 172 | 0.555 | 0.551 | 0.881 | 0.444 | 0.441 |
| anyio | `bcb2db6` | 42 | 158 | 0.555 | 0.499 | 0.643 | 0.615 | 0.480 |
| archy | `HEAD` | 12 | 24 | 0.550 | 0.459 | 1.000 | 0.667 | 0.299 |
| pytest | `09f969f` | 69 | 372 | 0.529 | 0.479 | 0.710 | 0.471 | 0.490 |
| fastapi | `622b635` | 48 | 114 | 0.522 | 0.522 | 0.771 | 0.615 | 0.300 |
| pydantic | `bd8e63e` | 104 | 496 | 0.513 | 0.636 | 0.385 | 0.615 | 0.459 |
| rich | `46cebbb` | 100 | 420 | 0.510 | 0.524 | 0.450 | 0.667 | 0.431 |
| requests | `e8d2c01` | 19 | 73 | 0.508 | 0.429 | 0.579 | 0.571 | 0.469 |
| mypy | `82fb613` | 195 | 1104 | 0.499 | 0.571 | 0.815 | 0.286 | 0.465 |
| sqlalchemy | `3c650ce` | 255 | 2536 | 0.492 | 0.565 | 0.388 | 0.471 | 0.568 |
| ansible | `b7c0900` | 583 | 2148 | 0.477 | 0.614 | 0.770 | 0.286 | 0.383 |
| django | `4d455ae` | 902 | 3234 | 0.477 | 0.641 | 0.754 | 0.267 | 0.401 |
| click | `fc6c7c4` | 17 | 60 | 0.470 | 0.451 | 0.235 | 0.800 | 0.575 |
| httpx | `b5addb6` | 23 | 87 | 0.463 | 0.482 | 0.261 | 0.667 | 0.550 |
| flask | `7374c85` | 24 | 94 | 0.463 | 0.484 | 0.208 | 0.800 | 0.569 |
| scikit-learn | `6e9ef2b` | 637 | 3856 | 0.459 | 0.523 | 0.826 | 0.216 | 0.476 |
| aiohttp | `bb35b1c` | 54 | 320 | 0.440 | 0.532 | 0.185 | 0.667 | 0.569 |
| msgspec | `3b2543b` | 10 | 19 | 0.384 | 0.440 | 0.100 | 0.889 | 0.553 |

## Pairwise Pearson correlations

| pair | r |
| --- | ---: |
| modularity ↔ acyclicity | +0.281 |
| modularity ↔ depth | -0.610 |
| modularity ↔ equality | -0.226 |
| acyclicity ↔ depth | -0.646 |
| acyclicity ↔ equality | -0.691 |
| depth ↔ equality | +0.350 |

## Vulture findings

| project | sha | LOC | vulture @60% | vulture @90% |
| --- | --- | ---: | ---: | ---: |
| sqlalchemy | `3c650ce` | 246,065 | 1827 | 415 |
| scikit-learn | `6e9ef2b` | 211,188 | 246 | 31 |
| django | `4d455ae` | 156,666 | 2017 | 12 |
| ansible | `b7c0900` | 135,915 | 949 | 54 |
| numpy | `3bea241` | 123,708 | 395 | 57 |
| mypy | `82fb613` | 113,094 | 208 | 21 |
| pydantic | `bd8e63e` | 45,563 | 210 | 22 |
| rich | `46cebbb` | 38,515 | 89 | 12 |
| pytest | `09f969f` | 37,079 | 162 | 11 |
| scrapy | `5223dbe` | 29,057 | 186 | 7 |
| aiohttp | `bb35b1c` | 26,237 | 199 | 17 |
| datasette | `aa84fe0` | 19,946 | 105 | 17 |
| fastapi | `622b635` | 19,335 | 129 | 8 |
| anyio | `bcb2db6` | 14,455 | 78 | 2 |
| click | `fc6c7c4` | 11,529 | 32 | 3 |
| flask | `7374c85` | 9,502 | 76 | 7 |
| httpx | `b5addb6` | 8,827 | 69 | 3 |
| mkdocs | `2862536` | 7,084 | 88 | 8 |
| starlette | `7793b92` | 6,584 | 67 | 0 |
| requests | `e8d2c01` | 6,371 | 54 | 4 |
| archy | `HEAD` | 2,528 | 16 | 0 |
| msgspec | `3b2543b` | 2,365 | 10 | 4 |
