# Change-coupling threshold + composition sweep

Output of `uv run --with pyyaml python bench/coupling_sweep.py`. Captured 2026-07-04.
`max_commit_files=30`. Each cell = total surfaced pairs (src<->src).

## Per-project (total / src_src) by (min_support, min_confidence)

| project | (3,0.3) | (5,0.3) | (5,0.5) | (8,0.5) | (5,0.7) |
| --- | ---: | ---: | ---: | ---: | ---: |
| starlette | 131 / 56 | 48 / 20 | 8 / 5 | 0 / 0 | 0 / 0 |
| httpx | 128 / 27 | 96 / 18 | 44 / 9 | 35 / 3 | 13 / 1 |
| click | 24 / 24 | 22 / 22 | 11 / 11 | 0 / 0 | 0 / 0 |
| rich | 347 / 293 | 255 / 236 | 231 / 220 | 7 / 4 | 215 / 212 |
| flask | 37 / 35 | 31 / 31 | 5 / 5 | 2 / 2 | 0 / 0 |
| requests | 30 / 24 | 10 / 5 | 1 / 0 | 0 / 0 | 0 / 0 |
| pytest | 102 / 45 | 47 / 20 | 12 / 5 | 5 / 1 | 5 / 2 |
| pydantic | 391 / 176 | 193 / 95 | 75 / 34 | 35 / 16 | 20 / 6 |
| fastapi | 149 / 87 | 55 / 49 | 24 / 19 | 12 / 12 | 17 / 12 |
| django | 1437 / 331 | 596 / 165 | 275 / 68 | 131 / 34 | 96 / 20 |
| sqlalchemy | 716 / 193 | 421 / 81 | 163 / 33 | 95 / 14 | 53 / 18 |
| scrapy | 353 / 140 | 150 / 80 | 48 / 21 | 17 / 14 | 13 / 3 |
| scikit-learn | 497 / 316 | 213 / 131 | 77 / 45 | 45 / 35 | 17 / 5 |
| numpy | 473 / 118 | 241 / 64 | 124 / 34 | 73 / 22 | 65 / 18 |
| pytorch | 9424 / 5084 | 4185 / 2211 | 2301 / 1159 | 980 / 587 | 1132 / 521 |
| aiohttp | 182 / 77 | 116 / 36 | 25 / 7 | 16 / 1 | 9 / 3 |
| anyio | 253 / 144 | 114 / 63 | 61 / 35 | 25 / 12 | 18 / 9 |
| msgspec | 74 / 7 | 30 / 1 | 9 / 1 | 5 / 1 | 6 / 1 |
| datasette | 168 / 37 | 103 / 23 | 48 / 15 | 22 / 9 | 18 / 3 |
| mkdocs | 135 / 76 | 90 / 58 | 27 / 22 | 10 / 7 | 2 / 1 |
| home-assistant | 22160 / 8450 | 11441 / 4983 | 6876 / 2956 | 3425 / 1714 | 2834 / 1151 |
| mypy | 581 / 386 | 393 / 270 | 130 / 85 | 82 / 58 | 30 / 17 |
| ansible | 586 / 230 | 199 / 87 | 89 / 34 | 51 / 15 | 24 / 4 |
| dagster | 4596 / 2220 | 2236 / 1095 | 1210 / 689 | 722 / 488 | 687 / 494 |
| boto3 | 123 / 40 | 41 / 12 | 30 / 10 | 13 / 2 | 11 / 4 |
| botocore | 297 / 21 | 148 / 14 | 93 / 6 | 53 / 5 | 54 / 2 |
| pygments | 277 / 257 | 62 / 55 | 21 / 19 | 7 / 5 | 4 / 4 |
| setuptools | 269 / 123 | 186 / 74 | 46 / 19 | 21 / 8 | 11 / 10 |

## Aggregate (median across projects)

| (min_support, min_confidence) | median total | median src_src | median src_test |
| --- | ---: | ---: | ---: |
| (3, 0.3) | 273 | 120 | 50 |
| (5, 0.3) | 132 | 60 | 24 |
| (5, 0.5) | 48 | 20 | 8 |
| (8, 0.5) | 22 | 8 | 4 |
| (5, 0.7) | 17 | 4 | 2 |

## FP spot-check

_Manual, not produced by this script._ Top 15 source-only pairs each on a
diverse trio at the shipped default (`--min-support 5 --min-confidence 0.5`,
`max_commit_files 30`), hand-classified 2026-07-04.

| project | genuinely co-changing + no edge | dominant pattern |
| --- | ---: | --- |
| fastapi | 15/15 | security-scheme cluster (oauth2 / api_key / http / open_id, no shared base) + `_compat`<->`dependencies.utils`; 5 are docs_src tutorial sibling variants (real, but intentional parallel maintenance) |
| django | 15/15 | per-backend implementation families (gis adapters/models, db backend clients, cache backends db/dummy/locmem, template loaders) that co-change in lockstep with no sibling edge |
| mypy | 15/15 | architectural couplings: `semanal`<->`semanal_pass1` (two-pass), the `ir.ops`/`subtype`/`sametype`/`rt_subtype` type-op cluster, `constraints`<->`typevartuples` |

**Precision is high by construction** and the FP taxonomy is different from
duplicates: the confidence metric guarantees the pair genuinely co-changes and
the no-edge filter guarantees they are structurally disconnected, so every
surfaced pair IS a real behavioral coupling the graph misses. The residual
judgment is the same benign-vs-refactorable call as duplicates (§12c): the
dominant pattern is **parallel-implementation families** (per-backend, per-scheme
siblings) - which is precisely the "missing shared abstraction / hidden
dependency" signal the tool is meant to surface, left for the reader to action
or dismiss. The **source-only default** (test modules excluded) is what makes
this clean: at (5,0.5) test-involving pairs were ~54% of the raw volume
(src<->test 3384, test<->test 3114 vs src<->src 5566 summed across the corpus),
and on test-heavy repos (django, sqlalchemy) they buried the source pairs
entirely. `--include-tests` restores them.
