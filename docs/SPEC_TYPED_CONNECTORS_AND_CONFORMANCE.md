# SPEC: Typed connectors and intended-vs-actual conformance

Status: draft
Motivated by: Medvidovic & Taylor, "A Classification and Comparison Framework for
Software Architecture Description Languages," IEEE TSE 26(1), Jan 2000.

## Why this document exists

The 2000 ADL survey graded architecture languages on four axes: components,
**connectors** (first-class), **configurations** (topology + constraints +
refinement/traceability), and tool support. Classical ADLs were *prescriptive*:
you hand-wrote the intended architecture. Archy is the inverse: it *recovers* the
actual architecture from Python source. The paper's two richest ideas map cleanly
onto that recovery model and are the subject of this spec:

1. **Typed connectors.** The paper's signature thesis is that interaction should be
   modeled explicitly, not left implicit inside components. Archy today collapses
   every dependency into an undifferentiated arrow. We can do better with data we
   already extract.

2. **Refinement / traceability, reframed as conformance.** The paper rewards
   languages that can relate an intended architecture to its realization. For a
   recovery tool that becomes: declare the intended topology, measure the drift
   between intended and actual, and track that drift over time. This turns
   `archy_contracts` from a linter into an architecture-erosion monitor.

Both features are extensions of existing machinery, not new subsystems. Feature 1
is the substrate feature 2 builds on (richer edge types make richer constraints
possible), so they are specced together but can ship independently.

---

## Current state (as built)

Grounding references, so this spec stays honest about the delta:

- **Edges are already weakly typed.** `graph.py` writes `networkx` edge-attribute
  dicts via `_add_or_extend_edge` (import edges) and `_add_or_extend_call_edge`
  (call edges). Each edge carries a `kinds: tuple[str, ...]` that today holds only
  `"import"` and/or `"call"`, plus `is_relative`, `lines`, `call_lines`,
  `call_count`. Extraction is tree-sitter (`parser.py`, `ImportRef` / `CallRef`).
- **The wire model drops the type.** The MCP `GraphEdge` model (`mcp.py`) exposes
  only `source / target / is_relative / lines`. `kinds` and `call_count` never
  reach an agent.
- **DSM weight is type-blind.** `dsm.py` weights a cell `1.0` for imports or
  `call_count` for calls; it cannot distinguish an inheritance edge from a
  type-only import.
- **Constraints today are edge-agnostic.** `layers.py` (`archy_check`) matches
  *direct forbidden edges* between layers; `contracts.py` wraps import-linter
  (Layers / Forbidden / Independence / Protected). Neither can say "no *inheritance*
  across this boundary" because edge kind is not available to the rule engine.
- **Score ignores edge type entirely.** `score.py` geomean of five axes
  (modularity, acyclicity, depth, equality, complexity). No axis is edge-typed.
- **History exists.** `history.jsonl` append-only rows + `diff.py` single-baseline
  snapshot already give us the temporal substrate conformance-trend needs.

---

## Feature 1: Typed connectors

### Goal

Replace the two-value `kinds` tuple with a richer, closed set of **connector
kinds** derived from what the tree-sitter pass already sees, and surface that type
through the DSM, the MCP wire model, and (optionally) coupling weights.

### Proposed connector taxonomy

A closed enum, ordered roughly by coupling severity (worst first). Severity order
matters because it lets downstream weighting and conformance rules reason about
"how bad" a connector is.

| kind | meaning | detectable from | severity |
|---|---|---|---|
| `inheritance` | base class / metaclass reference across modules | tree-sitter `class ... (Base)` where `Base` resolves cross-module | highest |
| `call` | runtime function/method call | existing `(call)` query | high |
| `instantiation` | constructor call of a foreign class | `call` whose target resolves to a class | high |
| `decorator` | `@foreign.thing` applied | tree-sitter `(decorator)` query (new) | medium |
| `exception` | `except foreign.Error` / `raise foreign.Error` | tree-sitter `(except_clause)` / `(raise_statement)` (new) | medium |
| `import` | plain module import, symbol used at runtime | existing `(import_*)` query | medium |
| `type_only` | import used solely in annotations / `TYPE_CHECKING` | import inside `if TYPE_CHECKING:` block, or symbol only in annotation positions | lowest |

Notes:

- `type_only` is the highest-value new distinction: a type-only import is nearly
  free coupling (erasable, no runtime edge) and today inflates coupling and DSM
  density. Detecting `if TYPE_CHECKING:` guarded imports is a cheap tree-sitter
  scope check.
- `inheritance` is the highest-value *severity* distinction: subclassing across a
  boundary is the tightest coupling there is and deserves to weigh more than a
  function call, which weighs more than a type import.
- The enum is closed and ordered; unknown/ambiguous defaults to `import` (never
  crash on an unclassifiable edge).

### Data model changes

