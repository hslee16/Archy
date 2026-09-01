# `archy conventions`, scored against a hand-written key

Run: `uv run python bench/conventions_bench.py`. Key: `bench/conventions_key.yaml`.
SHAs from `bench/projects.yaml`, or inline where the project is not in the manifest.

## Result

| set | score | what it means |
|---|---|---|
| **development** | **15/15 (100%)** | `click`, `mypy`, `pydantic`, `pytest`, plus this project at an external evaluation's pin. **A fit, not evidence.** |
| **held-out** | **12/13 (92%)** | `httpx`, `attrs`, `requests`, `rich`. **This is the number.** |

The surfaces fix below changed no held-out row: 12/13 before and after.

All 8 projects analysed twice per run; both payloads byte-identical.

## Why the split exists

#410 measured four projects and shipped the numbers in a pull-request body with no
key, no pins and no scorer, so nobody could re-derive them. Worse, those same four
projects had motivated the features **and then scored them**, and two heuristics were
tuned directly against them:

- the doc-matching strictness ladder took **three passes against mypy** until it
  reached 78 of 78 codes;
- the shadow-subtree thresholds were tuned until they caught `pydantic.v1`.

So mypy's and pydantic's scores in #410 are in-sample. That table is a development
set wearing a test set's label, and this file exists to say so and to fix it.

**The held-out keys were derived first.** Four projects were read by readers who were
never shown archy's output, answering the same four questions from source alone, at
the SHAs pinned here. Only then was the bench run. A key written after seeing the
output measures nothing.

**Held-out holds at 92%.** The heuristics are not fitted to the four projects they
were written against (which was the open worry), and it is now answered rather than
asserted.

## What the held-out set actually tests

It is a harder set than the development four, on purpose:

- **`httpx` is a silence test.** All four of its surfaces are complete: 28/28 in
  `__all__` and in `docs/exceptions.md`, with `tests/test_exported_members.py`
  asserting the export list. Any gap reported here is a **false positive**, the one
  thing this section must never emit. Archy stays silent. ✅
- **`attrs` cannot be answered by the kind census at all.** Its 9 exceptions share no
  local base; each subclasses whichever stdlib exception matches its semantics. The
  report has to answer via the suffix census or not at all. It answers. ✅
- **`rich` has no convention to find.** Two disjoint error roots, 9 classes in
  `rich.errors` against 11 scattered elsewhere, no error re-exported from
  `__init__.__all__`, and no reference page. Its rows assert that archy does **not
  invent** a surface convention that the project does not have. ✅
- **`requests` is the one that fails.** See below.

🔴 **Questions the project itself does not answer are not scored.** No tool can
recover a convention that does not exist, and scoring archy for failing to would be
as dishonest as scoring it for inventing one.

## 🔴 The held-out failure, and why it is not being fixed here

`requests` q4_surfaces **FAILS**. It has the largest export gap in the whole set:
25 exception classes defined, 11 re-exported, 9 in `__all__`, 8 documented, and
archy reports nothing.

Cause, measured: `_export_gaps` only reports where the export list already governs
**≥50%** of a family. `requests.__all__` lists **9 of 22** `RequestException`
descendants = **0.41**, so the guard suppresses it.

That guard exists for a good reason (one member mentioned in passing is not a promise
to document the rest), but `requests` is the opposite case: a large family with a
partially-maintained export list, which is precisely what the section should catch.

**Lowering the threshold would fix this row and invalidate the held-out set.** Tuning
against held-out data is how a test set silently becomes a development set, which is
the exact failure this file was written to correct. The finding is recorded; any fix
must be validated on a *new* project that has never been scored.

## What this does and does not measure

✅ **It measures correctness**: does archy produce the answer a careful reader derives
from the same source?

🔴 **It does not measure effectiveness**: whether a model is better off having it.
Those came apart badly in #408, where a real 5.5x per-occurrence effect was killed by
a 1.6% base rate. A tool can be right about everything it says and still change no
outcome.

## On competitors

There is no direct competitor to bench against, and that is a finding rather than an
excuse. Tools in the neighbourhood do a different job:

- **Linters** (ruff, pylint naming rules, import-linter) *enforce* conventions you have
  already declared. They do not derive one from source; that is the whole gap here.
- **Code-graph / MCP indexers** (codegraph, code-review-graph) build symbol and edge
  graphs and answer *navigation*, measured at **6.7%** of an agent's deliberation
  against design judgement's ~31%. Neither indexes third-party packages, and neither
  reports naming, gating or surface conventions at all.
- **Editor rule files** (`.cursorrules`, `AGENTS.md`, `CLAUDE.md`) are *human-authored*.
  They are the artefact this command would let you write, not a competing deriver.
  None of the eight projects here ships one.

**The meaningful comparator is the unaided model**, because that is what happens today
and it is not less accurate. Every key in this file was produced by a model reading
the repo with no archy, and those keys are correct; that is why they can score archy at
all. The difference is **cost**, not correctness:

| project | file opens per question, unaided |
|---|---|
| httpx | ~2-3 |
| attrs | ~2-4 |
| requests, rich | ~4-6 |
| pytest | ~9 total |
| mypy | ~12 total |

plus, in every case, the judgement to *design* a multi-instance consistency check:
grep every file mentioning each member, then decide whether 12 of 13 is a convention or
a drift. Archy answers in one command.

