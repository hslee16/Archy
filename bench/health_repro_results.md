# Result: can archy reproduce CodeScene's function-level smells? (#870)

Run 2026-11-09 against the protocol in
[`docs/research/HEALTH_REPRODUCIBILITY_PREREG.md`](../docs/research/HEALTH_REPRODUCIBILITY_PREREG.md),
committed before any run. Reference tool: `cs version 1.0.58 (b2580d71)`. Harness:
[`health_repro.py`](health_repro.py), tests in `tests/test_health_repro.py`. The
pilot projects (archy, click, requests) are excluded, as the protocol requires.

## Result against the pre-registered thresholds

26 projects, 16,314 scored files, at each project's pinned SHA.

| Detector | archy flags | cs flags | both | pooled Jaccard | bar 0.80 |
|---|---:|---:|---:|---:|---|
| Excess Number of Function Arguments | 10,946 | 10,679 | 10,659 | **0.972** | met |
| Complex Conditional | 4,789 | 4,738 | 4,736 | **0.989** | met |
| Large Method | 4,659 | 4,786 | 4,647 | **0.969** | met |
| Deep, Nested Complexity | 3,025 | 3,213 | 2,986 | **0.918** | met |
| Complex Method | 10,747 | 13,452 | 9,737 | **0.673** | **missed** |

File-level Spearman between archy's per-file smell count and the `cs` file score:
**-0.889** pooled (bar: at most -0.80), met. Per project: median -0.909, range -0.977
to -0.743.

Per the protocol's own contingencies:

