# `archy_simulate` oracle empirics

Companion to [`docs/SPEC_SIMULATE.md`](../SPEC_SIMULATE.md). Validates, on real
repos and at synthetic scale, the spec's central claim:

> `archy_simulate(delta)` equals the post-edit `archy_diff` once the delta is
> actually written.

Harness: [`bench/simulate_oracle.py`](../../bench/simulate_oracle.py). Run
2026-06-02 with `--samples 15` over the 11 source repos in
`bench/replay_cache/` (327 sampled deltas) plus a synthetic scale sweep.

## The methodological trap (and why the first cut was near-worthless)

The first version of this bench only compared samples where the real text edit
reproduced *exactly* the simulated edge set. But cycles, score, violations, and
back-edges are pure functions of graph topology, so once you gate on "the edge
sets are identical," the two graphs are identical and every metric matches **by
construction**. That measures determinism, not prediction.

The honest design compares on **every** sample and splits the result:

- **fidelity (clean rate)** -- how often an intended single-edge delta maps 1:1
  to the written import. This is the real, agent-facing number.
- **oracle match** -- does simulate's report equal the real diff's? On clean
  samples it must be 100% (a failure is a bug); on dirty samples it diverges,
  and we quantify by how much.

## Results

| Metric | Value |
|---|---|
| Samples | 327 (315 clean, 12 dirty) |
| **Fidelity (clean rate)** | **96%** (315/327) |
| **Oracle on clean samples** | **315/315 matched, 0 bugs** |
| Oracle on dirty samples | 0/12 (every divergence explained, below) |
| Complexity-axis nonzero on an edge delta | 0 (as predicted) |
| simulate vs diff wall-clock (corpus) | 1.23x |

Additions use the realistic `from <pkg> import <leaf>` form an agent would write
(not `import <qualname>`), and the oracle compares **all** comparable
`SimulateReport` fields: cycles, layer violations, **SDP violations**, score
delta, new back-edges, **and propagation cost** (only `applied`, an input echo,
and `summary`, a deterministic function of the rest, are excluded).

**An adversarial review caught a real bug here that a weaker oracle missed.** An
earlier version of `_matches` compared only 4 of those fields and the additions
used `import <qualname>`; both were tightened during review. The expanded oracle
immediately surfaced a clean-sample mismatch on `rich.box`: archy *does* produce
module-imports-itself edges (`from . import box as box`), which contradicted the
spec's assumption that self-imports are impossible, so simulate was wrongly
*rejecting* a self-loop removal. Fixed (self-loops are now handled as normal
edges); the oracle is **315/315** after the fix.

**On the precision of "315/315."** On a clean sample the real re-parse reproduces
exactly the simulated edge set, so the two graphs are topologically identical and
the topology-derived fields *must* agree. What this validates is therefore
narrow but real: that simulate's **delta application + the `lines=()` synthetic
edge** produce a graph whose every reported field matches a true re-parse, with
zero leakage, across 315 real cases. It is *not* evidence that simulate predicts
something an oracle on identical graphs couldn't; the **fidelity rate (96%)** is
the separate measure of how often the agent's intended delta *is* that graph.
Together: simulate is exact for the delta it is given, and the delta matches the
written import 96% of the time.

## The fidelity gap is ancestor-package edges, not "re-export"

The spec framed the resolved-edge caveat loosely as "re-export indirection." The
dirty samples show the dominant cause is sharper and more common:

> Importing a submodule creates dependency edges to its **ancestor packages**
> too. `from a.b.c import x` yields graph edges `a -> a.b.c` **and** `a -> a.b`
> (the package `__init__` runs), so removing or adding one import line changes
> more than one graph edge.

Examples (from the run):

- `rm datasette.app -> datasette.utils.baseconv` really removed *both*
  `datasette.app -> datasette.utils.baseconv` and `datasette.app -> datasette.utils`.
- `add datasette.actor_auth_cookie -> datasette.default_permissions.config` really
  added that edge *and* `datasette.actor_auth_cookie -> datasette`.

This is correct archy behavior (the importing module does depend on the ancestor
packages), not a bug. It means an agent's single-edge mental model under-models a
real submodule import by the ancestor edges. **Actionable guidance (now in the
tool description):** to model a submodule import exactly, include the ancestor
edges in the delta; a lone submodule edge is a lower bound on the real impact.
~4% of single-line import edits in the corpus (12/327) touch more than one graph
edge. (The bench skips qualnames that are not valid dotted identifiers, e.g. a
`unicode8-0-0.py` data module, so a syntactically invalid injected import cannot
masquerade as an ancestor-edge divergence.)

