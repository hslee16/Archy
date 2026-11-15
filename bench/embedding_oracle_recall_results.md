# Embedding oracle: what the AST clone tiers miss on agent PRs (#587)

Captured 2026-11-15. Pre-registration:
[`docs/research/EMBEDDING_ORACLE_RECALL.md`](../docs/research/EMBEDDING_ORACLE_RECALL.md), merged
before any embedding or label existed, with four dated addenda (models and pins; path-keyed
identity; pilot outcome and the capped A target; the five frozen cutoffs, M = 250 and the
evaluation draw). No threshold, bar, rubric, cap or cutoff rule was changed after a label was
read.

## Verdict: precision kill. No embedding tier is built.

| step | pre-registered bar | measured | result |
| --- | --- | --- | --- |
| Scorable floor | at least 30 B pairs from at least 10 repos | 174 from 43 repos | pass |
| B precision, strict (both labelers TRUE) | pass at least 0.60, kill below 0.40 | **46 of 174 = 0.264**, interval 0.158 to 0.377 | **kill** (interval top under 0.40) |
| Only-by-B share of the TRUE pool, exact tier excluded | pass at least 0.30, kill below 0.10 | 22 of 35 units = 0.629, interval 0.463 to 0.768 | **not measurable**: 35 TRUE units is under the floor of 40 |

The kill is on precision, which stands whether or not the share floor is met. The
share, with exact-tier pairs included, is 22 of 52 = 0.423. Neither share figure decides
anything here.

**The kill is a kill at A's operating point** (about 0.5 capped pairs per PR). It does not
say what an embedder does at a stricter cutoff, and it is not a claim about fine-tuned
models or any other use of embeddings.

For scale, the AST tiers' precision on the same labelers and the same pool is 30 of 160 =
0.188 (#548 measured 0.160 on a different pool). The primary model is better than the AST
tiers on precision and still far below a shippable level.

## Primary and what was measured

- Primary: `jina-embeddings-v2-base-code`, cutoff 0.893929, frozen before evaluation. 250
  measured PRs drawn from the ledger after the 40-PR calibration draw (seed 587, at most 25
  per repository), 125 of which had at least one embeddable new function.
- Pool: A's pairs (160) plus the primary's capped B pairs (175), 37 emitted by both, 298
  distinct pairs. That is under the 300-pair budget, so every pair was labeled and no
  budget draw applied. 297 were scorable.
- Labelers: `claude-sonnet-5-5` and `claude-opus-5-5`, one fresh-context run per packet,
  blind to generator, arm, similarity, tier and merge outcome. Cohen's kappa 0.627 over
  all 495 labeled packets (primary and arm pairs). Neither labeler returned UNSCORABLE.

## Things to hold next to the number

- **No human label exists.** Both labelers are models from one vendor, so shared blind
  spots are likelier than between a person and a model, and kappa 0.63 is model-to-model
  agreement.
- **Byte-identical pairs are the easy cases.** The pre-registered leak check splits
  precision by whether the two bodies are byte-identical. B is 8 of 12 (0.67) on identical
  pairs and 38 of 162 (0.23) on different ones; A is 9 of 12 (0.75) and 21 of 148 (0.14).
  Only 12 pairs per generator are identical, so the split is reported, not interpreted.
  Without exact-tier (shape-identical) pairs B precision is 31 of 144 = 0.215.
- **Recall is relative to the pooled set.** Redundancy that neither generator surfaced is
  invisible to the labelers, so every share is of what was pooled, never absolute.
- **The measured population leans toward PRs that parse cleanly.** Module-name collisions
  were dropped (kept as pre-registered), as in #548.

## Sensitivity arms (never decisive)

Each arm's precision is over its own arm-only pairs, those outside A's pairs and the
primary's pool, at most 100 per arm. It is not comparable to the primary's precision and
does not enter the primary verdict or the share.

| model | cutoff | capped B pairs | labeled arm-only pairs | strict TRUE | precision (95% interval) |
| --- | ---: | ---: | ---: | ---: | --- |
| unixcoder-base | 0.920389 | 174 | 92 | 15 | 0.163 (0.067 to 0.298) |
| bge-code-v1 | 0.842663 | 193 | 75 | 11 | 0.147 (0.068 to 0.259) |
| jina-code-0.5b | 0.909055 | 157 | 40 | 3 | 0.075 (0.000 to 0.212) |
| jina-code-1.5b | 0.899234 | 170 | 54 | 3 | 0.056 (0.000 to 0.140) |

Qwen3-Embedding 0.6B and 8B are VOID (CUDA out of memory at the pinned dtype and batch
size) and were not run at another configuration, as pre-registered. The arm pairs are the
ones the primary and A did not already surface, so they are the less obvious cases; a low
number there is expected and says little about the arm's overall precision. No arm
changes the primary verdict, and none is a candidate.

## Departures and incidents, in order

1. **Evaluation embedding, first attempt.** The primary's run hit a CUDA out-of-memory on
   its last chunk, a batch-halving fallback let it finish, and the pre-registered
   determinism gate then fired (two full passes differed by at most 2.4e-07, since a
   halved batch is not bitwise equal to a whole one). Those vectors were discarded. The
   shipped vectors came from a rerun with halving off and zero splits for all five
   models, every one with two bitwise-equal passes. The primary's unit log still
   carries four halving lines from the discarded attempt.
2. **Four labels were read before bulk labeling**, in a plumbing smoke test of the
   labeler on two packets per labeler. They are the study's own labels under the same
   rubric and packet format, were not used to choose anything, and are included.
3. **A parser bug in the labeler harness.** A reply whose note contained braces (a quoted
   f-string) was rejected as unparseable, so two sonnet answers (both clear TRUE verdicts)
   errored on every retry. The parser now tries the whole reply first. No other label
   changed.
4. **Two pairs had no packet**, because the qualified name occurs more than once in a file
   and the function cannot be located (one primary B pair, one arm pair). Dropped and
   reported, never scored. Seven further pairs failed to extract only because the
   running Python could not parse newer syntax; a layout-based fallback recovered them.
5. **Shown source.** The new function is shown at head and the existing one at base, with
   the surrounding directories at head as read-only context.

## Reproduce

`bench/embedding_oracle_pool.py` builds the pool and the arm draws,
`embedding_oracle_packets.py` writes the blind packets, `diff_duplicates_labeler.py` labels
them (`--name sonnet` and `--name opus`), and `embedding_oracle_report.py` produces every
number above from the two ledgers. The caches are gitignored; the shard, cutoff and key-list
hashes are recorded in addendum 4.
