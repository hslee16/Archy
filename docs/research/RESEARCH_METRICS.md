# Architecture-quality metrics: research notes for Python

Survey of architecture-assessment methods that exist beyond the four
sub-metrics archy currently ships (modularity, acyclicity, depth,
equality), evaluated for **practical applicability to Python
specifically**. Intended as input to roadmap discussions; nothing
here is committed.

For the four metrics archy already ships, see
[`SCORING.md`](../SCORING.md). For the concrete near-term roadmap, see
[`FUTURE.md`](../FUTURE.md). This doc is wider than both: it catalogues
the candidate space.

---

## Why a Python-centric survey

archy is Python-only by deliberate design choice
([`FUTURE.md`](../FUTURE.md): "no multi-language support"). The
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
  resolver bug in [`docs/CASE_STUDIES.md`](../CASE_STUDIES.md)).
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
  ([`FUTURE.md`](../FUTURE.md): "acceptable").

These features mean some metrics from the literature translate
cleanly, others need redefinition, and a few are not worth shipping
at all because the false-positive rate on real Python code would
exceed the signal.

---

## 1. Already implemented

For reference, archy ships these and they're documented in
[`SCORING.md`](../SCORING.md):

| Metric         | What it captures                                |
| -------------- | ----------------------------------------------- |
| Modularity     | Newman's Q over greedy community partition     |
| Acyclicity    | `1 - tangle_ratio` (fraction of nodes in SCCs of size ≥ 2) |
| Depth         | Longest path through the SCC condensation      |
| Equality      | `1 - Gini(out_degree)`                          |
| Martin's `I` (instability) | Per-module `Ce / (Ce + Ca)`; exposed per-node in `archy graph --format json` (`compute_instability` in `instability.py`) |
| SDP violation check | Edges from stable to less-stable modules; enabled via `sdp:` in `archy.yaml`, reported by `archy check` (`find_sdp_violations` in `layers.py`) |

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

**Status:** `I` and SDP are **shipped** - `compute_instability` in
`src/archy/instability.py`, exposed via `archy graph --format json`,
and `find_sdp_violations` in `src/archy/layers.py`, wired into
`archy check` (enable with `sdp:` in `archy.yaml`). `A`/`D`/SAP
remain deferred: `A` requires AST work (tree-sitter pass to count
`ABC`/`Protocol` subclasses and `@abstractmethod`), on the roadmap
as part of the cyclomatic-complexity AST work.

**Signal:** Strong for `I` and SDP (confirmed in practice). Weaker
for `A`/`D`/SAP in Python specifically because Python's structural
typing means many "abstractions" never appear as `Protocol`
subclasses at all (e.g., file-like objects passed by convention).
The "ship `I` and SDP first, treat `A`/`D`/SAP as an experiment"
plan turned out right.

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

NetworkX exposes `pagerank`, though as of NetworkX 3.x the
implementation requires `numpy`/`scipy`. Applied to the import
graph, each module's PageRank captures "importance weighted by the
importance of things that depend on me." Used by NDepend's "rank"
for type popularity ([NDepend metrics][ndepend-metrics]); used in
academic work to identify ["key classes"][pagerank-key-classes].

**Python translation:** pure graph computation, language-neutral. One
specific Python quirk: `__init__.py` re-exports inflate PageRank for
package roots that re-export everything (because every importer of a
sub-name pulls in `__init__.py`). archy's planned re-export-aware
resolver ([`FUTURE.md`](../FUTURE.md), already noted) cleans this up.

**Feasibility:** ~15-line hand-rolled power iteration (archy avoids
the numpy dependency `nx.pagerank` now pulls in). Linear per
iteration, fast. Live in `archy_graph_summary`'s `top_pagerank`
field; see [`SPEC_GRAPH_MCP.md`](../SPEC_GRAPH_MCP.md).

**Signal:** Useful as a per-module *diagnostic*, weak as a
graph-level summary. Better than raw in-degree because it weights by
importance recursively (a utility imported only by `__main__` looks
less important than one imported by core modules).

**Fit:** shipped in `archy_graph_summary` as `top_pagerank`. Still
open as a `FUTURE.md` item: surface per-module PageRank in
`archy graph --format json` and in `archy_impact` so CLI and
blast-radius callers get the same diagnostic. Use it for navigation
and diagnostics, not scoring.

---

## 6. Tangle ratio (Structure101)

[Structure101][structure101-xs] frames complexity as two ratios:

- **Fat:** percentage of the codebase inside packages with
  above-threshold dependency density.
- **Tangle:** percentage of the codebase inside cyclic regions.

Tangle is **a percentage of code, not a count of cycles**. archy's
acyclicity score is **already** computed as `1 - tangle_ratio`,
where `tangle_ratio = |nodes in SCCs of size ≥ 2| / N`
(`compute_acyclicity` in `src/archy/score.py`). The earlier
`acyclicity = 1 / (1 + N)` normalization treated one big SCC the
same as one small SCC and understated the problem when 60% of the
codebase was in a single tangled component; the tangle-ratio
replacement is what's running today.

**Python translation:** clean and especially relevant. Python
codebases tend to acquire one of two cycle profiles:

1. *Phantom cycles* from `__init__.py` re-exports (already documented
   in `CASE_STUDIES.md` for FastAPI). These will disappear when the
   re-export-aware resolver lands and Tangle ratio will drop sharply.
2. *Real cycles* from `if TYPE_CHECKING:` workarounds gone wrong (one
   module type-imports another at module top level instead of guarded
   by `TYPE_CHECKING`). Tangle ratio captures these accurately.

**Status:** Shipped. Tangle ratio is computed after SCC
condensation as `|nodes in SCCs of size ≥ 2| / N` and exposed in
`ScoreInputs`; the acyclicity sub-metric is `1 - tangle_ratio`.

**Signal:** Strong, complementary to the prior cycle-count
normalization. The replacement was the smallest-cost improvement on
the list when this section was written.

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
is the de-facto Python standard, and archy computes it natively via
the tree-sitter walker that landed in v0.17 (`src/archy/complexity.py`).
The churn half reuses a one-pass `git log --name-only --format=`
stream rather than the per-file co-change matrix from section 7;
hotspots only needs per-file commit counts, not cross-file
correlations.

One Python specifically: decorators that wrap a function (`@retry`,
`@cache`, `@app.route`) can dramatically increase the *effective*
complexity of a function without changing its CC count. Treat these
as a known limitation rather than try to model them.

