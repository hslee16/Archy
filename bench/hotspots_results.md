# Hotspots window sweep

Output of `uv run --with pyyaml python bench/hotspots_sweep.py`. Captured 2026-05-15.

Top-K = 20. Windows: full history, 12 months, 6 months. 
Jaccard = |A intersect B| / |A union B| on the per-window top-K module sets. 
`stale_full_frac` = fraction of full-history top-K modules that are NOT in the 12-month top-K (the recency-contamination proxy: high means full-history is dominated by complex-but-dead files).

## Per-project results

| project | sha | |full| | |12mo| | |6mo| | J(full,12mo) | J(full,6mo) | J(12mo,6mo) | stale_full_frac |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| starlette | `7793b92` | 20 | 20 | 20 | 0.82 | 0.60 | 0.60 | 0.10 |
| httpx | `b5addb6` | 20 | 10 | 3 | 0.15 | 0.05 | 0.30 | 0.80 |
| click | `fc6c7c4` | 20 | 15 | 15 | 0.75 | 0.75 | 1.00 | 0.25 |
| rich | `46cebbb` | 20 | 20 | 20 | 0.67 | 0.60 | 0.74 | 0.20 |
| flask | `7374c85` | 20 | 14 | 7 | 0.55 | 0.29 | 0.50 | 0.40 |
| requests | `b684dcb` | 20 | 20 | 20 | 0.74 | 0.74 | 1.00 | 0.15 |
| pytest | `856da14` | 20 | 20 | 20 | 0.54 | 0.38 | 0.54 | 0.30 |
| pydantic | `5c63f86` | 20 | 20 | 20 | 0.67 | 0.60 | 0.82 | 0.20 |
| fastapi | `e89a37e` | 20 | 20 | 20 | 0.60 | 0.48 | 0.82 | 0.25 |
| django | `4d455ae` | 20 | 20 | 20 | 0.54 | 0.60 | 0.74 | 0.30 |
| sqlalchemy | `1e1c008` | 20 | 20 | 20 | 0.48 | 0.54 | 0.90 | 0.35 |
| scrapy | `5223dbe` | 20 | 20 | 20 | 0.54 | 0.48 | 0.74 | 0.30 |
| scikit-learn | `13f20d7` | 20 | 20 | 20 | 0.43 | 0.33 | 0.82 | 0.40 |
| numpy | `0a1ed72` | 20 | 20 | 20 | 0.54 | 0.54 | 0.90 | 0.30 |
| aiohttp | `e8f4371` | 20 | 20 | 20 | 0.67 | 0.67 | 0.60 | 0.20 |
| anyio | `bcb2db6` | 20 | 20 | 20 | 0.74 | 0.67 | 0.90 | 0.15 |
| msgspec | `3b2543b` | 20 | 20 | 19 | 0.67 | 0.39 | 0.56 | 0.20 |
| datasette | `aa84fe0` | 20 | 20 | 20 | 0.60 | 0.54 | 0.74 | 0.25 |
| mkdocs | `2862536` | 20 | 1 | 0 | 0.00 | 0.00 | 0.00 | 1.00 |
| mypy | `e53693b` | 20 | 20 | 20 | 0.74 | 0.54 | 0.74 | 0.15 |
| ansible | `b7c0900` | 20 | 20 | 20 | 0.60 | 0.48 | 0.74 | 0.25 |
| dagster | `8e7f318` | 20 | 20 | 20 | 0.48 | 0.29 | 0.48 | 0.35 |
| boto3 | `81a86c9` | 20 | 20 | 20 | 0.43 | 0.38 | 0.82 | 0.40 |
| botocore | `2b64927` | 20 | 20 | 20 | 0.54 | 0.54 | 0.82 | 0.30 |
| pygments | `6fe2c31` | 20 | 20 | 20 | 0.67 | 0.67 | 0.90 | 0.20 |
| setuptools | `84ed591` | 20 | 20 | 20 | 0.60 | 0.21 | 0.38 | 0.25 |
| archy | `v0.18.0` | 20 | 20 | 20 | 1.00 | 1.00 | 1.00 | 0.00 |

## Aggregate (median across projects)

| metric | median |
| --- | ---: |
| J(full, 12mo) | 0.600 |
| J(full, 6mo)  | 0.538 |
| J(12mo, 6mo)  | 0.739 |
| stale_full_frac | 0.250 |
