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
  detector is not re-run and not edited. Near-miss budget hits (`near_miss_budget_hit`, 62 of
  the 1,049 measured PRs) are flagged; A may be short on those PRs, which can only inflate B's
  share, so the results report the share with and without them.
  - **A is expanded to pairs.** A firing is a cluster; the labeled item for both generators is
    a (PR, new function, existing function) **pair**. An A firing contributes one pair per
    member that is new in the diff, each paired with the existing member of its cluster that
    has the highest token overlap with it (archy's near-miss token scorer, ties by sorted path
    then qualified name) and that exists at both base and head. A firing with no such member
    is dropped as unscorable. The ledger holds 557 firings and 1,309 such new members over
    1,049 PRs, with one PR holding 703 and one firing holding 181; the cap below exists for
    that reason. **A's pair-level precision is a different quantity from #548's 0.160**, which
    was per cluster, and the results report it as such, never as a replication.
- **B (embedding oracle, offline only):** for each function whose `def` line falls inside an
  added hunk of the diff, clears the same 30-node floor, and is not in a test or vendored path,
  embed its source text. Embed every function of the same repository that **existed at base
  outside the added hunks**, under the same source-only filter and floor. Take the top-1 nearest
  neighbour by cosine similarity. Emit the pair as a candidate if the similarity is at or above
  the cutoff. One candidate is one (PR, new function, existing function) pair.
  - Function extraction, nested and async defs, decorators and the 30-node count use archy's
    own extraction code, the same code that produced A's units, so the two generators agree on
    which functions exist. Lambdas are out.
  - The existing function must exist at **both base and head**, matched by path and qualified
    name. A function the PR deleted, renamed or moved is not a valid neighbour (a move would
    pair at similarity near 1.0 and be the refactor itself). Candidates lost to this are
    reported as their own drop reason. Packets read the existing function at head.
  - **Per-PR cap, both generators:** at most K = 10 pairs per PR, keeping the pairs whose new
    function has the most AST nodes (ties by path, then qualified name). For B the cap is
    applied **after** the similarity cutoff, to the pairs that pass it; B scores every eligible
    new function. For A it is applied to the expanded pairs.
  - **Exact-tier predicate, for both generators:** a pair is exact-tier if the new function's
    AST shape hash equals the existing function's, computed by archy's own hasher, whatever
    either generator emitted.
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
  already in the primary pool are labeled in a separate pass of at most 100 pairs, reported
  separately.
- Qodo-Embed-1-1.5B is out of scope (licence `other`, unverified).

## Cutoff: fixed before any label, at equal volume

The ticket fixes the cutoff on the 20-PR pilot to the AST tiers' volume. The pilot measured 7
PRs and 1 firing, which is not enough to set a rate, so that rule would calibrate on noise.
Replaced here, before anything ran, by:

- **Unit of equality: capped pairs per measured PR.** A's expanded pairs, after the K = 10
  cap, summed over the 1,049 measured PRs and divided by 1,049, is the target. (Uncapped it is
  1,309 / 1,049 = 1.25; the earlier idea of 0.531 was firings, a different unit, and is not
  used.) From the ledger alone, capping by new-member count gives 536 pairs, **0.511 per PR**,
  with 177 of the 1,049 PRs holding any pair. That figure is provisional: the ledger has no
  per-member node counts and no base-and-head existence flags, so the final target is
  recomputed by re-parsing the PRs that hit the cap and checking existence, counts only, and
  recorded in the post-pilot addendum before calibration runs.
- **Calibration subset:** 40 measured PRs, at most 5 per repository, drawn first, with
  `random.Random(587)` over the sorted PR keys. The evaluation subset is then drawn from the
  remaining measured PRs, same generator and key order, so the two cannot overlap. B's
  similarity scores are computed on the calibration subset and the cutoff is the **largest
  observed similarity t such that the mean capped count of B pairs at or above t, per PR over
  the subset, is at least the target**. Pairs tied at t are all included. If no observed t
  reaches the target the cutoff is the lowest observed similarity, and that is recorded. The
  median is not used: A has no pair in 83% of PRs, so its median is 0 and would not bind. The
  share of PRs with at least one B pair is reported beside A's 17%. A's labels are never read
  for calibration. The cutoff is frozen in the post-pilot addendum before the evaluation
  subset runs.
- Pairs at similarity exactly 1.0 are counted and reported. They count toward calibration
  volume, since the shipped tier would emit them.
