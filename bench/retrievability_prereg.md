# Pre-registration: is `conventions --module` retrievable? (#418)

Written 2026-10-28, BEFORE any candidate derivation was drawn or scored. Edits after
that date go in a dated addendum, never over this text.

## Question

Given a structural question an agent actually asked itself while working, does
`compute_module_view` (CLI `conventions --module`, MCP `archy_module_view`) return the
answer, complete and correct? This is coverage of the lookup, not whether an agent
reaches for it: six delivery-line interventions have already come back null, so no agent
time is spent here.

## Unit and sampling

The unit is a DERIVATION: one assistant text or thinking block from an archived
transcript that states or asks a structural fact about the code under work.

- Source: `bench/q1b_transcripts` and `bench/greenfield_transcripts` only. The original
  24 (development data) came from the archy footprint corpus, so
  `bench/agent_footprint_transcripts` is excluded.
- Candidates: blocks matching the keyword filter in `bench/retrievability_draw.py`
  (imports, imported by, consumers of, reach, depends on, import cycle). The filter is
  fixed before reading any candidate.
- Draw: seeded random sample (seed 418) of candidates, stratified by corpus, labelled in
  draw order. Stop at 40 derivations that the labeller marks `structural` (below), or
  when candidates run out, whichever is first. Derivations are not cherry-picked after the
  draw; a candidate is skipped only for the reasons in the label table.

## Labels (assigned from the question text alone, BEFORE running the view)

| label | meaning |
| --- | --- |
| `direct` | answerable from one module's direct imports or importers |
| `transitive` | needs "reaches" across more than one hop, or "any of A reaches B" |
| `docs` | about documentation or non-code content |
| `tests` | about test helpers or test modules |
| `other` | not a structural import question (behavior, values, runtime); excluded from the denominator |

`structural` = `direct` + `transitive`. Labelling `transitive` requires quoting the
phrase that needs more than one hop.

## Scoring

For each `structural` derivation, the labeller works out the ground truth by grep on
the tree the transcript ran against, then records the expected answer. The view is run on
that tree. A derivation scores:

- **answered** if the view's output contains the answer, complete (a negative needs the
  target absent from a list the view marks as complete);
- **not answered** otherwise, including `transitive` ones, which `ModuleView` cannot
  state in one call. Reported separately so the gap is visible.
- Runs on a tree that cannot be reproduced at the transcript's commit are DROPPED, never
  counted as answered.

Baseline arm: the same questions scored against the ordinary `conventions` report (top 12
default). Pilot: the first 5 structural derivations are scored against both arms before
the thresholds below are applied, to confirm the baseline is not saturated (the #369
trap). If the baseline answers 4 or 5 of the 5, the contrast is uninformative and the
result is reported as such, with no claim made.

## Thresholds (fixed now)

Primary: `p = answered / structural` for `--module`, with a Wilson 95% interval.

| outcome | condition | consequence |
| --- | --- | --- |
| supported | lower bound of the interval >= 0.60 and point estimate >= 0.80 | retrievability claim stands, scoped to the classes measured |
| inconclusive | anything between | report both numbers, no claim either way |
| **kills the claim** | point estimate < 0.50 | the "answers the derivations it was built for" claim is retired in SHIPPED.md; feature stays |

Secondary, reported but NOT thresholded: the class mix (`direct` / `transitive` / `docs`
/ `tests` / `other`), and `--module` minus baseline. A transitive share above 0.25 of
`structural` is reported as evidence for a separate ticket; it does not by itself gate
anything here.

## Not in scope

`--module` correctness (unit-tested); whether agents call it (null six times, see
`docs/WHAT_DIDNT_WORK.md` Study 8); the original 24, which are development data and are
not re-scored.