- **Fewer than four detectors reach 0.80?** No: four do. `archy health` (#871) may
  ship with those four.
- **Complex Method fails: say which constructs differ, then decide, and do not
  quietly adopt the reference's count.** Done below and in #886. No count has been
  adopted.
- **Large and small projects differ by more than 0.15?** The protocol did not define
  the split. Post hoc, by median file count (13 projects at or below 157 files, 13 at or
  above 159): the largest gap for any detector is 0.029, so the pooled figures stand.
  File-level Spearman is -0.913 for the small half and -0.887 for the large half.

## Why Complex Method missed

`cs` counts a different set of constructs than archy, which is radon-compatible by
design (`src/archy/complexity.py`). Measured with `cs check` on synthetic functions with
12 of one construct each: `cs` counts each `raise` (archy does not); archy counts
conditional expressions, `match`/`case` and a ternary in a lambda (`cs` does not); archy
counts a comprehension `if` twice where `cs` counts it once. The table is in #886.

**Exploratory, post hoc, not a result.** Recounting archy's flags with a `cs`-style rule
(1 + `if`/`elif`, loops, `except`, boolean operands, comprehension `for`, and `raise`; not
comprehension `if`, ternary, `match` or lambda) gives pooled Jaccard **0.849** (12,490
archy flags, 11,908 shared). The rule was written from the synthetic probes before it
was run over the corpus and was not fitted to it, but about 0.15 of disagreement is
still unexplained, and it has not been checked on projects outside this corpus. A
`cs`-style count would move the project-level mean cyclomatic by 0.989x to 1.155x
(median 1.063x, 185,499 functions; project order Spearman 0.982, 3 of the 5 most
complex projects unchanged), so adopting it would shift archy's `complexity` axis and
every committed baseline. That decision is #886.

## Audit for artifact-shaped numbers

Checked before believing the result (AGENTS.md). No project has zero flags on both
sides for every detector (the lowest project has 20 archy and 24 `cs` flags in
total); archy and `cs` flag counts are within about 17% of each other for every
project; the unresolved share is 1.03%, not zero. The detectors near 1.0 (arguments,
conditionals, large method) are simple counting rules whose definitions were
calibrated against this same tool, so high agreement is what they were built to
show, not a surprise, and says nothing beyond "the rule matches". Complex Method,
the one with a real definitional gap, is the one that missed.

## Threats and limits

- The reference is a vendor tool. Agreement with it shows the detectors are not buggy;
  it says nothing about whether any smell predicts defects, review effort or agent
  success, and none of that is claimed.
- **26% of files were dropped** (5,745 of 22,059): `cs` returned no score for them or
  they did not parse. The ledger does not record which, so the split is unknown and a
  detector could behave differently on the dropped files. Dropped files were never
  counted as clean.
- 384 of 37,252 `cs` findings (1.03%) did not resolve to a function and are listed in
  the ledger as unresolved; they count against agreement.
- archy's cyclomatic comes from tree-sitter and the other four detectors use Python's
  `ast`. A shipped detector built on tree-sitter throughout could differ.
- Tests, docs, examples, migrations and vendor directories are excluded; Python only.
- Bumpy Road, Brain Method, Low Cohesion and Code Duplication were not in this round.
- `fastapi` failed once with `clone failed` and succeeded on retry (see #882).

## Per-project pooled Jaccard

Columns: CM Complex Method, Args Excess Arguments, LM Large Method, Nest Deep Nested
Complexity, CC Complex Conditional. `n/a` means neither tool flagged anything.

| project | files | dropped | CM | Args | LM | Nest | CC |
|---|---:|---:|---:|---:|---:|---:|---:|
| aiohttp | 53 | 7 | 0.705 | 1.000 | 0.950 | 0.815 | 1.000 |
| ansible | 465 | 128 | 0.740 | 0.995 | 0.992 | 0.917 | 0.990 |
| anyio | 39 | 4 | 0.522 | 0.950 | 1.000 | 0.875 | 1.000 |
| boto3 | 22 | 5 | 0.500 | 0.879 | 1.000 | 1.000 | 1.000 |
| botocore | 54 | 6 | 0.657 | 0.964 | 0.923 | 1.000 | 0.955 |
| dagster | 3278 | 1075 | 0.628 | 0.998 | 0.961 | 0.920 | 1.000 |
| datasette | 67 | 6 | 0.726 | 1.000 | 0.980 | 0.806 | 0.974 |
| django | 550 | 309 | 0.700 | 0.988 | 0.974 | 0.874 | 0.993 |
| fastapi | 406 | 121 | 0.702 | 1.000 | 0.983 | 0.938 | 1.000 |
| flask | 20 | 4 | 0.714 | 0.933 | 1.000 | 1.000 | 1.000 |
| home-assistant | 6861 | 2953 | 0.680 | 0.990 | 0.956 | 0.900 | 0.960 |
| httpx | 21 | 2 | 0.640 | 1.000 | 1.000 | 0.667 | 0.600 |
| mkdocs | 28 | 9 | 0.697 | 1.000 | 1.000 | 1.000 | 1.000 |
| msgspec | 21 | 7 | 0.667 | 1.000 | 1.000 | 0.750 | 1.000 |
| mypy | 269 | 81 | 0.831 | 0.991 | 0.983 | 0.985 | 1.000 |
| numpy | 202 | 49 | 0.720 | 0.985 | 0.973 | 0.944 | 1.000 |
| pydantic | 92 | 20 | 0.717 | 0.869 | 1.000 | 0.930 | 1.000 |
| pygments | 157 | 201 | 0.661 | 1.000 | 1.000 | 0.909 | 1.000 |
| pytest | 231 | 27 | 0.639 | 0.965 | 0.955 | 0.980 | 0.948 |
| pytorch | 2456 | 523 | 0.642 | 0.967 | 0.972 | 0.911 | 0.997 |
| rich | 71 | 38 | 0.762 | 1.000 | 0.962 | 0.897 | 1.000 |
| scikit-learn | 336 | 80 | 0.663 | 0.915 | 0.974 | 0.947 | 1.000 |
| scrapy | 159 | 18 | 0.643 | 1.000 | 1.000 | 0.857 | 1.000 |
| setuptools | 185 | 39 | 0.702 | 1.000 | 0.958 | 0.895 | 1.000 |
| sqlalchemy | 239 | 31 | 0.625 | 0.971 | 0.941 | 0.899 | 0.985 |
| starlette | 32 | 2 | 0.529 | 1.000 | 1.000 | 1.000 | 0.923 |

## Reproduce

```
uv run python bench/health_repro.py --ledger LEDGER.jsonl --exclude archy click requests
uv run python bench/health_repro.py --ledger LEDGER.jsonl --report --exclude archy click requests
```

The ledger is 10.8 MB (every flagged function on both sides) and is not committed; the
SHAs are pinned in `bench/projects.yaml` and the run is resumable. It needs the `cs` CLI
and `cs auth`.