**Status:** **Shipped in v0.18.0** as `archy hotspots`
(`src/archy/hotspots.py`). The rank is `cc_sum * commit_count` per
internal module, with zero-CC and zero-churn rows filtered so the
top-K only contains files that score on both axes. The 27-project
`--since` window sweep (`bench/hotspots_sweep.py`,
`bench/hotspots_results.md`) settled the default at full history;
narrower windows collapse the result set on low-activity codebases
(`mkdocs`, `httpx`) while only buying about 25% less recency
contamination on the median project. The `--since` flag is the "what
should I refactor right now" lens.

**Signal:** Very strong, and very actionable. Produces a *prioritized
list* rather than a single number - "refactor these three files
first." Pairs well with the AI-agent loop
(`docs/AGENT_LOOP.md`): "before you start work, here are the
highest-risk files you might be touching."

**Fit:** standalone CLI command (`archy hotspots`) plus the
`archy_hotspots` MCP tool (shipped in v0.19.0) so an agent can read
the ranking without spawning a subprocess. The MCP variant returns
an empty list + a `note` pointing at `archy_high_risk_modules` when
the project isn't under git, rather than raising; the structural
cousin needs no git history and is the natural fallback for the
agent loop.

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
(60% confidence) and at 90% confidence on the full 27-project
benchmark (see [`bench/projects.yaml`](../../bench/projects.yaml)),
captured 2026-05-13:

| Project        |    LOC | Vulture @ 60% | Vulture @ 90% |
| -------------- | -----: | ------------: | ------------: |
| **sqlalchemy** |246,049 |     **1,827** |           415 |
| scikit-learn   |211,188 |           246 |            31 |
| dagster        |202,927 |         1,417 |            18 |
| **django**     |156,666 |     **2,017** |            12 |
| ansible        |135,915 |           949 |            54 |
| pygments       |125,868 |            84 |             5 |
| numpy          |123,753 |           396 |            57 |
| mypy           |113,108 |           208 |            21 |
| setuptools     | 59,153 |           409 |            20 |
| pydantic       | 45,563 |           210 |            22 |
| botocore       | 39,075 |           320 |            17 |
| rich           | 38,515 |            89 |            12 |
| pytest         | 37,089 |           162 |            11 |
| scrapy         | 29,057 |           186 |             7 |
| aiohttp        | 25,800 |           188 |            17 |
| datasette      | 19,946 |           105 |            17 |
| fastapi        | 19,335 |           129 |             8 |
| anyio          | 14,455 |            78 |             2 |
| click          | 11,529 |            32 |             3 |
| flask          |  9,502 |            76 |             7 |
| httpx          |  8,827 |            69 |             3 |
| boto3          |  8,619 |            72 |             6 |
| mkdocs         |  7,084 |            88 |             8 |
| starlette      |  6,584 |            67 |             0 |
| requests       |  6,369 |            54 |             4 |
| archy          |  3,725 |            71 |             0 |
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

The 2,017 / 1,827 figures for Django and SQLAlchemy collapse to
12 / 415 at `--min-confidence 90`, which is itself nowhere near a
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
([`FUTURE.md`](../FUTURE.md) deferred item). Catching layer violations
that hide behind `if TYPE_CHECKING:` is precisely what this enables.

**Feasibility:** annotation coverage is a tree-sitter pass over
`FunctionDef` nodes. Strict-mode pass rate requires running mypy as
a subprocess (heavyweight; user already has mypy configured in many
cases). Annotation coverage is the cheap win.

**Signal:** Strong and uniquely Python (and TypeScript). A project
moving from 30% annotation coverage to 90% has measurably improved
maintainability in a way no graph-level metric will catch.

