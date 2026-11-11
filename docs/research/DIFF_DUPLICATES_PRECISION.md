# Pre-registration: does a diff-scoped "re-implemented an existing function" finding fire often enough, and is it right when it fires? (#548)

Written 2026-11-11, BEFORE `bench/diff_duplicates_precision.py` exists, before any PR was
drawn, and before any label was read. Edits after this date go in a dated addendum at the
bottom, never over this text. The commit that adds this file is quoted in the results.

## Question

`archy duplicates` reports the duplication backlog of a whole tree. A reviewer asks a
narrower thing at change time: did this diff add a function that already exists elsewhere?
Keeping only the clusters with one member inside the diff's added lines and one member that
already existed at the base commit would turn the report into that check. This study measures
two numbers on Python agent PRs before anything is built:

1. **Firing rate:** how many PRs produce at least one such cluster.
2. **Precision:** of the firings, how many are cases where the new function should have
   called or reused the existing one.

It measures the clones archy can see (AST-shape exact plus token-overlap near-miss). It does
not claim to cover Type-4 (semantic) redundancy, which is the kind the agent-PR literature
measures (arXiv 2601.21276) and which this detector misses by design. Low recall is expected
and is not a finding of this study.

No agent time is spent. The unit of work is a git checkout and a parse, so the expensive part
of any bench here (live agent runs) does not apply.

## The action, if it passes

The PR author or the agent is told "new `f` in `a/x.py` matches existing `b/y.f`" and deletes
the copy in favour of a call. If the study fails, nothing is built and
`docs/WHAT_DIDNT_WORK.md` records why. This ticket ships no CLI flag, MCP parameter or hook,
is never a score input, and adds no embeddings and no per-turn hook output.

## Prior art recorded before the run

- **SonarQube Cloud, "Sonar way for agentic AI"** already fails a PR whose new code has more
  than 3% duplicated lines. A Python duplicated block there needs at least 100 successive
  duplicated tokens over at least 10 lines, with only indentation and string literals
  ignored, so a copy with renamed identifiers is not a duplicate to Sonar. archy folds
  identifiers and literals. The gate is a density threshold, so one 15-line copy in a
  600-line PR (2.5%) passes. See "Reported, never decisive" for what this study records
  about it.
- **mick-gsk/drift** ships the in-loop version as a Claude Code hook and measures its own
  speak-rate by replay (0.0% to 5.6% on five repos). Its first run said 27% to 69% and the
  output was junk; the classes it derived are enumerated below so they are counted as
  reasons for FALSE labels instead of tuned away after the fact. Its replay measures noise,
  not precision or effect, so it does not change the anti-theater answer for this ticket.
