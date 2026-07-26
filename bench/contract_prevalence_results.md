# Does the problem archy targets actually occur? (#359)

**Result: 1 of 151 commits (0.66%) introduced a violation of an architecture the
project itself declared.** Measured 2026-07-26 over 14 repositories that ship
import-linter contracts, using each project's own contracts and its own tool.
Raw rows: `bench/contract_prevalence.jsonl`.

No agent time was spent, and **no rule was authored by the measurer**, which is
what separates this from every earlier archy measurement.

## The number, next to the other two

| measurement | subject | rate |
| --- | --- | --- |
| Q1a (`bench/inloop_prevalence.py`) | humans, cycles introduced | **0.5%** per commit |
| **this bench** | humans, declared-contract violations | **0.66%** per commit (1/151) |
| #356 (`bench/q1b_run.py`) | agents, cycles or layer violations | **0%** (0/25, upper bound 12%) |

Three independent measurements, three different populations, three different
outcome definitions, all landing in the same low-single-digit regime. **The event
archy exists to catch is rare for everyone.** That is the honest headline, and it
is a stronger statement than any one of the three numbers alone.

## What was found

The single violation, in full:

```
AndreasHeine/i3x2ua  30b4d9ebcc  "Application must not depend on bootstrap"
```

A developer wrote a clean-architecture rule in their own words and a later commit
broke it. This is the first time in this project's research that the target event
has been observed in the wild rather than constructed.

Also observed, and arguably more interesting for the product: **commits that sit
on an already-broken contract.**

```
Clarity-Digital-Twin/brain-go-brrr   broken 2 -> 2   (violations persist across commits)
AndreasHeine/i3x2ua                  broken 1 -> 1
```

Nobody fixed these between commits, and **zero commits in the whole sample fixed
a standing violation**. A declared rule that is broken and stays broken is
constraint decay (#139), and it is a different failure mode from introduction:
detection did not help, because the project already knew.

## Per repo

| repo | introduced | pairs | commits on a broken contract |
| --- | --- | --- | --- |
| AndreasHeine/i3x2ua | 1 | 15 | 2 |
| Bucha11/axor-core | 0 | 15 | 0 |
| Martossien/transcria | 0 | 15 | 0 |
| FELIGN/pantr | 0 | 15 | 0 |
| MarcusJellinghaus/mcp_coder | 0 | 15 | 0 |
| AssemblyAI/cli | 0 | 15 | 0 |
| MatthiasBurger-Coder/Tiny-Swarm-World | 0 | 15 | 0 |
| BinaryBand/himark | 0 | 14 | 0 |
| AlexKapadia/AutoFirm | 0 | 10 | 0 |
| Clarity-Digital-Twin/brain-go-brrr | 0 | 7 | 1 |
| BernardUriza/free-intelligence | 0 | 6 | 0 |
| Aries-Serpent/_codex_ | 0 | 5 | 0 |
| NVIDIA/cloudai | 0 | 3 | 0 |
| Ali99f1/openpilot | 0 | 1 | 0 |

## How thin the adopter population is

This is a finding in its own right, and it bears on whether the category has a
market at all:

- GitHub code search reports **~1,590** hits for import-linter configuration.
- Of 40 repos pulled from that search, **9 declare no import-linter architecture
  at all** (matched a doc, a lockfile, or a deleted config).
- More were skipped for having **fewer than 50 Python-touching commits**: a rate
  needs a denominator, and many hits are scaffolds or abandoned experiments.
- **14 repos** survived to contribute a single measurable commit pair.
- Only two are well-known projects (NVIDIA/cloudai, Aiven-Open/kio), and kio
  contributed nothing (see below).

Declaring an architecture and checking it in CI is, on this evidence, a rare
practice.

## Limits, stated plainly

**107 of 258 sampled pairs (41%) could not be evaluated, and the failures cluster
by repo rather than scattering.** The dominant cause is **version skew**: these
are historical commits evaluated with import-linter 2.11, and a config that
worked with the version the project used at that commit may not load today.
Aiven-Open/kio is the clean example, failing all 15 pairs with:

```
Modules have shared descendants.
```

kio's contracts evaluate fine at HEAD (verified: 5 contracts, 1,841 files), so
this is the tool's validation changing under a fixed config, not a broken repo.
Those pairs are dropped, never scored as clean, which is the #356 discipline.

**Reach was not captured for this run.** `files_analyzed` and contract counts
were added to the harness mid-run, so the recorded rows do not carry them. This
matters because a zero from contracts that govern three modules is not evidence
of anything, and #362 shows the trap is live: archy's own config leaves 33 of 76
modules in no layer while `archy check` reports a clean pass. **A rerun should
capture reach before this number is quoted as a bound.**

Other limits:

- **Small and skewed corpus.** 14 repos, mostly small, ordered by stars from a
  code-search sample that never reached large adopters such as PostHog.
- **One violation is one violation.** The 95% CI on 1/151 runs from roughly 0.02%
  to 3.7%. This bench distinguishes "rare" from "common", not much finer.
- **Conditional on adoption.** Sampling is restricted to commits after the
  project first declared an architecture; the pre-adoption era answers a
  different question.
- **Contracts are not the whole architecture.** A project may hold intent that
  its contracts do not encode, and violations of that are invisible here.

## What this licenses, and what it does not

**Supports:** the event is real and occurs in human work on real projects. #356's
0/25 was not evidence that the phenomenon does not exist.

**Supports:** archy's target problem is **rare**, at well under 1% of commits, in
the same regime as the human cycle rate. Any pitch implying it is frequent is not
supported by any measurement this project has made.

**Does not support:** any claim about archy's detection ability. This bench used
import-linter, not archy. #347 is that experiment.

**Changed the emphasis, and then that failed too.** Zero commits in this sample
fixed a standing violation, while three sat on one, which read as a signal worth
weighting toward #139's territory rather than the introduction rate.

**#364 tested that conditional directly and it did not hold.** Across 11 repos
and 107 samples, standing violations turned out to be just as rare (1 sample
pair, 1 of 11 repos), and the other three decay signals came back null or
untestable. See `bench/contract_decay_results.md` and `docs/WHAT_DIDNT_WORK.md`
Study 5. The "living with a broken rule" framing is **not** a surviving
replacement for the introduction-rate framing; it is a third null.