- The sensitivity model gets its own cutoff by the same rule.
- **Realised volume** of B against the target is reported on the evaluation subset, beside A's
  realised capped volume.
- This compares precision at equal volume and keeps the cutoff from being tuned on labels. It
  does not make B's recall comparable to A's; see "What the share does and does not say".

## Sample

- **Evaluation subset:** M measured PRs drawn with seed 587 from the measured set remaining
  after the calibration draw, at most 25 per repository, disjoint from the pilot. M is fixed
  in the post-pilot addendum from the pilot's wall time per PR and a separate embedding cost
  model (functions per PR, measured throughput, cache reuse across a repository's PRs), and
  only from those. The pilot's 7 measured PRs and its archy-only timings are a weak basis, so
  the addendum states the minimum M that gives an expected TRUE pool at the floor below. If
  that minimum is unaffordable the result is "not measurable at this sample"; the floors are
  never lowered to fit. The sample is not extended after the run starts.
- **Unmeasurable PRs are dropped, never scored clean.** A PR whose base or head cannot be
  checked out, whose functions cannot be extracted, or whose embedding run fails is recorded
  with its reason and removed from the denominator. Drops are reported by reason.
- Labeling budget: if the pool (below) exceeds 300 distinct pairs, a seeded draw (seed 587)
  of 300 pairs is labeled, taken over pair ids alone before any packet is built, stratified by
  nothing. Below 300, every pair is labeled. A (PR, new function) unit with a pair outside the
  draw is scored on the labeled pairs only and the count of such units is reported.

## Pool and labeling

- **Pooled labeling.** The labelers see the union of A's expanded pairs and B's candidates for
  the evaluation subset, blind to which generator produced each, to similarity, to tier, to
  merge outcome and to the git history.
- **Packets are one pair each, for both generators.** Every packet shows one new function and
  one existing function, never a cluster, so packet shape does not reveal the generator. A pair
  both generators emit is one packet, tagged with both. Packets are built by one code path and
  shuffled together. **Leak check, pre-registered:** the results report A and B precision
  separately by whether the two bodies are byte-identical, so a reader can see whether the
  labelers' answers track an obvious feature of the pair.
- **Unit.** A (PR, new function) unit is the thing counted, because the two generators may
  pair the same new function with different existing functions. A unit is **TRUE** if at least
  one labeled pair attached to it is TRUE under the strict reading. A unit is **found by A** if
  an A pair for it is TRUE, **found by B** if a B pair for it is TRUE, and **only by B** if
  found by B and not by A. A pair is not TRUE merely because a generator emitted it.
- **The headline share excludes exact-tier pairs**, by the pair-level predicate above.
  Such pairs are trivially top-1 for any embedder, so "found by both" is inflated by
  construction on them. The headline "only by B" share is computed over the TRUE pool without
  them. The share with them is reported beside it and is not decisive. The headline therefore
  leaves out A's most precise tier (4 of 10 strict in #548), a bias toward B that the results
  state next to the number. B's precision is measured over all labeled B pairs, exact-tier
  included, and reported again without them.
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
- **TRUE-pool floor (added here):** the strict TRUE pool, A and B together and excluding
  exact-tier pairs, must hold at least 40 (PR, new function) units from at least 15
  repositories and at least 20 PRs. Below it the share is "not measurable at this sample" and
  no pass or kill on the share is declared. A floor failure does not override B's precision
  bars: a precision kill or pass stands if the scorable floor above is met. The minimum M in
  the post-pilot addendum is computed in labeled pairs after the 300-pair draw, with the
  expected TRUE pool at the affordable M. If that is under 40, "not measurable" is recorded
  before the run, not after it.
- **Pass (an embedding tier is worth a feature ticket):** B precision at least 0.60 under the
  primary oracle AND at least 30% of the strict TRUE pool found only by B.
- **Kill:** B precision below 0.40, OR fewer than 10% of the TRUE pool found only by B.
  **A kill is a kill at A's operating point** (about 0.51 capped pairs per PR, 1.25 uncapped
  and dominated by one PR), and the line is closed only as far as that point measures. It is not a claim about an embedder at a
  stricter cutoff.
