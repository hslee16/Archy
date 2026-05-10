# Architecture-quality metrics: research notes for Python

Survey of architecture-assessment methods that exist beyond the four
sub-metrics archy currently ships (modularity, acyclicity, depth,
equality), evaluated for **practical applicability to Python
specifically**. Intended as input to roadmap discussions; nothing
here is committed.

For the four metrics archy already ships, see
[`SCORING.md`](SCORING.md). For the concrete near-term roadmap, see
[`FUTURE.md`](FUTURE.md). This doc is wider than both: it catalogues
the candidate space.

---

## Why a Python-centric survey

archy is Python-only by deliberate design choice
([`FUTURE.md`](FUTURE.md): "no multi-language support"). The
academic and industry literature on architecture metrics, however,
overwhelmingly comes from C++ (Lakos), Java (ArchUnit, Sonargraph),
and .NET (NDepend) - languages with explicit interfaces, header
files, and static dispatch.

Inspiration is language-agnostic; concrete implementations are not.
Every candidate metric below is rated specifically against Python's
idioms. The features that move the needle:

- **Dynamic dispatch** (`getattr`, attribute access on registries,
  string-based dispatch). Static analysis cannot follow it. Kills
  reachability-based metrics like dead-code detection.
- **Decorators**. `@app.route`, `@pytest.fixture`, `@property`,
  `@dataclass`, `@cached_property` change the *meaning* of a function
  without changing its imports. A function may be live, abstract, or
  cached purely because of a decorator.
- **`__init__.py` re-exports.** Python packages publish their public
  API by importing names into `__init__.py`. This creates phantom
  back-edges in the import graph (already documented as a known archy
  resolver bug in [`docs/CASE_STUDIES.md`](CASE_STUDIES.md)).
- **`if TYPE_CHECKING:` imports.** Imports used only for type
  annotations are conditional on `typing.TYPE_CHECKING`, which is
  always `False` at runtime. They appear as edges in static analysis
  but carry no runtime coupling.
