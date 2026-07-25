# I measured twice whether my architecture tool makes agents cheaper. Both null.

Here is everything I learned anyway, including the part where the question I
actually care about is still unmeasured.

archy is a static analysis tool for Python. It builds a module dependency
graph, holds the layer rules you declare, and reports when an edit breaks them.
I built it because I kept watching coding agents produce changes that passed
review and rotted the import graph underneath, where every individual diff
looked fine and six weeks later the cycle count had doubled.

The premise was plausible enough that a lot of tools are built on it: if you
give an agent structural feedback, its output gets structurally better, and
probably cheaper too.

I ran two controlled experiments. Both came back null. This is the write-up,
with the actual numbers and p-values, because the field has a shortage of those.

**One thing to be precise about up front**, because it is the kind of thing this
write-up exists to insist on. Those two experiments tested the *cheaper* half of
that premise: agent token footprint, and exploratory reads before the first
edit. Neither tested whether archy actually stops an agent breaking the
architecture. That question is real, it is the reason the tool exists, and it is
still unmeasured. There is a section on it below.

Every figure below is quoted from a document in
[`docs/research/`](research/) that was adversarially reviewed before
publication. Where a document corrected itself, I say so.

## Experiment 1: does refactoring for cleanliness reduce an agent's footprint?

A 2026 SonarSource paper ran a controlled minimal-pair study on code
cleanliness and coding agents. There was **no pass-rate effect (+0.1 pp)**, but
the agent's *footprint* shrank on the clean side: input tokens -7.1%, output
-8.5%, reasoning -11.1%, and file revisitation **-34%**, the largest effect,
concentrated on multi-module tasks. So cleanliness moved cost, not capability.

That is a claim about the effect of cleanliness in general. My question was
narrower and more self-interested: **if you apply archy's own top
recommendation to a real repo, does the agent's footprint move?**

Design: take archy's #1 `what-to-refactor-next` target on Flask, author a
behavior-preserving decomposition, then run the same task against both variants
in interleaved pairs, with a gate that fails the run if the repo's pre-existing
test suite regresses.

**Result at N=10 pairs, 11 metrics tested:**

| metric | median delta | split | sign p |
|---|---|---|---|
| `file_revisitations` (pre-registered primary) | **-1.0** | 5/5 | **1.000** |
| `footprint_tokens` | **+4,213** | 4/6 | 0.754 |
| `pre_edit_reads` | -3.0 | 7/10 | 0.344 |

The pre-registered primary is dead flat. Not "slightly positive," not
"trending." A 5/5 split at p=1.000.

The token headline leaned the *wrong* way, and I want to be careful about how
that is stated, because overclaiming a negative result is still overclaiming.
**+4,213 tokens is not evidence that archy made the agent worse.** At p=0.754
with per-pair deltas running from -28,565 to +27,125, that median sits well
inside the noise. The honest reading is: no detectable effect, and what signal
there was pointed the wrong way.

Zero test regressions across the run.

## Experiment 2: does an archy brief reduce exploratory reads before the first edit?

The second attempt targeted a different mechanism. If an agent's bill is driven
by reads rather than edits, maybe archy's value is *substitution*: inject a
structural brief up front, and the agent does less flailing to find the
relevant files.

Design: inject a 581-token archy brief into the prompt, measure
reads-before-first-edit against an unbriefed control on the same task.

**At N=10 this looked like a result**: `pre_edit_reads` median 13.0 to 8.5, a
delta of -3.5, 8 of 10 pairs, p=0.109. Promising. Underpowered, but promising.

**At N=22 it regressed to null**: median **-2.0**, 14/8, sign **p=0.286**.

That is the part worth sitting with. The effect did not fail to reach
significance for want of power; **it moved back toward zero as data
accumulated**, which is what an underpowered false positive does.

A secondary metric, `num_turns`, was nominally lower (-5, p=0.041), and it does
not survive multiple-comparison correction across the 5 metrics tested. It is
also not a read count, so relabeling it as read reduction would be a second
error on top of the first.

The most informative number was the one that didn't move at all: the paired
median delta on `pre_edit_distinct_files` was **exactly 0.0** (9/6/7,
p=0.607). The agent found the roughly 3-file spine on its own, briefed or not. There was no headroom for
any brief on a task that grep-able. That reframes the whole question: the path
to an effect here is a harder *setting*, not a better *brief*.