**Fit:** the original survey rated this a candidate sixth sub-metric
(`typing` axis) or companion stat. **Status: rejected (2026-05), in
both forms.** The empirical study in
[`TYPE_HINT_COVERAGE_EMPIRICS.md`](TYPE_HINT_COVERAGE_EMPIRICS.md)
concluded against axis promotion (independence is the weakest archy has
measured, max `|r| = 0.551`; discriminant validity is contested) *and*
against shipping it as a diagnostic (mypy / pyright own the typing
niche, the signal is not structural, and the "single sensor for
everything" framing dilutes archy's graph-shape focus). See
[`ROADMAP.md`](../ROADMAP.md) "Rejected". The text above is kept
as the pre-study survey rationale, not a live recommendation.

---

## 14. AI-agent-specific framing

archy positions itself as architecture-feedback for AI agents
(`docs/AGENT_LOOP.md`). As of mid-2026 this is no longer entirely
speculative: a small but growing empirical literature directly
validates the structural-graph-feedback thesis for coding agents.
See §14c below for the citations. The §14a and §14b framings
predate that literature and now have empirical grounding.

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

### 14c. Empirical validation from the 2025-2026 coding-agent literature

When this section was first written, the agent-feedback-loop framing
was a positioning bet. As of mid-2026 there are three converging
empirical results worth citing as direct support.

**c.1. The Navigation Paradox (Feb 2026)**
([arxiv:2602.20048][nav-paradox]). The paper introduces an
MCP-based graph navigation tool (CodeCompass) shaped almost
identically to archy's `archy_graph_*` family: static AST extraction
of structural edges, queryable from an MCP client. The headline
finding is that **larger LLM context windows do not eliminate the
need for structural navigation**: failure shifts from retrieval
capacity to *navigational salience*, where architecturally critical
but semantically distant files are absent from the model's
attention. The implication for archy is that `archy_graph_focus`
(bounded local neighborhood with edge metadata) and `archy_impact`
(blast radius) are not redundant with a long-context model; they
solve a distinct failure mode the model can't budget its way out
of. This is direct external validation of archy's MCP surface and
its category, not just its individual metrics.

**c.2. LocAgent ablation (ACL 2025)**
([aclanthology:2025.acl-long.426][locagent]). The paper builds a
heterogeneous code graph with four edge types - **invoke**, import,
inherit, contain - and ablates each. Invoke edges contribute the
most to LLM-agent code-localization accuracy, more than imports.
Removing the graph traversal tool entirely causes "a more
significant decrease" at function-level localization. This is
direct evidence that **archy's call-graph roadmap item is the
highest-impact addition for agent-facing positioning**, not just a
"nice to have" - the missing edge type is precisely the one with
the strongest ablation contribution in an agent context.

**c.3. Coding-agent failure-mode literature (2026)**. Cross-source
characterization of agent failures yields a small recurring set of
patterns: **scope drift** (agent edits adjacent code, introduces
subtle regressions), **context exhaustion** (agent runs out of
attention before finishing), **deprecated-pattern propagation**
(agent copies dated patterns it sees nearby), **half-implemented
features** (agent declares done prematurely), and **cross-file
reasoning failures** (agent fails to connect distant files even when
they're cited). Sources include Columbia DAPLab's "9 Critical
Failure Patterns" ([daplab][daplab-9-patterns]), Anthropic's
"Effective harnesses for long-running agents"
([anthropic-harnesses][anthropic-harnesses]), and the Stack Overflow
synthesis ([stackoverflow-bugs][so-bugs-coding-agents]). Mapping
these failure modes to archy capabilities:

| Failure mode                          | What archy can predict / detect                                                                                  | Status                                                              |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Scope drift causes regression         | Did this PR introduce new cycles / layer violations / score drops?                                               | **Shipping** (`archy_snapshot` + `archy_diff`).                     |
| Hidden cross-file impact              | Blast radius, weighted by propagation cost                                                                       | **Partially shipping** (`archy_impact` returns reverse-closure set; propagation-cost weighting on the roadmap). |
| Edit lands in fragile area            | Per-module risk score (propagation cost × instability × fan-in)                                                  | **Shipping** (`archy_high_risk_modules` / `edit_risk`, v0.14.0).                 |
| Agent should read X first             | Top-N by PageRank / fan-in                                                                                       | **Shipping** (`archy_graph_summary`).                                |
| Deprecated-pattern propagation        | Out of scope for archy (handled by ruff / mypy / pattern lints).                                                 | **Not shipping; not planned.**                                       |
| Edit affects a hotspot                | CC × per-file churn                                                                                              | **Shipping** (`archy hotspots` CLI v0.18.0; `archy_hotspots` MCP tool v0.19.0).               |
| Cross-file reasoning failure          | Bounded subgraph navigation, edge-level metadata                                                                 | **Shipping** (`archy_graph_focus` with import line numbers).         |

**Implication for archy's roadmap.** The three top-priority
additions identified here, in order of evidence weight, have all
since shipped: (1) call graph as a second edge type (LocAgent
ablation §14c.2; shipped v0.16.0); (2) NCCD/propagation cost as a
diagnostic and as a weighting input for `archy_impact` (MacCormack
literature plus §c.3 mapping above; shipped v0.13.3); (3) a
per-module risk composite that surfaces the "navigational salience"
the Navigation Paradox paper §c.1 names as the residual failure mode
after large context windows (shipped v0.14.0 as
`archy_high_risk_modules` / `edit_risk`). Detailed roadmap entries in
[`FUTURE.md`](../FUTURE.md). The §c.4 paper below
adds two more, both low-cost and both reusing the existing layer
machinery: convention-based layer inference and a layer-presence
check.

**c.4. Constraint Decay (May 2026)**
([arxiv:2605.06445][constraint-decay]). The paper asks whether LLM
coding agents can generate backend systems that satisfy *structural*
constraints (architecture, database, ORM) rather than merely passing
functional tests, across 80 greenfield and 20 feature-implementation
tasks built from a shared OpenAPI spec (RealWorld Conduit). The
headline result is **Constraint Decay**: as structural requirements
accumulate from baseline (L0) to fully specified (L3), assertion pass
rates fall ~30 percentage points (the strongest configuration drops
95.6% to 78.6%), and the single most expensive *architectural*
constraint is **Clean Architecture layering at -9.1±1.6 pp** in
isolation (specifying a database engine costs more, -19.3 pp; ORM
adherence is near-zero, -0.6 to -1.5 pp). The failures persist on
feature-implementation tasks against existing codebases, so they are
not an artifact of building from scratch. Crucially, the agents
received static prompts describing the architecture but **no dynamic
course-correction** on violations during generation. The study's
ground-truth oracle is a static verifier checking (1) **layer
presence** (at least 3 of 4 canonical layers - routes, services,
repositories, models - exist as separate directories, with an alias
map so `routes`/`handlers`/`routers`/`views` all count as one layer)
and (2) **dependency direction** (a file in a lower-rank layer
importing from a higher-rank layer is a violation).

Two implications for archy, both sharper than the earlier citations:

- The verifier's dependency-direction rule **is** archy's `forbid`-rule
  check (`find_violations` in `src/archy/layers.py`), and the
  transitive version is `archy contracts`. The paper independently
  reinvented archy's core check as its measurement instrument, which
  validates the *category*, not just individual metrics: archy already
  ships the harder version of the thing the study had to build, and
  the quantified -9.1 pp is the cleanest external evidence yet that the
  architectural feedback loop has measurable value at generation time.
- The study's explicit gap, "no dynamic course-correction", is exactly
  archy's MCP loop. But it surfaces two capabilities archy does *not*
  yet have, both motivated directly. **Convention-based layer
  inference**: the verifier's alias map lets it check any repo with
  zero config, whereas archy requires a hand-written `layers:` block,
  so a greenfield repo or an agent on an unfamiliar codebase gets no
  layering feedback at all. **Layer-presence check**: archy gates
  forbidden edges but never asserts that an expected layer *exists*, so
  the degenerate single-file solution the paper says agents produce
  passes archy's layer gate silently. Both become roadmap items in
  `FUTURE.md`. The v0.15.0 lesson (archy.yaml-to-Forbidden auto-
  translation was demoted because it manufactured permanent CI false-
  positives it could not whitelist) constrains the design: inference
  ships as an **advisory, agent-facing report, not a CI gate**, in the
  same diagnostic-not-axis spirit as `archy dsm`.

The paper's largest failure cluster - data-layer defects (bad query
composition 25.5%, ORM runtime violations 21.2%, auth misconfiguration
22.6%; logic errors are ~71% of all failures) - is semantic, not
structural, and stays out of archy's scope per the anti-goals (no
replacement of linters / type checkers). The structural-leak variant
(forbidding DB/ORM imports above the repository layer) was considered
and rejected as a built-in because it would require maintaining a
framework-specific DB/ORM package allowlist; a user who has defined
layers can already express it as their own `forbid` rule.

