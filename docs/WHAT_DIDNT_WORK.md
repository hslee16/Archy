# My tool prevents a problem that happens on 0.66% of commits. I measured it.

Here is everything I learned on the way there, including two nulls, one
retracted premise, and the part where the tool turned out to be blind to its own
blind spot.

archy is a static analysis tool for Python. It builds a module dependency
graph, holds the layer rules you declare, and reports when an edit breaks them.
I built it because I kept watching coding agents produce changes that passed
review and rotted the import graph underneath, where every individual diff
looked fine and six weeks later the cycle count had doubled.

The premise was plausible enough that a lot of tools are built on it: if you
give an agent structural feedback, its output gets structurally better, and
probably cheaper too.

I ran two controlled experiments on the *cheaper* half of that premise. Both
came back null. Then I ran the experiment that tests the actual premise, and
then a study asking whether the problem exists at all.

**The headline, up front.** Across three measurements with different subjects and
different outcome definitions, the event archy exists to catch shows up at
roughly half a percent of commits, for humans and agents alike:

| measurement | subject | rate |
| --- | --- | --- |
| Q1a, 1,072 human commits | cycles introduced | **0.5%** per commit |
| 25 live agent runs | cycles or declared-layer violations | **0%** (95% upper bound 12%) |
| 151 commit pairs, projects that declare an architecture | contract violations | **0.66%** per commit |

The problem is **real** and I have now watched it happen in the wild. It is also
**much rarer than the pitch I built the tool on**, which was that agents rot the
import graph underneath you. I no longer claim that, and this write-up is where
I retract it.

Every figure below is quoted from a document or a committed result ledger, and
where a document corrected itself, I say so.

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

## Experiment 3: do agents actually break the architecture?

Both nulls above are about *efficiency*: does archy make an agent cheaper or
less exploratory. That was never the point of the tool. archy exists to catch an
agent breaking the architecture, and for a long time this section said I had not
measured whether it does. **I have now.**

**Setup.** 25 live agent runs (`claude-sonnet-5`, no archy in the loop) on the 25
structurally riskiest tasks in SWE-bench, selected by a filter written before any
run. Six repositories, each given a hand-authored layer config derived from that
project's own documented conventions and validated to be silent on pristine code.
Outcome, fixed in advance: a newly introduced import cycle, or a violation of a
declared layer rule.

**Result: 0 of 25.** All 25 measurable, nothing dropped. The agents were not
idling: 332 files changed, median 12 per run, median 64 turns.

**What I pre-registered, and why it matters.** Before spending anything, I wrote
two thresholds into the repository: at `p_B >= 25%` the A/B is powered and I
proceed; at `p_B <= 10%` **the corpus is wrong, not the tool**. That second
branch is the kind of claim a motivated author invents after seeing a null, so I
committed it first, along with the measurement that justifies it: **430 of
SWE-bench Verified's 500 gold patches touch exactly one `.py` file.** A corpus of
single-file bug fixes cannot exhibit multi-file structural damage.

I also wrote down the constraint that keeps that honest: **"the corpus was wrong"
is playable once.** The successor corpus is pre-registered the same way, and if
it returns ~0 as well, that counts against the thesis rather than against the
corpus. Otherwise this becomes a search for a dataset where my tool wins.

**A confound I missed, and a reader caught.** Every one of those 25 runs edited a
*mature, human-architected* codebase: django, sympy, matplotlib. Those repos have
decades of accumulated structure, and an agent editing them copies idiom that
already exists in the files it reads. **The corpus supplied the architecture.**
So 0 of 25 is consistent with "agents inherit human architecture", which is a
different and much weaker claim than "agents do not damage architecture". The
regime that would actually test it is code largely written by agents, which is
now measurable in the wild and which I have not done.

**One number I could have reported and did not.** A score regression fired on 12
of 25 runs. "archy detects degradation in 48% of agent edits" was available from
this data. Every one of those deltas was inside the 0.005 noise floor established
by the human baseline, the largest drop being 0.004, the median exactly 0.0. It
was noise, the outcome definition excluded it in advance, and reporting it would
have been theater.

## Study 4: does the problem happen at all, to anyone?

The agent result raised a harder question than it answered. If humans introduce a
cycle on 0.5% of commits and agents did it zero times in 25, maybe the event is
rare for everyone, and the tool addresses something that barely occurs.

So I stopped measuring agents and measured the phenomenon. There are projects
that already declare an architecture and check it in CI, using `import-linter`.
Their contracts are written by their own developers, in their own words. **I
authored nothing**, which fixes the largest bias in the previous experiment,
where I wrote the rules I then measured against.