- **Between:** null, not shipped.
- **Wilson 95% intervals decide ties.** Thresholds are applied to the point estimate only when
  the interval does not straddle the bar it would cross. When it does, the verdict is **null**,
  whichever side the point estimate is on. Intervals are reported beside every proportion:
  Wilson on units for the share, and a bootstrap clustered by PR for pair precision, since up
  to 10 pairs share a PR. This makes a pass hard and a share kill nearly unreachable (about 0
  of 40 only-by-B), so a kill will in practice come through B's precision. That is accepted
  and stated here.
- **Secondary, never decisive:** a second, stricter cutoff, chosen by the same rule at one
  third of the target volume, is reported with its precision. It adds no threshold.
- **No post-hoc subgroup rescue.** The sensitivity model, per-agent, per-tier and similarity
  band breakdowns are reported in full and are not results. The cutoff is not revised after
  the first label is read.

## What the share does and does not say

**Recall here is relative to the pooled set, never absolute.** Redundancy that neither
generator surfaces is invisible to the labelers, so "A's share of the TRUE pool" overstates
A's true recall, and "only by B" understates what is missing from both. The results state this
and never write the share as the detector's recall.

A second limit specific to this design: A and B are held to the same capped volume of pairs per
PR, so B is measured at A's operating point, not at its own best operating point. A precision
read at that point does not say what B would do at a stricter or looser one. The per-PR cap
also means a PR with hundreds of new functions contributes at most 10 pairs to either side.

## Harness requirements

All of AGENTS.md "Measurement and benches" applies. In particular:

- `bench/embedding_oracle_recall.py`, checkpointed per PR with one ledger row per unit
  appended with its newline in a single write; only `status="ok"` counts as done; a crash in
  one unit is recorded and the loop continues; stalls are retried and results never; interrupts
  exit cleanly and print how to resume. Shape copied from `bench/_supervise.py` and
  `bench/q1b_run.py`.
- Model loads pin the model and tokenizer revisions and the `transformers` version, and run
  offline after the first fetch. Truncated inputs are kept, not dropped, and counted by
  generator. Embeddings are cached by function content hash (plus model id, revision,
  truncation length and input normalisation) under
  `bench/cache/`, which is gitignored. A test that reaches the cache or a model skips when it is
  absent, and `mv bench/cache /tmp/bench-cache-aside` is run before pushing.
- Isolate `/tmp` per unit. State in the results whether anything ran before isolation.
- **Canary, before any real run** (`--canary`): a fixture where a loop and a comprehension
  compute the same thing (Type-4) must be found by B and missed by A; two unrelated functions
  of similar length must not be paired. Each branch is asserted reached before its outcome is
  asserted.
- **Verify by mutation**, clearing `__pycache__` between mutants and making the mutation helper
  exit non-zero when its match is missing: reverse the cutoff direction; drop the "exists at
  base and head" condition; swap A and B in the pooled sample; key the unit by PR instead of
  (PR, new function). Each must turn a test red. A mutation
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

### 2026-11-12, before any embedding was computed and before any label was read

Thresholds, the pair and unit definitions, the rubric, the cap, the cutoff rule and the
strict reading are unchanged. The primary model is unchanged: it stays
`jinaai/jina-embeddings-v2-base-code`. This addendum makes no change of model, thresholds or
rubric; it adds loading constraints that the pre-registration did not state, all fixed before
the first embedding exists.

**Pins, including the code that runs**

- The primary needs `trust_remote_code`, and its `auto_map` points at a second repository,
  `jinaai/jina-bert-v2-qk-post-norm`, so pinning the model repository alone would not pin what
  executes. Both are pinned by commit: `jinaai/jina-embeddings-v2-base-code` at
  `516f4baf13dec4ddddda8631e019b5737c8bc250` and `jinaai/jina-bert-v2-qk-post-norm` at
  `3baf9e3ac750e76e8edd3019170176884695fb94` (last modified 2024-05-07).
- **Every fetched file is hashed.** Each model is fetched once as a snapshot of its
  pinned revision with an allow-list of file patterns (weights, configs, tokenizer files and
  `.py` files; no `onnx/`), every file's sha256 is recorded, and every later load runs with
  `HF_HUB_OFFLINE=1` and refuses if any file differs. For the primary, the harness asserts
  that the `auto_map` code resolves into the pinned code snapshot and not into `main`, and
  loads the model from its local pinned snapshot directory and never by repository id: the
  primary's `encode()` and `__init__` call `AutoTokenizer.from_pretrained(config._name_or_path)`
  (`modeling_bert.py` lines 1063 to 1065 and 1124 to 1126), which would fetch the tokenizer
  from the repository's `main` unless `_name_or_path` is that local directory.