- Keep `kinds: tuple[str, ...]` as the storage (an edge legitimately has multiple
  kinds: `import` + `call` + `inheritance` all at once). Constrain the *values* to
  the taxonomy above. No schema migration: existing snapshots with `("import",)` /
  `("call",)` remain valid subsets.
- Add derived helpers rather than new stored fields where possible:
  `edge_severity(kinds) -> float` (max severity over the tuple) computed on demand.
- `parser.py`: add tree-sitter queries for `(decorator)`, `(except_clause)`,
  `(raise_statement)`, and class-base extraction; extend `ImportRef` with a
  `type_checking_guarded: bool`.

### Surfaces to update

1. `graph.py` `_add_or_extend_edge` / `_add_or_extend_call_edge`: populate the
   richer kinds. `graph_to_dict` already emits all edge attrs, so JSON output gets
   it for free.
2. `mcp.py` `GraphEdge`: **add `kinds` (and optionally `severity`) to the wire
   model.** This is the single most impactful line for agent consumers, who
   currently cannot see edge type at all.
3. `dsm.py`: add a `weight="severity"` option alongside `imports` / `calls`, using
   `edge_severity`. Back-edge (cycle) detection unchanged.
4. `score.py` (optional, gated): a *severity-weighted* variant of the modularity /
   equality axes. Do NOT silently change the baseline; introduce behind a flag and
   re-anchor the ~0.6695 baseline deliberately if adopted. Keep this out of scope
   for the first cut to avoid churning the score.

### Non-goals

