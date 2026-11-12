# Pre-registration: what do the AST tiers miss that an embedding model finds on agent PRs? (#587)

Written 2026-11-12, BEFORE `bench/embedding_oracle_recall.py` exists, before any embedding was
computed, and before any label for this study was read. Edits after this date go in a dated
addendum at the bottom, never over this text. The commit that adds this file is quoted in the
results.

## Question

#548 measured the clones archy can see (AST-shape exact plus the token-overlap `near_miss`
tier) and found them 0.160 precise under two blind labelers (strict), below its 0.40 kill line
(`bench/diff_duplicates_precision_results.md`, Study 10). It did not measure Type-4 (semantic)
redundancy, which is the kind the agent-PR literature reports (arXiv 2601.21276) and which
those tiers miss by design. This study asks, offline and shipping nothing:

1. Of the added Python functions an embedding model pairs with a pre-existing function, how
   many are cases where the new function should have called or reused the existing one?
2. How much of the redundancy that people (here, two labelers) would act on is found only by
   the embedding model and not by the AST tiers?

It is research with a decision at the end. Nothing is added to the product, to `pyproject.toml`,
to `uv.lock`, to any CLI flag, MCP parameter or hook, or to the score. No agent time is spent;
the only cost is local CPU.

## What changed since the ticket was written

The ticket assumed #548 might ship a feature whose documentation #587 would then bound. #548
ended in a precision kill, so that feature is not built. Consequences, recorded here so they
are not discovered afterwards:

- Decision 3 in the ticket (state the AST share as the limit of #548's feature) is moot. The
  measured AST share is recorded instead as a limit on what the shipped `near_miss` and exact
  tiers can see, and nothing else.
- The AST tiers' strict precision is already known to be low (0.160). The pooled TRUE set is
  therefore expected to be small and to contain few pairs the AST tiers found. This makes the
  "at least 30% only by B" bar easier to clear than it looks, and is why a floor on the TRUE
  pool is added below. It is not a reason to move any bar.
- Any feature this study could justify would be a Type-4 tier, a separate feature with its own
  pre-registration, in a repository with fewer than 20 real users (usage baseline 2026-09-05).
  A pass files a ticket and builds nothing.

## The action, if it passes or fails

1. **Pass:** file a separate feature ticket for an opt-in Type-4 tier, to be pre-registered on
   its own. This ticket ships nothing.
2. **Kill:** record it in `docs/WHAT_DIDNT_WORK.md` and close the line so nobody re-proposes it.
3. **Null or not measurable:** record it, do not extend the sample after the run starts.

## Reused from #548, unchanged (no second harness)

- The AIDev pool, the seed-548 shuffle, the 20-PR pilot split (the pilot is excluded from
  everything below), and the per-repository cap.
- The set of **measured Python PRs** from #548's main run (1,049 PRs, ledger rows in
  `bench/diff_duplicates_data/precision.*.jsonl`). This study draws only from that set, so a PR
  is already known to clone, parse and scan, and A's firings for it already exist in the ledger.
  A PR that #548 dropped is not re-admitted. The population caveat from #548 carries over
  verbatim: module-name collisions dropped 22% of Python PRs, so the population may lean toward
  smaller, cleaner repositories.
- The firing definition for generator A, the source-only filter (`is_test_path`, vendored
  paths), the size floor (30 AST nodes, `MIN_NODES`), and the git base: `merge-base(PR base
  sha, PR head)` against the PR's `pull/N/head` ref.
- The frozen labeling rubric (TRUE definition and the eight FALSE reasons), the packet format,
  the read-only repository export and the strict reading (both labelers TRUE). The rubric text
  is taken byte for byte from the #548 labeler module, and a test asserts it has not drifted.
- The labelers: `claude-sonnet-5-5` and `claude-opus-5-5`, each a fresh-context run per
  candidate, as in #548's third addendum. **No human label exists.** Models from one vendor
  share training lineage, so the results state this beside the headline number and a pass is
  never described as human-validated.

## Generators

- **A (AST tiers):** the firings #548 already computed for the PR, read from its ledger. The
  detector is not re-run and not edited. Near-miss budget hits (`near_miss_budget_hit`) are
  flagged; A may be short on those PRs, which can only inflate B's share, so the results report
  the share with and without them.
