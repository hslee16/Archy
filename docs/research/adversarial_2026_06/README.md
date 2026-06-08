# Adversarial review of archy, 2026-06

A 2-round multi-agent adversarial review swept archy's three pillars: **code
correctness**, **research reasoning**, and **bench rigor** (a "theater" check,
in the spirit of the simulate-oracle tautology lesson: a validation that gates
on the property it tests proves nothing, and comparing a subset of output
fields hides bugs).

Master tracking issue: **[#180](https://github.com/hslee16/archy/issues/180)**.
Per-subsystem tickets: **#161 - #179** (label `adversarial-review`).

## Method

- **Round 1 (broad).** ~29 adversarial finders (12 code subsystems, 9 research
  docs, 8 bench script+result pairs); every finding re-checked by an independent
  skeptic that defaults to *refuted*. **174 raised, 144 survived, 30 refuted**
  (204 agents).
- **Round 2 (empirical).** 10 probes that *ran experiments* (built fixtures,
  swept the score axes, ran on the bench corpus, web-verified citations) against
  the round-1 completeness gaps; confirmed findings independently reproduced
  (18 agents).
- **Final confidence: HIGH**; the completeness critic recommended no further
  round (round 2 showed diminishing returns).

## Files

- `round1_findings.json` / `round1_findings.md` - all 144 verified round-1
  findings with location, evidence, impact, suggested fix, and verifier notes.
- `round2_findings.json` - the 10 empirical probes, their confirmed findings,
  experiment summaries, and the final confidence assessment.

## Highest-signal confirmed findings

- **graph.py:416 off-by-one** - an over-dotted relative import injects a phantom
  external node and silently drags a real project's score 0.6774 -> 0.5942 with
  no error surfaced ([#161]).
- **index.py crashes on a corrupt cache row** despite a docstring promising a
  self-healing reparse ([#167]).
- **risk.py zeroes the highest-blast-radius pure sinks** (e.g. `scrapy.signals`:
  fan_in 26, propagation_cost 0.868, edit_risk 0.0), so `archy_high_risk_modules`
  is blind to them ([#166]).
- **No bench validates score-delta DIRECTION** on injected structural changes;
  adding one real cycle can move `overall` the *wrong* way at scale ([#178]).
- **SCORING.md "depth barely moves the score" is measurably false** (depth ranks
  #2 of 5 in local sensitivity); corrected in this PR ([#176]).
- **LocAgent citation overstated/mis-attributed** in `RESEARCH_METRICS.md`;
  corrected in this PR ([#175]).

## Honest negatives (hypotheses tested and DISPROVEN)

- **Modularity is deterministic**: `raw_modularity`, `community_count`, the
  weighted/unweighted Q gap, and `overall` were bit-identical across 20-40 runs
  and shuffled node-insertion orders on 6 real repos plus adversarial synthetic
  graphs.
- **Core metric formulas are correct**: Martin instability, MacCormack
  propagation cost, and the `edit_risk` geomean were hand-derived on a small
  known graph and matched archy's output (no off-by-one, self-inclusion, or
  normalization error).
- **impact.py has no glob bug** (no glob code); the `**` defect is confined to
  `affected.py` / `layers.py`.
- **3 of 4 load-bearing research citations verified faithful** with exact
  numeric matches (Constraint Decay, Navigation Paradox, MacCormack).
- **30 round-1 findings were refuted** by the verification pass and dropped.