I shipped no feature. The N=10 hint would have been enough to ship one.

## What generalizes

The nulls are specific to archy. These four lessons are not.

**1. Repo-level separation is not evidence. Always check which items got
flagged.**

In a separate study, I tested whether architectural patterns (hexagonal,
Repository, Service Layer) could be inferred from a dependency graph. Two
positional detectors separated the corpora *perfectly*: 0/8 false positives on
repos where the pattern was present, 3/3 correct on controls where it was
absent.

Had the bench stopped at repo-level verdicts, that would have shipped.

Scoring *which modules* each detector flagged gave domain-identification
precision on the three controls of **0.00, 0.33, 0.00**. The flagged modules
were CLI entrypoints, a translation adapter, and an exception module. Every one
an adapter, and adapters are supposed to import infrastructure. **The detector
produced the right repo-level answer from modules that had nothing to do with
the property being checked.**

**2. A tautological metric flags nothing and looks clean doing it.**

A fourth detector in that study defined "domain module" as one importing
nothing external, then checked whether domain modules import external packages.
It returned zero violations across all 40 repos. Not because the codebases were
clean, but because the definition made the violation impossible to express.
Zero findings reads like a passing grade. It was a question defined out of
existence.

**3. Check the denominator before you repeat the percentage.**

I surveyed the token-reduction claims in the agent tooling space and sorted
them by mechanism rather than by size. The famous numbers mostly do not survive
their own baselines:

- **"99.9% fewer input tokens"**: measured against 2,500+ API endpoints exposed
  as individual MCP tools, estimated at 1.17M tokens. That baseline exceeds
  every production context window. The "before" case is impossible, not merely
  bad.
- **"98.7% saving"**: a single illustrative figure in an engineering blog post.
  No task set, no N, no model named.
- **"typically 95%+ on code-reading tasks"**: the raw column is *reading the
  entire repository*, which is not what an agent does. The one third-party A/B
  that source cites reports 15-25%.

The one rigorous entry (Hrubec & Cito, arXiv 2606.01326, SWE-bench Verified,
independent re-implementation) is also the only one that reports what the
reduction *costs*: **42% fewer input tokens for a 12-point drop in resolution
rate**, at maximum compression. Its own ablation gives a gentler -22% for -3pp.

There is also a pricing confound almost nobody adjusts for. Under prompt
caching, cache hits bill at 0.1x, so a token count quoted at uncached list
price overstates the real cost saving of a repeated prefix by roughly **3-6x**
over a realistic session (1.5x at N=2, 4.65x at N=10, 6.35x at N=20).

**4. After a correction pass, re-verify the corrections.**

That survey went through adversarial review and came back with 16 findings. The
NO-GO verdict survived unchanged. A lot of its supporting claims did not.

The two I find hardest to look at:

- I had cited a specific N for a vendor's benchmark. **That number appears
  nowhere in the source.** It came from a search summary I failed to check
  before repeating. In a document whose entire thesis is "check the
  denominator."
- I quoted the "42% for -12pp" figure as the representative tradeoff. It is the
  *maximum-compression corner* of that paper. Quoting only the worst point,
  while elsewhere in the same file indicting other vendors for "up to"
  framing, was me doing the exact thing I was criticizing.

A later pass over those corrections found more errors *in the corrections
themselves*, including a fabricated figure introduced while fixing a
fabrication. The full log is in the document, kept visible rather than folded
in silently, because a survey about checking denominators has to show its own.

## The question I actually care about is still unmeasured

Both nulls above are about *efficiency*: does archy make an agent cheaper or
less exploratory. That was never the point of the tool. archy exists to catch an
agent breaking the architecture, and **I have not measured whether it does
that.**

The research notes are explicit about the split, which is why I can be:

- **Q1a, the headroom question, is answered.** Across **1,072 human-authored
  commits in 11 mature repos**, the low-false-positive signal archy gates on (a
  newly introduced import cycle) appears in only **0.5% of commits**. But those
  commits are large and transformative: median **7 `.py` files changed against a
  baseline of 1**. The composite score drops on 29% of commits, and **98% of
  those drops are sub-0.005 noise**. So archy is a rare-firing, low-FP gate on
  severe damage concentrated in big changes, not a continuous quality dial.
