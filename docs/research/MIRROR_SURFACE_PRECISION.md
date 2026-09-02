# Mirror-surface detection: precision pre-registration (#429)

**Status: PRE-REGISTERED, NOT YET RUN.** Written before the harness exists and
before any number is known. Nothing in this file has been changed to fit a
result. If it is edited after the run, the edit is an amendment and says so.

## Why this file exists before the feature

[#429](https://github.com/hslee16/archy/issues/429) proposes that `archy check`
report, unasked, when a change updated a producer symbol and did not update the
surfaces that mirror it. Its own pre-registration clause says the precision
pilot runs **before any behaviour ships**, and this repository has a cautionary
case for doing it the other way round: [#369](https://github.com/hslee16/archy/issues/369)
fixed a win condition against a control rate inferred from a paper, and the
measured control rate made the win arithmetically unreachable before the run
started. Nobody noticed until afterwards.

So the order here is: thresholds, then reachability arithmetic, then harness,
then run, then decide. **A feature that misses the threshold does not ship**,
and the null goes in [`../WHAT_DIDNT_WORK.md`](../WHAT_DIDNT_WORK.md) with the
other four.

## What the detector will do

For a diff (a dirty tree, or `--since <ref>`):

1. Find the symbols the diff changed.
2. For each changed symbol, find its callers in the call graph archy already
   builds, and group them by the module they live in.
3. Report the caller modules the diff did **not** touch.

The mirror relation is **derived, never configured**. A config listing
`cli.py` and `mcp.py` as mirrors of each other would be a rule the maintainer
declared, which is the thing archy already does with `archy.yaml`; the claim
here is that the relation can be read off the graph.

## Definitions, fixed now

- **Producer symbol.** A top-level function or class defined in a module that
  the diff modified.
- **Caller module.** A module with a call or import edge into that symbol.
- **Candidate.** A `(commit, symbol)` pair where the symbol has caller modules
  in **two or more** distinct modules, at least one of which the commit touched.
  A symbol with one caller module has no mirror to be missing.
- **Firing.** A `(commit, symbol, module)` triple where `module` is a caller
  module the commit did not touch, and the commit touched at least one other
  caller module of that symbol. The asymmetry is the signal: updating one
  caller and not another is what "did not propagate" means.
- **Unit of analysis:** the firing. Precision is over firings, not commits,
  because one commit can fire on several symbols and a reader sees each line.

## The oracle, and its known bias

Labelling by hand would mean grading my own detector, so the label is
mechanical and comes from the repository's own future:

- **True positive:** within the next **5 commits** on the same branch lineage, a
  commit modifies the named module *and* its diff text mentions the symbol (or
  the symbol's name appears in the added/removed lines). The omission was real
  and somebody later had to go back and fix it.
- **False positive:** otherwise.

**The bias this carries, stated in advance: it undercounts true positives.** An
omission that was never corrected is a real bug still latent in the tree, and
this oracle scores it as a false positive. That biases **against** the feature,
which is the safe direction, and it is the reason a passing result can be
believed and a failing one cannot be blamed on the oracle alone.

Two further limits, also stated now: a later commit that touches the module for
an unrelated reason and happens to mention the symbol scores as a true positive
(inflating precision), and the 5-commit window is arbitrary. Both get reported
alongside the headline number rather than folded into it.

## Reachability arithmetic, done BEFORE the run

This is the #369 check. Over 286 non-merge commits, 36 touch one of the three
producer modules (`layers.py`, `graph.py`, `score.py`); of those, 14 touched
both `cli.py` and `mcp.py`, 11 touched `cli.py` but not `mcp.py`, and 10
touched neither surface. So the coarse, file-level firing population is **at
most ~21 commits**.

The real unit is finer than a commit (a commit can fire on several symbols), so
the firing count will be larger than 21, but the same handful of commits
generates it and the firings are **not independent**. A precision estimate on
this population is wide: at n=25 firings, the 95% interval on a point estimate
of 0.75 runs roughly 0.55 to 0.89.

**Consequence, accepted in advance: this pilot can rule the feature OUT and
cannot strongly rule it IN.** A precision of 0.4 is a clear no. A precision of
0.75 on 25 correlated firings is not proof, and the write-up must say so rather
than reporting the point estimate alone.

## Pre-registered thresholds

- **Primary (ships / does not ship): precision >= 0.70** over all firings.
  Below that, the feature does not ship in any form. A line an agent learns to
  skip is worse than no line, and this one fires unasked on every dirty tree.
- **Minimum firings: n >= 20.** Below that the run is **underpowered and
  yields no verdict**, which is not the same as a pass. An underpowered pass
  does not ship either.
- **Reported alongside, not gates:** the count of firings per commit (a
  detector that fires 30 times on one commit is noise even at high precision),
  the number of distinct commits that fired, and the false-positive examples in
  full so a reader can judge the oracle.

**Do not reinterpret these afterwards.** In particular, "precision was 0.62 but
the misses were all the same kind" is a reinterpretation, not a result.

## What a null means, and what it does not

A miss kills the `check` surface proposed in #429. It does **not** retract the
census that motivated the ticket: 6 patches on tasks naming all three surfaces,
1 touching `mcp.py`, and 7 of 25 cells wiring nothing. The gap is measured and
real; a null here says only that this derivation cannot name it precisely
enough to be worth printing.

The honest limit #429 already states stands either way: this can only catch an
omission after the edit, never prevent it, and only in runs that invoke `check`
after editing at all (42 of 54 on `archy-02`, so ~22% of runs are out of reach
by construction).

---

# Result (amendment, 2026-09-02)

**Everything above this line is unchanged from `a20d22f`, the commit that
pre-registered it.** No threshold was edited after the run. This section is
appended, which is the only honest way to add a number to a pre-registration.

## Headline

| | |
|---|---|
| commits scanned | 287 (all non-merge) |
| firings | **42** (threshold required >= 20, so the run is powered enough for a verdict) |
| true positives | **1** |
| precision | **0.024** |
| distinct commits that fired | 21 |
| most firings on one commit | 4 |
| **verdict** | **FAIL. Does not ship.** |

The threshold was 0.70. This missed it by a factor of thirty, which is the one
kind of result the reachability arithmetic above said this pilot *could* deliver
cleanly: it can rule the feature out, and it did.

## It is not the oracle

The oracle was built to be the prime suspect, and it is not the cause. It scores
a firing true only if a later commit went back and touched the named module for
that symbol, so a real omission nobody ever corrected reads as a false positive.
That bias runs against the feature by construction.

But the false positives are not uncorrected bugs. The three most common shapes:

```
load_config   changed in layers.py; cli.py updated, contracts.py NOT updated
LayerConfig   changed in layers.py; cli.py updated, diff.py     NOT updated
compute_score changed in score.py;  cli.py updated, diff.py     NOT updated
```

`contracts.py` calls `load_config`; it does not mirror it. Nothing about
`load_config` changing obliges its callers to change. Raising the oracle's
sensitivity would not convert these, because there is nothing to convert: no
later commit fixed them, and none should have.

## The finding

**"Caller" is the wrong relation.** A mirror is narrow: a set of surfaces that
must all be updated when a finding kind is added. Callers are wide and dominated
by ordinary consumers. #429's proposed derivation conflates them, and archy's
graph cannot separate them, because it records *that* `contracts.py` calls
`layers.load_config`, not *why*.

## Post-hoc subgroup, reported because this file asked for it

Restricted to firings where both the updated and the unmirrored module are in
the `cli.py`/`mcp.py` pair, the two surfaces that genuinely do mirror each
other: **18 firings, 1 true positive, precision 0.056.**

This was computed after the run and is **not a result**. It is here because the
"reported alongside, not gates" clause above asked for the false positives in
full so a reader could judge the oracle. It rescues nothing; it is the same null
at higher resolution, and it closes the obvious escape hatch rather than opening
one.

## The one true positive

```
385efdf08631  build_graph changed in graph.py; cli.py updated, mcp.py NOT updated
              corrected by e4fcc9629a6a
```

This is exactly the pattern #429 describes, and it happened once in 287 commits.

## Harness bugs caught before the number existed

Both would have produced a publishable-looking result:

1. **Substring matching measured English.** Symbol names were matched with
   `symbol in source`, and archy's CLI commands are named `check`, `diff` and
   `snapshot`, so every module containing the word "check" scored as a caller of
   `cli.check`. 42 firings at precision 0.038 -- a clean-looking FAIL produced
   entirely by vocabulary collision.
2. **Module names lost their package prefix**, returning `layers` rather than
   `archy.layers`, so no `ImportFrom` ever matched and the detector fired zero
   times on every input. A rule set that cannot fire looks exactly like a clean
   codebase.

`bench/mirror_precision.py --canary` now asserts the resolver still resolves two
known cross-surface symbols, and the run refuses to report numbers if it does
not.

## What is NOT licensed by this

A narrower relation (for instance, the mirrored-surface families
`archy conventions` already detects) is a **different hypothesis**. It needs its
own pre-registration and its own pilot. Fitting one to this data set is exactly
the move this file exists to prevent.
