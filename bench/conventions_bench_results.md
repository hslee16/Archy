# `archy conventions`, scored against a hand-written key

Run: `uv run python bench/conventions_bench.py`. Key: `bench/conventions_key.yaml`.
SHAs from `bench/projects.yaml`, or inline where the project is not in the manifest.

## Result

| set | score | what it means |
|---|---|---|
| **development** | **14/14 (100%)** | `click`, `mypy`, `pydantic`, `pytest`. **A fit, not evidence.** |
| **held-out** | **12/13 (92%)** | `httpx`, `attrs`, `requests`, `rich`. **This is the number.** |

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