- **Remote code, stated exactly.** The primary needs the two files of its code repository
  (`modeling_bert.py`, 2,312 lines; `configuration_bert.py`, 177 lines), whose card declares no
  license while the file headers carry the Apache-2.0 notice; the model repository declares
  apache-2.0. `BAAI/bge-code-v1`
  needs one in-repo tokenizer file, `tokenization_qwen.py`, named in its tokenizer config. The
  other five arms need none. Both remote files are pinned and hashed like any other.
- **What was read.** On 2026-11-12 the two code-repository files were parsed with Python's
  `ast` and the call sites below were read in place. Imports at module top level are `torch`,
  `transformers`, `numpy` and the standard library (`collections`, `dataclasses`, `math`, `os`,
  `typing`, `warnings`). `modeling_bert.py` also imports `torch.nn.functional`,
  `tqdm.autonotebook`, `re`, `numpy` and `tensorflow` inside functions or guarded `try` blocks;
  `tensorflow` appears only in `load_tf_weights_in_bert`, which loading from safetensors does
  not call, and `tqdm` is optional. `configuration_bert.py` imports `optimum.exporters.onnx`
  inside a guarded `try` and is optional. The harness refuses to run if `tensorflow` or
  `optimum` is importable in the embedding environment, so those paths are not taken. The files
  contain no network call, process call, string `eval` or `exec`, `open`, `pickle` or
  `__import__`; the `getattr` calls read config or module attributes by fixed names, `self.eval()`
  is `nn.Module.eval`, and every URL is in a comment or docstring. The one runtime fetch is the
  tokenizer load described above. A first regex pass reported a single comment hit and was
  defective (its trailing word boundary could not match `eval(`, and it saw only top-level
  imports); this paragraph does not rest on it. This is a reading of the pinned files and not
  an audit, and it does not exclude behaviour reachable through the libraries they call.
  `tokenization_qwen.py` is read the same way before its first load, and a finding there is
  triaged by reading before an arm is stopped.
- **Weights.** Loads pass `use_safetensors=True` for every model except
  `microsoft/unixcoder-base`, which ships only `pytorch_model.bin` at its pinned revision. That
  arm loads through the `weights_only` path with the file's sha256 pinned; if that path is
  unavailable the arm is dropped, never loaded with pickle deserialisation enabled.
- **Library versions** (`transformers`, `torch`, `sentence-transformers`) are fixed by the
  canary and recorded in every manifest row. Remote modeling code written in 2024 can fail on a
  newer `transformers`, and a version that loads it is part of the pin.

**Sensitivity arms, reported and never decisive**

The pre-registration names one sensitivity model; it is extended to six. Each is calibrated by
the same equal-volume rule on its own similarities.