*Reception and caveats* ([HN discussion][constraint-decay-hn]).
Several threads sharpen archy's reading. **Supporting:** the top
architecture comment proposes exactly archy's model - adopt an
ArchUnit-style framework to "spoon feed the LLM what exactly it's
doing wrong," treating architectural rules as *executable constraints
rather than prose guidelines* (archy is that, for Python, over MCP);
a second thread notes agents do markedly better when a *deterministic
feedback loop* like a compiler lets them iterate, which is the
structural analog archy provides for the import graph; a third names
a failure the paper does not measure but archy is well-placed to fix,
**cross-session decay**: "architectural rules an agent wrote down on
Monday don't reach the agent making the next change on Tuesday." A
persisted `.importlinter` / `archy.yaml` plus an always-on MCP server
*is* the durable cross-session architecture memory that prose
CLAUDE.md notes are not. One commenter correctly counters that
Markdown prompts *also* persist across model generations (and survive
dependency/API churn better than code), so durability alone is not the
wedge: the distinction is that prose persists but is never *enforced*,
whereas a config is persisted **and checked deterministically every
session**. Durable-and-enforced is the claim, not durability; and that
is the part independent of model strength. **Tempering:** the single
top comment flags that **frontier models were not fully tested, for
cost reasons**, so the absolute pass-rate numbers are directional, not
definitive; others argue the effect may partly rebrand known
long-context degradation ("context rot") and that some of it erodes as
models improve. Net: treat the -9.1 pp layering penalty as *evidence
the feedback loop has value, not as a fixed constant*, and lean
hardest on the cross-session-persistence argument.

**A sharper mechanism than the paper states: aspiration vs consequence
constraints.** The thread's most load-bearing observation is that
agents *ignore* aspirational constraints ("be modular", "follow Clean
Architecture") but *obey* brief, precise, preventative rules ("a file
in this layer must not import that one") "because it is brief,
unambiguous, and precise." This is the mechanism behind the paper's
own -9.1 pp layering penalty: Clean Architecture, as handed to the
agent in prose, *is* an aspiration, so it decays; the identical
constraint expressed as a directional `forbid` edge is a consequence,
and consequences get obeyed. The implication sharpens the #122/#135
inference design directly: the inferred layering report must emit
**consequence-shaped negative rules** (resolved `X must not import Y`
pairs), never aspirational prose, or it reproduces the exact failure
the paper measures. It also yields the cleanest one-line statement of
archy's value the docs currently lack: **archy converts architectural
aspirations into checkable consequences.**

Two further threads converge on *timing* and *division of labor*.
**Calcification** (one commenter, 2B tokens on a C compiler):
architectural patterns self-reinforce once they dominate the context,
so constraints applied up front stick while constraints retrofitted
after the agent has calcified a different pattern do not. This is
independent support for the timing of archy's loop (risk/affected
*before* edits; the pre-edit `archy install --hooks` framing) and
argues for surfacing the dominant existing pattern at task start.
**Single-objective optimization**: a separate commenter frames
constraint decay as the impossibility of optimizing two objectives at
once (functional + non-functional); the design consequence is that
offloading the *structural* objective to an external deterministic
checker frees the agent's budget for the *functional* one, which is
precisely archy's role.

**Two features these threads motivate, both larger than they first
look** (filed as Deferred epics in [`FUTURE.md`](../FUTURE.md), not Next
items):

- *Positive exemplar surfacing.* The thread's most-repeated practical
  claim is that showing the agent a good example beats describing the
  rule ("exemplar-based constraints proved phenomenally powerful"; a
  separate report of ~75-80% style-match when the agent could see how
  a pattern was already implemented). archy today is purely negative -
  it ranks violations (`archy check`) and fragile modules
  (`archy_high_risk_modules`); the inverse, ranking the *cleanest*
  existing module as a copy-me template, is the highest-payoff
  technique in the thread and has no roadmap item. It is **not small**,
  and the obvious framing (build a corpus of patterns) is a trap; the
  design question - corpus vs project-relative, and what archy can
  legitimately claim given it only sees the graph - is worked through
  in §14c.5 below.
- *Rule-rot / constraint-staleness detection.* One commenter names a
  failure the paper does not measure: agents obey constraints but
  cannot judge when a constraint itself should *change*, so a stale
  rule gets satisfied by inelegant indirection rather than revised. The
  mirror image of constraint decay is rule rot, and archy can see the
  symptom (a `forbid` rule carrying many `ignore_imports` exceptions,
  or a layer boundary accumulating indirection edges that exist only to
  route around it - the psycopg-through-db-engine shape from the v0.15
  lesson). This is **also large**: distinguishing "the rule is stale"
  from "the code is wrong" is the hard part and needs the same FP-rate
  discipline as the dead-code study (§12) before anything ships.

### 14c.5. Positive exemplar surfacing: why it is project-relative, not a corpus

Of the two §14c.4 epics, exemplar surfacing has the larger design
trap, and it is worth resolving on paper before any code. The naive
reading - "ship a curated corpus of good patterns and best practices
the agent can copy" - is wrong for archy on five independent grounds,
and the literature is unusually clear about each.

**1. The pattern space is combinatorial and the implementations vary
without bound.** Design-pattern-detection research has a standing
result that "patterns are only a guideline ... each pattern can be
implemented in various ways," which is exactly why classical static
detectors "struggle with the complexity and variability of real-world
pattern implementations" and the field has moved to LLM-based
detection ([LLM-Based Design Pattern Detection][dp-llm],
`arxiv:2502.18458`). Layer in per-language idiom (the
[Pythonic-idioms refactoring][pythonic-idioms] work, `arxiv:2207.05613`,
enumerates *nine* Python idioms and treats that as a research
contribution) and a "corpus of patterns" is unbounded and contested
before it ships. The user's intuition here is correct and
literature-backed.

**2. Curated example/template catalogs rot, and the rot is the
dominant failure mode even for teams whose whole job is to maintain
them.** The platform-engineering "golden path / paved road" literature
([Spotify golden paths][spotify-golden], [The New Stack][newstack-paths])
is the closest thing to a working exemplar corpus in industry, and its
catalogued failure modes are *railroads* and *golden cages*: templates
whose "documentation is outdated and refers to tools that no longer
exist," left "to rot" once the platform team is reassigned, which
developers then bypass by "copying YAML from old repos." A corpus archy
shipped would rot faster, because archy is one tool, not a staffed
platform team. By contrast, a **project-relative exemplar is recomputed
from the live repo on every call and is therefore current by
construction** - it cannot go stale.