- **B (embedding oracle, offline only):** for each function whose `def` line falls inside an
  added hunk of the diff, clears the same 30-node floor, and is not in a test or vendored path,
  embed its source text. Embed every function of the same repository that **existed at base
  outside the added hunks**, under the same source-only filter and floor. Take the top-1 nearest
  neighbour by cosine similarity. Emit the pair as a candidate if the similarity is at or above
  the cutoff. One candidate is one (PR, new function) pair.
  - The embedded text is the function's verbatim source from `def` through its last line,
    docstring included. No identifier folding. Inputs beyond the model's context are truncated
    from the end and the truncation count is reported.
  - A new function never pairs with itself or with another function added by the same diff
    (the "exists at base" condition).
  - Model weights are pinned by Hugging Face commit hash, recorded in the harness and the
    results. `jinaai/jina-embeddings-v2-base-code` needs `trust_remote_code`; the pinned
    revision is the code that runs and no other.
- **Primary model:** `jinaai/jina-embeddings-v2-base-code` (Apache-2.0, 161M parameters,
  verified on its card 2026-10-28). It is the only candidate that could ship later without a
  licence problem.
- **Sensitivity model, reported and never decisive:** `jinaai/jina-code-embeddings-0.5b`
  (`cc-by-nc-4.0`, bench-only). Its role is to say whether a stronger embedder would change the
  verdict. It runs with its own cutoff, calibrated the same way, and its candidates that are not
  already in the primary pool are labeled in a separate pass, reported separately.
- Qodo-Embed-1-1.5B is out of scope (licence `other`, unverified).

## Cutoff: fixed before any label, at equal volume

The ticket fixes the cutoff on the 20-PR pilot to the AST tiers' volume. The pilot measured 7
PRs and 1 firing, which is not enough to set a rate, so that rule would calibrate on noise.
Replaced here, before anything ran, by:

- **Target volume:** 557 A firings over 1,049 measured PRs, **0.531 candidates per measured
  PR**. This is a count taken from #548's ledger; no label is involved.
- **Calibration subset:** 40 measured PRs drawn with seed 587 from the measured set, at most 5
  per repository, containing no PR from the evaluation subset. B's similarity scores are
  computed on it, and the cutoff is the **highest similarity at which B emits at least 0.531
  candidates per PR averaged over the subset**. No label is read, and A's firings in the subset
  are not consulted. The cutoff is frozen in the post-pilot addendum before the evaluation
  subset runs.
- The sensitivity model gets its own cutoff by the same rule.
- This compares precision at equal volume and keeps the cutoff from being tuned on labels. It
  does not make B's recall comparable to A's; see "What the share does and does not say".

## Sample

- **Evaluation subset:** M measured PRs drawn with seed 587 from the measured set, at most 25
  per repository, disjoint from the calibration subset and the pilot. M is fixed in the
  post-pilot addendum from the pilot's measured wall time per PR, and only from that. The
  sample is not extended after the run starts; a short result is a result.
- **Unmeasurable PRs are dropped, never scored clean.** A PR whose base or head cannot be
  checked out, whose functions cannot be extracted, or whose embedding run fails is recorded
  with its reason and removed from the denominator. Drops are reported by reason.
- Labeling budget: if the pool (below) exceeds 300 units, a seeded draw (seed 587) of 300 is
  labeled, taken over unit ids alone before any packet is built, stratified by nothing.
  Below 300, every unit is labeled.

## Pool and labeling

- **Pooled labeling.** The labelers see the union of A's firings and B's candidates for the
  evaluation subset, blind to which generator produced each, to similarity, to tier, to merge
  outcome and to the git history. Packets for A and B candidates are built by the same code
  path and are shuffled together.
- **Unit.** The pool is keyed by (PR, new function), because the two generators may pair the
  same new function with different existing functions. An A firing contributes one unit per
  member of its cluster that is new in the diff. A unit is **TRUE** if at least one candidate
  attached to it is labeled TRUE under the strict reading. A unit is **found by A** if an A
  candidate for it is TRUE, **found by B** if a B candidate for it is TRUE, and **only by B**
  if found by B and not by A. A unit is not TRUE merely because a generator emitted it.
- **Relabel, do not reuse.** #548's 119 labels are not reused, since they were produced under
  a different packet mix. They are compared with the new labels on the overlap as a
  consistency check, reported and never decisive.
- **Unscorable candidates are dropped, never scored FALSE or TRUE.** A candidate is
  unscorable if the existing member cannot be opened at base, the new function cannot be
  located at head, or either labeler returns UNSCORABLE. Drops are reported by reason and by
  generator.
