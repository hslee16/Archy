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
