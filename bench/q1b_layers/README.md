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

## Every repository is authored independently

**Do not carry a rule, or a rule *shape*, from one repository to another.** The
safe assumption is that each codebase has its own architecture and shares
nothing with the last one you looked at.

This is not hypothetical caution. Writing scikit-learn's candidate list, I
generated `utils -> linear_model` and eight siblings *by analogy to Django*,
where `utils` really is close to a leaf. In scikit-learn that is simply false:
`sklearn/utils/estimator_checks.py` exists to exercise estimators, so it imports
them on purpose, and `utils -> linear_model` alone is 29 edges.

Validation caught all nine, and none reached the shipped config. But the
analogy should never have produced candidates in the first place, because of
what it does to the selection:

> candidates from analogy, filtered by "does it fire upstream", leaves
> **{my guesses} intersected with {pairs that happen to be zero}**, and the
> zero-filter is then doing the real work.

That is the `D_purity` tautology in a slower form. The whole point of sourcing
candidates from documented intent is that the rule is justified *before* the
graph is consulted.

**The validation step is a safety net, not a candidate generator. If a
candidate's only justification is that it survives validation, it is not a
rule.**

## The procedure

1. **Derive candidates from THIS project's own documented conventions**, never
   from the import graph and never from another repository. Django documents
   `contrib` as optional add-ons; scikit-learn's README documents `externals` as
   vendored third-party code. Those are the sources, and each was read for that
   project alone.
2. **Validate every candidate against pristine upstream code** (`q1b_layers.py`
   for the matrix, then `q1b_layers_check.py`), at HEAD **and at every base
   commit the repo's tasks use**.
3. **Drop what already fires**, and record it in the config with edge counts
   rather than deleting it silently.
4. **Author blind.** Do not open `gold_py_files`, problem statements, or gold
   patches. Rules that cannot see the tasks cannot be tuned toward them.

Acceptance per repo, all three enforced by `bench/q1b_layers_check.py`:

- `archy check <repo> --config bench/q1b_layers/<repo>.yaml` exits **0** on the
  pristine tree. A rule that fires on unmodified upstream code fires on every
  agent run too, making the signal constant and the measurement worthless.
- It also exits 0 at **every task base commit** for that repo (`--base-commits`).
- It exits **1** on a single deliberately violating import (`--canary`). Without
  this one, a config that cannot fire at all is indistinguishable from a clean
  repository.

## Step 2 is not a formality

It has caught a wrong prior on every repo attempted so far, and the failure mode
is **silent**: a wrong rule does not error, it fires on every run, makes p_B look
like 100%, and quietly corrupts the result.

| repo | candidates | dropped | kept | what was wrong |
| --- | --- | --- | --- | --- |
| django | 19 | 6 | 13 | my reading of Django's docs was too strict: `utils` reaches `db`, `forms`, `template`, `core` reaches `views` (1 edge each), and `db` reaches `forms` (4) |
| scikit-learn | 25 | 14 | 7 | nine candidates were cross-repo analogy and should never have been generated; the rest were genuinely too strict |
| requests | ~30 | most | 22 | core is bidirectionally tangled upstream (`models <-> utils <-> adapters <-> cookies`) |
| sympy | 16 | 6 | 10 | `sympy.core` is not a leaf (168 edges into `functions`, 52 into `simplify`, 47 into `matrices`), and `core -> parsing`, `integrals -> plotting`, `physics -> plotting\|interactive` all exist |
| xarray | 18 | 7 | 11 | `xarray/core` is the public object layer, not a pure data model: it reaches the optional plotting surface (3) and concrete backends (12). `namedarray` is not standalone (18 into `core`) |
| matplotlib | 19 | 5 | 14 | `figure` imports `pyplot` and a concrete backend; `backend_bases` imports the backend registry (1 edge each) |

The drop counts include the base-commit pass below, which killed one rule each
in django, sympy, and matplotlib.

Read the django row carefully: those are cases where the *project* departs
from its own documented convention, which is a legitimate reason to drop a rule.
The scikit-learn row is different and worse: most of its drops were my own
method error, not a discovery about scikit-learn.

The sympy and xarray rows are the same finding as scikit-learn's `utils`, twice
more: **the package that looks like the foundation is usually not a leaf.**
`sklearn.utils`, `sympy.core`, and `xarray.core` all failed that prior. Do not
write a `X -> <foundation>` rule without checking first.

## The layer pattern must be `pkg.**`, not `pkg`

