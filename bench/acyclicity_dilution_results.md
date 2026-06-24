# Acyclicity-term dilution study (issue #192)

Should the score composite be changed so a single structural regression registers on `overall` at scale? This compares the current acyclicity axis (`1 - tangle_ratio`) against three count-sensitive candidates. Only the acyclicity term is swapped; the other four axes and the geometric mean are unchanged. See `docs/research/ACYCLICITY_DILUTION_EMPIRICS.md` for the decision.

## 1. Clean-graph acyclicity axis, per candidate (the decisive cost)
A healthy graph with a few stock cycles. The count candidates dock a large, near-acyclic codebase (low `tangle_ratio`, nonzero `cycle_count`) the same per-cycle penalty as a tiny tangled one, inverting the proportional-pathology rationale (`tangle_ratio` already says a small isolated cycle in a big graph is a small pathology).

| graph | modules | cycles | tangle_ratio | current | A_countlin | B_floor | C_logcount |
|---|--:|--:|--:|--:|--:|--:|--:|
| synthetic-5000 | 5000 | 0 | 0.000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| synthetic-1500 | 1500 | 0 | 0.000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| fastapi | 1118 | 2 | 0.010 | 0.9902 | 0.8902 | 0.9000 | 0.9242 |
| scrapy | 439 | 4 | 0.146 | 0.8542 | 0.6542 | 0.8000 | 0.7576 |
| pydantic | 402 | 3 | 0.162 | 0.8383 | 0.6883 | 0.8383 | 0.7551 |
| synthetic-300 | 300 | 0 | 0.000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| rich | 213 | 2 | 0.258 | 0.7418 | 0.6418 | 0.7418 | 0.6759 |
| datasette | 143 | 2 | 0.070 | 0.9301 | 0.8301 | 0.9000 | 0.8642 |
| starlette | 67 | 1 | 0.209 | 0.7910 | 0.7410 | 0.7910 | 0.7495 |
| mkdocs | 65 | 1 | 0.200 | 0.8000 | 0.7500 | 0.8000 | 0.7584 |
| httpx | 60 | 2 | 0.283 | 0.7167 | 0.6167 | 0.7167 | 0.6507 |
| flask | 54 | 2 | 0.389 | 0.6111 | 0.5111 | 0.6111 | 0.5452 |
| requests | 37 | 1 | 0.216 | 0.7838 | 0.7338 | 0.7838 | 0.7422 |
| click | 22 | 2 | 0.591 | 0.4091 | 0.3091 | 0.4091 | 0.3432 |

Sharpest inversion: **fastapi** (1118 modules, 1.0% of nodes tangled, 2 isolated cycles) is 0.990 acyclic under the current axis but only 0.890 under `A_countlin` -- a 0.10 dock on a codebase that is 99.0% acyclic. A count term scores it as if those cycles were a tenth of its structural health.

## 2. Single-cycle response: absolute `overall` delta on inject
Injecting exactly one fresh 2-cycle, the ABSOLUTE `overall` delta under each formula (x1e3). The count candidates DO raise the response (this is not in dispute) -- the question is whether that is worth the cost in section 1, given the FP-free per-edge signal (`cycles.added`, acyclicity delta sign) already exists in `archy_diff` regardless of the composite.

| graph | modules | current (x1e3) | A_countlin (x1e3) | B_floor (x1e3) | C_logcount (x1e3) |
|---|--:|--:|--:|--:|--:|
| synthetic-5000 | 5000 | 0.0053 | 2.7206 | 2.6984 | 2.2559 |
| synthetic-1500 | 1500 | 0.0227 | 3.4765 | 3.3825 | 2.8854 |
| fastapi | 1118 | 0.0423 | 7.7756 | 7.4238 | 2.6011 |
| scrapy | 439 | 0.4060 | 10.3572 | 7.9366 | 2.3337 |
| pydantic | 402 | 0.5135 | 9.8147 | 5.6534 | 2.8097 |
| synthetic-300 | 300 | 1.5961 | 6.3552 | 5.7091 | 5.5407 |
| rich | 213 | 1.0830 | 11.3701 | 1.0830 | 4.4795 |
| datasette | 143 | 1.8196 | 9.5601 | 6.9251 | 4.4178 |
| starlette | 67 | 3.5500 | 13.2905 | 3.5500 | 8.2785 |
| mkdocs | 65 | 4.4910 | 13.6712 | 4.4910 | 8.9460 |
| httpx | 60 | 4.6206 | 16.5033 | 4.6206 | 8.7072 |
| flask | 54 | 6.1356 | 20.1137 | 6.1356 | 10.9461 |
| requests | 37 | 7.7088 | 16.7554 | 7.7088 | 12.1628 |
| click | 22 | 17.8300 | 51.0908 | 17.8300 | 29.9591 |

On the largest graph (`synthetic-5000`, 5000 modules) the count candidates lift a single cycle's `overall` delta from 0.0053e-3 to 2.7206e-3 -- they work, by making the axis sensitive to cycle count rather than proportion.

## 3. Sign correctness and corpus rank stability
- `current` clean single-inject `overall` sign: all correct (14/14). (Wrong-direction events in the wild come from multi-change commits, not single edges -- no axis swap fixes those.)
- `A_countlin` clean single-inject `overall` sign: all correct (14/14).
- `B_floor` clean single-inject `overall` sign: all correct (14/14).
- `C_logcount` clean single-inject `overall` sign: all correct (14/14).
- `A_countlin` clean-graph rank stability vs current: Spearman rho = 0.960.
- `B_floor` clean-graph rank stability vs current: Spearman rho = 0.996.
- `C_logcount` clean-graph rank stability vs current: Spearman rho = 0.996.

> The acyclicity axis and the four fixed axes come from archy's own functions; `overall` deltas depend on networkx community detection and may shift across versions. The clean-graph axis penalties (section 1) are exact functions of `tangle_ratio` and `cycle_count` and are version-independent.

