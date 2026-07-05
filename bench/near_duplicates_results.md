# Type-3 near-miss threshold sweep (#246)

Output of `uv run --with pyyaml python bench/near_duplicates_sweep.py`. Captured 2026-07-05. `compute_near_duplicates` cluster count (and seconds) per `min_similarity`, whole-repo. The default floor is 0.85. Whole-repo counts are inflated by near-clones in test/example code (as in #247); the giants (django/numpy/pytorch/home-assistant, 30-44s) hit the comparison cap so their counts are incomplete (the warning fires). See §12h for the source-only spot-check and why the count is a poor precision proxy (connected-component clustering makes it non-monotonic in the threshold).

| project | modules | @0.75 | @0.8 | @0.85 | @0.9 |
| --- | ---: | ---: | ---: | ---: | ---: |
| starlette | 67 | 49 (0.7s) | 64 (0.5s) | 75 (0.4s) | 52 (0.3s) |
| httpx | 60 | 55 (0.6s) | 93 (0.5s) | 98 (0.4s) | 85 (0.3s) |
| click | 22 | 24 (0.1s) | 16 (0.1s) | 7 (0.1s) | 4 (0.1s) |
| rich | 213 | 70 (1.4s) | 77 (1.2s) | 89 (1.0s) | 63 (0.6s) |
| flask | 55 | 17 (0.1s) | 9 (0.1s) | 4 (0.1s) | 2 (0.1s) |
| requests | 37 | 21 (0.3s) | 43 (0.2s) | 34 (0.2s) | 19 (0.1s) |
| pytest | 256 | 118 (13.3s) | 166 (10.3s) | 199 (7.5s) | 172 (5.1s) |
| pydantic | 404 | 220 (20.0s) | 319 (14.8s) | 399 (11.1s) | 305 (7.5s) |
| fastapi | 1121 | 70 (1.4s) | 77 (1.2s) | 74 (1.0s) | 76 (0.8s) |
| django | 2922 | 41 (32.0s) | 84 (31.7s) | 218 (32.0s) | 441 (33.3s) |
| sqlalchemy | 668 | 66 (31.9s) | 156 (32.1s) | 351 (32.8s) | 536 (33.4s) |
| scrapy | 440 | 142 (9.2s) | 243 (7.3s) | 262 (5.5s) | 191 (3.7s) |
| scikit-learn | 1014 | 107 (39.3s) | 290 (40.7s) | 508 (40.3s) | 372 (27.0s) |
| numpy | 495 | 125 (35.6s) | 400 (37.1s) | 575 (29.5s) | 469 (19.8s) |
| pytorch | 4608 | 47 (44.5s) | 102 (43.2s) | 213 (43.1s) | 252 (42.8s) |
| aiohttp | 164 | 111 (12.9s) | 215 (10.1s) | 336 (7.6s) | 313 (5.2s) |
| anyio | 74 | 67 (1.6s) | 97 (1.3s) | 125 (1.0s) | 86 (0.7s) |
| msgspec | 63 | 61 (1.4s) | 102 (1.2s) | 117 (0.9s) | 118 (0.6s) |
| datasette | 153 | 118 (3.4s) | 165 (2.7s) | 118 (2.1s) | 65 (1.5s) |
| mkdocs | 65 | 29 (0.6s) | 49 (0.5s) | 80 (0.4s) | 58 (0.3s) |
| home-assistant | 17666 | 30 (44.2s) | 77 (44.3s) | 161 (43.3s) | 308 (42.4s) |
| mypy | 437 | 157 (21.7s) | 354 (17.2s) | 300 (13.0s) | 205 (8.7s) |
| ansible | 1811 | 236 (36.8s) | 456 (29.0s) | 414 (21.5s) | 292 (14.5s) |
| dagster | 5918 | 63 (34.7s) | 158 (34.8s) | 331 (34.9s) | 455 (36.1s) |
| boto3 | 104 | 57 (0.5s) | 84 (0.4s) | 96 (0.3s) | 76 (0.3s) |
| botocore | 290 | 99 (16.2s) | 183 (12.7s) | 281 (9.4s) | 320 (6.3s) |
| pygments | 403 | 76 (0.9s) | 74 (0.8s) | 54 (0.7s) | 38 (0.6s) |
| setuptools | 327 | 121 (6.1s) | 169 (4.8s) | 149 (3.7s) | 77 (2.6s) |

| min_similarity | median clusters | max secs |
| ---: | ---: | ---: |
| 0.75 | 68 | 44.5 |
| 0.8 | 102 | 44.3 |
| 0.85 | 155 | 43.3 |
| 0.9 | 145 | 42.8 |

## FP spot-check

_Manual, not produced by this script._ At the chosen `min_similarity`, draw ~15 near-miss clusters from a diverse trio, hand-classify genuine-Type-3 (a real gapped clone) vs coincidental (two unrelated functions the tiny token vocabulary made look alike), record the rate + the dominant FP pattern. See RESEARCH_METRICS §12h.
