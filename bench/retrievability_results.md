# #418 result: the held-out retrievability measurement could not be run

Pre-registration: [`retrievability_prereg.md`](retrievability_prereg.md), written
2026-10-28 before any candidate was drawn. Draw: `retrievability_draw.py`, seed 418.

## What happened

The keyword filter matched **143** assistant blocks across `q1b_transcripts` and
`greenfield_transcripts`. Labelled from the text alone, per the pre-registered table:

| label | count |
| --- | ---: |
| `direct` | 0 |
| `transitive` | 0 |
| `docs`, `tests` | 0 |
| `other` | 143 |

Ids 0 to 85 were read in full or in keyword context; ids 86 to 142 were screened for any
question-shaped cue ("?", "does X import/reach", "who uses/imports", "any other
module/caller") and had **none**. Not one of the 143 asks a structural question. They are
the agent narrating its own edits ("Now the models layer, which must not import
repositories"), declaring the architecture it is building, or checking that an import
works.

So `structural = 0`, the Wilson interval is undefined, and the pre-registered outcome is
**unmeasurable on this source**: no row of the threshold table applies. Nothing is scored
clean and nothing is scored as a miss.

## Why, and what it licenses

The questions `--module` was built for ("does `risk` import `hotspots`", "does any of
graph/cycles/score still reach `layers`") appear in agent reasoning on archy's own
multi-surface wiring tasks. The held-out sources are different task shapes: SWE-bench
fixes and greenfield builds, where the agent writes a layer or edits a file and never
stops to ask a cross-module question. That is a property of those tasks, not a defect in
the sampling.

This licenses exactly two statements:

- `--module` has **no held-out retrievability score**, and none is obtainable from the
  archived transcripts.
- Nothing here says it fails, either. The 0 of 24 scored the ordinary ranked report
  before `--module` existed, so it is not evidence about `--module` in either direction.

## Why no follow-up

The only way to get held-out derivations of this shape is new agent runs on archy-style
cards. That is the expensive path, and it would measure whether the questions arise and are
answerable, on a feature whose delivery line has come back null six times
(`docs/WHAT_DIDNT_WORK.md`, Study 8). The pre-registration's class-mix question (what
share of derivations are transitive) cannot be answered from 0 structural rows, so no
ticket is filed on the transitive-reach hypothesis either; it stays a hypothesis.

The remaining actions were documentation: `docs/SHIPPED.md` now says plainly that the
retrievability claim is unmeasured, and `docs/WHAT_DIDNT_WORK.md` flags that the 0 of 24
in its delivery table cannot be read as a `--module` result.