- Detector precision history: about 50% similarity-only ceiling, about 63% exact tier, about
  74% source with co-change demotion (#242, #247, `docs/LEARNINGS.md`). Co-change abstains
  for files with fewer than 5 commits (`min_evidence`), so a copy in a NEW file falls back
  toward the ceiling. This study is expected to land nearer 50% than 74% for that reason.
- Two shipped detectors missed their pre-registered precision bars: Study 7 (0.024 against
  0.70) and Study 9 (0.215 against 0.40). Both harness bugs in Study 7 produced
  publishable-looking numbers, which is why the canary below runs before any real run.

## Population and sampling

- **Source:** AIDev (`hao-li/AIDev`), tables `pull_request` (state, `merged_at`, `repo_url`,
  `agent`) and `pr_commit_details` (`filename`, `status`, `patch`, per commit). The base
  commit and head commit of each PR are recovered from the repository's git history, not
  from the dataset.
- **Python PR:** a PR with at least one `.py` file whose patch carries an added line. Merged
  and closed-unmerged PRs are both in; open PRs are out (no outcome and the head moves).
- **Pilot:** 20 Python PRs drawn with seed 548. The pilot is disjoint from the main sample
  by construction (drawn first, removed from the pool).
- **Main sample:** N Python PRs drawn uniformly at random from the remaining pool with seed
  548 and at most 25 PRs per repository (the cap stops one prolific repository from
  supplying the whole firing set; it is applied in draw order). N is fixed in the
  post-pilot addendum from the pilot's measured wall time per PR, and only from that. The
  sample is not extended after the run starts: a short result is a result.
- **Unmeasurable PRs are dropped, never scored clean.** A PR whose base or head cannot be
  checked out, whose tree fails to parse, or whose scan exceeds the module cap is recorded
  with its reason and removed from the denominator. Dropped counts are reported by reason,
  and the headroom rate is also reported with every dropped PR counted as a non-firing, so
  the effect of dropping is visible.

## The detector and what counts as a firing

The shipped detector, unmodified, run on the head tree:

- exact clusters: `compute_duplicates(min_size=30)`, then `classify_variants`, then
  `demote_independent` where git history is present (the `archy duplicates` default);
- near-miss clusters: `compute_near_duplicates(min_size=30)` at the shipped 0.85 floor.
  These have no variant tier of their own, so the harness applies the same path filter
  (below) to them;
- **source-only (#247):** a cluster is dropped if any member is a test path
  (`is_test_path`) or a vendored path. Exact clusters the detector demotes to the `variant`
  tier are dropped. No other filter is added.

A cluster is a **firing** iff both hold, computed from `git diff base..head`:

- **A (new):** at least one member's `def` line falls inside an added hunk of the diff;
- **B (pre-existing):** at least one OTHER member existed at base, in a location outside the
  added hunks (a function present at base whose lines the diff did not add).

One firing is one (PR, cluster) pair. The unit of precision is the firing. Firings in one PR
are reported both ways (per firing and per PR) so a single PR cannot hide behind a count.
Only clusters the diff touches can fire, so a backlog cluster wholly at base never counts.

Headroom is `PRs with at least one firing / measured Python PRs`.

## Oracles

### Primary: blind labels from two labelers under the frozen rubric

Each scorable firing is labelled by two labelers working independently. Each sees the
repository, the new function and the existing function (paths, bodies, surrounding file),
and is blind to: the detector tier and similarity, the PR's merge outcome, the reviewers'
comments, and the git oracle. They label each firing TRUE or FALSE, and for FALSE record one
reason from the list below.

**TRUE** means: the new function should have called or reused the existing one (directly,
or by extracting a shared helper both call).

**FALSE** means: the duplication is intended or benign.

Frozen rubric for FALSE, one reason recorded per firing:

| reason | meaning |
| --- | --- |
| `sibling` | per-backend, per-provider, per-version or per-format siblings that differ in ways the shape folds away |
| `boilerplate` | property shims, one-line delegation, dunder methods, argument-parsing scaffolds, CLI or schema glue |
| `example-or-test-like` | examples, docs, scripts, fixtures, migrations or generated code restating a name by design (including paths the harness did not recognise as test or vendored) |
| `isolation` | deliberate independence: vendored or forked code, a layer that must not import the other |
| `same-class` | symmetric methods of one class or one protocol |
| `widespread-name` | the same shape appears in many places as a convention, so no single "existing one" exists to reuse |
| `short-or-trivial` | too small for reuse to be worth a call, despite clearing 30 nodes |
| `other` | free text, which the labeller must write |

A firing is **TRUE under the primary oracle only if both labelers say TRUE.** A split
decision is FALSE for the headline. The lenient reading (either says TRUE) is reported as
the upper bound and the strict reading as the headline; if they straddle a threshold, the
strict one decides. Cohen's kappa is reported with the counts. Labelers are recorded by
identity in the results; the labelling script reads nothing but the rubric and the two
functions.

If the firing set exceeds 120, a seeded draw (seed 548) of 120 is labelled, before anything
else is read. Below that, every firing is labelled.

**Firings that cannot be scored are dropped, never scored as clean.** A firing is
unscorable if either labeller cannot open the pre-existing member at its location, or the
two functions cannot be located at head. Drops are reported by reason.

### Secondary, reported and never decisive: the git oracle

A firing scores TRUE if the copy was removed or consolidated in a later revision of the same
PR or within 6 months on the default branch: the new function's qualified name is gone, or
its body no longer shares a shape with the existing member. The oracle is biased against the
feature, since reviewers do not object to redundant agent code (2601.21276), so it can only
under-count. It never decides a threshold.

### Reported, never decisive: the Sonar comparison

For each firing the primary oracle scores TRUE, record:

1. whether the copy is at least 10 lines and identifier-identical (byte-identical concrete
   body, which is the `exact` tier);
2. the PR's new-code duplicated-line density from the same added hunks. This is a PROXY:
   duplicated lines are the added lines of functions that are members of kept clusters, over
   all added lines in `.py` files. It is not Sonar's 100-token, 10-line detector and is
   labelled as a proxy wherever it appears.

If almost every TRUE firing would also have failed Sonar's 3% gate, the results say so,
because a follow-up feature would then add nothing for SonarQube users. The slice archy
alone would add is short copies, renamed copies, near-misses and copies diluted below 3% by
a large PR; any follow-up has to justify that slice. This adds no threshold.

## Thresholds

Fixed here, and not moved by the pilot or by anything read afterwards.

- **Headroom kill:** fewer than 1 firing per 50 Python agent PRs in the main sample (a rate
  below 2%), OR fewer than 30 scorable firings from at least 10 repositories. Either means
  the feature is not built. This is a result, not "underpowered, try again."
- **Precision pass:** at least 0.60 over at least 30 scorable firings from at least 10
  repositories, under the primary oracle.
- **Precision kill:** below 0.40.
- **Between 0.40 and 0.60:** null, and not shipped.

**No post-hoc subgroup rescues a null.** Exact versus near-miss, and new file versus
existing file, are reported in full, with counts and the reason breakdown, but they are not
results. The detector is not edited after the first labelled firing: the FALSE reasons above
are what a filter would have to remove, and removing them after seeing the labels would be
tuning on the test set.

## Process, in order

1. This document is committed. Its hash goes in the results.
2. The harness, the added-hunk unit tests (hand-built two-commit fixture; red when "member
   existed at base" is removed, red again when "def line inside an added hunk" is removed;
   the fixture asserts it contains a pre-existing match before asserting the outcome) and
   the canary.
3. `--canary`: a fixture PR that copies an existing function into a NEW file must fire, and a
   fixture PR that only edits an existing function must not. It runs before any real run.
4. Pilot on 20 disjoint PRs. **No labels are read.** It reports firing rate and harness bugs.
   A pilot of 20 expects well under one firing at a 2% rate, so it cannot estimate the rate
   and is a plumbing test only; the addendum says so. Its one design output is N, from
   wall time per PR.
5. Dated addendum fixing N, then the full run, resumable from the ledger (one row per PR,
   appended in a single write, only `status="ok"` counts as done, a crash in one PR does not
   end the run, stalls are retried and results never).
6. Labelling, then scoring against the thresholds, then
   `bench/diff_duplicates_precision_results.md` and a Study entry in
   `docs/WHAT_DIDNT_WORK.md`, published pass or fail.

## Descriptive extra, not a threshold

The headroom measurement already yields a speak-rate comparable to drift's replay numbers;
it is reported next to them so a reader can see whether archy would be noisier than the
in-loop tool that exists today.

## Addenda

### 2026-11-11, after the 20-PR pilot (no label read, no firing content read)

Thresholds, the firing definition and the rubric are unchanged. What the pilot fixed or
revealed:

- **Pilot result (counts only).** 20 PRs drawn, 7 measured, 9 had no added Python lines
  (dropped), 4 were dropped on `QualnameCollisionError`. 1 firing in 7 measured. As the
  plan said, this cannot estimate a rate near 2%; it is a plumbing test. Mean wall time was
  54 s per measured PR including cold clones. The pilot ledger is
  `bench/diff_duplicates_pilot.jsonl`; the pilot PRs are excluded from the main sample.
- **N = 2000.** The main sample is the 2000 PRs that follow the pilot in the seed-548
  shuffle, at most 25 per repository in draw order. About a third of draws are measurable
  in the pilot, so expect roughly 700 measured PRs; that expectation is an estimate from 20
  PRs and is not a commitment. The sample is not extended.
- **Pool is a proxy, the diff decides.** The draw pool is closed AIDev PRs in repositories
  whose AIDev `language` is Python (57,517 PRs in 4,255 repositories). A PR is a "Python
  PR" only if its own diff adds a `.py` line; the rest are dropped and recorded.
- **Diff base.** The base is `merge-base(PR base sha, PR head)`, the three-dot diff GitHub
  shows, so a PR branched long ago is not charged with unrelated base-branch changes. The
  head is the PR's `pull/N/head` ref.
- **Module-name collisions are dropped, not worked around.** 4 of the pilot's 11 Python PRs
  (prefect, mlflow, nexus and one more) hit `QualnameCollisionError`: archy refuses a tree
  where two files share a module name (#531), and the shipped CLI would refuse the same
  repositories. These are dropped and reported by reason, and the headroom rate is also
  reported with drops counted as non-firing. The results state plainly that this attrition
  falls on larger repositories, so the measured population leans toward smaller, cleaner
  ones. No fallback that ignores the colliding paths is added, since it would be a detector
  configuration the shipped CLI does not use.
- **Near-miss budget hits are flagged, not dropped.** The shipped near-miss pass stops at
  its 5,000,000-comparison budget and warns; at least one pilot repository hit it. A PR that
  hits it is still measured (the exact tier is complete) and its ledger row carries
  `near_miss_budget_hit`. The results report near-miss firings split by that flag, since a
  PR whose near-miss result may be short is not a clean negative for that tier.
- **Labelers.** Labeler A is the maintainer; labeler B is a fresh-context LLM given only
  the rubric and the two functions. Identities are recorded in the results.