| Model | License | Revision | Pooling |
|---|---|---|---|
| `jinaai/jina-code-embeddings-0.5b` (as pre-registered; bench only) | cc-by-nc-4.0 | `4db235132dafbe56a8b9c5f59b59795ecf58a4a7` | last token |
| `jinaai/jina-code-embeddings-1.5b` (the larger of the two jina-code models, same family and recipe as the 0.5b; bench only) | cc-by-nc-4.0 | `39aeb4fb9b60f930934c78ae5d749a46287c248a` | last token |
| `Qwen/Qwen3-Embedding-0.6B` | apache-2.0 | `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | last token |
| `Qwen/Qwen3-Embedding-8B` (the "would a much stronger embedder change the verdict" arm) | apache-2.0 | `1d8ad4ca9b3dd8059ad90a75d4983776a23d44af` | last token |
| `microsoft/unixcoder-base` (context 1,026 tokens, so long functions truncate; counts reported) | apache-2.0 | `5604afdc964f6c53782a6813140ade5216b99006` | mean over the attention mask |
| `BAAI/bge-code-v1` | apache-2.0 | `bd67852057c5d7ddcc7b8234d9d6c410117ed851` | last token |

The primary pools by mean, per its `1_Pooling` config. Pooling for the other models is the one
in their `1_Pooling` config at the pinned revision, except UniXcoder, which has none: mean over
the attention mask is chosen here and is this addendum's choice, not the model's published one.
Licences, revisions and pooling configs were read from the Hugging Face API on 2026-11-12.
Padding side is set explicitly to left for the last-token models, since the pinned tokenizer
configs do not reliably carry it, and end-of-sequence handling comes from the pinned tokenizer.
The canary asserts that a text embedded alone and inside a mixed batch gives the same vector
to 1e-5.

- **No instruction prefix, made operational.** Texts are tokenized verbatim. `prompt_name=None`
  and `prompt=""` are passed explicitly, since the Qwen3 and jina-code configs ship named
  prompts (`bge-code-v1` ships none in its sentence-transformers config), and the canary asserts
  that no prompt text appears in the tokenized input beyond the tokenizer's own special tokens.
  Four of the six arms (both jina-code models and both Qwen3 models) ship named prompts, so a
  null from them at this default is not evidence about instructed use.
- **What an arm may and may not claim.** Arm results are labeled `sensitivity`. A pass or kill
  is computed only from the primary: no arm number appears in the verdict line, in the headline
  of the results or in the `docs/WHAT_DIDNT_WORK.md` entry's headline, and no arm enters the
  headline share, the TRUE-pool floor or the primary precision. All seven models, the primary and
  the six arms, are reported in one table, each with its interval. With seven models reported
  in full, roughly one in seven will look best by chance (the arms are correlated, so this is
  approximate), and the study makes no claim that any non-primary arm would or would not pass.
  A kill is a kill for the primary at A's operating point. If an arm's precision interval lies
  wholly above 0.60, the results note it as a hypothesis for a new pre-registration; it is not a
  pass, it does not change the primary verdict, and no share is computed for it.
- **What an arm reports.** Its precision over its arm-only labeled pairs (below) with an
  interval, which is not comparable to the primary's precision and is labeled so in the table, its realised
  volume next to A's and the primary's, its truncation counts and whether its calibration
  reached the target. It reports no share and no verdict, since "found by" needs the full
  pooled labeling that only the primary gets. Cutoffs are not comparable across arms, since
  score scales differ.

**Labeling budget for the arms**

This replaces the cap of 100 pairs in the "Sensitivity model" bullet under "Generators"; the
primary's cap of 300 in "Sample" is unchanged and is drawn first.

- Arm pairs are drawn only after the primary draw is fixed, from pairs outside the full
  pre-draw primary pool (A's pairs and the primary's B pairs, whether or not the 300-pair draw
  kept them). They never enter the primary pool, the headline share, the TRUE-pool floor or the
  primary precision. Arm precision is therefore measured over arm-only pairs, excluding pairs
  shared with A or the primary.
- Each arm draws at most 100 pairs independently with `random.Random(587)` over sorted pair
  ids. The union is labeled once under the same rubric and packet format, and packets carry no
  arm tag. Six arms of 100 is a ceiling of 600 only when no pair is shared; the cap binds only
  then.

**Numerics and what is recorded**

- All embeddings come from one CUDA GPU host with one fixed dtype (float32), TF32 matmul
  disabled, `torch.use_deterministic_algorithms` set with `CUBLAS_WORKSPACE_CONFIG` (which CUDA
  requires for it) and both recorded, one fixed batch size, and
  batches ordered by token length then pair id. The 8B arm needs at least 48 GB of GPU memory
  at float32; if that is unavailable the arm is not run, not run at another dtype.
- Vectors from different devices, dtypes, batch sizes or library versions are never merged, and
  a cutoff is applied only to vectors from the configuration that calibrated it, because a
  threshold on similarity can flip a pair that mixed numerics move. The cache key gains the
  device, dtype, batch size and library versions, and the canary asserts the same pair order at
  two batch sizes.
- The results quote the model revisions, `torch` and `transformers` versions, CUDA version,
  dtype, the TF32 and determinism settings and the batch size. They describe the machine only
  as "a single CUDA GPU host" and do not name the GPU model, which a private manifest records.
  That withholds one reproducibility detail, and the results say so.

**Pipeline and the rest**

- Function texts are extracted on the analysis machine with archy's own parser and keyed by the
  sha256 of the normalised text. Embedding is a text-in, vectors-out job on the GPU host with a
  manifest per shard. Top-1 cosine, the cutoff and all scoring run on the analysis machine from
  the returned vectors. No definition changes.
- The PR-clustered bootstrap for pair precision uses 2,000 resamples and seed 587, as in
  `bench/embedding_oracle_stats.py` (#973). M, the cutoffs and the capped A target are still to
  be fixed in the post-pilot addendum of "Process, in order" step 4. The model revisions are
  fixed here, and that addendum does not fix them.
- A fine-tuned model is not part of this study. Using one needs its own dated addendum before
  its weights are used, with training data disjoint from every repository in this study's
  sample and never the study's labels.
- **Why the primary stays.** It was pre-registered before anything ran, and keeping it means
  the study's instrument was not chosen after seeing anything. The cost accepted is the remote
  code above, and the canary still has to pass for it before any real run. The model repository
  was last modified on 2025-01-06 and the organisation's newer models are different families,
  so the pin is also the guarantee that it does not change under the study.

### 2026-11-12 (second addendum), before any embedding, similarity, cutoff or label exists

No threshold, bar, rubric, cap, cutoff rule, model, pin or definition of a firing, a pair or a
unit changes. One identity rule is made explicit, and one number is corrected.

- **A function's identity includes its file.** Wherever this document says "function", "new
  function", "(PR, new function)" or "(PR, new function, existing function)", the function is
  identified by its **file path and its qualified name**. A qualified name is local to its file: in
  one measured PR, five different files each define `main`. The harness's ids for a pair and for a
  unit therefore encode the path of the new function, the path of the existing function and their
  qualified names (as a JSON array, so no character in a path or a name can make two different
  identities produce one id).
- **Why now.** A review of the pull request that builds generator B found that the earlier ids
  ignored the file. That merged 11 distinct new functions of one PR into 7, and in pooled labeling
  would have merged distinct items into one unit. No similarity, cutoff or label had been computed,
  so no result depends on it.
- **What this does to the provisional A target.** The figure of 536 capped pairs (0.511 per PR,
  from the ledger's members, in the "Cutoff" section above) was already marked
  provisional and was counted from members, not ids, so it is not a final figure either way. A later
  recount reported as 533 was produced with the faulty ids and is **withdrawn**. The recount that
  fixes the target is still made in the post-pilot addendum, keyed by the full identity.
- **What is still ambiguous.** Two functions with the same path **and** qualified name in one tree
  (a property getter and setter, a conditional redefinition) still share an id. The extractor numbers
  them with `occurrence`, and the pair rules must drop or flag a unit whose name is duplicated rather
  than guess which one is meant. That is decided in the layer that has the index.
- **A number corrected.** The text under "Generators" says one firing holds 181 new members. The
  ledger's largest firing holds **183** (the same PR has a second firing holding 181). No rule depends
  on the figure; the cap exists for the reason stated there, and the correction only fixes the number.

### 2026-11-13 (third addendum), after the pilot and the calibration-shard scoring, before any cutoff, label or evaluation vector exists

No threshold, bar, rubric, cap, cutoff rule, model, pin or definition of a firing, a pair or a unit
changes. This addendum records the pilot outcome, fixes the A target and the calibration
denominator, and says what is still to come. The cutoffs, M and the model revisions are fixed in a
fourth addendum, after `calibrate` has run, because the target below has to be on record before
calibration runs.

- **What ran.** `score` (every new function's top-1 neighbour and cosine, before any cutoff) on the
  pilot shard (8,997 rows, sha256 `413b0518e26d8d15453f04a6c188ca58554d61e1a91704e4ea60d44ad36e9ac9`)
  and the calibration shard (22,190 rows, sha256
  `e56c4590f501f2706663f957a14488572e04dc8f216e70160af39db338bc43be`), for five of the seven models:
  jina-embeddings-v2-base-code (primary), jina-code-embeddings 0.5b and 1.5b, unixcoder-base and
  bge-code-v1. Every run passed its canary and a bitwise-equal second pass, and no scored row holds
  an id without a vector. `calibrate`, `emit` and `freeze` were not run, and the evaluation shard
  was not touched. No label was read.
- **Two arms are not available.** Qwen3-Embedding 0.6B ran out of device memory at the harness
  memory cap and is VOID; the owner decided on 2026-11-13 not to re-run it. A missing model
  directory is read as VOID and not as an error. Qwen3-Embedding 8B had not finished when this was
  written; if it also fails it is VOID by the same rule, and if it lands it is handled as the other
  sensitivity arms are. Neither affects the primary or any rule above.
- **Pilot outcome (plumbing only, no rate).** Of #548's 20 pilot PRs, 3 hold new functions (4 new
  functions in all), 4 were measured and hold none, and 13 are unmeasurable and dropped: 9 had no
  added Python lines and 4 hit `QualnameCollisionError`. That is the 7 measured PRs the
  pre-registration already counted. The pilot's one design output was to show the pipeline runs end
  to end; 4 functions say nothing about a rate, and no cutoff or M is taken from them.
- **Collision drops are kept.** 4 of the 20 pilot PRs (20%) were dropped because several files in a
  repository share one module name, in line with the 22% #548 measured (299 of 1,358 Python PRs). A
  PR #548 dropped is not re-admitted (see "Reused from #548"): generator A cannot fire on it, so
  admitting it for B alone would add pairs A could never have found and push the "found only by B"
  share up. The caveat stands as written: the measured population leans toward repositories without
  duplicate module names. The results restate it beside the headline.
- **Calibration denominator: all measured PRs.** The calibration subset holds 40 measured PRs, 19
  with at least one new function (392 new functions scored) and 21 measured with none. The 21 count
  as zero B pairs; a PR whose measurement failed would be dropped, and none was (0 unmeasurable).
  This is the same basis as A's 0.5110 per PR over 1,049 measured PRs, which also counts the PRs
  with no pair. The owner chose it on 2026-11-13; the alternative, scored PRs only, is not used.
- **The A target is fixed: 536 capped pairs over 1,049 measured PRs, 0.5110 per PR.** The recount
  keyed by file path and qualified name re-parsed base and head for the 8 PRs that reach the cap,
  applied the real pair rules (partner existing at both base and head, one pair per pair id, the
  cap keeping the largest new functions), and found nothing to remove: 536 again, each of the 8 PRs
  at exactly 10 pairs, 177 PRs with any pair, 1,309 pairs uncapped, 0 unresolved members and 0
  ambiguous new members. The provisional figure is therefore confirmed and the 533 withdrawn in the
  second addendum stays withdrawn. One limit: a PR under the cap keeps its ledger count, which can
  only be too high, so the target is an upper bound for those PRs. A target that is too high lowers
  B's cutoff and gives B more volume than A, which makes a precision pass harder and a precision
  kill easier. The bias runs against B, not for it.
- **Order.** The target above depends on A's ledger alone. B's similarities on the calibration
  shard were computed before this addendum was written, and the descriptive spread was seen (median
  top-1 cosine about 0.58 to 0.63 across the five models); no B similarity was used to choose the
  target, the denominator or anything else here. The cutoffs are calibrated next, per model, by the
  rule in "Cutoff", and frozen in the fourth addendum together with M and the model revisions.
- **Truncation is counted, not dropped.** unixcoder-base is capped at 1,024 tokens: 408 of 8,997
  pilot texts and 744 of 22,190 calibration texts are truncated. jina-embeddings-v2 truncated 2 per
  shard and the other arms none. The flags are kept with each shard's vectors and the results report
  the counts by arm.
- **A limit stated in advance.** The calibration shard scores 392 new functions, so the cutoff
  rests on about 20 pairs (0.5110 times 40 PRs). It is noisy, and the rule in "Cutoff" is applied as
  written; the realised volume on the evaluation subset is reported beside A's, as already required.

### 2026-11-13 (fourth addendum), after calibration, before any evaluation vector or label exists

No threshold, bar, rubric, cap, cutoff rule, model, pin or definition changes. Following "Process, in
order" step 4, this addendum freezes the cutoffs, fixes M and the evaluation draw, and records the
model revisions. Calibration ran under the third addendum's target (536 capped pairs over 1,049
measured PRs, 0.5110 per PR) and denominator (all measured PRs) with `calibrate`, per model. No
label was read and no evaluation vector exists.

- **Cutoffs, frozen by this addendum.** The largest observed top-1 cosine t at which the mean
  capped count of B pairs per calibration PR (40 PRs, 21 of them with no new function) is at least
  0.5110. Every model reached the target; none fell back to its lowest observed similarity. No pair
  has similarity exactly 1.0. The realised mean at each cutoff is 0.525 (21 pairs over 40 PRs), the
  closest the data allow since the count moves in whole pairs. Each cutoff is bound to the
  calibration vectors it came from and applies only to vectors from the same configuration.

  | Model | Revision | Cutoff | Calibration vectors sha256 |
  |---|---|---|---|
  | jina-embeddings-v2-base-code (primary) | `516f4baf13dec4ddddda8631e019b5737c8bc250` | 0.893929 | `947ceb5f57611cfccf4a05cd6998c7ca0be0b040ba6816346fcee1ce7a2a9ec4` |
  | jina-code-embeddings 0.5b | `4db235132dafbe56a8b9c5f59b59795ecf58a4a7` | 0.909055 | `4709b636a19c72a06dd264eaa090aedb0fab3326c249cb00f47a9cf4d6575e7d` |
  | jina-code-embeddings 1.5b | `39aeb4fb9b60f930934c78ae5d749a46287c248a` | 0.899234 | `de5a6582be1afe083d3a97ca405b30747a20666525f7a13dc07267e28825941c` |
  | unixcoder-base | `5604afdc964f6c53782a6813140ade5216b99006` | 0.920389 | `1137ee38a3f7dc88b0c843c33cc4d009f9c63fe93ea1eb3307f2451bd598cfbb` |
  | bge-code-v1 | `bd67852057c5d7ddcc7b8234d9d6c410117ed851` | 0.842663 | `b20267f77aa598acfb1db98b26e7a274e9825f6171e56f19f976f0c291734427` |

  The remote-code pin for the primary is the one in the first addendum. The two Qwen3-Embedding
  arms have no cutoff: the 0.6B arm is VOID and the 8B arm had not returned vectors. If the 8B arm
  returns, it is calibrated by the same rule and recorded in a later dated addendum before its
  evaluation vectors exist; it is never added silently.
- **M = 250 measured PRs.** The evaluation subset is the first 250 PR keys of a
  `random.Random(587)` shuffle of the sorted keys of the measured PRs that remain after the
  calibration draw (1,009 PRs from 536 repositories; the pilot PRs are not in the measured set),
  skipping a PR once its repository already holds 25. This is the same generator and key order as
  the calibration draw. The draw is 250 PRs from 186 repositories, the largest repository holding
  3% of them, and the sha256 of its sorted keys joined by newlines is
  `dafa1fa79a93da51d3ff808198eab07dd5a8fe4d0533b4ba16f9edf7963582f0`; the harness refuses an
  evaluation shard whose keys hash otherwise. The calibration draw's hash by the same construction
  is `721eff0ad057bc6f79369657ecb03179bb536f8d221449222cae0645ca779f83`.
- **Why 250 (planning figures, not results).** Two inputs only: A's ledger counts on the drawn PRs
  and the labeling budget already registered.
  - A's ledger, read for counts and no label, gives at most 160 capped A pairs on these 250 PRs
    (an upper bound, since PRs under the cap keep ledger counts), 48 PRs with an A pair, 45
    repositories. B at its calibrated volume adds about 128 pairs (0.511 per PR). Their sum, about
    288 with no overlap assumed, stays under the 300-pair labeling budget, so every pair is expected
    to be labeled and the seeded draw is not expected to apply. If the realised pool exceeds 300 the
    rule in "Sample" applies as written.
  - Minimum M for the TRUE-pool floor (40 units). With A's strict precision taken as 0.16 (#548's
    figure, used here only to size the sample) and B's precision p, the expected TRUE units per PR
    are about 0.102 + 0.51 p. The minimum M is about 131 at p = 0.40 (the kill line), 196 at p = 0.20
    and 98 at p = 0.60 (the pass line). At M = 250 the floor is expected to be met for any B
    precision above about 0.11. Below that, the share is recorded "not measurable at this sample" and
    B's precision bars stand if the scorable floor (30 labeled B candidates from 10 repositories) is
    met, which about 128 B pairs from 186 repositories makes likely. This is declared before the run.
  - Embedding cost is not binding: the primary embedded the 22,190-row calibration shard in about
    five minutes, so roughly 139,000 texts (about 555 per PR) is about half an hour per arm without
    cross-PR reuse. M is therefore bounded by the labeling budget, not by compute.
- **The evaluation shard still has to be built.** The extractor cannot name it, by design. A
  separate change adds a command that takes this addendum's key-list hash, refuses any other draw,
  and writes the shard and its unit index. It adds no rule and is reviewed on its own.
- **Order and what stays open.** The cutoffs are frozen by naming this addendum (`freeze --addendum`)
  after it merges. The evaluation shard is then extracted, embedded once per arm, scored with `emit`,
  pooled, labeled and evaluated as the earlier sections say. The sample is not extended after the run
  starts.
