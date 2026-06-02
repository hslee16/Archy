# SPEC: `archy_simulate` -- counterfactual pre-edit consequence check (#144)

> Status: Implemented (`src/archy/simulate.py`, `archy_simulate` MCP tool +
> `archy simulate` CLI). Empirically validated; see §12 and
> [`SIMULATE_ORACLE_EMPIRICS.md`](research/SIMULATE_ORACLE_EMPIRICS.md).
> Tracking issue: [#144](https://github.com/hslee16/archy/issues/144).

## 1. Problem

The agent loop is asymmetric:

- `archy_impact` reasons over edges that **already exist**.
- `archy_diff` only works **after** the edit, against a saved snapshot.

There is no way for an agent to ask *"if I add an import from A to B and remove
the one from C to D, what breaks?"* without first writing the files. That makes
every structural refactor a write-then-check loop: the agent commits to an edit,
then discovers the new cycle or layer violation in the diff, then unwinds it.

`archy_simulate` closes the loop on the **predict** side: given the current
graph plus a proposed **edge delta**, return the structural consequence
**before any file is written**. No files are touched.

This is the one feature that mechanizes a *transformative* (hypothesis-testing)
structural workflow rather than an *additive* (write-then-observe) one -- the
"predict consequences, not tokens" framing from
[`docs/research/AGENT_CAUSAL_REASONING_SYNTHESIS.md`](research/AGENT_CAUSAL_REASONING_SYNTHESIS.md) §6.A.

## 2. Non-goals (stay in archy's lane)

- **Advisory only**, graph-shape only. Sensor, not fixer.
- Does **not** model behaviour, runtime state, persistent/DB effects, or
  integration semantics. Those belong to mypy / property tests / tracing.
- Does **not** edit files, suggest the edit, or rank candidate refactors. It
  answers "what would this specific delta do," nothing more.
- Phase 1 simulates **import-edge** deltas between **existing modules**. Adding
  or moving whole modules is a later phase (§8).

## 3. The key insight: this is mostly composition

The post-edit `archy_diff` machinery already does everything needed, *if* it is
run against a **hypothetical** graph (the current graph with the delta applied
in memory) instead of a re-parsed one. The implementation is therefore a thin
delta-application layer wrapped around functions that already ship:

| Need | Existing function |
|------|-------------------|
| score / cycles / layer + SDP violations of a graph | `diff.take_snapshot(graph, config_path)` |
| added/resolved cycles, violations, per-axis score delta | `diff.compute_diff(before, after)` |
| risk-ranked headline + judgment prompts (#154) | `diff_summary.summarize_diff(report, after_graph)` |
| new back-edges in topological order | `dsm.build_dsm(.., "topological")` + `dsm.diff_dsm(before, after)` |
| blast-radius / propagation cost | `reach.compute_propagation_cost(graph)` |

The novel parts are only: (a) the **delta API**, (b) **applying** the delta to
a copied graph, (c) the **output model**, and (d) the **validation oracle**.

## 4. Input

```
archy_simulate(
    path: str,
    add: list[EdgeSpec] = [],      # import edges to add
    remove: list[EdgeSpec] = [],   # import edges to remove
)

EdgeSpec = {"from": str, "to": str}   # resolved (Q1)
```

- The MCP wire shape is a list of `{"from": SRC, "to": DST}` objects (Q1
  resolved). `SRC` / `DST` are module qualnames **or** file paths, resolved the
  same way `archy_impact` / `archy_graph_focus` resolve modules
  (`graph.resolve_modules`), so an agent can pass `src/app/auth.py` or `app.auth`.
- CLI form: `archy simulate . --add app.a:app.b --remove app.c:app.d`
  (repeatable). `:` separates SRC and DST to avoid quoting `->` in a shell;
  qualnames use dots so `:` is unambiguous between them.
- An endpoint that does not resolve to a node in the current graph goes into
  `unresolved` (mirrors `Impact.unresolved`); phase 1 does **not** invent new
  modules, so an unresolved endpoint means that edge is skipped.
- A `remove` edge that does not currently exist is reported in
  `no_op_removes` rather than silently ignored (no silent caps).

### Synthetic edge attributes (verified safe)

A real import edit gives the edge `lines=[N]` (the import's source line). A
hypothetical edge has no line, so added edges are created with
`kinds=("import",)`, `lines=()`. **This is correct and load-bearing for the
validation oracle (§7).** Three code facts confirm `lines=()` changes nothing
the report compares:

- `diff._cycle_set_diff` keys cycles by `frozenset(modules)` -- topology only.
- `diff._violation_set_diff` keys violations by
  `(rule.from_layer, rule.to_layer, source, target)` -- excludes `lines`.
- `dsm.build_dsm` assigns import edges a **constant** weight `1.0` (not
  `len(lines)`), so a synthetic edge still appears in the DSM and is eligible to
  be a `new_back_edge`.

So the simulated and real graphs agree on every field `SimulateReport` reports.

### Resolved-edge semantics (important)

The graph's edges are **post-resolution** (relative imports, re-export chains,
and alias tables are all followed during `assemble_graph`). The delta is
therefore expressed in **resolved-graph-edge** terms: `SRC` and `DST` must be
nodes already in the graph, and `add SRC -> DST` means "as if a resolved import
edge `SRC -> DST` existed." If the agent writes `from y import thing` but `y`
re-exports from `z`, archy's resolver would edge `SRC -> z`, not `SRC -> y` -- so
the agent must name the **resolved** target. The oracle (§7) uses direct imports
with no re-export indirection, where this distinction does not arise; re-export
cases are a documented caveat, not a silent divergence.

### Edge-delta rules

- **Internal-only graph.** Simulate runs on the same internal-only graph
  `archy_diff` uses, so the oracle compares like with like. An endpoint that is
  external (third-party / stdlib) is not a node here → it lands in `unresolved`.
  (Resolves Q4: external-target deltas are out of scope for phase 1; a real
  `from somelib import x` also leaves the internal cycle/score picture unchanged,
  so this is consistent.)
- **`add` of an edge that already exists** → reported in `no_op_adds` (topology
  unchanged), symmetric with `no_op_removes`.
- **`remove` removes the entire `SRC -> DST` dependency** (both its import and
  any call sub-edge). Phase 1 does not model "drop the import but keep a call
  that resolves another way." (Caveat, Q6.)
- **Self-loops** (`SRC == DST`) are handled as normal edges, NOT rejected. The
  resolver *does* produce module-imports-itself edges (e.g. `from . import box
  as box` in rich's `box.py`), so a self-edge can genuinely exist and removing
  it must be simulable. (The original spec assumed self-imports were impossible;
  the oracle bench disproved that, see §12.)
- **Duplicate / conflicting specs.** Resolved pairs are de-duplicated per list;
  a pair in both `add` and `remove` cancels (recorded in `rejected`). Without
  this a repeated `remove` would call `remove_edge` twice and raise.

## 5. Output (`SimulateReport`)

Mirrors the `archy_diff` / `DSMDiff` schema family so an agent that already
reads diffs needs no new mental model:

```
SimulateReport:
  applied:                       # echo of what was actually simulated
    added_edges:   tuple[EdgeRef, ...]      # SRC -> DST that took effect
    removed_edges: tuple[EdgeRef, ...]
    unresolved:    tuple[str, ...]          # endpoints not in the graph
    no_op_adds:    tuple[EdgeRef, ...]      # add targets that already existed
    no_op_removes: tuple[EdgeRef, ...]      # remove targets that did not exist
  score_delta:   ScoreDelta                 # reuse diff.ScoreDelta
  cycles:        CycleSetDiff               # added / resolved
  violations:    ViolationSetDiff           # layer rules added / resolved
  sdp_violations: SdpViolationSetDiff
  new_back_edges: tuple[EdgeRef, ...]       # from DSMDiff, mapped to qualnames
  propagation_cost:                          # blast radius
    before: float
    after:  float
    delta:  float
  summary:       DiffSummary                # reuse summarize_diff: risk-ranked
                                            # headline + per-item judgment prompts
```

Notes:
- `new_back_edges` is translated from `DSMCell(row, col)` positions back to
  `SRC -> DST` qualnames via the after-DSM ordering, because the agent does not
  hold the DSM ordering. (DSMCell is positional; an agent needs names.)
- Simulation reuses `summarize_diff` for the risk-ranked headline + per-item
  prompts, rendered in **conditional mood** (Q2 resolved): *"Acyclicity would
  drop because `a -> b -> a` would become a cycle. Proceed, or pick a different
  seam?"* rather than the indicative past tense `archy_diff` uses. Implemented by
  a `hypothetical: bool = False` flag on `summarize_diff` that switches the
  prompt vocabulary, keeping all prompt wording in `diff_summary.py`.
- The whole object is computed and returned; **no file is written, no baseline
  is touched.**

## 6. Algorithm

```
1. graph      = load current internal-only graph (cache-backed, like other tools)
2. config     = discover_config(path)            # for layer/SDP violations
3. before     = take_snapshot(graph, config)
4. hypo       = graph.copy()                      # never mutate the live graph
   - for each resolved (src,dst) in add:    hypo.add_edge(src, dst,
                                               kinds=("import",), lines=())
   - for each resolved (src,dst) in remove: hypo.remove_edge(src, dst) if present
5. after      = take_snapshot(hypo, config)
6. report     = compute_diff(before, after)
7. summary    = summarize_diff(report, hypo, hypothetical=True)   # conditional mood
8. back_edges = diff_dsm(build_dsm(graph,"topological"),
                         build_dsm(hypo,"topological")).new_back_edges  -> names
9. prop       = (compute_propagation_cost(graph)[0],
                 compute_propagation_cost(hypo)[0])
10. assemble SimulateReport; return. (purely in-memory)
```

Cost: one extra graph copy + one extra snapshot/DSM/propagation pass over the
hypothetical graph. On the warm cache the live graph is already built, so the
marginal cost is a second analysis pass, not a re-parse.

## 7. Validation oracle (the spec's headline test)

By construction:

> `archy_simulate(delta)` must agree with the post-edit `archy_diff` once the
> delta is actually written.

Concretely, the core test:

```
g0   = build_graph(project)
sim  = simulate(g0, add=["app.x -> app.y"])
# now actually write `from app.y import thing` into app/x.py
g1   = build_graph(project)
real = compute_diff(take_snapshot(g0), take_snapshot(g1))
assert sim.cycles      == real.cycles
assert sim.violations  == real.violations
assert sim.score_delta == real.score_delta          # graph-shape axes
assert sim.new_back_edges == <real DSM back-edges>
```

**Equivalence boundary (must be documented, see Q3):** the equality holds for
every dimension derived from graph **shape**. It does *not* extend to metrics
derived from file **content** beyond imports -- specifically the `complexity`
axis (per-function cyclomatic complexity). An import-only delta cannot change
CC, so simulate always reports `complexity` delta `0`; a *real* import-only edit
also does not change CC, so they still agree. They would only diverge if the
real edit also added/removed functions -- which is outside a graph-delta's
expressive power *by design*. Simulate predicts the consequences **of the graph
delta you describe**, not of an arbitrary code edit.

**Empirical validation (run 2026-06-02, [`SIMULATE_ORACLE_EMPIRICS.md`](research/SIMULATE_ORACLE_EMPIRICS.md)).**
`bench/simulate_oracle.py` ran 327 sampled deltas across 11 real repos plus a
synthetic scale sweep. On the 308 samples where the written import maps 1:1 to
the intended edge, simulate's report equalled the real post-edit diff **308/308
with zero bug-level mismatches**, complexity delta `0` every time. The oracle is
not tautological: the two graphs are built independently (in-memory edge add vs
real text edit + re-parse), so the match confirms `lines=()` leaks into no
reported field. Two findings refined this spec:

- **Fidelity is 96%, and the gap is ancestor-package edges, not "re-export."**
  Importing a submodule `a.b.c` also creates edges to its ancestor packages
  (`a.b`, `a`; their `__init__` runs), so ~6% of single-line imports touch more
  than one graph edge. A lone submodule edge is a *lower bound* on real impact.
  Now in the tool description; a Phase-2 candidate is auto-expanding a submodule
  `add` to its ancestor edges (clean for `add`, ambiguous for `remove`).
- **Performance: ~1.2x a diff, flat to 10k nodes** (better than the 2x assumed
  here). Absolute latency (5s at 5k, 17s at 10k modules) is a snapshot/propagation
  cost inherited from `archy_diff`, sub-second under ~200 modules.

## 8. Phase 2 (later, not this PR)

- `add_module` / `move_module`: simulate a new node (with no CC data → its
  `complexity` contribution is modelled as neutral or flagged unknown) or a
  module moved between packages (re-attaches its edges, changes layer
  membership). This is where `move app.a -> app.core.a` becomes simulable.
- Multi-step deltas / sequencing.

Gate Phase 2 on whether real agent loops actually call Phase 1 (per the
project's usage-signal-over-capability prioritization).

## 9. Surface + ripple

- New MCP tool `archy_simulate` → tool count **17 → 18**. This triggers the
  known tool-count ripple: README "17 tools" table + count prose, install
  `TOOL_NAMES`, `tests/test_mcp.py` tool-surface set, plugin README rationale,
  `docs/LEARNINGS.md` count, install snapshots. (Budget for it; this is the
  first new *tool* since `archy_status`.)
- New CLI subcommand `archy simulate`.
- `loop` MCP prompt: add an optional step 2.5 -- "before a structural refactor,
  simulate the edge delta and abandon/reshape it if it introduces a cycle."
- Docs: SKILL.md (+ byte-identical plugin mirror), README tool table,
  AGENT_LOOP.md, this spec.

## 10. Anti-theater test

An agent that calls `archy_simulate` **abandons or reshapes a planned edit
before writing a single file** when the simulation shows a new cycle. That is a
different *action*, not a different dashboard. Promotion beyond advisory gates
on observing that behaviour in real loops.

## 11. Decisions (resolved in review)

- **Q1 (resolved).** MCP wire format is a list of `{"from", "to"}` objects; CLI
  uses `--add SRC:DST` / `--remove SRC:DST`.
- **Q2 (resolved).** `summary` prompts render in **conditional mood** for
  simulation, via a `hypothetical=True` flag on `summarize_diff`.
- **Q3 (open, minor).** Where to document the §7 equivalence boundary so it is
  not mistaken for a bug -- lean: a short `caveats` note in the tool description
  plus a sentence in the payload docs. (Not blocking.)
- **Q4 (resolved → deferred).** External targets are out of scope for phase 1:
  the internal-only graph has no external node to point at, so they fall into
  `unresolved`. Consistent with `archy_diff`, which is also internal-only.
- **Q5 (resolved).** Accept the 17 → 18 tool count; `archy_simulate` is a
  first-class capability, not folded behind another tool. Budget the ripple.
- **Q6 (resolved).** `remove SRC -> DST` drops the whole dependency (import +
  any call sub-edge). Fine for phase 1.

## 12. Empirical validation (bench)

The oracle (§7) is an *assertion*; the project's culture is to prove assertions
on the real-repo corpus, not assume them. Two passes:

### Existing bench (re-run, validates the value-prop)

- **`bench/inloop_prevalence.py`** (already has results): replays real merged
  commits and measures how often one commit introduces a new import cycle. This
  is the *motivation* for `archy_simulate` -- if cycle-introducing edits are real
  but concentrate in large/transformative changes, a pre-edit predictor has
  headroom. Re-run to confirm the published base rate still holds on the corpus.
- **`bench/run.py`**: baseline per-repo scores, so any score-delta we report in
  simulation is anchored to the same numbers.

### New bench: `bench/simulate_oracle.py` (validates the headline claim)

For each repo in `bench/replay_cache/` (10 repos with source on disk), build the
graph, then **empirically test `simulate(Δ) == diff(after writing Δ)`**:

1. **Removals (gold case -- exact ground truth).** Sample existing internal
   import edges. For each edge `A -> B`: (a) `simulate(remove=[A:B])`;
   (b) actually delete that import's source line(s) (we hold `edge.lines`),
   rebuild the graph, run the real `compute_diff`. Assert the simulated and real
   reports agree on cycles, layer/SDP violations, `new_back_edges`, and every
   score axis. Removals are the cleanest oracle because the source location is
   known exactly.
2. **Additions.** Sample internal module pairs with no current edge. For each:
   (a) `simulate(add=[A:B])`; (b) write `import <B>` into A's file, rebuild,
   real diff. Assert agreement. **Expected divergences here validate the
   re-export caveat (§4):** when the written import resolves through a re-export
   to a different node than named, simulate and reality differ -- we count and
   characterize these rather than treat them as failures.

**Metrics reported** (to `bench/simulate_oracle_results.md`):
- **Oracle match rate** per repo and overall (target: 100% on removals; <100%
  on additions only via the documented re-export cases, which we enumerate).
- **Complexity-axis invariance**: confirm every edge-delta yields `complexity`
  delta exactly 0 on both sides (validates the §7 equivalence boundary).
- **Cost**: wall-clock of `simulate` vs a full `diff` per repo, by graph size,
  to confirm the "~2x a diff, no re-parse on the warm cache" claim and surface
  any blow-up on the largest graphs.
- **Finding prevalence**: across sampled deltas, how often a single edge add
  introduces a cycle / a layer violation / a back-edge -- the empirical analogue
  of the inloop-prevalence study, but for *synthetic* deltas, characterizing how
  "loud" simulate is per edge.

The bench lives in `bench/` and never imports from a mutated `src/archy`; it
calls the shipped `simulate` API. Companion results doc:
`docs/research/SIMULATE_ORACLE_EMPIRICS.md`. This bench **gates promotion** of
any claim in this spec from "asserted" to "validated."

## 13. Prior art / comparisons

The market splits into two camps relative to `archy_simulate`, and **the
validation oracle (§7) is the most defensible novelty** -- no prior tool found,
commercial or academic, *guarantees and tests* that its what-if prediction
equals the actual post-change analysis. (Sonargraph even warns its virtual-model
metrics "may shift," implying no such guarantee.) Lead with the oracle.

### Camp A -- interactive "what-if on a model" (closest analogues)

These genuinely simulate hypothetical structural changes before editing code,
but all are **human-driven GUI workflows**, not an agent-callable delta API, and
none ship a composite score-delta + blast-radius bundle or a simulate-vs-actual
oracle.

- **Sonargraph (hello2morrow)** -- *strongest analogue.* "Virtual models" let you
  "delete, move or rename elements without actually changing your code" and see
  the simulated effect, including cycles; a `cycle-breakup` analyzer proposes
  refactorings. Gap: element-level move/rename/delete in a GUI, not a raw
  import-edge add/remove **delta via an API**; no agent surface; no composite
  score-delta or blast-radius scalar; no published oracle.
  <https://eclipse.hello2morrow.com/doc/standalone/content/motivation.html>
- **Lattix Architect (LDM)** -- DSM + design rules; drag-and-drop the model to
  "create what-if scenarios… assess the impact of potential changes." Gap: GUI
  DSM editing (move element between subsystems), not an `add import X->Y / remove
  A->B` edge-delta API; no 5-axis score; no oracle.
- **Structure101 (now Sonar)** -- "Structure Spec" plan; "simulate restructuring…
  move classes between packages," export an action list to apply later. Gap:
  models package/folder placement, plan-then-apply human workflow; not
  agent-callable; no scalar score-delta; no oracle.

### Camp B -- reactive fitness functions (the rhetorical foil)

Predicates over **written** state: they fail *after* the violating import already
exists in the analyzed snapshot. This is the loop `archy_simulate` removes
(write → re-run → revert).

- **import-linter (Python)** -- the sharpest contrast, because **archy already
  wraps it for `archy contracts`.** It lints forbidden/layered imports
  transitively over the *current* tree. The one-line positioning: *"we run the
  same contract engine, but on `graph + Δ` before you write Δ."*
- **ArchUnit (Java) / ArchUnitNET / NetArchTest (.NET)** -- JUnit-style assertions
  ("should not depend on," cycle-freedom) over compiled code. Reactive; the
  import must exist for the rule to evaluate.
- **Tach (Python), dep-cruiser (JS/TS)** -- boundary / forbidden-dep / cycle
  checks over the actual import graph. Reactive.
- **NDepend** -- CQLinq dependency rules + DSM; "warns as soon as a cycle is
  accidentally created" (i.e., after the import exists). Its "Refactoring Impact
  Analysis" is DSM-guided planning + baseline-vs-baseline diff of *real* code,
  not un-applied-edge simulation. (Medium confidence on this boundary.)

All of Camp B answer *"is the current graph legal?"* `archy_simulate` answers
*"would `graph + Δ` be legal, by how much per axis, and what newly enters the
blast radius?"* -- without writing `Δ`.

### Academic framing (canonical citations)

- **Software Reflexion Models** -- Murphy, Notkin, Sullivan, FSE 1995 (TSE 2001
  extended). Planned-vs-actual conformance (convergence/divergence/absence); the
  root of archy's layer rules. Static and post-hoc -- no forward simulation of a
  proposed un-applied edge. archy adds the counterfactual/delta dimension.
- **Change Impact Analysis** -- Lehnert 2011, "A Review of Software Change Impact
  Analysis" (canonical survey); Arnold & Bohner 1996. archy's **blast radius**
  *is* CIA (forward reachability); archy adds structural-defect prediction (new
  cycles/violations) and a composite score-delta on top of the affected set.
- **Architectural erosion / drift** -- Perry & Wolf 1992 (origin of erosion vs
  drift); de Silva & Balasubramaniam 2012 survey. Their "prevent" category is
  mostly *reactive enforcement*; `archy_simulate` is **prevention-by-prediction**
  -- moving erosion control left of the edit.
- **DSM / SDP lineage** -- Baldwin & Clark, *Design Rules* (2000); Martin's Stable
  Dependencies Principle. Principles evaluated on real structure; archy makes SDP
  a **simulatable** axis. Background, not a competing tool.
- No canonical paper frames pre-edit import-graph what-if as a counterfactual
  prediction *with a validation oracle* -- the whitespace archy claims (medium
  confidence; absence of evidence).

### IDE refactoring previews (complementary, not competing)

IntelliJ refactoring preview / Safe Delete and VS/Roslyn "Preview Changes" are
**local correctness** oracles (usages list, conflicts, a text diff) at the
granularity of one refactoring op -- "will this break a reference / compilation."
`archy_simulate` is a **global structural-health** oracle over the whole import
graph, consumable by an agent rather than rendered to a human dialog.

### LLM/agent tooling

- **Code Digital Twin** -- Peng/Wang et al., Fudan (arXiv 2503.07967): a *vision*
  paper proposing that "when an LLM proposes a code change, it can query the twin
  to identify impacted modules." It gestures at "query before you change" but
  ships no edge-delta API, no cycle/violation prediction, no score-delta, no
  oracle. `archy_simulate` is a concrete, narrow, verifiable instantiation of
  exactly that idea.
- The dominant agent pattern today is **edit → run check (ArchUnit/import-linter)
  → revert** -- reactive verification. No existing agent tool found offers a
  pre-edit structural *simulate-the-delta* primitive. (Medium confidence this is
  genuine whitespace.)

### One-sentence positioning

> Camp A does interactive what-if on a model for a human; Camp B checks legality
> after the import is written. `archy_simulate` is the first *agent-callable,
> pre-edit, import-graph-delta* simulator that returns new cycles + back-edges +
> layer/SDP violations + per-axis score-delta + blast-radius, and is backed by a
> `simulate == post-edit diff` validation oracle.

Sources collected in the research pass (Sonargraph/Lattix/Structure101 docs,
import-linter/ArchUnit docs, Murphy FSE'95, Lehnert 2011, Perry & Wolf 1992,
Code Digital Twin arXiv 2503.07967) are linked inline above.
