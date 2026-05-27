# Does archy-in-the-loop reduce structurally-bad edits? Part 1: the prevalence base rate

Empirical study answering the deepest open question recorded in
[`AGENT_CAUSAL_REASONING_SYNTHESIS.md` §10](AGENT_CAUSAL_REASONING_SYNTHESIS.md):

> Does archy-in-the-loop measurably reduce structurally-bad edits? Everything in §6-§7 (and
> [#144](https://github.com/hslee16/archy/issues/144)) assumes that supplying an agent with
> `archy_impact` / `archy_simulate` causes safer changes than not.

That claim has two layers. **Q1a (this study, runnable now):** how often do real single changes
actually introduce the structural regressions archy is built to catch? This is the prevalence /
headroom precondition. **Q1b (designed below, needs an agent A/B):** does putting archy in an
agent's loop *reduce* them? This document fully resolves Q1a and specifies an executable protocol
for Q1b. Dated 2026-05-27. Harness: [`bench/inloop_prevalence.py`](../../bench/inloop_prevalence.py);
raw rows: [`bench/inloop_prevalence_results.json`](../../bench/inloop_prevalence_results.json).

---

## Why Q1a has to be answered first

The synthesis proposes building a counterfactual pre-edit check (#144), a review brief (#145), and
framing reframes (#146), all premised on agents introducing structural regressions that archy can
catch. If real changes almost never introduce such regressions, the premise is weak and the features
are productivity theater. If regressions are common, or rare-but-severe, the premise holds. Q1a
measures the **human-authored base rate**, which is both interesting on its own and the control arm
the Q1b agent study must beat. In the spirit of Phroneses' [*Evaluating AI Systems*][evaluate], this
is a behavioural measurement on real history, not a synthetic benchmark.

## Method

Replay real merged history one commit at a time. For each sampled commit `C` (single-parent, touches
`.py` files under the package), check out `C^` and `C`, build the import graph over the package
directory at each, and compare:

- **cycle regression** = the import cycle count rose **and** modules that were acyclic at `C^` sit
  inside a strongly-connected component at `C` (a genuinely new tangle, not a relabeled one). This is
  archy's FP-free `cycles.added` signal.
- **score regression** = the composite `archy score` overall fell versus the parent.

Package directories match [`bench/projects.yaml`](../../bench/projects.yaml) `src_dir` so the numbers
are apples-to-apples with archy's published benchmarks (test and doc files excluded). Commits are
sampled evenly across up to 2000 most-recent package-touching commits per repo, so the base rate
spans years of history rather than only recent release-time fixes. Graph build and score use archy's
own `build_graph` and `compute_score` (the uncached path), so the measurement *is* what archy would
report.

**Corpus:** 1,072 commits across 11 mature, well-maintained, multi-contributor Python projects
spanning CLI, HTTP, web, validation, terminal, scraping, and docs domains: click, requests, flask,
httpx, starlette, rich, pydantic, fastapi, scrapy, mkdocs, datasette. These match the article's "real
repositories, thousands of lines, years old, dozens of contributors" criterion.

## Results

### Prevalence (per-repo and total)

| Signal | Rate |
| --- | --- |
| **New import cycle introduced** (FP-free gate) | **0.5%** (5 / 1072) |
| Composite score dropped (any amount) | 29.4% (315 / 1072) |
| Either | 29.5% |

The new-cycle rate was stable across sample sizes (0.6% at N=360, 0.5% at N=1072) and low in every
repo (0-2%). The score-drop rate sat at 22-35% per repo.

### Finding 1: the gate signal is rare per commit

New import cycles, the FP-free signal archy gates on, are introduced in **~1 of every 200
human-authored commits** in mature code. archy is not catching a constant stream of breakage. Any
value proposition that implies "agents constantly rot the import graph and archy constantly catches
it" is not supported by the human base rate.

### Finding 2: but cycle-introducing commits are large, and severe when they fire

The rarity is not the whole story. The five cycle-introducing commits were systematically **larger
and more consequential** than typical commits:

| | cycle-regression commits | all other commits |
| --- | --- | --- |
| median `.py` files changed | **7** | **1** |
| mean `.py` files changed | 7.8 | 2.0 |

The new tangles were multi-module, not all trivial: the size of the **newly-formed** SCC (the one
containing a module that was acyclic at the parent, not the largest pre-existing SCC) was **min 2,
median 3, max 8**; only 1 of 5 was an easy 2-module cycle. The five events:

| repo | files | cycle count | new-SCC size |
| --- | --- | --- | --- |
| requests | 16 | 0 → 1 | 8 |
| httpx | 9 | 1 → 2 | 4 |
| httpx | 7 | 0 → 1 | 3 |
| scrapy | 3 | 2 → 3 | 3 |
| mkdocs | 4 | 0 → 1 | 2 |

This is the bridge from the human base rate to the agent concern. Cycle introduction concentrates in
large changes (median 7 vs 1 files), and the AI-PR literature documents agent PRs as **154% larger**
than human PRs ([synthesis §7](AGENT_CAUSAL_REASONING_SYNTHESIS.md)). Agents push toward exactly the
change regime where the per-change cycle rate is elevated. And when a cycle does appear it is a
multi-module tangle (new-SCC median 3, up to 8; 4 of 5 were larger than a trivial 2-cycle) of the
kind that is invisible in a large diff and compounds silently, which is archy's founding origin story
("six weeks later the cycle count had doubled and nobody noticed"). Two of the five events
(scrapy at 3 files, mkdocs at 4) introduced a new cycle in a small diff, so a per-edit structural
check (`archy_diff` / `archy_simulate`) catches damage that review-by-file-count would underweight.

### Finding 3 (design-validating): the composite score is a trend signal, not a per-commit gate

Score drops are common (29%) but almost entirely **trivial in magnitude**: of the 315 commits that
dropped, **98% dropped by less than 0.005** (median drop -0.00009, i.e. effectively zero; worst case
-0.0197). A per-commit gate on "score went down" would fire on ~30% of commits, overwhelmingly on
noise. This is independent empirical support for archy's existing advisory-vs-blocking discipline
(the v0.15.0 lesson, reaffirmed in [`AUTONOMY_CONTINUUM_SYNTHESIS.md`](AUTONOMY_CONTINUUM_SYNTHESIS.md)):
the composite score belongs in `archy trend` and advisory diff summaries, and `cycles.added` (rare,
FP-free, severe) is the right thing to ever block on.

### Context: the size regime

The median commit in this corpus touches **1 `.py` file**, and only **2.1%** touch ten or more. Human
open-source development is overwhelmingly small, incremental commits, the additive regime where the
synthesis agrees agents do fine. Structural risk lives in the rare large/transformative change, which
is the regime the synthesis (and the article) say agents handle worst and produce most of.

## What this resolves, and what it does not

**Q1a is answered.** The structural regression archy gates on is rare in mature human-authored code
(~0.5% of commits) but concentrated in large changes (cycle-introducing commits touch ~7x the files of a
normal commit) and non-trivial when it occurs (multi-module tangles, new-SCC median 3). This reframes
archy's value with evidence: not a high-frequency catcher but a **low-noise gate that fires seldom
and flags a real tangle when it does**, precisely the shape an autonomy-scaled blocking check should
have, and precisely the regime (large, transformative changes) that agents disproportionately
produce.

**Honest limitations:**

- This is the **human-authored control base rate.** It does not measure agent-authored regression
  rates; that is Q1b. The study's purpose is to establish the control and the size-risk mechanism, not
  to prove the agent claim.
- **Small absolute event count** (5 cycle regressions). The size relationship (median 7 vs 1) is
  consistent across two sample sizes and directionally strong, but the precise multiplier is not
  tightly estimated. More events (a larger or refactor-enriched corpus) would sharpen it.
- **Corpus is small-to-medium mature repos** with strong review cultures; large, tightly-coupled
  repos (django, sqlalchemy, pytorch) were excluded for build cost. Those already carry more cycles in
  stock ([`CASE_STUDIES.md`](../CASE_STUDIES.md)); their per-commit introduction rate may differ.
- "Cycle introduced" is measured at package granularity; a few SCCs may be intentional mutual
  recursion. The cycle-regression test is conservative (it requires both a count rise and a newly
  cyclic module), so cycle-for-cycle swaps are not counted. The "score drop" signal includes
  complexity/modularity jitter from simply adding code.
- **Silent skips bias `src/`-layout repos toward recent history.** A commit pair is dropped when the
  package path does not build at one end (e.g. requests, which migrated to `src/requests` in 2023,
  yielded N=72 of 100 because pre-migration commits have no `src/requests`). Those repos' samples
  therefore skew post-migration rather than spanning their full history.

## Q1b: the executable protocol to finish the resolution

The remaining causal question needs an A/B, now well-specified by this study:

- **Tasks:** a set of real Python issues with known-good fix PRs, **enriched for structural risk**
  (multi-file features, refactors, cross-module changes), because Finding 2 shows that is where the
  base rate is non-trivial and where statistical power lives. A uniform task sample would need
  impractically large N given the 0.5% base rate.
- **Arms:** the *same* model and scaffolding, with the `archy mcp` tools (snapshot / impact /
  `archy_simulate` / diff in the loop, per [`AGENT_LOOP.md`](../AGENT_LOOP.md)) present in arm A and
  absent in arm B. Paired design: run both arms on each task.
- **Primary outcome:** rate of structurally-bad produced diffs = introduced cycle OR declared-layer /
  contract violation OR score regression beyond the 0.005 noise floor established in Finding 3,
  scored deterministically by archy (objective, blind by construction).
- **Secondary:** task success (target tests pass), diff size, and the mechanism check, did the agent
  *revise* after an archy signal (the anti-theater test from the synthesis: archy changes what the
  agent does, not just what it reports).
- **Decision rule:** archy-in-loop is validated if arm A's structurally-bad rate is materially below
  arm B's at equal task success. This control study supplies arm B's expected floor.

This protocol is gated, per the maintainer's 2026-05-26 prioritization, on a usage signal that agents
will actually call the tools; it is the resolution path, not a commitment to run it now.

## Conclusion

The premise behind #144 / #145 / #146 survives empirical contact, but in a sharpened form. archy is
not justified as a frequent catcher of routine breakage; it is justified as a **rare-firing,
low-false-positive gate on multi-module, easy-to-miss structural damage that concentrates in exactly the
large transformative changes agents produce most and handle worst.** That is a stronger and more
honest value proposition than the original "agents constantly rot the graph" framing, and it is the
shape that makes a blocking gate safe to ship at higher autonomy levels.

[evaluate]: https://phroneses.com/articles/build/notes/evaluate-ai.html