**When the gap strikes (it is not uniform).** The divergence is concentrated, not
random, so the headline 96% understates how reliable simulate is for the common
case and overstates it for one specific case:

- **By edit direction:** 11 of the 12 dirty samples are *removals*. Removing a
  submodule import line (`from a.b import c`) usually also drops the `a -> a.b`
  ancestor edge, because the `__init__` was reached only through that line.
  Additions diverge far less often (1/12) -- adding `from a.b import c` to a
  module that already imports something else under `a.b` adds only the leaf edge.
  An agent simulating a *removal* of a deep submodule import is the case most
  likely to under-model real impact.
- **By repo:** the 96% is a corpus-wide pooled rate, not a per-repo guarantee.
  Per-repo clean rates run ~93% (scrapy, pydantic, datasette, mkdocs, flask --
  repos with deep submodule packages) up to 100% (fastapi, starlette, httpx,
  click -- flatter package trees). The fidelity an agent actually sees depends on
  how deeply nested the target repo's packages are.

This is the honest reading of the fidelity number: it is a real, agent-facing
limitation (an intended edge that does not match the written one), not just a
measurement artifact, and it falls predominantly on submodule-import removals in
deeply-nested packages.

**Corpus caveat.** 327 samples drawn from 11 mid-sized Python repos (<=174
modules) is a convenience corpus, not a stratified sample, and the run reports no
confidence interval. The 96% should be read as "roughly 19 in 20 on repos of this
shape," not a precise population estimate; a corpus that over- or under-represents
deeply-nested packages would move it. Wider and stratified sampling is future
work.

## Scale + performance (closes the corpus gap)

The corpus tops out at ~174 modules, so a synthetic mostly-acyclic graph sweep
measures behavior where it matters:

| nodes | simulate | diff | ratio |
|--:|--:|--:|--:|
| 500 | 0.21s | 0.17s | 1.24x |
| 2,000 | 1.36s | 1.05s | 1.29x |
| 5,000 | 5.23s | 4.33s | 1.21x |
| 10,000 | 17.39s | 15.72s | 1.11x |

Two findings:

1. **simulate's overhead over a diff is ~1.2x and stays flat at scale** -- better
   than the spec's conservative "~2x." simulate's extra work is two DSM builds
   (for `new_back_edges`) plus two propagation passes; these are cheap next to the
   shared snapshot cost. **Caveat:** this synthetic graph is a sparse near-DAG
   (out-degree 2, one injected back-edge). On a dense or heavily-cyclic graph the
   DSM/cycle work is super-linear, so the ratio could rise above 1.2x; the "flat"
   claim is established only for sparse structure. A density sweep is future work.
2. **Absolute latency is the real cost, reported plainly**: ~5s at 5k modules,
   ~17s at 10k. This is inherited from `take_snapshot` / propagation being
   super-linear, and simulate *adds* ~1-2s of its own DSM/propagation work on top.
   At 10k+ modules it is a multi-second call, not an interactive-per-keystroke
   one; small/medium repos (<200 modules) stay sub-second.

## Layer-violation dimension

The corpus carries no `archy.yaml`, so a synthetic 4-layer graph (forbid
`l0 -> l1`) provides bench coverage: simulating a forbidden `l0 -> l1` edge flags
the violation; simulating an allowed `l1 -> l0` edge stays silent. Combined with
the unit test `test_added_layer_violation_is_surfaced`, the violation path is
covered; it reuses archy's own `find_violations` on the hypothetical graph, so
correctness follows from that function plus the verified edge application.

## Bottom line

- The oracle holds exactly (315/315, 0 bugs) whenever the agent's delta is the
  edge that actually gets written.
- It diverges precisely when a single import maps to multiple graph edges
  (ancestor packages, ~4%: 12/327); this is documented, quantified, and surfaced
  in the tool description rather than hidden. The divergence is directional --
  11 of the 12 dirty samples are *removals* of a submodule import, where dropping
  the line also drops the ancestor `__init__` edge.
- simulate is cheap relative to a diff (~1.2x) at every scale tested; its
  absolute cost on very large graphs is a snapshot-cost property, not a simulate
  one.