**Result: 1 of 151 commit pairs, 0.66%**, across 14 repositories. The one
violation is real and specific:

```
AndreasHeine/i3x2ua  "Application must not depend on bootstrap"
```

A developer wrote a clean-architecture rule and a later commit broke it. That is
the first time in this whole line of work that I have observed the target event
in the wild instead of constructing it, and it settles that the previous zero was
not evidence of nonexistence.

**The finding I was not looking for, which is the one that changed the roadmap.**
Zero commits in the entire sample *fixed* a standing violation, while several sat
on one: 2 of 14 repositories carry contracts that are broken and stay broken
across commits. Detection did not help those projects, because they already knew.
The interesting failure is not the moment of violation. It is **living with the
violation**.

**How thin the category is.** GitHub code search returns roughly 1,590 hits for
import-linter configuration. Of 40 repositories I sampled from it, **9 declare no
such architecture at all** (they matched a doc, a lockfile, or a deleted config).
Most of the rest had too little history to yield a rate. **14 survived**, only two
of them well known. Declaring an architecture and enforcing it in CI is a rare
practice, and that bears on whether this category has users, not just whether it
has a problem.

**Limits I am not going to bury.** 107 of 258 sampled pairs (41%) could not be
evaluated at all, clustered by repository, mostly because historical configs do
not load under a current import-linter. Those were dropped, never counted as
clean. And 1 of 151 has a 95% confidence interval running from roughly 0.02% to
3.7%: this distinguishes "rare" from "common" and not much else.

## The tool was blind to its own blind spot

While building the study above, I pointed `import-linter` at archy itself and it
reported a broken contract:

```
graph layer must not reach policy/cli layers
archy.diff is not allowed to import archy.layers
```

`archy check` on the same tree reported **"No layer violations"** and exited 0.

Not a matching bug. A coverage one: **33 of archy's 76 modules match no declared
layer at all.** Its own architecture governs 43 modules and says nothing about the
rest, and there is no output anywhere that would tell you that. I had two config
files, one claiming to mirror the other, quietly disagreeing about which modules
belong to which layer, and a real violation sitting on the main branch.

This is the same failure I had just found in my own benchmark configs, where
pattern globs matched one empty `__init__.py` instead of a 338-module package and
**every rule in two shipped configs was dead**. I fixed it there with a canary:
add a deliberately violating import, assert the check fails. Users will never do
that.

**A rule set that cannot fire is indistinguishable from a clean codebase**, and a
tool whose entire differentiator is holding the structure you declared should be
able to tell you how much of your code its rules actually reach. Mine could not.

## What I am retracting, and what I am changing

**Retracted: the premise in the second paragraph of this document.** "Agents
produce changes that pass review and rot the import graph underneath" is the
belief I built archy on, and I no longer have evidence for it. Twenty-five agent
runs produced zero structural regressions, and the honest bound is under 12%
rather than zero. If I ever write that sentence again it needs a citation I do
not currently have.

**Retracted: the framing that this is an agent problem.** Humans introduce cycles
on 0.5% of commits and break their own declared contracts on 0.66%. Whatever this
is, it is not something coding agents do to you.

**Not retracted: that the problem is real.** I watched a developer's own
architecture rule get broken by a later commit. Rare is not zero, and a rare
event can still be worth catching. But I cannot argue that from rarity alone: I
would need the cost of one occurrence, and **nobody has measured that, including
me.** Until someone does, "rare but expensive" is a hypothesis, not a defence.

**What changes.** Two findings above point somewhere different from where I was
aiming, and both arrived unbidden:

1. **Nobody fixed a standing violation.** Zero, out of 151 commit pairs, while
   2 of 14 repositories sat on broken contracts across commits. The moment of
   violation is rare and already visible in CI. *Living with* the violation is
   neither.
2. **My own rules governed 43 of 76 modules and I did not know.** Silent
   under-coverage is invisible by construction, and it is invisible in every tool
   in this category, not just mine.

Those are the same shape: **the failure is not that a rule fires and you miss it.
The failure is that the rule stopped meaning anything and nothing told you.** So
that is what I built next, and then I measured whether it was true.

## Study 5: the replacement claim is also false

The pivot above deserved the same treatment as the premise it replaced, so I
pre-registered four thresholds that would kill it and ran it over the same
corpus: 11 repositories, 107 evaluable samples of their own contracts across
their own history.

**All four came back null or untestable.**