- Full dataflow / semantic connector analysis (the paper's "connector semantics").
  We type by *syntactic form*, not behavior.
- Cross-language connectors (paper's heterogeneity axis). Python-only for now, but
  keep the taxonomy language-neutral so it ports.

### Risks

- **Score churn.** Any change that reaches `score.py` moves the baseline. Mitigation:
  ship typing as pure enrichment (graph + DSM + wire) with score untouched in v1.
- **Detection precision.** `type_only` misclassification (a `TYPE_CHECKING` import
  that is also used at runtime via string-eval) should fail *safe* toward `import`,
  never toward `type_only`, so we never under-report coupling.
- **Wire-model size.** Adding `kinds` to every `GraphEdge` grows large-graph
  payloads. Mitigation: only emit non-default kinds, or gate behind
  `response_format`.

---

## Feature 2: Intended-vs-actual conformance

### Goal

Let a user declare an **intended architecture** (components + allowed connectors),
then have Archy score the **conformance gap** against the recovered graph and track
it over time. This is the paper's refinement/traceability axis for a recovery tool,
and architecturally it is `archy_contracts` generalized from per-rule pass/fail
into a single **conformance score** with drift tracking.

### Intended-architecture spec (the "little ADL")

Reuse the existing `archy.yaml` config surface (already parsed by `layers.py`
`load_config`). Add an optional `intended:` block. Deliberately minimal, a
component-and-connector model in the paper's vocabulary:

```yaml
intended:
  components:                 # named groupings of modules (globs)
    api:      ["archy.mcp", "archy.cli"]
    core:     ["archy.graph", "archy.score", "archy.dsm"]
    parsing:  ["archy.parser", "archy.index"]
    io:       ["archy.history", "archy.diff", "archy.watcher"]
  connectors:                 # allowed edges between components
    - from: api      to: core
    - from: api      to: parsing
    - from: core     to: parsing
    - from: core     to: io
    # any edge not listed is a conformance violation
  rules:                      # optional per-connector kind constraints (needs Feature 1)
    - between: [core, parsing]
      forbid_kinds: [inheritance]   # core may call parsing, but not subclass it
```

Semantics:

- Every recovered internal edge is mapped to a `(from_component, to_component)`
  pair. An edge whose component-pair is not in `connectors` is a **divergence**.
- `rules` add kind-level constraints on *allowed* connectors (this is where Feature
  1 pays off: "allowed to depend, not allowed to inherit").
- Modules matching no component are reported as **unassigned** (visibility, not
  penalty, so partial specs are usable from day one).

### Conformance score

A single number in [0, 1], reported alongside (not folded into) the existing score,
mirroring how `ScoreInputs` carries diagnostics that do not move `overall`:

```
conformance = 1 - (weighted_divergent_edges / weighted_total_edges)
```

where each divergent edge is weighted by `edge_severity` (Feature 1) so an
illegal *inheritance* across a boundary hurts conformance more than an illegal
type-only import. With Feature 1 absent, all weights are 1.0 (pure edge count).

Reported payload:

- `conformance: float`
- `divergences: [{from, to, from_component, to_component, kinds, lines}]` (the
  actionable list, most-severe first)
- `unassigned_modules: [...]`
- `absent_connectors: [...]` (declared-but-never-realized intended edges, i.e. the
  spec is stale or the feature was removed): the reverse-drift signal.

### Surfaces

- **Where it lives:** extend `contracts.py` / the constraint path, not a new
  top-level MCP tool. This aligns with the in-flight consolidation (#265, folding
  `archy_check` + `archy_contracts` + `archy_cycles` into one constraint-validation
  tool, #268). Conformance is naturally a *mode* of that unified constraint tool.
- **Config:** `layers.py` `load_config` gains the `intended:` block parse.
- **Trend:** add `conformance` as an optional column to `HistoryRow` /
  `history.jsonl` (nullable for pre-feature rows, exactly like `complexity` was
  added post-v0.20). `archy_diff` / `Snapshot` gains `conformance` so drift shows
  up in the diff summary. This is the erosion-monitor payoff: conformance trending
  down over commits is the headline signal.

### Non-goals

- Auto-synthesizing the intended spec from the current graph. (Tempting, but it
  would bless the current structure as "intended" and defeat the purpose. A
  `--scaffold` helper that emits a *starting* `intended:` block from detected
  communities is a reasonable future convenience, clearly labeled as a draft.)
- Enforcing conformance as a hard gate in v1. Report first; gating is a later opt-in
  (`fail_under` style, mirroring score gating).

### Risks

- **Spec staleness.** An intended spec that nobody maintains rots. Mitigation:
  `absent_connectors` + `unassigned_modules` make staleness *visible* in every
  report, and the trend column makes "we stopped conforming" a graph, not a
  surprise.
- **Component granularity.** Too-coarse components hide real divergences; too-fine
  is unmaintainable. Mitigation: components are glob-based and hierarchical-friendly;
  ship the archy self-spec above as the worked example / dogfood.
- **Interaction with Feature 1 severity.** If Feature 1 is not shipped, conformance
  still works at weight 1.0. No hard dependency, only enrichment.

---

## Gating: anti-theater + OECD (read before building)

This spec is motivated by an external paper, which is exactly the kind of proposal
`docs/research/AGENT_CAUSAL_REASONING_SYNTHESIS.md` and `docs/research/AXIS_REVIEW.md`
exist to vet. Neither feature ships on "the ADL paper says connectors/refinement
matter." Both must clear the project's own gates first:

- **Anti-theater test** (PR #142/#143): *what does an agent or human do differently
  because of this?* A different action, not a different dashboard. Corollary: a new
  number or synthesis that changes no user action is a vanity thermometer.
- **OECD four-condition gate** (`AXIS_REVIEW.md`), required only if a signal reaches
  the score: independence, directionality, actionability, discriminant validity.

Applied here:

- **Typed connectors**: the *consumer* is the deliverable, the typing is plumbing.
  Enrichment alone (richer `kinds` + a wire `severity`) changes no action and fails
  the test. Ship only with a concrete consumer: conformance `forbid_kinds`, coupling
  de-inflation for `type_only` edges in `duplicates`/`impact`, or a load-bearing-edge
  brief. The signal itself is sound (unlike the rejected `calls_per_edge`: "replace
  inheritance with composition" is canonical, `type_only` is lower coupling
  cross-population), so the only real risk is shipping without a consumer. Stays out
  of `score.py` (no baseline re-anchor).
- **Conformance**: the divergence *list* is the action-changer (an agent removes the
  divergent edge, exactly as `archy_contracts`/`archy_check` already work). The
  `conformance` *number* is the theater-risk surface: never fold it into `overall`,
  justify it only as an erosion-trend signal a human acts on. Known ceiling: it
  measures conformance to a hand-authored `intended:` spec, not intrinsic quality, so
  its discriminant validity decays as the spec goes stale (`absent_connectors` /
  `unassigned_modules` exist to make that staleness visible). Evaluate it *inside* the
  #268 constraint-tool consolidation, not as a standalone surface.

**Sequencing reality:** per the maintainer's recorded 2026-05-26 prioritization, the
highest-leverage next move is a **usage signal** (does anyone call archy inside an
agent loop?), not more capability. Both features are "more capability" and sit below
that line until usage evidence exists.

## Suggested sequencing

1. **Feature 1, enrichment-only** (graph + `GraphEdge` wire + DSM `severity`
   weight). Zero score impact. Immediately useful to agents.
2. **Feature 2, report-only** (`intended:` parse + conformance score + divergence
   list), weight 1.0 or severity-weighted if Feature 1 landed.
3. **Trend/diff integration** for conformance (the erosion monitor).
4. *(Deferred)* score-axis integration for either feature, behind a flag with a
   deliberate baseline re-anchor.

## Dogfood check

Both features should be validated against archy itself: the taxonomy against
archy's own edges, and the `intended:` self-spec above against archy's recovered
graph. Per project convention, use archy's own `mcp__archy__` tools as the dev loop
(affected → risk → diff) and review via the DeepWork review workflow.
