# How CodeScene's `cs` judges code health, and what archy could take from it

Fact-gathering date: 2026-11-09. Sources: `cs docs` (CLI, v as installed), the
CodeScene [Code Health guide](https://codescene.io/docs/guides/technical/code-health.html),
`cs rules-config list-thresholds --language python`, and probes run against
synthetic Python files and archy's own `src/`. Every number below was read from
the tool or measured, not recalled; where it is the vendor's claim rather than
ours, it says so.

## What `cs` measures

A file gets a score from 1 to 10 (10 is healthy). The score falls with each
**smell** found in it. Three families (vendor docs):

- **Function smells:** Complex Method (cyclomatic complexity), Large Method,
  Brain Method, Excess Number of Function Arguments, Primitive Obsession,
  String Heavy Function Arguments, Code Duplication.
- **Implementation smells:** Deep Nested Complexity, Bumpy Road Ahead (several
  separate nested blocks in one function), Complex Conditional (several
  boolean operators in one condition), plus test-only Large and Duplicated
  Assertion Blocks.
- **Module smells:** Lines of Code in a Single File, Number of Functions in a
  Single Module, Low Cohesion (LCOM4), Overall Code Complexity, Overall
  Function Size, Brain Class.

Aggregation (vendor docs): a project score is a **LoC-weighted average** of its
file scores, because a plain mean hides one huge bad file (their example: 2.0 at
5,000 LoC plus two tiny perfect files is 7.33 plain, 2.18 weighted). CodeScene
reports two KPIs, **Hotspot Code Health** (weighted over the files that change
most) and **Average Code Health** (all files), because a healthy average can
hide a bad hotspot.

Enforcement (vendor docs and CLI): `cs delta <base>` compares the working tree
or a branch with a base and reports degraded, improved and fixed issues per
file. It exits 0 unless `--error-on-warnings` is passed. A "declining" file is
one whose score fell by at least 0.1. Customisation is two-layered:
`.codescene/code-health-rules.json` (a weight per rule from 1.0 down to 0.0, a
numeric threshold per metric, rule sets matched by path glob) and a
function-level source comment `@codescene(disable:"Complex Method")` that
cannot suppress file-level smells.

## Exact Python defaults (read from `cs rules-config list-thresholds`)

| Smell | Threshold |
|---|---|
| Complex Method | cyclomatic complexity 9 (alert at 100) |
| Large Method | 70 lines (alert at 500) |
| Excess Function Arguments | more than 4 (constructors: more than 5) |
| Deep Nested Complexity | nesting depth 4 |
| Bumpy Road | 2 bumps, each at nesting depth 2 |
| Complex Conditional | 2 branches (alert at 10) |
| Lines of Code in a File | 600 warn, 3,000 alert, 6,000 critical |
| Mean cyclomatic complexity of a file | 4 |
| Code Duplication | functions of 10+ lines, 75% similar |
| Primitive Obsession | 30% of a file's function arguments |
| Large assertion block (tests) | 4 consecutive asserts |

## Probes (synthetic files, `cs review --output-format json`)

All matched the table: 5 sequential `if`s is clean and 8 fires Complex Method;
4 arguments is clean and 5 fires; a 60-statement function is clean and 70 fires
Large Method; nesting 3 is clean and 4 fires; a condition with two `and`s fires
Complex Conditional and one with a single `and` does not; two separate
`for`/`if` blocks fire Bumpy Road. Module smells were harder to
calibrate. Repetitive synthetic files of ten-line functions earned Low Cohesion
at 600 lines and Number of Functions in a Single Module at about 333 functions,
but **never earned Lines of Code in a Single File even at 1,500 lines**, while
the real 1,003-line `conventions.py` did. So synthetic files cannot calibrate
that smell; read the 600/3,000/6,000 table instead. Probes that did **not**
fire, for reasons we did not establish: a 16-line duplicated function pair,
Primitive Obsession on four small functions, and Low Cohesion on an eight-line
two-group class.

Command surface worth knowing: `cs review FILES --output-format json` returns
`{path, score, review[]}` per file with `category`, function names and line
ranges, which is easy to script. `cs check FILE` prints lint-style
`warn: file:line: Smell (detail)` lines. `cs delta main` prints per-file
before and after scores and, usefully, the metric and threshold behind each
change ("cyclomatic complexity from 23 to 10, threshold = 9"). `cs review`
needs `cs auth` (it reported OAuth against api.codescene.io), so treat it as
needing the network. Run outside a git repository it still returned scores but
printed a `git execution failed` exception; `cs delta` is the command that
needs git.

## archy's own code, scored (51 files in `src/archy`, before any refactor)

Mean 8.58 over 48 scorable files, 32 below 10. Worst: `conventions.py` 2.93,
`cli_render.py` 3.59, `cli.py` 4.40, `graph.py` 4.44, `mcp_runs.py` 4.60,
`layers.py` 4.85. Warnings across `src`: 109 Complex Method, 81 Bumpy Road, 48
Excess Arguments, 32 Complex Conditional, 14 Deep Nested, 13 Large Method, 4
Low Cohesion, 3 file length, 2 Duplication.

## Overlap with what archy and ruff already do

- **Cyclomatic complexity:** archy already computes it per function
  (`archy.complexity`). Flagging archy's cyclomatic at or above 9 on `src/archy`
  gives 112 functions against `cs` Complex Method's 109, **92 of them the same
  function**; the exact value differs by 1 to 4 for most (68 of 92), so the two
  count different constructs. ruff's `C901` at `max-complexity = 8` finds only
  39, so it is a different, smaller measure and not a stand-in.
- **Arguments, branches, statements:** ruff has `PLR0913` (55 at `max-args = 4`),
  `PLR0912` (10) and `PLR0915` (2). These are the same niche as the archy
  value-prop note in `AXIS_REVIEW.md` (type-hint coverage was ruled out because
  mypy and pyright own it); function-level size rules are owned by linters.
- **Churn weighting:** archy already ranks files by cyclomatic complexity times
  git churn (`archy hotspots`, `what-to-refactor-next`), which is CodeScene's
  hotspot idea.
- **Ratchet on new findings:** `archy diff --strict` already fails only on
  findings that are new relative to a committed baseline, for cycles and layer
  violations.

## What is genuinely missing in archy

1. Per-file aggregation: no file-level score, no LoC-weighted project number, no
   hotspot-weighted versus all-files split.
2. The composite smells ruff has no rule for: Bumpy Road, Brain Method, Low
   Cohesion, Overall Code Complexity, module and file size.
3. A delta gate on **changed files' health**, not only on structure.
4. A rules file with per-metric thresholds, path-scoped rule sets and a
   function-level suppression comment that stays visible in the output.

## What this document does not claim

- That any of these smells predicts defects or agent success. The vendor claims
  a link to speed and quality; that is theirs, unverified here, and archy's own
  record is that every pre-registered claim it measured came back null
  (`WHAT_DIDNT_WORK.md`).
- That archy should add a sixth score axis. Any new indicator would face the
  OECD four-condition check in `AXIS_REVIEW.md` first, and the likely home is a
  diagnostic next to `hotspots`, not the composite.
- That the threshold table transfers: it is CodeScene's calibration, read from
  its CLI, not archy's.
