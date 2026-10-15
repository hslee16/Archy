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
