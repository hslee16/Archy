# archy vs `ruff analyze graph`: file-level import graph diff (#532)

Run: `uv run python bench/ruff_differential.py --clone`. Ruff 0.15.12 (experimental
command; do not compare figures across ruff versions). Depth-1 clones of the
`contract_repos.json` corpus under 100 MB into `bench/cache/ruff_differential_clones/`.
Run on archy at f754bab, after the #531/#850 qualname refusal landed. 50 trees.

| Outcome | Trees |
|---|---|
| Compared | 45 |
| Refused by archy (two files map to one qualname, #850) | 5 |
| Errors | 0 |

The 5 refusals are by design (monorepos with several `tests/conftest.py` roots),
and are a finding about those trees rather than a divergence.

## One-sided gaps above 2x (the acceptance bar)

| Tree | archy edges | ruff edges | Cause |
|---|---|---|---|
| K4liber/definit-dsa | 0 | 2563 | stray `src/__init__.py` makes archy name modules `src.*` while the code imports `definit_db.*`, so every edge resolves external |
| DiiOliiver/finances-api | 3 | 247 | same shape: `src/__init__.py` plus `from infra.config...` |
| BernardUriza/free-intelligence | 314 | 1494 | `apps/aire-server/server/` is the real root (hyphenated dir, no `__init__.py` chain), imports are `from aire...`; a `roots:` case |
| Codfor/openpilot2 | 696 | 325 | 278 archy-only edges to `openpilot/__init__.py`, and `third_party`/`panda` init edges; not yet triaged |

The first two are filed as #853. free-intelligence needs `roots:` and is
allow-listed as such. openpilot2 is untriaged.

## Smaller unexplained edges

593 archy-only and 3138 ruff-only edges remain unexplained across the 45 trees, but
the four trees above hold almost all of the ruff-only mass. The rest cluster in:
archy-only `tests/... -> pkg/__init__.py` edges (Na0S, axor-core, ggshield) and
archy-only `tests/... -> tests/conftest.py` edges, where ruff has no edge. These
are candidates, not confirmed bugs, and no figure here is quoted anywhere else.

## Harness artifact fixed on the way

`ruff analyze graph` returned absolute targets under archy's own checkout for
pytest `conftest` references, because the clones sit inside archy's tree. Those
are dropped in `run_ruff`; left in, they showed up as false ruff-only edges.
