# Pre-registration: can archy reproduce CodeScene's per-function smells? (#870)

Committed 2026-11-09, **before any run over the bench corpus**. Thresholds below
are fixed. A result that misses one is written up as a miss; none is
reinterpreted afterwards.

Background and sources: [`CODESCENE_HEALTH_MODEL.md`](CODESCENE_HEALTH_MODEL.md).
Ticket: #870. The decision it feeds: whether `archy health` (#871) ships, and
which detectors it ships with.

## Question

For each of six function-level smells, does a detector built on archy's own
parse flag the same functions that `cs review` flags, on real projects? And does
a per-file count of those smells track `cs`'s file score?

## What is and is not claimed

A hit licenses only "archy reports the same flagged functions as `cs`". It does
not license "healthier code", fewer defects, or better agent runs, and it is not
a case for a score axis (that has its own gate, recorded on #870). The reference
is a vendor tool whose own validity this repository has not checked.

## Detector definitions

Read from `cs rules-config list-thresholds --language python` and fixed by the
calibration probes below. The calibration is not a result; it only settles what
each detector means, so the comparison is not lost to a definition mismatch.

| Smell | archy-side rule | Settled by calibration |
|---|---|---|
| Complex Method | `archy.complexity` cyclomatic at or above 9 | threshold from `cs` |
| Excess Arguments | more than 4 parameters, **excluding `self` and `cls`**, counting `*args`, `**kwargs` and keyword-only | `self` and `cls` not counted; the other three counted |
| Large Method | more than 70 **code** lines, counting docstring lines, excluding blank and comment-only lines | blanks and comments not counted; docstrings counted |
| Deep Nesting | nesting depth of 4 or more through `if`, `for`, `while` only | four nested `for` fires; `with`/`try` and `elif` chains do not; comprehension filters do not |
| Complex Conditional | two or more boolean operators in an `if` or `while` condition | `return a and b and c` and assignments do not fire; `if a and b or c` does |
| Bumpy Road | **not in this round** | the definition (what separates two bumps) was not recoverable from probes |

One calibration surprise recorded rather than papered over: a function nested
inside another function, with five parameters, was not flagged by `cs`. Methods
of a top-level class were. The detector follows `cs` (top-level functions and
class methods, not functions nested in functions) so the comparison is like for
like; whether archy should differ is a separate decision.

## Data

- Projects: every entry in `bench/projects.yaml`, cloned at its pinned SHA by
  `bench/_common.clone_or_update`.
- Files: tracked `*.py`, excluding any path with a `tests`, `test`, `docs`,
  `examples`, `migrations` or `vendor` segment. The unit is the function,
  identified by (file, name, line).
- Files `cs review` returns a null score for are **dropped, never counted as
  clean** (AGENTS.md).
- Pilot: `archy` itself plus two small projects, to catch harness bugs only. The
  pilot is excluded from the result and may not change any threshold below.
- Cost guard: a project is skipped, and listed as skipped with the reason, if
  `cs review` over its files exceeds 30 minutes. A skipped project is not
  scored as agreeing.

## Matching

A flagged function on one side matches the other when it has the same file and
the same name. If a name is not unique in the file, the nearest start line
within 3 lines. Decorated functions start on the decorator line in one tool and
the `def` line in the other, which is what the 3-line window is for. An
unmatched flag counts against agreement on the side that has it.

## Measures

1. **Per-smell flag-set agreement:** Jaccard index of flagged functions,
   pooled over all projects, plus each project's own index reported beside it.
2. **File-level agreement:** Spearman rank correlation between archy's
   per-file count of flagged functions (all five detectors) and `cs`'s file
   score, pooled over files with a score, plus the per-project median.

## Thresholds, fixed now

- A detector is **reproducible** when pooled Jaccard is at least **0.80**.
- A file measure is **reproducible** when pooled Spearman is at most **-0.80**
  (more archy smells, lower `cs` score).
- If pooled Spearman is between -0.70 and -0.80, say it is not reproducible, and
  ship individual detectors only.
- If it is above -0.70, do not ship a file-level number at all.
- Complex Method is already measured on archy's own `src/` (2026-11-09, outside
  the corpus): Jaccard 92 / (112 + 109 - 92) = 0.71. That is a baseline, not a
  result, and it is below the bar.

## What would change the plan

- Fewer than **four** detectors reach 0.80: ship `archy health` with only those
  that did, and say plainly which smells it does not report.
- Complex Method fails because the two tools count different constructs: say
  which, then decide whether to match `cs` or document the difference. Do not
  quietly adopt `cs`'s count to pass the bar.
- Results differ between large and small projects by more than 0.15 in Jaccard:
  report both and do not pool.

## Threats, stated up front

- archy's cyclomatic comes from tree-sitter; the other detectors in the
  prototype use Python's `ast`. A shipped detector that uses tree-sitter
  throughout could differ.
- `cs` may change thresholds between releases. The version string is recorded
  in the result.
- Agreement with `cs` is not agreement with anything else. If it is high, that
  is evidence the detectors are not buggy, not evidence they measure quality.

## Calibration record (2026-11-09, `cs review` on synthetic files)

| Probe | Result |
|---|---|
| method with `self` + 4 parameters | not flagged |
| method with `self` + 5 parameters | flagged, "Arguments = 5" |
| `cls` + 4 parameters (classmethod) | not flagged |
| `a, b, c, *args, **kwargs` | flagged, "Arguments = 5" |
| `a, b, *, c, d, e` (keyword-only) | flagged, "Arguments = 5" |
| nested function, 5 parameters | not flagged |
| 40 statements separated by blank lines | not flagged |
| 40 statements each preceded by a comment | not flagged |
| 70-line docstring | flagged, "LoC = 72 lines" |
| four nested `for` | flagged, "Nesting depth = 4" |
| `for`, `while`, `with`, `try`/`except` nested | not flagged |
| five-branch `elif` chain | not flagged |
| comprehension with four `if` filters | not flagged |
| `return a and b and c`, `x = a and b and c` | not flagged |
| `if a and b and c`, `if a and b or c`, `while a and b and c` | flagged, "2 complex conditional expressions" |
| `if not a and not b` | not flagged |