- **Q1b, the causal question, is open.** *Does putting archy in an agent's loop
  reduce structurally-bad edits?* It has a powered, executable A/B protocol
  written, and a control baseline from Q1a. It has never been run. It is gated
  behind a usage signal that does not exist yet, and I deprioritized it on
  2026-05-26.

I want to be careful not to launder that into a defense. "The experiments that
failed were not testing the real claim" is exactly the move a motivated author
makes, and it is only legitimate here because the split was written down in
2026-05, before either null came back, rather than after.

What it does mean is narrower and less flattering: **two nulls on efficiency are
not evidence archy works, and not evidence it doesn't.** They are evidence about
a side benefit I hoped for and did not get. The central claim remains untested,
which is a worse position to be in than either a positive or a negative result.

The reason it stays untested is circular in a way worth naming. The A/B needs
people running archy inside an agent loop, and nobody does, because there are
zero outside issues. Waiting for a usage signal to justify measuring the thing
that would produce a usage signal is a closed loop, and I do not have a clean
answer to it.

## What survived, and why archy still exists

Two nulls on agent-side benefit did not kill the tool, but they did narrow what
I am allowed to claim about it. archy's README carries no token-savings number,
and a rule in `RESEARCH_METRICS.md` §14c.7 forbids adding one.

What survived is a narrower and more defensible job. A competitive analysis I
ran against the most successful tool in the adjacent category found the
distinction is **descriptive versus normative**. Navigation-first graph tools
describe the structure that *exists*: what is here, what calls what. They are
better at that than archy is, and archy's README says so by name.

archy holds the structure you *declared*: layers, forbidden edges, an
acyclicity invariant, a recorded score baseline. That job needs user-supplied
intent, which is not recoverable from source code at any parsing quality. It is
the same wall the pattern-detection study hit from the inside: "domain" is a
statement of intent, and the import graph does not carry intent.

That analysis also concluded its own maintainer was working on the wrong thing.
9 of 11 archy tools had no counterpart in the competitor, so differentiation
was real. It also recorded, at the time, archy at 5 stars against 62,279, and
zero issues filed by anyone but me out of 89. The gap was not a scope problem,
and the interesting number is the zero rather than the denominator: as of
2026-07-25, across 101 issues, **not one has been filed by anyone but me.**

And the strongest counterargument to archy is in its own docs: in a 2026-05
session on a real codebase, a fresh agent caught a forbidden cross-layer import
**by reading `archy.yaml` directly**, never calling an archy tool. If reading
the config is enough, the tool adds nothing. The narrower claim that survives
is the transitive multi-hop case, where the rule breaks several imports away
from anything the diff mentions and reading does not get you there. There is a
[one-command reproduction of exactly that](WALKTHROUGH.md), and it is honest
about the three archy surfaces that miss it.

## The raw work

Everything above is summarized from [`docs/research/`](research/), which holds
the full studies including their correction logs:

- [`PREWALK_READ_REDUCTION_SYNTHESIS.md`](research/PREWALK_READ_REDUCTION_SYNTHESIS.md) - the brief null, §6
- [`../bench/agent_footprint_results.md`](../bench/agent_footprint_results.md) - the footprint null, full metric table
- [`PATTERN_DETECTION_EMPIRICS.md`](research/PATTERN_DETECTION_EMPIRICS.md) - pattern inference, the tautology, the precision scoring
- [`TOKEN_REDUCTION_CLAIMS.md`](research/TOKEN_REDUCTION_CLAIMS.md) - the survey and its 16-item correction log at §10
- [`CODEGRAPH_COMPETITIVE_ANALYSIS.md`](research/CODEGRAPH_COMPETITIVE_ANALYSIS.md) - descriptive vs normative
- [`AXIS_REVIEW.md`](research/AXIS_REVIEW.md), [`DSM_EMPIRICS.md`](research/DSM_EMPIRICS.md), [`SCORE_SHAPE_REDESIGN_EMPIRICS.md`](research/SCORE_SHAPE_REDESIGN_EMPIRICS.md), [`TYPE_HINT_COVERAGE_EMPIRICS.md`](research/TYPE_HINT_COVERAGE_EMPIRICS.md), [`ACYCLICITY_DILUTION_EMPIRICS.md`](research/ACYCLICITY_DILUTION_EMPIRICS.md) - five more "we tested this and did not ship it"

archy is MIT, Python-only, one maintainer, and does not phone home:
[github.com/hslee16/archy](https://github.com/hslee16/archy).