**3. The "find similar code" half is already owned, and doing it badly
actively hurts.** In-repo semantic retrieval is the core competence of
Cursor (local index + custom retrieval, "writes code that matches your
style"), Cody (org-wide semantic search, cites sources), Copilot, and
the embeddings-RAG stack generally. And the RAG-for-code literature is
blunt that naive similarity retrieval is *net-negative* if quality
isn't controlled: retrieved similar code "often introduces noise,
degrading results by up to 15%" ([What to Retrieve...][what-to-retrieve],
`arxiv:2503.20589`), there is a "[When More Retrieval Hurts][more-hurts]"
result (`arxiv:2511.05302`), and the consistent finding is that ICL
performance "is highly dominated by the quality of selected examples,"
with diversity-aware selection (MMR) beating pure similarity. archy
trying to be a retriever would be redundant *and* off-positioning (it
is not a semantic/embedding tool).

**4. A corpus is a different product and breaks three archy
anti-goals.** A curated pattern/template library is scaffolding
(Backstage), a linter/idiom-fixer (ruff, the Pythonic-idioms tool), or
a pattern catalog (refactoring.guru) - none of which is a static
graph-shape sensor. Shipping one would violate "no replacement of
linters," "not the single source of truth for codebase health," and
the graph-shape-sensor focus. It also imposes *external* taste on the
user's repo, whereas archy's whole stance is to judge the repo on its
own structure.

**5. Nobody ships the thing archy could uniquely ship.** The gap-check
turned up knowledge-graph repo-level code-gen ([KG-based code
gen][kg-codegen], `arxiv:2505.14394`) and noise-reduction-by-pruning
work, but no tool that *ranks an exemplar by structural quality*. That
is precisely the seam the ICL literature says matters most and the
retrieval tools leave open.

**The resolution.** archy should not enumerate patterns or normalize
against the universe of "best practice." It should treat **"pattern" =
a structural peer group that already exists in this repo** (siblings in
a layer, a directory, a graph community, or a naming convention - all
detectable from the graph archy already builds, and from the #122
layer-inference machinery) and **"best practice" = best structural
health relative to those peers** (low `edit_risk`, respects layer
direction, not in a cycle, low propagation cost, healthy local Newman
Q, moderate `cc_mean`), normalized as a within-group percentile rather
than against any external baseline. The combinatorial-explosion and
language-idiosyncrasy problems *dissolve* because archy never names a
pattern: it points at the healthiest instance of whatever the repo
already does.

