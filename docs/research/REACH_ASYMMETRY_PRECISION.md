# Reach-asymmetry sensing: precision pre-registration (#387, item 4)

**Status: PRE-REGISTERED, NOT YET RUN.** Written before the harness exists and
before any number is known. Nothing in this file is changed to fit a result. If
it is edited after the run, the edit is an amendment and says so.

## Why this file exists before the feature

[#387](https://github.com/hslee16/archy/issues/387) item 4 proposes a sensor:
for each package, for each target, flag a split where some sibling modules reach
the target and the rest do not. Its motivating incident is a package of 34
command modules where 13 reached the SQLAlchemy model registry and 21 did not;
the 21 crashed when run as entrypoints. The ticket says the sensor ships only if
a precision pilot clears a threshold written down first, and that the pilot must
run on transitive **reach** (with package-`__init__` edges), not on direct
imports. The reporter measured why: on the one real sample, direct-import
asymmetry flags 23 modules where 21 were broken, and reach flags exactly the 21.

The same discipline as [`MIRROR_SURFACE_PRECISION.md`](MIRROR_SURFACE_PRECISION.md)
applies: thresholds, then population arithmetic, then harness, then run, then
decide. **A sensor that misses the threshold does not ship**, and the null goes
in [`../WHAT_DIDNT_WORK.md`](../WHAT_DIDNT_WORK.md).

## What the sensor does

On one snapshot of a project, with the graph built by `build_graph` and widened
by `reach.with_package_init_edges`:

1. Find each package with enough sibling modules to have a convention.
2. For each internal target outside that sibling set, compute which siblings
   reach it transitively.
3. Report the splits that are neither near-universal nor near-absent, naming the
   siblings that do not reach the target.

It cannot know which side of a split is right. The claim under test is that a
reported split is an inconsistency that the project itself later resolves.

## Definitions, fixed now

- **Graph.** `with_package_init_edges(build_graph(src_dir, max_modules=0))`,
  internal nodes only. Test modules (`duplicates.is_test_path` on the module's
  path) are dropped everywhere: as packages, as siblings and as targets.
- **Package `P` and siblings `S`.** `S` is the set of internal non-test modules
  whose parent qualname is `P` (direct children; a subpackage counts as a
  child). `P` qualifies when `|S| >= 6`.
- **Target `T`.** Any internal non-test module that is not `P`, not an ancestor
  package of `P`, and not in `S`. Siblings are excluded as targets. Every
  sibling reaches `P` and its ancestors through the package-`__init__` edges, so
  those are uninformative by construction.
- **Reach set.** `R(T) = {s in S : T in descendants(s)}`; `k = |R|`, `n = |S|`.
- **Candidate split.** `0.2 <= k/n <= 0.8` and `min(k, n - k) >= 2`. The
  motivating incident (13 of 34, 0.38) is inside the band.
- **Firing (the unit of analysis).** Within one package, candidate targets with
  an identical reach set are one firing. Transitive chains otherwise turn a
  single convention into a dozen lines with the same modules on each side, and a
  reader sees one line, not twelve. A firing is `(P, R, targets)`; its
  **missing set** is `M = S \ R`.
- **Snapshots.** For each project in `bench/projects.yaml`, `T1` is the pinned
  SHA and `T0` is the last first-parent commit dated at least **365 days**
  before `T1`. The sensor runs at `T0`; the oracle reads `T1`. A project whose
  `src_dir` does not exist at `T0` is skipped and listed.

## The oracle, and its known biases

Labelling by hand would mean grading my own detector, so the label is
mechanical and comes from the project's own future, as in the mirror study.

For a firing at `T0`, let `M'` be the members of `M` that still exist at `T1`
under the same qualname, and `T*` the firing's targets that still exist at `T1`.

- **Unscorable** if `M'` or `T*` is empty. These are dropped and counted. Per
  AGENTS.md, an unmeasurable unit is never scored clean.
- **True positive (propagation)** if at least one module in `M'` reaches at least
  one target in `T*` at `T1`. The convention spread to a module that lacked it.
- **False positive** otherwise.

**Reported alongside, not gating: retraction.** The split also resolves if no
surviving member of `R` reaches any target in `T*` at `T1`. That is counted and
shown separately. It is not folded into the headline, because modules routinely
stop reaching things for reasons unrelated to any convention.

**Biases, stated in advance:**

1. **It undercounts real inconsistencies.** A gap nobody fixed within a year
   scores as a false positive. This biases against the sensor, which is the safe
   direction for a gate.
2. **It overcounts through ordinary growth.** Modules accumulate imports over a
   year, and "at least one of the missing modules gained reach" can happen by
   chance, especially for a widely used target. This biases toward the sensor,
   and the control below exists to measure it.
3. **One year is arbitrary.** It is fixed here and not tuned afterwards.

## The control

The same oracle is applied to **below-band** splits at `T0`: target classes
with `1 <= k` and `k/n < 0.2`, grouped by identical reach set in the same way.
Their propagation rate is the base rate at which a missing module gains reach to
something only a few siblings reach, which is the chance level that bias 2 adds
to the firings.

## Pre-registered thresholds

The sensor ships only if **all three** hold:

1. **Precision >= 0.40** over scorable firings (propagation only). The figure is
   a lower bound because of bias 1. It is set below the 0.70 used for the mirror
   sensor because this one would be an advisory list in a report a person asks
   for, not a line printed unasked on every `check`. It is set above what the
   duplicates sensor shipped at (~0.38 before its de-noiser) because that sensor
   had a semantic fix in prospect and this one does not.
2. **Lift >= 2.0**: firing precision is at least twice the control's propagation
   rate. Without this, a pass could be measuring that codebases grow.
3. **Power: >= 30 scorable firings from >= 5 projects.** Below that the run is
   underpowered and yields **no verdict**, which is not a pass. An underpowered
   pass does not ship.

**Reported alongside, not gates:** firings per project (a sensor that fires 300
times on one repository is noise even at good precision), the retraction count,
the unscorable count, the per-project precision, and at least ten false
positives in full so a reader can judge the oracle.

**Do not reinterpret these afterwards.** "Precision was 0.33 but the misses
were all test helpers" is a reinterpretation, not a result. A narrower sensor is
a different hypothesis and needs its own pre-registration.

## Harness canary

The harness must reproduce the incident before it is trusted. A synthetic
package of 34 modules, where 11 import a registry directly, 2 reach it through
an unrelated module and 21 do not, must produce exactly one firing for that
target. Its missing set must be the 21, and the 2 transitive reachers must sit
on the reaching side. A negative control removes one reaching edge and requires
the missing set to grow by one. If the canary fails, no number is reported.

## What a null means, and what it does not

A miss kills the sensor as specified. It does not touch items 1 and 2 of #387,
which shipped in v0.43.0 and are rules a maintainer writes, not inferences. It
also does not show that the incident was not real: the 21 modules crashed. A null
says only that "some siblings reach it and some do not" is too common a shape,
in ordinary codebases, to be worth printing.

## Population arithmetic, done BEFORE the oracle ran

Recorded after the harness passed its canary and before any `T1` snapshot was
scored. `--count-only` measures the population at `T0` only and computes no
outcome. Nothing above this section was edited after it ran.

- 29 manifest projects. 2 were skipped (`msgspec`: its `src_dir` did not exist at
  `T0`; `archy`: no commit a year before its pin). That leaves 27 scanned.
- **152 firings from 18 projects; 1,096 control classes.**
- Concentrated: `django` 75, `ansible` 26, `home-assistant` 17, `mypy` 10, then
  9 more projects with 1 to 7 each. Nine projects fire zero times. That is
  structural, not a harness fault. In a flat package every module is a sibling,
  and siblings are excluded as targets (canary-tested), so `rich`, `click`,
  `flask` and similar layouts have no out-of-package targets to split on.
- The control is dominated by `home-assistant` (913 of 1,096), so the pooled
  base rate is mostly that project's. The per-project control rate is reported
  alongside, as with the firings.

**Consequence, accepted in advance.** The gate is reachable: precision 0.40 on
152 firings needs about 61 true positives, if most are scorable. But the firings
are not independent. Half come from one project, and firings in one package
share siblings. So a pass would be a weak pass, and the write-up must report
precision with `django` excluded alongside the headline. A fail is the cleaner
of the two outcomes, as in the mirror study.

---

# Result (amendment, 2026-10-15)

**Everything above this line is unchanged from the two commits that recorded it
before the oracle ran.** No threshold or definition was edited after the run.
This section is appended.

## A correction to the population section, first

The arithmetic above says "152 firings from **18** projects" and "**nine**
projects fire zero times". Both were transcription errors on my part, made when
summarising the `--count-only` output. The output itself shows **14** projects
with at least one firing and **13** with none: `django` 75, `ansible` 26,
`home-assistant` 17, `mypy` 10, then 10 more with 1 to 7 each. The totals (152
firings, 1,096 control classes) were right. The error changes no gate: the power
condition needs 5 projects, and 14 cleared it.

## Headline

| | |
|---|---|
| firings | 152 (149 scorable, 3 unscorable) |
| true positives (propagation) | **32** |
| **precision** | **0.215** (gate: >= 0.40) |
| control: scorable classes / propagated | 1,084 / 80 |
| control rate | 0.074 |
| lift | 2.91 (gate: >= 2.0) |
| projects with scorable firings | 14 (gate: >= 5) |
| retracted (reported, not gating) | 2 |
| **verdict** | **FAIL. Does not ship.** |

The run was powered and the lift condition held, so this is a verdict and not a
no-result. Precision missed the threshold by about half. Re-running after an
efficiency change to the harness (the later graph is now widened once per
project instead of once per firing) reproduced every number above exactly.

**Reported alongside, as the population section required:**

- **Without `django`:** 74 scorable, 19 true positives, precision **0.257**.
  Still a fail; the result is not a `django` artifact.
- **Control without `home-assistant`:** 180 scorable, 23 propagated, rate 0.128.
  Against that rate the lift is 1.7, which would also miss the lift gate. The
  pooled control is the pre-registered one and the headline uses it.
- **Per project** (scorable firings / true positives): django 75/13, ansible
  26/9, home-assistant 17/3, mypy 10/3, setuptools 5/0, scrapy 3/1, botocore 3/0,
  pygments 3/0, pytorch 2/2, and five projects with a single firing (starlette,
  pytest, sqlalchemy, mkdocs 0 each; datasette 1).
- **Per-project control rate** (propagated / scorable): home-assistant 57/904
  (0.063), django 3/49 (0.061), pytorch 8/28 (0.286), ansible 4/25 (0.160),
  scrapy 1/18 (0.056), scikit-learn 3/15 (0.200), sqlalchemy 0/12, setuptools
  0/9, mypy 1/5, datasette 3/4, and 0 in seven projects with four or fewer
  classes each.
- **Counting each package once** (a package is a hit if any of its firings is):
  15 of 62, 0.24. **Averaging precision per project:** 0.238. Neither correlation
  correction rescues it.
- **The oracle's growth bias points the same way.** True positives have larger
  missing sets than false positives (mean 18.2 modules against 7.6), so they had
  more chances to gain reach by accident. Corrected for that, precision would be
  lower, not higher.
- **Volume is not the problem.** The median firing names one target, and no
  project produces more than 75. A reader would not drown; they would be wrong
  four times in five.

## What the false positives are

Twelve misses drawn with `random.seed(387)`, in full. They are reproducible
with `--examples`, which also prints the per-project control rates below. The
firing order in the ledger is sorted, so the draw does not depend on Python's
hash seed. (An earlier draw, made before that sort existed, could not be
reproduced. The review caught it, and this list replaces it.)

```
fp           django     django.db.backends.oracle                    5/10  django.db.backends.base.validation
fp           django     django.contrib.gis.db.backends.spatialite    5/8   django.db.backends.base.client (+1)
fp           django     django.contrib.flatpages                     6/10  django.contrib.auth (+20)
fp           ansible    ansible.utils                                19/26  ansible.module_utils
fp           django     django.contrib.gis.db.backends.postgis       4/9   django.contrib.gis.db.models (+11)
fp_retracted ansible    ansible.utils                                17/26  ansible.module_utils.six
fp           django     django.contrib.gis.db.backends.spatialite    5/8   django.db.backends.base.introspection (+1)
fp           django     django.contrib.gis.db.backends.postgis       2/9   django.db.backends.base.introspection (+1)
fp           django     django.db.backends.mysql                     2/9   django.db.backends.base.introspection
fp           django     django.contrib.gis.db.backends.spatialite    3/8   django.contrib.gis.db.backends.base.operations
fp           sqlalchemy sqlalchemy.dialects                          2/6   sqlalchemy.connectors.pyodbc
fp           botocore   botocore                                     14/44  botocore.crt.auth
```

The dominant shape is a **role-structured** package. The siblings of
`django.db.backends.mysql` are `base`, `client`, `compiler`, `creation`,
`features`, `introspection`, `operations`, `schema` and `validation`: nine modules
with nine different jobs. That one package produces seven firings, one per
`django.db.backends.base.*` module: `mysql.features` reaches `base.features`,
`mysql.client` reaches `base.client`, and so on, each a 2-of-9 or 3-of-9 split.
A client has no use for a feature table. Nothing is missing
there, and no later commit "fixed" it, because there was nothing to fix. The
rest are ordinary uneven need: some of `ansible.utils` uses `six` and some does
not.

The motivating incident was a **peer-structured** package: 34 command modules,
each an entrypoint, each with the same obligation. In a package like that a
split *is* an inconsistency. In a role-structured one it is the design. The
import graph cannot tell the two apart, for the same reason Study 7 found that
"caller" is the wrong relation: it records that a module reaches a target, not
why.

## The finding

**Sibling modules are peers only sometimes, and the graph does not say when.**
The sensor works on the package it was designed from, and that package is the
minority shape among the firings: the false positives above are dominated by
role-structured packages, and a role split looks exactly like a missed
convention. (How common each shape is across all packages was not measured.)

The lift of 2.91 says there is *some* signal. A split in the band propagates
about three times as often as a rare reach does. But that is a fact about how
conventions spread, not a usable alarm. At 0.215 a reader acting on the list
would chase four non-problems for every real one.

## What a null means here

It kills item 4 as specified, as the pre-registration said a miss would. It does
not touch items 1 and 2 (`required:` rules and package-`__init__` edges), which
are declared, not inferred, and shipped in v0.43.0. Those already cover the
incident once someone knows to look.

A narrower sensor, for example one limited to packages whose siblings are all
entrypoints (a `__main__` guard or a `console_scripts` entry, which is #387 item
3's declaration), is a **different hypothesis**. It would need its own
pre-registration and its own pilot. Writing this sentence is not permission to
fit one to this data.

Harness: [`bench/reach_asymmetry_precision.py`](../../bench/reach_asymmetry_precision.py),
ledger `bench/reach_asymmetry_precision.jsonl`. `--canary` rebuilds the incident
and was checked by mutation. It goes red when any of these is broken:
- the package-`__init__` edges, in the sensor and in the oracle;
- the sibling exclusion;
- either band edge;
- the control's membership (an above-band class is in the fixture so that
  "everything not fired is control" is caught);
- the test filter's snapshot-relative path (the fixture sits under a `tests`
  directory);
- each oracle outcome: `tp`, `fp`, `fp_retracted`, and `unscorable` with either
  side gone.

One mutant was found inert and is not claimed: widening the control's upper
bound to 0.5 changes nothing, because an in-band class takes the firing branch
first. The final ledger comes from the harness as committed, and it reproduces
the first run's headline exactly.