| signal | pre-registered null | observed |
| --- | --- | --- |
| standing violations | dies if the median clears in <= 2 commits | 1 sample pair, in 1 of 11 repos |
| rule relaxation | dies below 5% of resolutions | no violations resolved at all: untestable |
| dead rules | dies if >80% of rules still govern existing modules | 89% do |
| coverage erosion | dies if the slope is >= 0 | 10 of 11 repos flat or **rising** |

The intuition was that configs get written once and silently outgrown. These
projects do the opposite: their rules govern *more* of their code over time.

Decay is real and rare, like everything else here. Two of eleven repositories
genuinely name modules that no longer exist (`brain_go_brrr.data`, `.tasks`,
`.training`, `.core`; `secondsign.audit`), and one deleted five of its six
contracts outright. That is a true thing a tool can report. It is not a product
thesis.

**The part worth keeping is how nearly this went the other way.** The first run
said 57% of samples carried dead rules, which would have vindicated the pivot
completely. It was wrong. Six measurement artifacts sat between me and that
number: root packages inflating coverage to a trivial 100%, a Layers contract's
`a : b : c` sibling syntax parsed as one nonexistent module, `pkg.*` wildcards
never matching, container-relative layer names matched absolutely, `src/`-layout
packages never discovered at all, and external dependencies like `flask` counted
as missing internal modules. Removing them took the signal from 63 to 40 to 12
of ~107 samples.

**Every single artifact pointed toward the answer I wanted.** That is not a
coincidence, it is what motivated measurement looks like from the inside, and
the only defence I had was writing the kill thresholds down before running and
refusing to reinterpret them afterwards.

So: three claims, three nulls. The problem archy addresses is real, rare, and
rare in every direction I have looked at it. I am not going to keep looking for
the direction where it is common.

## Study 6: the first thing that worked, and why it changes less than it sounds

Everything above is a null. This one is not, and it belongs here anyway because
the reason it is small is the same reason the others were null.

The Constraint Decay paper's stated open question was whether *dynamic
course-correction* during generation would help, since its agents "received
static prompts describing the architecture but no dynamic course-correction on
violations." Nobody had measured it. #369 did: 25 greenfield Conduit backends
per arm, `claude-sonnet-5`, FastAPI, the paper's own architectural constraint,
its own two-part verifier, thresholds fixed in advance.

**A checker in the loop moved structural compliance from 88.0% to 100.0%, at a
behavioral cost of -0.3 pp.** Full write-up: [`bench/greenfield_results.md`].

Three things about that number.

**The win condition was unreachable, and that is my error.** The pre-registered
WIN threshold was +25 pp, set from a plausible unaided rate near 40% inferred
from the paper. The real unaided rate was 88%, which caps the maximum possible
delta at +12. The pre-registered reading is EXPAND and it stands unrevised, but
it reads EXPAND because of the design, not because the effect is ambiguous. A
five-run pilot would have shown 88% for about an hour of agent time, before the
thresholds were frozen. **"Validate the measurement before spending agent time"
applies to the design parameters, not just the harness.**

**Headroom was the binding constraint for the fourth time.** #282 and #289 found
no efficiency benefit, #356 found 0 of 25 agent edits damaged an existing
architecture, and here 3 of 25 unaided runs got it wrong. Four studies, and each
time the limit was how rarely the problem occurs, not whether archy detects it.
The paper's own reception predicted this: its frontier models were not fully
tested, for cost reasons, so its absolute numbers are directional. On a 2026
model, the mildest architectural constraint is satisfied 88% of the time
unaided.

**The failures were more interesting than the rate.** All three had 4 of 4
layers present and the dependency direction wrong, entities reaching into data
access. Agents got the layout right and the direction wrong, and prose in a
prompt did not prevent it. Combined with the decay study's finding that standing
violations are *never resolved* (no violation in that corpus was ever fixed, and
2 of 14 repositories sat on broken contracts indefinitely), the defensible claim
is narrow and specific: a directional violation introduced at scaffolding time
is cheap to prevent and evidently never repaired later.

Whether a 12% birth-defect rate justifies a tool is a product judgment, not a
measurement, and this study does not make it.

[`bench/greenfield_results.md`]: ../bench/greenfield_results.md

## What survived, and why archy still exists

Two nulls on agent-side benefit, one retracted premise, a study saying the
problem is rare, and one modest positive whose size is set by how rarely the
problem occurs did not kill the tool, but between them they have narrowed what
I am allowed to claim about it almost to nothing. archy's README carries no
token-savings number, and a rule in `RESEARCH_METRICS.md` §14c.7 forbids adding
one. It carries no agent-safety number either, and after Experiment 3 it never
will.

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
