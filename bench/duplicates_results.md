# Duplicate-function threshold sweep

Output of `uv run --with pyyaml python bench/duplicates_sweep.py`. Captured 2026-07-04.

`groups_N` = number of duplicate clusters at `--min-nodes N`; `dupfns_N` = total functions across those clusters. Each project is scanned at its pinned `src_dir`. Pick the shipping default at the knee where short-stub clusters have dropped but real duplication remains; confirm with the manual FP spot-check below.

## Per-project results

| project | sha | groups@5 | groups@10 | groups@20 | groups@40 | dupfns@5 | dupfns@10 | dupfns@20 | dupfns@40 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| starlette | `de970d7` | 39 | 33 | 21 | 8 | 142 | 103 | 51 | 20 |
| httpx | `b5addb6` | 43 | 37 | 26 | 16 | 132 | 104 | 65 | 41 |
| click | `8a1b1a3` | 34 | 29 | 18 | 7 | 90 | 75 | 41 | 16 |
| rich | `46cebbb` | 47 | 41 | 19 | 6 | 155 | 122 | 41 | 12 |
| flask | `36e4a82` | 33 | 28 | 14 | 8 | 115 | 93 | 42 | 23 |
| requests | `d64b9ad` | 18 | 13 | 8 | 3 | 45 | 32 | 19 | 8 |
| pytest | `070e35b` | 97 | 88 | 35 | 12 | 352 | 272 | 84 | 28 |
| pydantic | `8dbb2a1` | 181 | 168 | 119 | 58 | 575 | 499 | 290 | 138 |
| fastapi | `c61384e` | 33 | 30 | 23 | 16 | 91 | 80 | 63 | 47 |
| django | `189c2d2` | 657 | 626 | 432 | 153 | 2820 | 2358 | 1173 | 349 |
| sqlalchemy | `ddf3b65` | 783 | 746 | 502 | 205 | 4205 | 3442 | 1367 | 508 |
| scrapy | `fada8be` | 81 | 69 | 44 | 16 | 275 | 193 | 98 | 33 |
| scikit-learn | `8fac97f` | 459 | 439 | 339 | 173 | 1638 | 1396 | 1000 | 483 |
| numpy | `7dfe67e` | 725 | 699 | 568 | 387 | 2574 | 2184 | 1614 | 1047 |
| pytorch | `4ece0fc` | 2698 | 2618 | 1990 | 934 | 12917 | 10841 | 5654 | 2248 |
| aiohttp | `7b0b013` | 84 | 75 | 45 | 13 | 456 | 272 | 134 | 29 |
| anyio | `1dbc3b6` | 91 | 85 | 51 | 28 | 417 | 340 | 141 | 63 |
| msgspec | `54a7c2f` | 2 | 2 | 1 | 0 | 4 | 4 | 2 | 0 |
| datasette | `dfd5b95` | 43 | 36 | 20 | 8 | 142 | 97 | 49 | 17 |
| mkdocs | `2862536` | 156 | 149 | 135 | 99 | 426 | 380 | 343 | 242 |
| home-assistant | `ff87c54` | 4016 | 3990 | 3552 | 1738 | 23100 | 20756 | 14223 | 4827 |
| mypy | `74ecdd8` | 339 | 318 | 238 | 107 | 1739 | 1422 | 874 | 266 |
| ansible | `b475463` | 259 | 243 | 136 | 62 | 1075 | 842 | 331 | 140 |
| dagster | `c8a8460` | 653 | 622 | 448 | 193 | 3945 | 3093 | 1398 | 470 |
| boto3 | `447ab8f` | 40 | 37 | 20 | 10 | 113 | 104 | 47 | 22 |
| botocore | `dbfa9d0` | 151 | 135 | 69 | 27 | 563 | 442 | 172 | 62 |
| pygments | `74fdf56` | 53 | 48 | 38 | 22 | 247 | 225 | 185 | 58 |
| setuptools | `84ed591` | 205 | 190 | 111 | 59 | 712 | 583 | 283 | 131 |
| archy | `v0.22.0` | 21 | 18 | 11 | 4 | 46 | 39 | 23 | 8 |

## Aggregate (median across projects)

| min-nodes | median groups | median dup fns |
| ---: | ---: | ---: |
| 5 | 91.0 | 417.0 |
| 10 | 85.0 | 272.0 |
| 20 | 45.0 | 141.0 |
| 40 | 22.0 | 58.0 |

## FP spot-check (manual, 2026-07-04)

_Not produced by this script._ 15 stratified clusters (sampled across the full ranking, not top-only) hand-classified per project at `--min-nodes 20`, true-duplicate vs false-positive.

| project | clusters@20 | clusters@40 | TRUE/15 | FP/15 | precision |
| --- | ---: | ---: | ---: | ---: | ---: |
| fastapi | 23 | 16 | 7 | 8 | 47% |
| pytest | 35 | 12 | 7 | 8 | 47% |
| django | 432 | 153 | 5 | 10 | 33% |
| **total** | | | **19** | **26** | **~42%** |

**~58% false-positive rate at min-nodes 20.** Materially better than the rejected dead-code study (100% FP; RESEARCH_METRICS.md section 12), and real duplication is surfaced (3-way serializer copy-paste in django, byte-identical `__call__` overrides in fastapi, sibling-plugin drain loops in pytest). But not a low-FP signal, and **a bigger `--min-nodes` cannot fix it** because the FP classes live at different sizes per project:

- **pytest** - trivial boilerplate (property shims, one-line delegations), all below ~size 40; the floor helps.
- **fastapi** - intentional public-API signature expansion (HTTP-verb methods, `Query`/`Header`/`Form` factories), at the *largest* sizes (136-163); the floor is useless.
- **django** - paired/symmetric methods differing only in the one constant that is the point (forward/backward migration halves, x/y setters, date/time SQL casts, per-backend dispatch), spanning sizes 22-73; the floor cannot separate them.

**Decision:** ship the CLI at a **calibrated default of `--min-nodes 30`** (removes the trivial-boilerplate tail that concentrates below ~30 without losing most genuine small duplicates), framed frankly as a high-recall / moderate-precision advisory. The real precision fix is a semantic de-noiser (suppress same-class siblings differing only by a literal constant), tracked in #242, which also gates the `archy_duplicates` MCP tool.

## Post-de-noiser re-validation (#242, two-tier, min-nodes 30, 2026-07-04)

The de-noiser (`classify_variants`: same-class + trivial + `@overload`/`@property`) splits output into a primary "likely duplicate" tier and a demoted "variant" tier. Re-run 15x3 spot-check on the PRIMARY tier:

| project | primary precision | wrongly-demoted (recall loss) |
| --- | ---: | ---: |
| fastapi | 8/14 (57%) | 2/4 |
| pytest | 6/13 (46%) | 0/6 |
| django | 7/15 (47%) | 0/8 |
| **aggregate** | **~21/42 (~50%)** | 2/18 |

At the same floor the de-noiser lifts primary precision from ~38% (no filter) to ~50% (~12 pt); `same_class` demotes correctly ~83% of the time, with recall softened by the two-tier design (demoted != hidden). It plateaus at ~50% because the dominant residual FPs (cross-class / module-level symmetric siblings and public-API boilerplate) are semantically intentional, which no pure-syntax signal can separate - a finding confirmed by a 94-source literature review (RESEARCH_METRICS.md section 12c): ~50% refactorability precision is the expected ceiling (Kapser & Godfrey: up to 71% of real clones are benign). The one non-ML lever that breaks it is **change-history co-change** (>94% precision in the literature), planned as the next phase unified with change-coupling (#131).