⚠️ **The keys were not independently validated**, and that limits the accuracy half of
this comparison. They are the ground truth by construction, so a key that is wrong makes
archy wrong for being right, and the "not less accurate" claim inherits the same error it
would need to detect. The keys were read from source with care and the held-out ones were
written before the bench ran, which is what makes them worth using; a second independent
reader per row would be what makes the accuracy claim measurable. **The cost claim below
does not depend on this** and stands on its own.

So the claim this bench supports is **"one command returns what costs a model 2-12
targeted reads and a consistency check it has to invent"**. The claim it does not
support, at any sample size, is that the resulting work is better.


## The surfaces fix, and why it needed a bench to find

The section is asked *"what must be updated alongside this?"*, and on this project
it was answering with two thirds of the truth, ranked below the fold.

`check` renders layer violations through three surfaces: CLI text, CLI
`--format json`, and the MCP payload. The CLI pair is `_violations_to_text` and
`_violations_to_json`, which share the stem `_violations_to` and grouped
correctly. The MCP surface is `_run_check`, which shares **no stem with them**.
A stem-keyed census is structurally blind to the third surface, and that is
the surface half-wired features actually miss.

Two changes, both recombinations of what the census already parsed:

- **Consumer families.** What the three surfaces have in common is not a name,
  it is the symbol they consume. A consumer family is keyed on a definition and
  lists the internal modules importing it, **capped at 5**: a co-update set is
  small by nature. Sixteen modules import `build_graph`; forgetting one is not a
  failure mode, that is infrastructure.
- **Cross-module families rank first.** A family confined to one file is wired
  in a single edit; a family spanning several is the one that gets half-wired.

Measured on this project at `77517865e0d5`:

| | before | after |
|---|---|---|
| `find_violations` co-update set | not detected as a family | **rank 4 of 150**, naming `archy.cli`, `archy.diff`, `archy.mcp` |
| best partial answer | `_violations_to` at **rank 38 of 50** (`json`, `text` only) | still present, and no longer the best answer |
| visible in the default `--top 12`? | **no** | **yes** |

🔴 **It was derived and invisible, which is the failure mode a correctness score
cannot see.** The bench row `archy-at-eval-pin / q4_surfaces` asserts both the
membership and `max_rank: 12`, so a future ranking change that pushes it back
under the fold fails rather than passing quietly.

⚠️ **This does not raise the effectiveness ceiling.** On the corpus this was
measured against, 7 of 25 runs touched only the module holding the logic and
wired nothing at all; a correct, well-ranked surface rule does not reach them.
It converts the 2 runs that stopped at two surfaces.

## 🔴 What this bench does NOT measure, and the measurement that says it matters

Everything above is **detection**: does the census derive the fact? The answer is yes,
12/13 held-out. That is not the same as **retrievability**: can a reader get the fact
out of the default output?

They came apart, and the gap was measured rather than guessed. 24 pieces of real agent
reasoning were selected *because* an inventory could in principle have stated the fact
they were working out (a deliberately favourable sample) and scored blind against this
build and against the previous release. Two independent readers, neither told which
version was which:

**0 of 24, both versions, perfect agreement on every row.**

Both readers gave the same reason without being prompted for one:

> every near-miss failed on **truncation**, not on the wrong kind of fact; the report
> says `150; showing 12`, so **absence from the list proves nothing**

Four blockers, none of which is "the census derives the wrong things":

1. **Truncation.** The section computes 150 families and prints 12. The fix above moves
   one co-update set from rank 38 to rank 4, which is why the `archy-at-eval-pin` row
   asserts `max_rank: 12`, but that guards **one symbol**, not the class. Questions
   about `risk`, `hotspots`, `reach`, `instability` and `gitlog` are still below the fold.
2. **The questions are targeted; the output is a ranking.** "Does `risk.py` import
   `hotspots`?" is a lookup, and a top-N cannot serve a lookup.
3. **Many are negative and exhaustive.** "Does *any* of graph/cycles/score/history/trend
   reach `layers`?" A digest can never answer a negative.
4. **The largest cluster is documentation content**: which line of README says what.
   `77 doc file(s)` is a count.

One blocker is self-inflicted and worth stating plainly: one of the 24 asks where test
helpers live and how they are named. Test modules are set aside by default, so the report
**structurally cannot answer it**. That default is right for a vendored legacy subtree
and wrong here, and at the time of the scoring `--include-tests` existed with nothing
pointing a reader at it. The report now prints `(--include-tests to census them)` beside
the excluded count, and truncated sections now name `--top`.

⚠️ **The 0 of 24 was measured before those two fixes and has not been re-scored.** So it
describes the build the readers saw, not this one, and nothing here claims the fixes move
it. The honest prior is that they do not move it much: blockers 2, 3 and 4 above are
untouched by a hint, and a re-score would be the way to find out rather than an argument
about it.

**So the next change to this command is not a fifth census.** It is queryability: a
`--module` filter, an exhaustive mode, or a lookup that answers "what consumes X" and
"does X reach Y" on demand. 🔴 **And that should not be added to this PR**: the held-out
set scores detection, so a retrieval feature would ship unmeasured, which is the failure
this file exists to prevent. It needs its own held-out measurement, against derivations
rather than repositories.
