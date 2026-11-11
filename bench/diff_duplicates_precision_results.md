# Diff-scoped duplicates: firing rate and precision (#548)

Captured 2026-11-11. Pre-registration:
[`docs/research/DIFF_DUPLICATES_PRECISION.md`](../docs/research/DIFF_DUPLICATES_PRECISION.md),
committed as `d1f60b45` on `main` (`7f132773` on its branch), BEFORE the harness
(`6119dffd`) existed. Three dated addenda follow it, each written before the thing it
describes ran or was read: after the pilot (`7d16093f`), during the run (`65d15650`),
and labeler A (`65d15650`, tracked in #959). Thresholds, the firing definition and the
rubric were never changed.

## Verdict: precision kill. The finding is not built.

| step | pre-registered bar | measured | result |
| --- | --- | --- | --- |
| Headroom: firing rate | at least 1 PR in 50 (2%) | 177 of 1,049 measured Python PRs, **16.9%** (13.0% if dropped Python PRs count as clean) | pass |
| Headroom: scorable firings and repos | at least 30 from at least 10 repos | 557 firings in 134 repos; **119 scorable from 66 repos** | pass |
| Precision, strict (both labelers TRUE) | pass at least 0.60, kill below 0.40 | **19 of 119 = 0.160** | **kill** |

The lenient reading (either labeler TRUE) is 33 of 119 = 0.277, also below 0.40, so the
choice between the two readings does not matter here. Cohen's kappa between the two
labelers is 0.66.

By the pre-registration this is a result, not "try again", and no subgroup rescues it.

## The limitation that has to sit beside the number

**No human label exists.** Both labelers are LLMs (A `claude-sonnet-5-5`, B
`claude-opus-5-5`), a deviation recorded in addendum 3 before the second labeler ran and
tracked in #959. Models from one vendor share training lineage, so shared blind spots
are likelier than between a person and a model, and kappa 0.66 is model-to-model
agreement only. This result is not human-validated. For a human to move it out of the
kill zone, they would have to call at least 48 of the 119 firings true, against 33 for
the more generous of the two models, so it is unlikely to change the verdict, but it
has not been shown.

Three of labeler B's 119 labels were printed during an end-to-end check before that
decision (disclosed in addendum 3). They were not used to choose the model, the second
labeler or any threshold.

## Population and headroom

2,000 PRs drawn from 57,517 closed AIDev PRs in Python-language repositories (seed 548,
at most 25 per repo, pilot excluded). 87 ran in one process, the rest in 8 workers
sharded by repository.

| | PRs |
| --- | ---: |
| drawn | 2,000 |
| no added Python line (never a Python PR) | 642 |
| dropped on `QualnameCollisionError` | 299 |
| unresolvable (repo or pull ref gone) | 8 |
| scan too large | 2 |
| analysis timeout | 0 |
| **measured Python PRs** | **1,049** |
| with at least one firing | 177 |

557 firings; median 2 per firing PR, largest 39. The top five repos supply 24% of them
and the per-repo cap held (at most 24 measured PRs in one repo, 551 repos measured), so
this is not a one-repo artifact. Firing PRs by agent, over that agent's measured PRs:
Claude Code 106 of 538, Copilot 25 of 199, Codex 34 of 228, Cursor 7 of 24, Devin 3 of
36, Jules 2 of 24.

**Attrition may fall unevenly.** The 299 collision drops are archy refusing trees where
two files share a module name. In the pilot those were large repositories (prefect,
mlflow, nexus); I did not measure repository size across all 299, so that the measured
population leans toward smaller, cleaner repositories is a suspicion, not a finding.
Counting all 309 dropped Python PRs as clean gives the 13.0% figure. Neither moves the
headroom result.

**Near-miss supplies most firings and is least precise.** 348 of the 557 are near-miss
(Type-3, the 0.85 token-overlap tier). 16 firings are in PRs where the shipped near-miss
pass hit its 5,000,000-comparison budget and may be short.

## Primary oracle: two blind labelers

120 firings drawn by seeded sample from the 557 (seed 548) over ids alone, before any
packet was built. 119 packets were built; one firing could not be read at the head
commit and is dropped as unscorable, never scored FALSE. No firing was marked
UNSCORABLE by either labeler.

| | TRUE | of | precision |
| --- | ---: | ---: | ---: |
| strict (both TRUE) | 19 | 119 | 0.160 |
| lenient (either TRUE) | 33 | 119 | 0.277 |

Why the rest are FALSE (each labeler's reason, frozen rubric):

| reason | A | B |
| --- | ---: | ---: |
| short-or-trivial | 23 | 19 |
| widespread-name | 19 | 16 |
| sibling | 17 | 24 |
| example-or-test-like | 10 | 14 |
| other | 9 | 15 |
| boilerplate | 5 | 9 |
| isolation | 2 | 1 |
| same-class | 1 | 2 |

Most of what fires is something a person would call intended or too small to matter:
short functions that clear 30 nodes, the same shape repeated so widely that there is no
single "existing one" to reuse, and per-backend or per-format siblings.

## Subgroups (reported in full, never results)

| group | strict TRUE | lenient TRUE | firings |
| --- | ---: | ---: | ---: |
| exact (byte-identical body) | 4 | 7 | 10 |
| shape-identical, not byte-identical | 5 | 6 | 33 |
| near-miss | 10 | 20 | 76 |
| copy in a new file | 13 | 20 | 78 |
| copy in an existing file | 6 | 13 | 41 |

The exact tier is 4 of 10 strict, which sits at the kill line, on ten firings. It is not
a result: the pre-registration names exact versus near-miss as a subgroup to be
reported in full and never counted, forbids a post-hoc subgroup rescue, and an interval
on n=10 is far wider than any bar. A follow-up that wanted an exact-only finding would need its own
pre-registration and its own sample.

## Secondary: the git oracle (never decisive)

Only the 6-month arm is measured: firings are computed at the PR's final head, so the
ticket's "later PR revision" arm cannot apply by construction.

| outcome at the last default-branch commit within 183 days of the merge | primary TRUE (19) | primary FALSE (100) |
| --- | ---: | ---: |
| removed | 2 | 1 |
| diverged | 5 | 27 |
| unchanged | 3 | 10 |
| unscorable (unmerged, or a file gone, which a rename also looks like) | 9 | 62 |

It does not discriminate. Of the scorable firings, 7 of 10 primary-TRUE were removed or
diverged against 28 of 38 primary-FALSE, and 71 of 119 are unscorable. It is biased
against the feature by construction, so this says only that it could not confirm the
labelers.

## Secondary: the Sonar proxy (never decisive)

For the 19 strict-TRUE firings: 3 would also fail SonarQube's 3% new-code duplication
gate under the proxy (exact copy of at least 10 lines in a PR over 3% duplicated), and
16 only archy would have added. This is a proxy for Sonar's 100-token detector, not that
detector, and it is moot once the precision kill stands.

## What was found along the way

- **Module-name collisions drop about a fifth of the Python PRs.** 299 of 1,358 Python
  PRs (22%) hit `QualnameCollisionError`, a cost of the shipped detector that any in-loop use
  would also pay.
- **`git log -M` on a blobless clone can hang a worker.** A shard sat 22 minutes on the
  co-change layer of a very large repository. The analysis now runs in a supervised child
  with a 30 minute ceiling (a timeout is a drop, never clean). No PR hit the ceiling after
  the fix.
- **Harness tests that could not fail.** Several mutations survived the first test set
  and each exposed a missing assertion (the labeling draw taking a prefix, the lenient
  reading deciding a verdict, the strict reading in the report), per the repo's
  verify-by-mutation rule.
- **A headroom number that looked artifact-shaped (16.9% against a 2% bar) was audited**
  for concentration before being believed: spread over 134 repos and six agents.

## What this licenses and what it does not

It licenses: a diff-scoped "re-implemented an existing function" finding built on archy's
shipped exact and near-miss tiers would be right about one time in six (strict) to one in
four (lenient) on agent PRs, below the 0.40 kill line, with most false firings being
short, widely repeated or sibling code. It is not built, and nothing here proposes a
retry.

It does not license: any claim about Type-4 (semantic) redundancy, which this detector
misses by design and which is where the agent-PR literature measures its effect; any
claim about a human reviewer's judgment (no human labeled); or any claim about an
exact-only variant.

## Reproduce

See [`diff_duplicates_data/README.md`](diff_duplicates_data/README.md). The ledgers, the
two labelers' files and the blind key are committed there.