This reframes the role precisely and complementarily: **archy is the
quality ranker, not the retriever.** The IDE / RAG layer supplies
*similarity* (which existing files are relevant to this task); archy
supplies the *structural-quality* filter the ICL literature says
dominates outcomes (of those candidates, which one is the cleanest to
copy). The honest scope limit is that structural health is *necessary,
not sufficient* - a module can be graph-clean but a poor semantic
example - so archy's claim must be narrow: "the structurally cleanest
peer," never "the best example, period." Semantic correctness stays
with the agent. With that framing the feature is corpus-free,
never-stale, on-positioning, and a genuine gap; it remains an epic only
because it is gated on the per-module score breakdown (#129) and needs
a bench validation that "structurally cleanest peer" actually
correlates with "useful exemplar," plus the clean-but-trivial guard (a
one-function module must not win by default).

[nav-paradox]: https://arxiv.org/html/2602.20048v1
[constraint-decay]: https://arxiv.org/html/2605.06445v1
[constraint-decay-hn]: https://news.ycombinator.com/item?id=48256912
[locagent]: https://aclanthology.org/2025.acl-long.426/
[daplab-9-patterns]: https://daplab.cs.columbia.edu/general/2026/01/08/9-critical-failure-patterns-of-coding-agents.html
[anthropic-harnesses]: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
[so-bugs-coding-agents]: https://stackoverflow.blog/2026/01/28/are-bugs-and-incidents-inevitable-with-ai-coding-agents/
[dp-llm]: https://arxiv.org/pdf/2502.18458
[pythonic-idioms]: https://arxiv.org/pdf/2207.05613
[spotify-golden]: https://engineering.atspotify.com/2020/08/how-we-use-golden-paths-to-solve-fragmentation-in-our-software-ecosystem
[newstack-paths]: https://thenewstack.io/paved-roads-golden-paths-guardrails-and-railroads/
[what-to-retrieve]: https://arxiv.org/abs/2503.20589
[more-hurts]: https://arxiv.org/pdf/2511.05302
[kg-codegen]: https://arxiv.org/html/2505.14394v1

---

## 15. The Python tooling landscape archy interoperates with

Worth being explicit about what already exists, since archy should
complement rather than replicate. Each is a single-purpose tool;
archy's value is the integrated graph + score + governance surface.

| Tool                       | Scope                                       | archy relationship                                              |
| -------------------------- | ------------------------------------------- | --------------------------------------------------------------- |
| [import-linter][il]        | Module-level architecture rules              | Closest cousin. archy.yaml has Layers; adding Forbidden + Independence (section 10) closes the gap. |
| [pydeps][pydeps-doc]       | Module-level visualization                   | archy's `archy graph` is the same scope. archy adds scoring, governance, MCP. |
| [pyan][pyan-repo]          | Function-level call graph                    | Adjacent to archy's v0.16 call-graph diagnostic (section 16). pyan tracks function-level invoke edges with heuristic class-attribute resolution; archy's call extraction is module-level and import-alias-only, accepting lower coverage for a lower false-positive rate. |
| [radon][radon-repo]        | Per-function CC + maintainability index      | archy's planned CC pass would replicate this. Worth reading radon's AST visitor for inspiration. |
| [xenon][xenon-repo]        | radon-based CI gate                          | archy's `--strict` gate is a generalization across multiple metrics, not just CC. |
| [vulture][vulture-fp]      | Dead-code detection                          | False-positive rate (section 12) makes this hard to beat without runtime-coverage input. |
| [pyright][pyright-doc] / [mypy][mypy-doc] | Type checking                | Type-hint coverage (section 13) builds on these without re-implementing them. |
| [ruff][ruff-doc]           | Lint + format                                | Orthogonal - ruff is rule-based on individual files; archy reasons about the dependency graph. |

---

## 16. Call edges (LocAgent invoke edges)

archy v0.16.0 ships call edges as a second edge type, attached as
per-edge `kinds: tuple[str,...]`, `call_lines: tuple[int,...]`, and
`call_count: int` attributes on the same `nx.DiGraph` that already
carries the import edges. Call-only edges (kinds=('call',)) appear
when calls resolve to a deeper internal submodule than the import
itself - `import pkg; pkg.sub.foo()` adds a call edge to `pkg.sub`
on top of the existing import edge to `pkg`. The motivation is
LocAgent's (ACL 2025, [aclanthology:2025.acl-long.426][locagent])
ablation finding that invoke edges contribute *more* to LLM-agent
code-localization accuracy than imports, framed in
[`RESEARCH_METRICS.md §14c.2`](RESEARCH_METRICS.md) as the missing
edge type with the strongest measured contribution.

**Resolution strategy.** Static. Tree-sitter `(call) @call` query
extracts the leftmost identifier of each call expression plus the
attribute chain (`mod.sub.foo()` → head=`mod`, chain=`('sub','foo')`).
The head is looked up in a per-file alias table derived from the
file's import statements; if the lookup succeeds, the chain segments
(excluding the trailing function name, which is never itself a
module) are walked against the internal-qualname set to find the
longest internal prefix. `self`/`cls`/`super` calls and a small list
of common builtins are skipped at extraction time.

**Empirical orthogonality.** 27-project bench captured 2026-05-14,
SHAs pinned in [`bench/projects.yaml`](../../bench/projects.yaml):

| signal           |   r vs calls_per_edge |
| ---------------- | --------------------: |
| modularity       |                +0.148 |
| acyclicity       |                +0.208 |
| depth            |                -0.062 |
| equality         |                +0.212 |
| propagation_cost |                -0.229 |

All five correlations sit well below the OECD `|r| > 0.7` redundancy
threshold (max absolute is 0.229). Call density is the most
orthogonal new signal archy added between v0.2.0 and v0.17 (cc_mean
later beat it at max `|r| = 0.197`): every existing axis pair sits at
moderate-or-stronger correlation (median `|r| ≈ 0.45`), while every
pair *involving* `calls_per_edge` sits at `|r| ≤ 0.23`.

**Score-axis promotion: reviewed and rejected (2026-05).** This section's
original language called `calls_per_edge` "a strong candidate for
promotion to a score axis." A deliberate review after the v0.20 cc_mean
promotion concluded the opposite: see [`AXIS_REVIEW.md`](AXIS_REVIEW.md).
The short version is that orthogonality is necessary but not sufficient,
and `calls_per_edge` fails three of the four OECD composite-indicator
criteria (directionality is shape-driven not quality-driven, no canonical
positive refactoring exists, discriminant validity is weak). The call
data itself is still genuinely useful, but as a refinement of the
existing modularity axis (call-weighted Newman Q) and as agent
navigation data (already shipped via `archy_graph_*` MCP tools), not as
a new score axis.

**Raw distribution.** Calls-per-edge across the bench:

| project      | edges | call_edges | total_calls | calls/edge |
| ------------ | ----: | ---------: | ----------: | ---------: |
| numpy        |  1342 |        988 |       52044 |      52.68 |
| pygments     |   834 |        331 |        5926 |      17.90 |
| mkdocs       |   177 |        119 |        1425 |      11.97 |
| mypy         |  1105 |        716 |        6872 |       9.60 |
| scikit-learn |  3866 |       3083 |       25869 |       8.39 |
| sqlalchemy   |  2550 |       1085 |        7970 |       7.35 |
| datasette    |   180 |        111 |         672 |       6.05 |
| fastapi      |   114 |         51 |         272 |       5.33 |
| click        |    60 |         38 |         167 |       4.39 |
| httpx        |    87 |         36 |         155 |       4.31 |
| requests     |    73 |         41 |         174 |       4.24 |
| anyio        |   158 |         78 |         306 |       3.92 |
| setuptools   |   592 |        400 |        1520 |       3.80 |
| aiohttp      |   312 |        107 |         403 |       3.77 |
| dagster      |  6273 |       2872 |       10540 |       3.67 |
| pydantic     |   496 |        264 |         959 |       3.63 |
| botocore     |   257 |        207 |         714 |       3.45 |
| ansible      |  2145 |       1395 |        4448 |       3.19 |
| django       |  3274 |       1919 |        5969 |       3.11 |
| pytest       |   374 |        193 |         549 |       2.84 |
| rich         |   421 |        322 |         886 |       2.75 |
| archy        |    30 |         27 |          74 |       2.74 |
| msgspec      |    20 |          9 |          24 |       2.67 |
| boto3        |    71 |         57 |         142 |       2.49 |
| flask        |    94 |         36 |          88 |       2.44 |
| scrapy       |   858 |        354 |         762 |       2.15 |
| starlette    |   114 |         60 |         116 |       1.93 |

Two qualitative observations:

1. **Scientific Python tops the distribution.** numpy at 52.68
   calls/edge is an outlier driven by heavy intra-package function
   dispatch (every public ndarray method routes through the same
   handful of internal modules). scikit-learn and mkdocs sit in the
   8–12 band for the same reason. The "shape" of these codebases -
   small core, broad call surface against it - is exactly what the
   call signal captures and the import signal misses.
2. **Plugin/registry shapes bottom the distribution.** starlette,
   scrapy, flask, and boto3 sit at < 3 calls/edge: their internal
   structure is mostly attribute access on auto-generated or
   registry-built objects rather than direct function calls. The
   import graph picks up the structural coupling; calls add little
   on top.

**Known limitations.** Call resolution is alias-only: no class /
attribute-assignment tracking. `obj = SomeClass(); obj.method()`
resolves the call to the alias target of `obj`, which is only set
if `obj` came from an import (rare). Function returns (`f().g()`)
drop the outer call. Decorator-renamed callables resolve to the
decorator's target, not the decorated function's defining module.
These are accepted false negatives, matching LocAgent's static
extraction approach; the alternative is pyan-style heuristics with
the false-positive rate they imply.

**Edge-count shifts on upgrade.** A small fraction of projects show
modest edge-count growth in the v0.16 bench vs v0.15 because
call-only edges now appear (e.g., numpy 1192 → 1342, +13%). The
score numbers move by ≤0.01 on every project; the qualitative
ordering is unchanged. Trend histories in `.archy/history.jsonl`
remain comparable to within tolerance, but the cleanest signal is
a `--record` checkpoint immediately after the upgrade.

---

## 17. Cyclomatic complexity per function (McCabe 1976)

archy v0.17.0 shipped per-function McCabe cyclomatic complexity as a
diagnostic. **Promoted to a score axis in v0.20.0** as
`complexity = 1 - clamp((cc_mean - 1) / 5, 0, 1)`; the divisor was
widened to `/8` in v0.23.0 after real-world repos with
`cc_mean in [6, 9)` (validator/parser-heavy backends) were zeroing the
entire geomean on a single axis. Below the small-project threshold
(< 20 functions) the axis returns 1.0 vacuously since `cc_mean` is
statistically unstable on tiny inputs. The normalization, anchor
points, and the score-shape-versioning implications are in
[`SCORING.md`](../SCORING.md). The orthogonality data below is what
justified the original promotion.

Implementation in `src/archy/complexity.py`: a tree-sitter
walk over `(function_definition)` nodes counts branch-creating child
nodes (excluding descendants of nested `function_definition` /
`class_definition` so each function carries only its own branches).
The counted node types: `if_statement`, `elif_clause`, `for_statement`,
`while_statement`, `except_clause`, `case_clause`,
`conditional_expression`, `boolean_operator`, plus comprehension
`for_in_clause` / `if_clause`. `assert_statement` is excluded to match
radon's default-mode behavior (assert can be compiled out at `-O`).

Per-function rows roll up to per-module aggregates on each internal
node (`function_count`, `cc_sum`, `cc_max`, `cc_mean`) and to
project-wide aggregates on `archy score`'s `inputs` (`function_count`,
`cc_total`, `cc_max`, `cc_mean`). v0.17.0 shipped diagnostic-first
(same MacCormack v0.13.3 / call edges v0.16.0 precedent); v0.20.0
promoted `cc_mean` to the fifth score axis (`complexity`) after the
27-project bench showed max `|r| = 0.197` orthogonality against the
existing four axes.

