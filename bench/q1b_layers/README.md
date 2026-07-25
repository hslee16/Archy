# Authored layer rules for the Q1b A/B

One `<repo>.yaml` per repository in the Q1b task set (#348), giving archy the
*declared intent* its normative half needs.

## Why these exist at all

archy's differentiator (#316) is the normative job: holding the structure a user
**declared** and reporting when an edit breaks it. Q1b's outcome is "introduced
cycle OR declared-layer/contract violation OR score regression".

**SWE-bench repositories declare no architecture.** No `.importlinter`, no
`[tool.importlinter]`, no `tach.toml` in any of the six repos the pilot touches
(checked 2026-07-25). So without these files the declared-layer arm cannot fire
and Q1b would test only the descriptive half.

That means the measurer authors the intent being measured. This is a real bias
risk and the results doc has to carry it. Everything below exists to constrain
it, not to eliminate it, and the honest claim is "falsifiable in one specific
way", not "unbiased".

## Not solved by inferring architecture

#288 measured that and it was a NO-GO:

> "domain" is a statement of intent, and the import graph does not carry intent.

For this use it is worse than merely weak. Rules derived from observed edges are
**tautological by construction**, the `D_purity` failure (0 flags across 40
repos because the definition made the violation impossible). Generated rules
would make `archy check` pass trivially on every base commit and hand Q1b a null
produced by tooling rather than by finding.

`bench/q1b_layers.py` therefore only **describes** the observed subpackage
matrix. It has no capability to propose rules, which is the cheapest way to keep
that line from being crossed under time pressure.

## The procedure

1. **Derive candidates from the project's own documented conventions**, never
   from the import graph. Django documents `contrib` as optional add-ons;
   scikit-learn documents `externals` as vendored. Those are the sources.
2. **Validate every candidate against pristine upstream code** (`q1b_layers.py`
   for the matrix, then `archy check`).
3. **Drop what already fires**, and record it in the config with edge counts
   rather than deleting it silently.
4. **Author blind.** Do not open `gold_py_files`, problem statements, or gold
   patches. Rules that cannot see the tasks cannot be tuned toward them.

Acceptance per repo: `archy check <repo> --config bench/q1b_layers/<repo>.yaml`
exits **0** on the pristine tree. A rule that fires on unmodified upstream code
would fire on every agent run, making the signal constant and the measurement
worthless.

## Step 2 is not a formality

It has caught a wrong prior on every repo attempted so far, and the failure mode
is **silent**: a wrong rule does not error, it fires on every run, makes p_B look
like 100%, and quietly corrupts the result.

| repo | candidates | dropped | kept | what the prior got wrong |
| --- | --- | --- | --- | --- |
| django | 19 | 4 | 15 | `utils` reaches `db`, `forms`, `template`; `core` reaches `views` (1 edge each) |
| scikit-learn | 25 | 14 | 7 | "utils is a leaf" is flatly wrong: `utils -> linear_model` alone is 29 edges |
| requests | ~30 | most | 24 | core is bidirectionally tangled (`models <-> utils <-> adapters <-> cookies`) |

scikit-learn is the cautionary one. `sklearn/utils/estimator_checks.py` exists to
exercise estimators, so it imports them on purpose. Carrying django's prior
across would have added nine always-firing rules.

## Do not pad the rulesets

scikit-learn kept 7 rules where django kept 15, and four *additional* sklearn
peer-family pairs that **do** hold on pristine code were excluded anyway
(`cluster->ensemble`, `linear_model->ensemble`, `metrics->ensemble`,
`datasets->ensemble`). scikit-learn does not document estimator families as a
hierarchy, so keeping them would mean selecting rules **because they are
currently zero**, which is the tautology in a slower form.

Weak rulesets are the honest outcome. A repo that yields two defensible rules
gets two.

**Consequence for the results:** ruleset strength varies by repo, so tasks from
different repos offer unequal opportunity for a layer violation to fire. Report
p_B per repo as well as pooled, or a shift in repo mix will look like an effect.

## Rules name modules, not paths

Configs refer to `requests.compat`, not `src/requests/compat.py`. That matters:
modern `requests` lives at `src/requests` while the Q1b task's base commit
predates that move and uses `requests/`. The configs are unaffected, but the
runner must resolve the package directory **per base commit** (#356).

## Status

| repo | rules | validated against |
| --- | --- | --- |
| django | 15 | `189c2d2ce5` |
| requests | 24 | `d64b9ad4` |
| scikit-learn | 7 | `8fac97f31b` |
| sympy | - | #355 |
| xarray | - | #355 |
| matplotlib | - | #355 |
