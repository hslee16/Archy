# archy vs `ruff analyze graph`: file-level import graph diff (#532)

Run: `uv run python bench/ruff_differential.py --clone`. Ruff 0.15.12 (experimental
command; do not compare figures across ruff versions). Depth-1 clones of the
`contract_repos.json` corpus under 100 MB, now into a directory OUTSIDE the repo
(default under the system temp dir, `--clones DIR` to choose; #856).

Everything below this paragraph down to the post-#856 section is the FIRST pass,
run with clones inside archy's own tree. Do not pool it with the re-run.
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

The first two were filed as #853 and fixed by resolving `infra.x` against a `src` package (definit-dsa 0 to 2558 archy edges vs ruff 2563; finances-api 3 to 247 vs 247). free-intelligence needs `roots:` and is
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

## Post-#856 re-run (clones outside the repo)

Same corpus and ruff 0.15.12, run after #853. 45 compared, 5 refused, 0 errors.

The first pass put the clones inside archy's checkout, and ruff resolves against
ancestor directories, so `tests.conftest` landed in archy's own `tests/` and the
absolute-path filter then made archy's correct edges look archy-only. Moving the
clones out removed most of that artifact:

| Tree | archy-only before to after | ruff-only before to after |
|---|---|---|
| ggshield | 16 to 1 | 0 to 0 |
| axor-core | 42 to 23 | 0 to 0 |
| brain-go-brrr | 7 to 7 | 10 to 1 |
| RoleplayingCharacters | 19 to 16 | 2 to 0 |
| definit-dsa | 0 to 0 | 6 to 0 |

Totals: unexplained archy-only 596 to 559, ruff-only 1210 to 1193. Absolute ruff
targets are now kept (shown as ruff-only edges), not dropped.

What is left is concentrated, and mostly not an archy bug:

- **Codfor/openpilot2, 380 archy-only.** Its `openpilot/system`, `openpilot/common`
  and the rest are committed upstream as mode 100644 text files holding `../system`,
  not as symlinks (`git ls-tree` shows it), so no clone setting repairs the tree.
  Treat it as a broken input, not a finding.
- **BernardUriza/free-intelligence, 1141 ruff-only.** A `roots:` case; see #857.
- **M-Abrisham/Na0S, 83 archy-only.** `layer16/` holds only `__init__.py`, so
  `na0s.layer16.models` does not exist and archy attributes the import to the
  package. Same family as #857's missing-submodule question.
- **Relative imports from a directory with no `__init__.py`** (DNA, DNA-2,
  RoleplayingCharacters, parts of Chamartin3): archy reports `from .config import x`
  as an edge, ruff reports none (`ruff analyze graph` returned `[]` for
  `cli/dna_cli/lib/auth.py`). Python 3 resolves these as namespace sub-packages, so
  archy looks right and ruff looks incomplete. Not allow-listed, because the path
  sets alone cannot tell a relative import from an absolute one and a rule that
  broad would hide real bugs. Unconfirmed against ruff's source.
- Tiny-Swarm-World (16 ruff-only), Chamartin3 (16 ruff-only), hextools (9 ruff-only),
  axor-core (23 archy-only), mcp_coder (9 archy-only): not yet looked at.