**Empirical orthogonality.** 27-project bench captured 2026-05-14,
SHAs pinned in [`bench/projects.yaml`](../../bench/projects.yaml):

| signal           |  r vs cc_mean |
| ---------------- | ------------: |
| modularity       |        -0.149 |
| acyclicity       |        -0.082 |
| depth            |        +0.064 |
| equality         |        -0.110 |
| propagation_cost |        +0.113 |
| calls_per_edge   |        -0.197 |

All six correlations sit well below the OECD `|r| > 0.7` redundancy
threshold (max absolute is 0.197). `cc_mean` is the **most orthogonal
new signal archy has ever measured**: more orthogonal than v0.16's
call density (max `|r| = 0.229`) and substantially more so than any
existing axis pair (median `|r| ~ 0.45`). This is what justified
the v0.20.0 promotion to a 5th score axis (initially `1 - clamp((cc_mean - 1)
/ 5, 0, 1)`, widened to `/8` in v0.23.0). The alternative path - redesigning the equality axis to
use `gini(per_function_cc)` instead of `gini(out_degree)` - is still
on the roadmap; the v0.20 promotion is additive (5th axis) rather than
a replacement, so the equality redesign can land later without
needing to undo this work.

**Raw distribution.** cc_mean across the bench:

| project      | functions | cc_mean | cc_max |
| ------------ | --------: | ------: | -----: |
| msgspec      |        63 |    5.33 |     86 |
| ansible      |     4,925 |    4.42 |    127 |
| datasette    |       798 |    4.37 |     98 |
| mypy         |     6,485 |    4.04 |     79 |
| archy        |       157 |    3.73 |     13 |
| pygments     |       936 |    3.66 |     98 |
| fastapi      |       296 |    3.63 |     41 |
| pydantic     |     1,864 |    3.62 |     77 |
| rich         |       912 |    3.36 |     49 |
| click        |       544 |    3.26 |     48 |
| requests     |       267 |    3.22 |     21 |
| django       |     9,561 |    3.04 |     94 |
| pytest       |     2,010 |    2.94 |     37 |
| setuptools   |     3,811 |    2.91 |    340 |
| httpx        |       446 |    2.75 |     46 |
| aiohttp      |     1,497 |    2.72 |     91 |
| flask        |       388 |    2.59 |     23 |
| dagster      |    10,381 |    2.58 |     96 |
| scikit-learn |    10,841 |    2.52 |     75 |
| starlette    |       498 |    2.51 |     17 |
| scrapy       |     1,715 |    2.49 |     19 |
| sqlalchemy   |    11,480 |    2.45 |     73 |
| botocore     |     2,296 |    2.42 |     25 |
| numpy        |    11,283 |    2.15 |    181 |
| boto3        |       375 |    2.11 |     12 |
| anyio        |     1,051 |    2.03 |     20 |
| mkdocs       |     1,277 |    1.77 |     29 |

Qualitative observations:

1. **Top and bottom are shape-driven, not size.** msgspec (5.33 on 63
   functions) and ansible (4.42 on 4,925) both top the list; mkdocs
   (1.77) and anyio (2.03) bottom it. Project size doesn't explain
   either end; the underlying coding style does. Test-runner /
   inventory / DSL-heavy code (ansible, datasette, mypy) carries
   more branching per function; plugin-host code (mkdocs) carries
   less because most functions are thin registration calls.
2. **cc_max can be wildly out of line with cc_mean.** setuptools has
   cc_max=340 (one extreme function) and cc_mean=2.91 (typical
   restraint elsewhere). numpy at cc_max=181, pygments at 98 - all
   driven by single dispatcher / parser functions. cc_mean is the
   stable signal; cc_max is a hotspot pointer rather than a project
   health number. `archy hotspots` (v0.18) ended up ranking on
   `cc_sum` rather than `cc_max` so a file with twenty CC-7
   functions outranks one with a single CC-15 function - the
   refactor-priority signal is breadth-of-complexity, not the worst
   single function. The `setuptools` case (cc_max=340 from one
   dispatcher, cc_mean=2.91 elsewhere) is the empirical reason: a
   `cc_max`-based ranking would surface that single function on
   every run and bury everything else.
3. **CC is uncorrelated with size at the project level.** numpy at
   11,283 functions and msgspec at 63 sit at opposite ends of the
   `cc_mean` distribution. This is what makes the signal useful: it
   doesn't just re-encode "how big is the codebase."