archy matches a bare `modules: ["django.contrib"]` as an **exact** dotted name,
not as a package prefix. The first three configs shipped that way, so
`contrib` held exactly one module (`django/contrib/__init__.py`, which is
empty) instead of 338, and **every rule in django.yaml and scikit-learn.yaml was
dead**: appending `from django.contrib import admin` to
`django/db/models/base.py` left `archy check` at exit 0. requests was
unaffected only because it is a flat package where each layer really was the
single module it names.

`pkg.**` is the canonical form ("the package and all descendants"). All six
configs now use it, and each was re-validated at the corrected scope, which cost
one more django rule (`db -> forms`, 4 edges).

The lesson generalizes past this bench: **a config whose rules cannot fire looks
exactly like a codebase with no violations.** Any future authored ruleset needs
a canary -- add one deliberately violating import, confirm exit 1, revert -- and
that check is cheap enough that there is no excuse for skipping it. All six
configs have been canaried.

## Validate at the base commit, not only at HEAD

The cached clones are at upstream HEAD; the task base commits are up to seven
years older. A rule can hold today and fire there, and that is not a rare edge
case: **10 of the top 25 tasks** had a rule firing at their base commit on the
first pass. Three rules died as a result, each an import upstream later removed:

| rule | base commits | edge |
| --- | --- | --- |
| django `utils -> views` | 3 of 7 | `django.utils.log -> django.views.debug` |
| sympy `external -> core` | 5 of 8 | `sympy.external.importtools -> sympy.core.compatibility` (the Python 2/3 shim) |
| matplotlib `backend_bases -> pyplot` | 2 of 2 | `matplotlib.backend_bases -> matplotlib.pyplot` |

They were dropped rather than suppressed per-task, so **every task in a repo
runs that repo's whole ruleset**. A per-task ruleset would make p_B differ by
task for reasons unrelated to the agent.

Layers can also be *empty* at an old commit, which weakens the ruleset there
without failing anything: `xarray.namedarray` did not exist at any of the three
xarray base commits, `sklearn._loss` at one of the two scikit-learn ones, and
`requests._internal_utils` at the single requests one. Rules naming an empty
layer are unfirable at that commit, so a
per-repo p_B is still an average over slightly different effective rulesets.

## Tests are excluded from the scan

Every config carries `exclude: ["tests"]`. Test modules import across layers by
design in all six repos, and that is not architecture: with tests scanned,
sympy's `external -> core` is 7 edges (all from `sympy/external/tests/`) and
`sandbox -> functions` is 1. sympy and scikit-learn keep their tests inside the
packages they test, so the exclusion is load-bearing there; django's live
outside the package, so it changes nothing.

**Consequence for the results:** a layer violation an agent introduces inside a
`tests/` directory cannot fire. Q1b's outcome is about the source structure, so
that is the intended scope, but it does mean the declared-layer arm is blind to
one region of every repo.

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

**Rule count is not a strength measure across repos.** django, scikit-learn, and
requests declare one layer per subpackage, so a single documented statement
("`contrib` is optional") becomes seven rule rows. sympy, xarray, and matplotlib
group the layers that share a role (`engine`, `derived`, `core`), so the same
statement is one row. Coverage is what differs, not row count: sympy's 10 rules
constrain 301 engine modules. Compare per-repo p_B, never rule counts.

## Rules name modules, not paths

Configs refer to `requests.compat`, not `src/requests/compat.py`. That matters:
modern `requests` lives at `src/requests` while the Q1b task's base commit
predates that move and uses `requests/`. The configs are unaffected, but the
runner must resolve the package directory **per base commit** (#356).

## Status

All six repos in the Q1b task set are authored.

| repo | rules | top-25 tasks | HEAD validated |
| --- | --- | --- | --- |
| django | 13 | 7 | `189c2d2ce5` |
| requests | 22 | 1 | `d64b9ad4bf` |
| scikit-learn | 7 | 2 | `8fac97f31b` |
| sympy | 10 | 8 | `89796fa512` |
| xarray | 11 | 3 | `adc8005a20` |
| matplotlib | 14 | 2 | `357b274ac3` |

`bench/q1b_layers_check.py` runs all three gates, and all three currently pass
for all six repos:

    uv run python bench/q1b_layers_check.py                 # exit 0 on pristine HEAD
    uv run python bench/q1b_layers_check.py --base-commits  # exit 0 at all 25 base commits
    uv run python bench/q1b_layers_check.py --canary        # exit 1 on one violating import