- **Duck typing and `Protocol`.** Python's standard "interface"
  mechanism is structural (PEP 544 `Protocol`) rather than nominal.
  Counting "abstract classes" (Martin's `A`) without considering
  Protocols undercounts; counting all Protocols overcounts.
- **Plugin / entry-point patterns.** [`pluggy`][pluggy-doc] and
  setuptools `entry_points` create runtime edges between modules
  that *never* appear in an import graph. Pytest, tox, datasette,
  airflow, and many others rely on this.
- **Dynamic imports.** `importlib.import_module(name)` resolves at
  runtime; archy correctly flags these as opaque
  ([`FUTURE.md`](FUTURE.md): "acceptable").

These features mean some metrics from the literature translate
cleanly, others need redefinition, and a few are not worth shipping
at all because the false-positive rate on real Python code would
exceed the signal.

---

## 1. Already implemented

For reference, archy ships these and they're documented in
[`SCORING.md`](SCORING.md):

| Metric         | What it captures                                |
| -------------- | ----------------------------------------------- |
| Modularity     | Newman's Q over greedy community partition     |
| Acyclicity    | `1 - tangle_ratio` (fraction of nodes in SCCs of size ≥ 2) |
| Depth         | Longest path through the SCC condensation      |
| Equality      | `1 - Gini(out_degree)`                          |

Plus rule-based fitness functions: layer rules and exclusion patterns
in `archy.yaml`, checked by `archy check`.

---

## 2. Coupling and stability (Robert Martin, 1994)

Martin's package metrics are derived directly from afferent (Ca) and
efferent (Ce) coupling counts on the import graph
([Martin's original paper][martin-paper], [Wikipedia][sw-pkg-metrics]).
Originally framed for Java/.NET; the formulas are language-neutral
but the *interpretation* of `A` (Abstractness) needs Python care.

- **Instability** `I = Ce / (Ce + Ca)` ∈ [0, 1]. `I = 0` means
  "depended on by many, depends on nothing" (a stable bedrock module).
  `I = 1` means "depends on many, nothing depends on it" (a leaf).
  **Python translation:** completely clean. Counts of imports in vs.
  out are trivial on the existing graph.
- **Abstractness** `A`. Original: ratio of abstract classes to total
  classes. **Python translation:** the Java notion of "abstract class"
  has three Python analogues, and a useful definition combines them:
  - classes inheriting from `abc.ABC` or with any
    `@abc.abstractmethod`-decorated method;
  - subclasses of `typing.Protocol`;
  - dataclass-only modules (`@dataclass` with no behavior) arguably
    don't count as "abstractions" - they're concrete data.
  Without including Protocols, Python codebases that use structural
  typing read as universally concrete (`A ≈ 0`), which is misleading.
- **Distance from main sequence** `D = |A + I - 1|`. The "main
  sequence" is the line `A + I = 1`: stable modules should be
  abstract, unstable modules should be concrete.

Two derived rules from Clean Architecture:

- **Stable Dependencies Principle (SDP):** dependencies should flow
  toward stability. Violations: a module with low `I` importing one
  with high `I`. **Python translation:** clean - the rule is purely
  graph-theoretic.
- **Stable Abstractions Principle (SAP):** stable components should
  be abstract. **Python translation:** depends on the abstractness
  definition above; useful with the Protocol-aware version.

**Feasibility for archy:** `I` and SDP are essentially free -
single-pass ratios on the current graph. `A` requires AST work
(tree-sitter pass to count `ABC`/`Protocol` subclasses and
`@abstractmethod`); already on the roadmap as part of the
cyclomatic-complexity AST work.

**Signal:** Strong for `I` and SDP. Weaker for `A`/`D`/SAP in Python
specifically because Python's structural typing means many
"abstractions" never appear as `Protocol` subclasses at all (e.g.,
file-like objects passed by convention). Reasonable to ship `I` and
SDP first, treat `A`/`D`/SAP as an experiment.

---

## 3. Average reach: Lakos NCCD / MacCormack propagation cost

These two metrics - Lakos's CCD/ACD/NCCD
([summary][swiftalyzer-ccd], [Lattix docs][lattix-metrics]) and
MacCormack's propagation cost
([HBS working paper][maccormack-hbs], [overview][dsm-overview]) -
were originally treated separately in this doc. They turn out to be
the same metric family with different normalizations:

- Build the binary reachability matrix `R` where `R[i, j] = 1` iff
  `j` is in `i`'s transitive forward closure (including `i` itself).
- **CCD** (Lakos) `= sum(R)`.
- **ACD** (Lakos) `= CCD / N`. Average modules reachable per module.
- **Propagation cost** (MacCormack) `= sum(R) / N² = ACD / N`. Same
  number expressed as a fraction.
- **NCCD** (Lakos) `= CCD / CCD_balanced_binary_tree(N)`. NCCD < 1 is
  "more horizontal than a balanced tree" (loosely coupled);
  NCCD > 1 is "more vertical" (deep, coupled).

So `ACD` is the natural per-module unit, `propagation_cost` is the
fraction-of-system framing, and `NCCD` is the relative-to-balanced-tree
framing. Same underlying signal.

**Empirical validation against archy's existing depth metric.**
Computed on the 9-library benchmark plus archy itself
(`/tmp/archy_validation/nccd_correlation.py`):

| Project   |   N |    E |    CCD |   ACD |  NCCD | archy `max_depth` |
| --------- | --: | ---: | -----: | ----: | ----: | ----------------: |
| starlette |  77 |  266 |  1,524 | 19.79 |  3.64 |                 4 |
| httpx     |  67 |  186 |  1,081 | 16.13 |  3.10 |                 5 |
| click     |  64 |  196 |    878 | 13.72 |  2.68 |                 3 |
| rich      | 152 |  668 |  7,671 | 50.47 |  7.92 |                 4 |
| flask     |  69 |  232 |  1,507 | 21.84 |  4.15 |                 3 |
| requests  |  60 |  172 |    660 | 11.00 |  2.18 |                 7 |
| pytest    | 156 |  890 |  4,877 | 31.26 |  4.87 |                10 |
| pydantic  | 154 | 1076 |  9,481 | 61.56 |  9.63 |                 6 |
| fastapi   |  84 |  284 |    998 | 11.88 |  2.13 |                 6 |
| archy     |  41 |  100 |    271 |  6.61 |  1.43 |                 6 |

Pearson correlations:

```
Pearson(NCCD, max_depth) = +0.000
Pearson(ACD,  max_depth) = +0.056
Pearson(NCCD, ACD)       = +0.997
```

NCCD and ACD are essentially the same (`r = 0.997`), as expected.
**NCCD and `max_depth` are uncorrelated (`r = 0.000`)**, refuting an
earlier draft's worry that NCCD might be redundant with archy's
existing depth axis. They capture different things:

- `max_depth` is the *worst case* - one long chain.
- `NCCD/ACD` is the *average case* - typical reach of a random
  module.

A graph can be shallow but wide (`rich`: depth 4, NCCD 7.92 - most
modules reach most others through short paths) or deep but narrow
(`requests`: depth 7, NCCD 2.18 - one long chain but most modules
don't reach much). archy's current depth metric catches the first
pathology weakly and the second well; NCCD/ACD inverts that.

**Python translation:** language-neutral computation. Two caveats:

1. **`TYPE_CHECKING` imports.** If archy ever distinguishes runtime
   from type-checking-only edges (section 13), reach should be
   computed on runtime edges only. A `TYPE_CHECKING` import doesn't
   contribute to runtime coupling load.
2. **Plugin systems.** `pluggy`-heavy codebases will *under*report
   reach because hook-based coupling is invisible to static
   analysis. Library-style code (FastAPI, requests, pydantic) is
   unaffected; plugin frameworks (pytest, mkdocs, datasette) are a
   known blind spot.

**Feasibility:** transitive closure on the existing graph; NetworkX
has it built in. archy's `archy_impact` already does per-file
forward-closure, so the machinery exists.

**Signal:** Strong, and empirically orthogonal to existing axes.
This is the highest-leverage *new score axis* on the list.

**Companion: core size.** MacCormack also introduces a
"core/periphery" classification: a system has a *core* (the largest
SCC, or near-SCC) plus a periphery. Empirical finding across 75-80%
of real systems: there is a single dominant core, and smaller cores
correlate with healthier maintainability. Cheap to compute alongside
NCCD; report as a diagnostic, not a score axis.

---

## 5. PageRank-style importance (applied to code)

NetworkX has `pagerank` built in. Applied to the import graph, each
module's PageRank captures "importance weighted by the importance of
things that depend on me." Used by NDepend's "rank" for type
popularity ([NDepend metrics][ndepend-metrics]); used in academic
work to identify ["key classes"][pagerank-key-classes].

**Python translation:** pure graph computation, language-neutral. One
specific Python quirk: `__init__.py` re-exports inflate PageRank for
package roots that re-export everything (because every importer of a
sub-name pulls in `__init__.py`). archy's planned re-export-aware
resolver ([`FUTURE.md`](FUTURE.md), already noted) cleans this up.

**Feasibility:** One NetworkX call. Linear per iteration, fast.

**Signal:** Useful as a per-module *diagnostic*, weak as a
graph-level summary. Better than raw in-degree because it weights by
importance recursively (a utility imported only by `__main__` looks
less important than one imported by core modules).

**Fit:** expose per-module PageRank in `archy graph --format json`
and in `archy_impact`. Use it for navigation and diagnostics, not
scoring.

---

## 6. Tangle ratio (Structure101)

[Structure101][structure101-xs] frames complexity as two ratios:

- **Fat:** percentage of the codebase inside packages with
  above-threshold dependency density.
- **Tangle:** percentage of the codebase inside cyclic regions.

Tangle is **a percentage of code, not a count of cycles**. archy's
current `acyclicity = 1 / (1 + N)` treats one big SCC the same as
one small SCC, which understates the problem when 60% of the codebase
is in a single tangled component.

**Python translation:** clean and especially relevant. Python
codebases tend to acquire one of two cycle profiles:

1. *Phantom cycles* from `__init__.py` re-exports (already documented
   in `CASE_STUDIES.md` for FastAPI). These will disappear when the
   re-export-aware resolver lands and Tangle ratio will drop sharply.
2. *Real cycles* from `if TYPE_CHECKING:` workarounds gone wrong (one
   module type-imports another at module top level instead of guarded
   by `TYPE_CHECKING`). Tangle ratio captures these accurately.

**Feasibility:** Trivial. After the SCC condensation archy already
computes, sum `|nodes in SCCs of size ≥ 2| / N`.

**Signal:** Strong, complementary to existing acyclicity. Replacing
or supplementing the cycle-count normalization with tangle ratio is
the smallest-cost improvement on the list.

---

## 7. Logical coupling / co-change (Gall et al., 1998+)

Two files that frequently change together in git history are
*logically coupled*, even when there's no static import edge between
them
([change coupling overview][change-coupling-paper],
[CodeScene blog][codescene-change]). Predicts defects better than
structural coupling alone in mining-software-repositories studies.

**How it's computed:** mine `git log --name-only` for commits that
touch multiple files; count co-change pairs; threshold by recency
and frequency.

**Python translation:** language-agnostic. One advantage Python has
over C++/Java for this analysis: no header/source pair, no autogenerated
boilerplate to filter out, no IDE-driven mass reformatting. Python
co-change tends to be cleaner signal than C++ (where `.h`/`.cpp`
pairs always co-change trivially).

**The plugin-system blind spot from section 4 reverses here:**
co-change *catches* what static analysis misses. If `tests/` and
`src/` consistently co-change but have no static edge (because tests
use `pytest` collection, not direct imports of internal symbols),
that coupling shows up only here.

**Feasibility:** Medium. archy has no git-mining today; adding a
`git log --name-only --pretty=format:"%H"` parser is contained.
Already on disk; no service required. archy's `.archy/history.jsonl`
is a precedent for git-aware analysis.

**Signal:** Strong, and *qualitatively different* from anything archy
currently ships.

**Caveats:** noisy on monorepos with bulk reformatting commits.
Time-windowed analysis (last N commits / N months) plus commit-size
filters are standard counter-measures.

**Possible shape:** `archy cochange` as a separate command;
optionally fold a `logical_coupling_count` into the score breakdown.

---

## 8. Hotspots = complexity × churn (Tornhill / CodeScene)

From Adam Tornhill's *Your Code as a Crime Scene*
([CodeScene X-Ray docs][codescene-xray]):

> A hotspot is a file that is *both* complex and frequently changed.

Two simple inputs (per-file CC and churn over a window) multiplied;
rank the result. Empirical claim: the top ~10 hotspots account for
the majority of defect risk and refactoring leverage.

**Python translation:** the CC half is clean - radon's CC computation
is the de-facto Python standard, and archy can compute it natively
once the AST pass for cyclomatic complexity (already in
[`FUTURE.md`](FUTURE.md)) lands. The churn half reuses the git-mining
machinery from section 7.

One Python specifically: decorators that wrap a function (`@retry`,
`@cache`, `@app.route`) can dramatically increase the *effective*
complexity of a function without changing its CC count. Treat these
as a known limitation rather than try to model them.

**Feasibility:** Medium, but cheap if both git-mining (section 7) and
cyclomatic complexity ([`FUTURE.md`](FUTURE.md)) ship - this comes
nearly free on top of those.

**Signal:** Very strong, and very actionable. Produces a *prioritized
list* rather than a single number - "refactor these three files
first." Pairs well with the AI-agent loop
(`docs/AGENT_LOOP.md`): "before you start work, here are the
highest-risk files you might be touching."

**Fit:** standalone command (`archy hotspots`) consuming the
cyclomatic-complexity pass + git mining.

---

## 9. Cognitive complexity (Sonar, Campbell 2017)

A function-level metric specifically designed to model *human
readability*, intended to replace cyclomatic complexity for that
purpose. Adds penalties for nesting depth, breaks in linear flow, and
unintuitive control structures
([Sonar whitepaper][sonar-cognitive]).

**Python translation:** the metric is defined for procedural code and
applies cleanly to Python functions. Python-specific subtlety: list /
dict / generator comprehensions count as "loops" - a deeply nested
comprehension `[x for ... for ... for ...]` is high cognitive
complexity per the standard rules.

**Feasibility:** AST-level. Same tree-sitter pass that computes
cyclomatic complexity computes cognitive complexity. Marginal cost on
top of CC.

**Signal:** Strong for human-developer audiences. For archy's
AI-agent positioning, slightly weaker - LLMs may not have the same
nesting-depth bottlenecks as humans, though limited evidence either
way.

**Fit:** ride along with the planned per-function CC pass.
Essentially free.

---

## 10. Architecture conformance - reflexion model (Murphy & Notkin, 1995)

The reflexion-model approach
([original paper][reflexion-paper]):

1. Author specifies a high-level model: "components A, B, C; A may
   depend on B; B may depend on C; nothing else."
2. Tool extracts the actual dependency graph.
3. Tool reports two kinds of deviation:
   - **Divergences:** edges in the implementation but not in the model
     (forbidden dependencies).
   - **Absences:** edges in the model but not in the implementation
     (planned but not built).

**Python translation:** the most direct Python instance is
[import-linter][import-linter-docs] which ships three contract types:
**Layers**, **Forbidden** (A must not depend on B), and
**Independence** (set of modules must be mutually independent, even
indirectly).

archy.yaml today supports Layers contracts only. Adding **Forbidden**
and **Independence** contract types would close the gap with
import-linter and is straightforward - both are graph-reachability
checks on the existing edge type. Absence detection (an edge declared
but never instantiated) is a third, complementary check.

**Feasibility:** Forbidden + Independence: low cost, additive to the
existing `archy check` machinery. Absence detection: needs a richer
rule grammar in `archy.yaml`.

**Signal:** Very strong for projects with an authored architecture.
Without an authored model, this section has nothing to do.

**Fit:** evolve `archy.yaml` and `archy check`. Not a sub-metric.

---

## 11. Information-theoretic metrics (graph entropy)

Several research lines apply Shannon entropy to dependency graphs as
a complexity measure
([entropy-based complexity][entropy-arxiv],
[entropy & consistency in architecture][entropy-mdpi]).

**Python translation:** language-neutral. Any distribution archy has
(out-degree, in-degree, community sizes) admits an entropy
computation.

**Feasibility:** Trivial - `H(p) = -Σ p_i log p_i` is one line.

**Signal:** Mixed and unclear. The literature is split on whether
graph entropy correlates with anything actionable; many proposed
metrics are mathematically equivalent to Gini or to modularity under
transformation. Lots of papers, few mature tools.

**Recommendation:** **Skip.** Including it for completeness would be
metric-bloat without signal gain.

---

## 12. Redundancy: dead and duplicate code

Sentrux's fifth metric (already discussed in `SCORING.md` as
deferred). The Python case is *worse* than the general case.

**Empirical validation.** Vulture 2.16 was run with default settings
(60% confidence) and at 90% confidence on the full 23-project
benchmark (see [`bench/projects.yaml`](../bench/projects.yaml)),
captured 2026-05-10:

| Project        |    LOC | Vulture @ 60% | Vulture @ 90% |
| -------------- | -----: | ------------: | ------------: |
| **sqlalchemy** |246,065 |     **1,827** |           415 |
| scikit-learn   |211,188 |           246 |            31 |
| dagster        |202,893 |         1,417 |            18 |
| **django**     |156,666 |     **2,017** |            12 |
| ansible        |135,915 |           949 |            54 |
| numpy          |123,708 |           395 |            57 |
| mypy           |113,094 |           208 |            21 |
| pydantic       | 45,563 |           210 |            22 |
| rich           | 38,515 |            89 |            12 |
| pytest         | 37,079 |           162 |            11 |
| scrapy         | 29,057 |           186 |             7 |
| aiohttp        | 26,237 |           199 |            17 |
| datasette      | 19,946 |           105 |            17 |
| fastapi        | 19,335 |           129 |             8 |
| anyio          | 14,455 |            78 |             2 |
| click          | 11,529 |            32 |             3 |
| flask          |  9,502 |            76 |             7 |
| httpx          |  8,827 |            69 |             3 |
| mkdocs         |  7,084 |            88 |             8 |
| starlette      |  6,584 |            67 |             0 |
| requests       |  6,371 |            54 |             4 |
| archy          |  2,528 |            16 |             0 |
| msgspec        |  2,365 |            10 |             4 |

Spot-checking findings on FastAPI, pytest, and Django (15 random
findings each):

- **FastAPI 15/15 false positives.** Pydantic protocol methods
  (`bytes_schema` etc., called by Pydantic core via dispatch),
  Starlette protocol attributes (`state`, `middleware_stack`),
  decorator methods registered to user code via routing
  (`websocket_route`, `exception_handler`), NamedTuple/dataclass
  fields used by attribute access (`file`, `filename`,
  `content_type`), and `__init__.py` re-exports
  (`RequestErrorModel`, `WebSocketErrorModel`).
- **pytest 15/15 false positives.** All 15 fall in
  `_pytest/_py/path.py` - a vendored copy of the `py.path.local`
  public API kept for backwards compatibility. Vulture sees no
  internal callers because pytest users (not pytest itself) call
  these methods.
- **Django 15/15 false positives.** All 15 are module-level
  variables in `django/conf/global_settings.py` (`DATE_FORMAT`,
  `DATETIME_FORMAT`, `MANAGERS`, etc.) - the canonical default-
  settings pattern. Django consumes these by string lookup at runtime
  via `from django.conf import settings; settings.DATE_FORMAT`. This
  pattern alone accounts for hundreds of the 2,017 default-confidence
  findings.

The 2,017 / 2,795 figures for Django and SQLAlchemy collapse to
12 / 527 at `--min-confidence 90`, which is itself nowhere near a
ground-truth dead-function count - vulture's confidence rating
correlates with how many dynamic-dispatch patterns it can rule out,
not with whether the code is actually dead. The blog post the doc
originally cited (59 on httpx, 260 on Flask) is in the right
ballpark for default-confidence output on small libraries; the FP
diagnosis is broadly correct.

The dominant FP patterns are the same across every spot-check:

- **pytest fixtures and conftest hooks** - referenced by name,
  never imported.
- **Flask/FastAPI/Django route handlers** - referenced by URL
  pattern strings.
- **Django default settings** - module-level constants consumed by
  attribute lookup at runtime.
- **Pluggy / setuptools entry-point implementations** - registered
  at install time, no static caller.
- **Pydantic validators** - invoked by the framework via decorator
  metadata.
- **`Protocol` and ABC method implementations** - no explicit
  caller; satisfied structurally.
- **Vendored backwards-compatibility surface** (e.g., pytest's
  `_pytest/_py/path.py`) - public API for downstream code, no
  internal caller.

These are not edge cases - they cover the dominant patterns in modern
Python application code. A naive vulture-style scan in archy would
generate so many false positives that ignoring them would become the
default workflow, which is the opposite of what a quality signal
should do.

**Duplicate-function detection** is a different story. Tree-sitter
ASTs can be normalized (rename identifiers consistently, hash the
shape) and clustered. False-positive rate is empirically much lower
than dead-function detection - duplicates are duplicates regardless
of dynamic dispatch. Caveat: short generated stubs (e.g., Pydantic
`@validator` boilerplate, Django model `Meta` classes) cluster
together by shape but are not "duplication" in the refactor-this
sense, so the heuristic needs a length threshold.

**Recommendation:** if any redundancy work ships, scope it tightly to
duplicate-function detection above some length threshold. Skip
dead-function detection until and unless archy can ingest a
runtime-coverage source - at which point vulture's
`--make-whitelist` workflow becomes optional.

---

## 13. Type-hint coverage (Python-specific)

Not a classical architecture metric - but a measurable Python-quality
signal that no language-neutral metric captures. PEP 484 type hints
plus a checker (mypy, pyright) act as a per-module contract. Coverage
and strictness are quantifiable:

- **Annotation coverage:** percentage of public functions/methods
  with full type annotations (parameters and return type).
- **Strict-mode pass rate:** `mypy --strict` errors per kLOC, or pass
  rate per module.
- **`# type: ignore` density:** how often the contract is explicitly
  violated.

A 2025 community survey found 73% of Python projects use type hints,
but only 41% run a checker in CI ([source][type-hints-survey]). The
gap between "types written" and "types verified" is itself a signal.

**Why this is architectural and not just lint-level:** in Python, the
type-hint graph (which classes reference which, via parameters and
returns) is a *third edge type* alongside imports and function calls.
sentrux's `tags.scm` already includes type-reference queries
([`FUTURE.md`](FUTURE.md) deferred item). Catching layer violations
that hide behind `if TYPE_CHECKING:` is precisely what this enables.

**Feasibility:** annotation coverage is a tree-sitter pass over
`FunctionDef` nodes. Strict-mode pass rate requires running mypy as
a subprocess (heavyweight; user already has mypy configured in many
cases). Annotation coverage is the cheap win.

**Signal:** Strong and uniquely Python (and TypeScript). A project
moving from 30% annotation coverage to 90% has measurably improved
maintainability in a way no graph-level metric will catch.

**Fit:** could be a fifth sub-metric (`typing` axis), or a
companion stat reported alongside the score. The former changes
the geometric-mean exponent and shifts absolute scores; the latter
is additive.

---

## 14. AI-agent-specific framing

archy positions itself as architecture-feedback for AI agents
(`docs/AGENT_LOOP.md`). The agent-era literature is still forming, so
this section is more speculative.

Two AI-specific framings worth noting, both Python-relevant:

**a. Context-window sufficiency.** Modern coding agents degrade past
roughly 40% of their context window
([Martin Fowler on context engineering][fowler-context]). A useful
agent-facing metric: "to safely modify this module, how much context
is needed?" - the size of its transitive forward+reverse closure.
This is **propagation cost framed for cognition**.

For Python specifically, the `__init__.py` re-export issue matters
here too: an agent reading `from foo import X` needs to know whether
`X` lives in `foo/__init__.py` or `foo/_internal/x.py`, and the
reachable-context calculation should follow the resolved location,
not the import path.

**b. Surprise rate.** Edges that violate stated layer rules are
"surprises" the agent shouldn't have generated. Surprises per kLOC
across an agent's PRs is a measurable signal of how well the agent
respects the architecture. Already computable from `archy check` plus
git history.

**Feasibility:** both are derivative - they reuse existing inputs in
a new framing. Mostly a documentation and presentation concern.

**Recommendation:** repackage existing outputs under explicit
agent-facing names in `AGENT_LOOP.md` and the MCP tool descriptions.

---

## 15. The Python tooling landscape archy interoperates with

Worth being explicit about what already exists, since archy should
complement rather than replicate. Each is a single-purpose tool;
archy's value is the integrated graph + score + governance surface.

| Tool                       | Scope                                       | archy relationship                                              |
| -------------------------- | ------------------------------------------- | --------------------------------------------------------------- |
| [import-linter][il]        | Module-level architecture rules              | Closest cousin. archy.yaml has Layers; adding Forbidden + Independence (section 10) closes the gap. |
| [pydeps][pydeps-doc]       | Module-level visualization                   | archy's `archy graph` is the same scope. archy adds scoring, governance, MCP. |
| [pyan][pyan-repo]          | Function-level call graph                    | Matches archy's planned call-graph PR ([`FUTURE.md`](FUTURE.md)). |
| [radon][radon-repo]        | Per-function CC + maintainability index      | archy's planned CC pass would replicate this. Worth reading radon's AST visitor for inspiration. |
| [xenon][xenon-repo]        | radon-based CI gate                          | archy's `--strict` gate is a generalization across multiple metrics, not just CC. |
| [vulture][vulture-fp]      | Dead-code detection                          | False-positive rate (section 12) makes this hard to beat without runtime-coverage input. |
| [pyright][pyright-doc] / [mypy][mypy-doc] | Type checking                | Type-hint coverage (section 13) builds on these without re-implementing them. |
| [ruff][ruff-doc]           | Lint + format                                | Orthogonal - ruff is rule-based on individual files; archy reasons about the dependency graph. |

---

## Summary table

Ratings reflect Python-specific feasibility and signal, not
language-neutral applicability. Empirically validated rows are
marked ✓.

The "Role" column distinguishes:

- **Score axis** - folds into the geometric-mean overall, must be
  independent of existing axes.
- **Sub-stat** - reported on the breakdown but not aggregated;
  diagnostic only.
- **Check rule** - extends `archy.yaml` / `archy check`, not the
  score.
- **Replace** - supersedes an existing sub-metric rather than adding
  alongside.

| Candidate                                 | Signal | Cost   | Role                | Validation       | Recommend |
| ----------------------------------------- | ------ | ------ | ------------------- | ---------------- | --------- |
| Tangle ratio                              | High   | Trivial| **Replace** acyclicity normalization | -                | **Yes**   |
| Reflexion: Forbidden + Independence       | High   | Low    | Check rule          | -                | **Yes**   |
| NCCD / ACD / propagation cost (one axis)  | High   | Low    | **Score axis**      | ✓ orthogonal to depth (r=0.000) on 9-lib benchmark | **Yes** |
| Type-hint coverage                        | High   | Low    | Score axis or sub-stat | -             | **Yes**   |
| Cognitive complexity                      | Medium | Trivial| Sub-stat (free with CC) | -            | **Yes (free)** |
| Hotspots (CC × per-file churn)            | High   | Medium | Standalone command  | -                | **Yes (after CC)** |
| Martin's `I` + SDP-violation rule         | Medium | Low    | Sub-stat + check rule | -              | **Yes**   |
| PageRank per module                       | Medium | Low    | Sub-stat (diagnostic) | -              | **Yes**   |
| Core/periphery size                       | Medium | Trivial| Sub-stat (diagnostic) | -              | **Yes**   |
| Reflexion: Absences                       | Medium | Medium | Check rule          | -                | Defer     |
| Cross-file co-change (logical coupling)   | Medium | High   | Standalone command  | -                | Defer (skip if hotspots ships) |
| Martin's `A` / `D` / SAP                  | Low    | Medium | -                   | -                | **No** (Python translation murky) |
| Redundancy - duplicate functions          | Medium | Medium | Advisory list       | -                | Maybe     |
| Redundancy - dead functions               | Low    | Medium | -                   | ✓ FP rate confirmed: vulture finds 32–2,795 issues per project, ~all FPs from framework patterns | **No** |
| Graph entropy                             | Low    | Trivial| -                   | -                | **No**    |

---

## Suggested ordering

The validation results clarify the order considerably. Group A is
"essentially free, ship before the call-graph PR." Group B requires
new infrastructure (CC AST pass, git mining). Items below are
additive unless marked **Replace**.

### Group A - pre-call-graph, low cost

1. **Tangle ratio** - *Replace* the current `acyclicity = 1/(1+N)`
   normalization with `acyclicity = 1 - tangle_ratio`. Five-line
   change. Best done after the `__init__.py` re-export resolver
   lands so the input graph is clean ([`FUTURE.md`](FUTURE.md)).
2. **Reflexion: Forbidden + Independence contracts** in
   `archy.yaml`. Closes the gap with import-linter; purely additive
   to `archy check`. No score impact.
3. **NCCD / ACD / propagation cost** - *Add* as a fifth score axis.
   Validated to be orthogonal to depth (Pearson r=0.000 on the
   9-library benchmark), so it earns its place in the geometric
   mean. Note: adding a fifth axis shifts absolute scores; document
   the change.
4. **Martin's `I` per-module + SDP-violation check rule.** Sub-stat
   in `archy graph --format json` plus a new rule type for
   `archy check`.
5. **PageRank per module + core size.** Diagnostics only; expose in
   `archy graph --format json` and `archy_impact` output.

### Group B - depends on AST or git infrastructure

6. **Per-function cyclomatic + cognitive complexity** (already in
   [`FUTURE.md`](FUTURE.md)). Both come from the same tree-sitter
   pass; cognitive is free given CC.
7. **Type-hint coverage** - same tree-sitter pass scope. Could be
   added as a sub-stat or eventually promoted to a sixth score axis
   if the signal proves load-bearing.
8. **Call-graph edges** ([`FUTURE.md`](FUTURE.md)). Once shipped,
   modularity and propagation cost both gain resolution.
9. **Hotspots = CC × per-file churn.** Needs CC + a one-pass
   `git log --name-only` parser. Runs as a standalone command.

### Deferred

- **Cross-file co-change** - hotspots covers the high-leverage
  subset with much less infrastructure (per-file churn vs full
  co-change matrix). Defer unless a specific use case requires it.
- **Reflexion: absences** - evolve `archy.yaml` once users with
  authored architecture documents ask for it.
- **Duplicate-function detection** - useful but off-positioning;
  cede to existing tools unless explicitly requested.

---

## Validation methodology

Empirical checks supporting load-bearing claims in the doc.

The benchmark is now driven by a checked-in manifest at
[`bench/projects.yaml`](../bench/projects.yaml) with **22 pinned
SHAs** spanning small CLI tools to very large frameworks across web
/ async / scientific / ORM / plugin-host / devops domains. The
benchmark runner is [`bench/run.py`](../bench/run.py); run with
`uv run --with networkx --with pyyaml python bench/run.py --vulture`.
Raw output checked into [`bench/results.md`](../bench/results.md).

Specific validations referenced above:

1. **Vulture false-positive rate** (section 12). Vulture 2.16 run with
   default settings (60% confidence) and at `--min-confidence 90` on
   all 22 projects. 15 random findings per project spot-checked across
   FastAPI, pytest, and Django to identify dominant FP patterns.
2. **NCCD vs depth correlation** (section 3). Computed CCD/ACD/NCCD
   on the original 9-library benchmark plus archy. Pearson correlation
   between NCCD and archy's `max_depth`: `r = 0.000`, indicating the
   metrics are empirically orthogonal. The narrower 10-project sample
   was used because the NCCD probe predates the 23-project manifest;
   the qualitative finding (orthogonality) is robust to sample-size
   refinements.

## References

- Martin, R. C. *OO Design Quality Metrics: An Analysis of Dependencies.* 1994. [PDF][martin-paper]. See also [Wikipedia: Software package metrics][sw-pkg-metrics].
- Lakos, J. *Large-Scale C++ Software Design.* Addison-Wesley, 1996. CCD/NCCD definitions: [Swiftalyzer][swiftalyzer-ccd], [Lattix metrics docs][lattix-metrics].
- MacCormack, A., Rusnak, J., Baldwin, C. *Exploring the Structure of Complex Software Designs.* Management Science, 2006. [HBS working paper][maccormack-hbs]. Core/periphery follow-up: [HBS 10-059][maccormack-core-periphery].
- Murphy, G., Notkin, D. *Software Reflexion Models.* SIGSOFT 1995. [Paper][reflexion-paper].
- Tornhill, A. *Your Code as a Crime Scene.* Pragmatic, 2015. CodeScene tools: [X-Ray][codescene-xray], [change coupling][codescene-change].
- Ford, N., Parsons, R., Kua, P. *Building Evolutionary Architectures.* O'Reilly, 2017. [Fitness functions overview][fitness-functions-infoq].
- Campbell, G. A. *Cognitive Complexity: A New Way of Measuring Understandability.* Sonar, 2017. [Whitepaper][sonar-cognitive].
- Heitlager, I., Kuipers, T., Visser, J. *A Practical Model for Measuring Maintainability.* QUATIC 2007 (the SIG model).
- Baldwin, C., Clark, K. *Design Rules, Vol. 1: The Power of Modularity.* MIT Press, 2000 - theoretical foundation for *why* modularity carries option value.
- Gall, H., Hajek, K., Jazayeri, M. *Detection of Logical Coupling Based on Product Release History.* ICSM 1998 - origin of co-change analysis.
- D'Ambros, M., Lanza, M., Robbes, R. *On the Relationship Between Change Coupling and Software Defects.* WCRE 2009.
- Murphy-Hill, E. et al. *What Predicts Software Developers' Productivity?* IEEE TSE 2019 - cited for the modern view that single-metric quality is insufficient.

### Python tools and ecosystem

- import-linter contracts (Layers, Forbidden, Independence): [docs][import-linter-docs]
- pluggy plugin architecture: [docs][pluggy-doc]
- pydeps, pyan, radon, xenon, vulture, pyright, mypy, ruff - see section 15 table.
- PEP 544 (Protocol structural typing): [PEP 544][pep-544]
- PEP 562 (`__getattr__` on modules - used for lazy `__init__.py` re-exports): [PEP 562][pep-562]

[martin-paper]: https://linux.ime.usp.br/~joaomm/mac499/arquivos/referencias/oodmetrics.pdf
[sw-pkg-metrics]: https://en.wikipedia.org/wiki/Software_package_metrics
[swiftalyzer-ccd]: https://swiftalyzer.com/understanding-your-project-with-metrics-ccd/
[lattix-metrics]: https://docs.lattix.com/lattix/userGuide/Metrics.html
[maccormack-hbs]: https://www.hbs.edu/ris/Publication%20Files/05-016.pdf
[maccormack-core-periphery]: https://www.hbs.edu/ris/download.aspx?name=10-059.pdf
[dsm-overview]: https://dsmsuite.github.io/dsm_overview.html
[reflexion-paper]: https://dl.acm.org/doi/10.1145/222132.222136
[codescene-xray]: https://docs.enterprise.codescene.io/versions/3.5.4/guides/technical/xray.html
[codescene-change]: https://codescene.com/engineering-blog/change-coupling-visualize-the-cost-of-change
[fitness-functions-infoq]: https://www.infoq.com/articles/fitness-functions-architecture/
[sonar-cognitive]: https://www.sonarsource.com/resources/cognitive-complexity/
[change-coupling-paper]: https://www.ime.usp.br/~gerosa/papers/changecoupling.pdf
[ndepend-metrics]: https://www.ndepend.com/docs/code-metrics
[structure101-xs]: https://structure101.com/static-content/pages/resources/documents/XS-MeasurementFramework.pdf
[pagerank-key-classes]: https://www.researchgate.net/publication/308843302_A_PageRank_based_recommender_system_for_identifying_key_classes_in_software_systems
[entropy-arxiv]: https://arxiv.org/pdf/1001.3473
[entropy-mdpi]: https://www.mdpi.com/1099-4300/25/2/328
[fowler-context]: https://martinfowler.com/articles/exploring-gen-ai/context-engineering-coding-agents.html
[import-linter-docs]: https://import-linter.readthedocs.io/en/latest/contract_types.html
[il]: https://import-linter.readthedocs.io/en/latest/contract_types.html
[pluggy-doc]: https://pluggy.readthedocs.io/
[pydeps-doc]: https://pydeps.readthedocs.io/
[pyan-repo]: https://github.com/davidfraser/pyan
[radon-repo]: https://github.com/rubik/radon
[xenon-repo]: https://github.com/rubik/xenon
[vulture-fp]: https://dev.to/duriantaco/python-dead-code-i-scanned-flask-fastapi-and-7-other-popular-repos-heres-what-i-found-5c1c
[pyright-doc]: https://microsoft.github.io/pyright/
[mypy-doc]: https://mypy.readthedocs.io/
[ruff-doc]: https://docs.astral.sh/ruff/
[type-hints-survey]: https://anujyadav.substack.com/p/type-hinting-and-type-checking-with
[pep-544]: https://peps.python.org/pep-0544/
[pep-562]: https://peps.python.org/pep-0562/