- Cohen's kappa is reported with the counts. Labelers are recorded by identity.

## Thresholds

Fixed here, not moved by the pilot or by anything read afterwards.

- **Scorable floor:** at least 30 scorable labeled B candidates from at least 10 repositories.
  Below it the result is "not measurable at this sample", reported as such, and the line is
  not extended.
- **TRUE-pool floor (added here):** the strict TRUE pool, A and B together, must hold at least
  20 units. Below it the share is "not measurable at this sample" and no pass or kill on the
  share is declared, though B's precision is still reported against its own bars.
- **Pass (an embedding tier is worth a feature ticket):** B precision at least 0.60 under the
  primary oracle AND at least 30% of the strict TRUE pool found only by B.
- **Kill:** B precision below 0.40, OR fewer than 10% of the TRUE pool found only by B.
- **Between:** null, not shipped.
- Thresholds are applied to the point estimate, as in #548. Wilson 95% intervals are reported
  beside every proportion, and a point estimate whose interval straddles a bar is described
  as such wherever it is quoted.
- **No post-hoc subgroup rescue.** The sensitivity model, per-agent, per-tier and similarity
  band breakdowns are reported in full and are not results. The cutoff is not revised after
  the first label is read.

## What the share does and does not say

**Recall here is relative to the pooled set, never absolute.** Redundancy that neither
generator surfaces is invisible to the labelers, so "A's share of the TRUE pool" overstates
A's true recall, and "only by B" understates what is missing from both. The results state this
and never write the share as the detector's recall.

A second limit specific to this design: A and B are held to a volume of about 0.53 candidates
per PR, so B is measured at A's operating point, not at its own best operating point. A
precision read at that point does not say what B would do at a stricter or looser one.

## Harness requirements

All of AGENTS.md "Measurement and benches" applies. In particular:

- `bench/embedding_oracle_recall.py`, checkpointed per PR with one ledger row per unit
  appended with its newline in a single write; only `status="ok"` counts as done; a crash in
  one unit is recorded and the loop continues; stalls are retried and results never; interrupts
  exit cleanly and print how to resume. Shape copied from `bench/_supervise.py` and
  `bench/q1b_run.py`.
- Embeddings are cached by function content hash (plus model id and revision) under
  `bench/cache/`, which is gitignored. A test that reaches the cache or a model skips when it is
  absent, and `mv bench/cache /tmp/bench-cache-aside` is run before pushing.
- Isolate `/tmp` per unit. State in the results whether anything ran before isolation.
- **Canary, before any real run** (`--canary`): a fixture where a loop and a comprehension
  compute the same thing (Type-4) must be found by B and missed by A; two unrelated functions
  of similar length must not be paired. Each branch is asserted reached before its outcome is
  asserted.
- **Verify by mutation**, clearing `__pycache__` between mutants and making the mutation helper
  exit non-zero when its match is missing: reverse the cutoff direction; drop the "exists at
  base" condition; swap A and B in the pooled sample. Each must turn a test red. A mutation
  that did not apply, or is inert on the fixture, proves nothing.
- Dependencies are bench-only (`uv run --with ...`). `archy check`, `archy cycles --strict` and
  `archy conventions . --emit-headers --check` stay green, and the `bench/` tree stays inside
  `archy.yaml`'s existing `exclude:` rules.
- Audit results for artifact-shaped numbers before believing them: a similarity that is exactly
  1.0 for many pairs (identical content hashed under two names), a candidate count identical
  across repositories, a precision of exactly 0 or 1.
- The treatment is verified present from the subject's record: the results quote the model id
  and revision that each cached vector row carries, not the revision the harness intended.

## Process, in order

1. This document is committed. Its hash goes in the results.
2. The harness, its unit tests, the canary, and the mutation checks above.
3. Pilot on #548's 20 pilot PRs. **No label is read.** Plumbing and wall time only. Its one
   design output is M.
4. Calibration subset: compute B's similarities, freeze both cutoffs. Dated addendum fixing
   M, the two cutoffs and the model revisions, before the evaluation subset runs.
5. Full run, resumable from the ledger, then pooled labeling, then scoring against the
   thresholds.
6. `bench/embedding_oracle_recall_results.md` and a Study entry in `docs/WHAT_DIDNT_WORK.md`,
   published pass, null, kill or not measurable.

## Addenda

None yet.
