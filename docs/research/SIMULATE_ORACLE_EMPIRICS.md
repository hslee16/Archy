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
| Samples | 327 (308 clean, 19 dirty) |
| **Fidelity (clean rate)** | **94%** (308/327) |
| **Oracle on clean samples** | **308/308 matched, 0 bugs** |
| Oracle on dirty samples | 0/19 (every divergence explained, below) |
| Complexity-axis nonzero on an edge delta | 0 (as predicted) |
| simulate vs diff wall-clock (corpus) | 1.22x |

**The 308/308 with zero bugs is the load-bearing correctness result.** Because
the comparison runs on graphs built two independent ways (simulate's in-memory
edge add vs a real text edit + full re-parse), a match is *not* tautological: it
empirically confirms the `lines=()` synthetic-edge choice leaks into none of the
reported fields, across 308 real-repo cases.

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
~6% of single-line import edits in the corpus touch more than one graph edge.

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
   than the spec's conservative "~2x." The extra two DSM builds + two propagation
   passes are cheap next to the shared snapshot cost.
2. **Absolute latency is the real cost**: ~5s at 5k modules, ~17s at 10k. This is
   inherited from `take_snapshot` / propagation being super-linear, not from
   simulate. On a 10k+ module repo, simulate is a deliberate, not interactive-per-
   keystroke, call. Small/medium repos (<200 modules) stay sub-second.

## Layer-violation dimension

The corpus carries no `archy.yaml`, so a synthetic 4-layer graph (forbid
`l0 -> l1`) provides bench coverage: simulating a forbidden `l0 -> l1` edge flags
the violation; simulating an allowed `l1 -> l0` edge stays silent. Combined with
the unit test `test_added_layer_violation_is_surfaced`, the violation path is
covered; it reuses archy's own `find_violations` on the hypothetical graph, so
correctness follows from that function plus the verified edge application.

## Bottom line

- The oracle holds exactly (308/308, 0 bugs) whenever the agent's delta is the
  edge that actually gets written.
- It diverges precisely when a single import maps to multiple graph edges
  (ancestor packages, ~6%); this is documented, quantified, and surfaced in the
  tool description rather than hidden.
- simulate is cheap relative to a diff (~1.2x) at every scale tested; its
  absolute cost on very large graphs is a snapshot-cost property, not a simulate
  one.
