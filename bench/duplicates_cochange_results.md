# Duplicate co-change demotion sweep (#242)

Output of `uv run --with pyyaml python bench/duplicates_cochange_sweep.py`. Captured 2026-07-05. Thresholds: the shipped defaults (min_support 3, min_confidence 0.3, min_evidence 5).

| project | primary before | primary after | demoted independent | of which source-only |
| --- | ---: | ---: | ---: | ---: |
| starlette | 4 | 3 | 1 | 1 |
| httpx | 13 | 13 | 0 | 0 |
| click | 8 | 8 | 0 | 0 |
| rich | 1 | 0 | 1 | 1 |
| flask | 7 | 7 | 0 | 0 |
| requests | 1 | 1 | 0 | 0 |
| pytest | 55 | 22 | 33 | 1 |
| pydantic | 89 | 72 | 17 | 15 |
| fastapi | 68 | 68 | 0 | 0 |
| django | 171 | 115 | 56 | 44 |
| sqlalchemy | 151 | 120 | 31 | 29 |
| scrapy | 15 | 13 | 2 | 2 |
| scikit-learn | 126 | 92 | 34 | 30 |
| numpy | 58 | 56 | 2 | 2 |
| pytorch | 1278 | 1036 | 242 | 152 |
| aiohttp | 24 | 23 | 1 | 1 |
| anyio | 19 | 19 | 0 | 0 |
| msgspec | 2 | 2 | 0 | 0 |
| datasette | 7 | 7 | 0 | 0 |
| mkdocs | 6 | 6 | 0 | 0 |
| home-assistant | 1930 | 1579 | 351 | 341 |
| mypy | 80 | 65 | 15 | 15 |
| ansible | 52 | 42 | 10 | 8 |
| dagster | 620 | 549 | 71 | 58 |
| boto3 | 9 | 9 | 0 | 0 |
| botocore | 23 | 22 | 1 | 1 |
| pygments | 25 | 19 | 6 | 6 |
| setuptools | 14 | 11 | 3 | 2 |

Median demotion: 12% of the primary tier (range 0-100%). Total demoted across the corpus: 877 (709 source-only).

## FP spot-check

_Manual, not produced by this script._ Hand-classify a sample of the `independent`-demoted clusters (member modules) as genuinely-benign parallel copies (per-backend implementations, symmetric methods) vs real refactorable duplication wrongly hidden; the recall risk is a *recently* forked copy that has not yet had a chance to co-change (the evidence guard only catches rarely-touched files). See RESEARCH_METRICS §12f.
