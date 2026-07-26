# Does a declared architecture decay? (#364)

**Result: no. All four pre-registered signals came back null or untestable.**
Measured 2026-07-26 over 11 repositories and 107 evaluable samples, using each
project's own import-linter contracts. Raw rows: `bench/contract_decay.jsonl`.

This was the validation study for the decay/coverage pivot that #359 pointed at.
**The pivot does not survive it**, and per #364's own terms that is the finding,
not a reason to look for a different signal.

## Against the thresholds, written before the run

| signal | pre-registered null | observed | verdict |
| --- | --- | --- | --- |
| 1. standing violations | dies if the median clears in <= 2 commits | 1 sample pair held a violation, in **1 of 11 repos** | too rare to measure. NULL |
| 2. rule relaxation | dies below 5% of resolutions | **no violations resolved at all** | UNTESTABLE |
| 3. dead rules | dies if >80% of rules govern modules that still exist | **89%** do (12 of 107 samples name an absent module; 1 of 9 repos at the latest sample) | NULL |
| 4. coverage erosion | dies if the slope is >= 0 | **10 of 11 repos flat or rising**; only 1 falling | NULL |
| 2b. rules deleted outright | added mid-study after the smoke run | **1 of 11 repos** (NVIDIA/cloudai, 6 contracts -> 1) | too rare to build on |

## What was actually observed

Decay is real and it is rare, which is the same shape as everything else this
project has measured:

- **Genuine dead rules exist.** `Clarity-Digital-Twin/brain-go-brrr` names
  `brain_go_brrr.data`, `.tasks`, `.training`, and `.core` in its contracts at
  commits where those modules do not exist, and
  `Bestpart-Irene/secondsign-core` names `secondsign.audit`. Contracts really do
  keep pointing at modules that have gone. It happened in **2 of 11 repos**.
- **One project deleted most of its architecture.** cloudai went from 6
  contracts to 1 over the sampled window. That is decay in the plainest possible
  form, and it happened once.
- **Coverage does not erode; it improves.** Ten of eleven repos hold steady or
  govern *more* of their code over time. The intuition behind the pivot, that
  configs are written once and silently outgrown, is not what these projects do.

## Why this study was nearly worthless, and what saved it

**Six measurement artifacts were found and fixed before any number here was
trusted**, each of which made decay look worse than it is:

| artifact | effect |
| --- | --- |
| `root_packages` folded into the governed set | coverage trivially 100% for every repo, so every erosion slope was exactly 0.0000 |
| Layers ` : ` sibling syntax parsed as one name | `installer : parser : runner : ...` counted as a single module that does not exist |
| `pkg.*` wildcards never matched | `kio.*` read as rot |
| container-relative layer names matched absolutely | 7 of cloudai's 8 names read as missing when all 8 exist |
| src-layout packages never discovered | all 6 of kio's contract names read as missing, because the scanner never found `src/kio` |
| external packages counted as internal | `flask` and `fastapi`, named in perfectly healthy rules, read as missing modules |

The dead-rule signal fell **63 -> 40 -> 12 of ~107 samples** as these were
removed. Had the first run been believed, this document would have reported that
57% of samples carry rot and the pivot was vindicated. **Every one of those
artifacts pointed the same way: toward the answer the study wanted.**

That is the entire reason the thresholds were pre-registered and the numbers
audited before being written up.

## Limits

- **11 repos, 107 evaluable samples.** Small, and drawn from #359's star-ordered
  code-search corpus which never reached large adopters.
- **19 of 126 samples could not be evaluated** (version skew: historical configs
  under a current import-linter), dropped rather than scored clean.
- **Signal 2 is untestable here, not refuted.** Detecting whether a rule was
  edited rather than obeyed needs violations to resolve, and none did. A corpus
  with more violations could still answer it.
- Sampling is 12 evenly-spaced commits per repo, so a violation introduced and
  fixed between two samples is invisible. This biases signal 1 downward.

**None of these are offered as a reason to rerun with a different corpus.** #364
committed to one validation, not a search for a surviving signal, and the
thresholds were set with this corpus already in hand.

## What this means

The pivot was: stop claiming archy catches the moment of violation (rare), claim
instead that it catches rules quietly ceasing to mean anything. **That second
claim is now measured, and it is also rare.**

What survives is narrow and worth stating precisely:

- **Dead rules are real but affect a minority of projects** (2 of 11 here). A
  tool that reports them is doing something true and occasionally useful, which
  is a much smaller claim than a product thesis.
- **Coverage reporting (#362) is still justified**, but not by decay. It is
  justified because a config that governs 14% of your edges reads identically to
  one that governs all of them, which was demonstrated on archy's own repository
  rather than inferred from a corpus.

Everything measured about this problem now says the same thing: it is real, it
is rare, and no measurement this project has run supports a claim stronger than
that. That belongs in `docs/WHAT_DIDNT_WORK.md`, and the third consecutive null
is a fact about the problem, not about the instruments.