**Known limitations.** assert is not counted (radon-default). `try`,
`else`, `finally`, `with`, `async with` are not branches. Cognitive
complexity (Sonar / Campbell 2017) is NOT computed - it needs
nesting-depth bookkeeping the single-pass walker doesn't support; a
follow-up using the same `function_definition` AST surface is cheap
but didn't land in v0.17 because the call-graph PR established the
precedent that diagnostic-first means one signal at a time, validated
on the bench before bundling. Same status applies to type-hint
coverage. Lambda expressions don't get their own FunctionComplexity
row, but their internal branches do count for the containing function
(consistent with radon, inconsistent with pyan).

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
| Tangle ratio                              | High   | Trivial| **Shipped** (acyclicity = 1 - tangle_ratio) | ✓ shipped: `score.compute_acyclicity` | **Shipped** |
| Call edges (LocAgent invoke edges)        | High   | Medium | **Shipped** as diagnostic (kinds/call_lines/call_count per edge); follow-up promotion to score axis pending design choice | ✓ orthogonal: max `\|r\| = 0.229` against 5 existing signals on 27-project bench | **Shipped (diagnostic)** |
| Cyclomatic complexity per function        | High   | Medium | **Shipped as a score axis in v0.20**; v0.23 widened divisor: `complexity = 1 - clamp((cc_mean - 1) / 8, 0, 1)`; per-module function_count/cc_sum/cc_max/cc_mean still surfaced as diagnostics | ✓ orthogonal: max `\|r\| = 0.197` against 6 existing signals on 27-project bench | **Shipped (score axis, v0.20; recalibrated v0.23)** |
| Reflexion: Forbidden + Independence       | High   | Low    | Check rule          | -                | **Yes**   |
| NCCD / ACD / propagation cost (one axis)  | High   | Low    | **Score axis**      | ✓ orthogonal to depth (r=0.000) on 9-lib benchmark | **Yes** |
| Type-hint coverage                        | High   | Low    | -                   | ✓ rejected 2026-05: independence weakest measured (max `\|r\| = 0.551`), niche owned by mypy/pyright ([`TYPE_HINT_COVERAGE_EMPIRICS.md`](TYPE_HINT_COVERAGE_EMPIRICS.md)) | **No** (axis or diagnostic) |
| Cognitive complexity                      | Medium | Trivial| Sub-stat (free with CC) | -            | **Yes (free)** |
| Hotspots (CC × per-file churn)            | High   | Medium | **Shipped** as `archy hotspots` (v0.18.0): `cc_sum * commit_count` per internal module, single `git log --name-only` pass, zero-component rows filtered; `--since` window default settled at full history via the 27-project sweep in `bench/hotspots_results.md` | ✓ window choice empirically validated: median J(full, 12mo) = 0.60, J(12mo, 6mo) = 0.74, stale_full_frac = 0.25 on the bench | **Shipped (diagnostic)** |
| Martin's `I` + SDP-violation rule         | Medium | Low    | Sub-stat + check rule | ✓ shipped: `instability.py`, `layers.find_sdp_violations`, surfaced in `archy graph --format json` and `archy check` | **Shipped** |
| PageRank per module                       | Medium | Low    | Sub-stat (diagnostic) | -              | **Yes**   |
| Core/periphery size                       | Medium | Trivial| Sub-stat (diagnostic) | -              | **Yes**   |
| Reflexion: Absences                       | Medium | Medium | Check rule          | -                | Defer     |
| Cross-file co-change (logical coupling)   | Medium | High   | Standalone command  | -                | Defer (skip if hotspots ships) |
| Martin's `A` / `D` / SAP                  | Low    | Medium | -                   | -                | **No** (Python translation murky) |
| Redundancy - duplicate functions          | Medium | Medium | Advisory list       | -                | Maybe     |
| Redundancy - dead functions               | Low    | Medium | -                   | ✓ FP rate confirmed: vulture finds 10–2,017 issues per project, ~all FPs from framework patterns | **No** |
| Graph entropy                             | Low    | Trivial| -                   | -                | **No**    |

---

## Suggested ordering

The validation results clarify the order considerably. Group A is
"essentially free, ship before the call-graph PR." Group B requires
new infrastructure (CC AST pass, git mining). Items below are
additive unless marked **Replace**.

### Group A - pre-call-graph, low cost

1. **Tangle ratio** - *Done.* Shipped: `acyclicity = 1 - tangle_ratio`
   in `src/archy/score.py` (`compute_acyclicity`); `tangle_ratio`
   exposed as a diagnostic in `ScoreInputs`.
2. **Reflexion: Forbidden + Independence contracts** in
   `archy.yaml`. Closes the gap with import-linter; purely additive
   to `archy check`. No score impact.
3. **NCCD / ACD / propagation cost** - *Add* as a sixth score axis.
   Validated to be orthogonal to depth (Pearson r=0.000 on the
   9-library benchmark), so it earns its place in the geometric
   mean. Note: adding a sixth axis shifts absolute scores; document
   the change.
4. **Martin's `I` per-module + SDP-violation check rule.** *Done.*
   Shipped in `src/archy/instability.py` and
   `src/archy/layers.find_sdp_violations`; surfaced in
   `archy graph --format json` and `archy check` (enable via `sdp:`
   in `archy.yaml`).
5. **PageRank per module + core size.** Diagnostics only; expose in
   `archy graph --format json` and `archy_impact` output.

### Group B - depends on AST or git infrastructure

6. **Per-function cyclomatic + cognitive complexity** (already in
   [`FUTURE.md`](../FUTURE.md)). Both come from the same tree-sitter
   pass; cognitive is free given CC.
7. ~~**Type-hint coverage**~~ - **rejected (2026-05).** The original
   survey queued this as a sub-stat or candidate sixth axis; the
   empirical study ([`TYPE_HINT_COVERAGE_EMPIRICS.md`](TYPE_HINT_COVERAGE_EMPIRICS.md))
   concluded against both forms (weakest independence archy has
   measured, max `|r| = 0.551`; niche owned by mypy / pyright; not a
   structural signal). See [`ROADMAP.md`](../ROADMAP.md) "Rejected".
8. **Call-graph edges** ([`FUTURE.md`](../FUTURE.md)). Once shipped,
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
[`bench/projects.yaml`](../../bench/projects.yaml) with **27 pinned
SHAs** spanning small CLI tools to very large frameworks across web
/ async / scientific / ORM / plugin-host / devops / build-tooling /
syntax-highlighting / generated-SDK domains. The
benchmark runner is [`bench/run.py`](../../bench/run.py); run with
`uv run --with networkx --with pyyaml python bench/run.py --vulture`.
Raw output checked into [`bench/results.md`](../../bench/results.md).

Specific validations referenced above:

1. **Vulture false-positive rate** (section 12). Vulture 2.16 run with
   default settings (60% confidence) and at `--min-confidence 90` on
   all 27 projects. 15 random findings per project spot-checked across
   FastAPI, pytest, and Django to identify dominant FP patterns.
2. **NCCD vs depth correlation** (section 3). Computed CCD/ACD/NCCD
   on the original 9-library benchmark plus archy. Pearson correlation
   between NCCD and archy's `max_depth`: `r = 0.000`, indicating the
   metrics are empirically orthogonal. The narrower 10-project sample
   was used because the NCCD probe predates the 27-project manifest;
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
