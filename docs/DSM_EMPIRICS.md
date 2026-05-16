# DSM-derived signals: empirical study and the visualization-only decision

This document records an empirical investigation into whether a Design
Structure Matrix (DSM) view of the import graph contributes any scalar
signal that the existing five score axes (modularity, acyclicity,
depth, equality, complexity) plus `propagation_cost` do not already
capture. The companion artifacts are `bench/dsm.py` (the script) and
`bench/dsm_results.md` (the raw numbers).

The parent question comes from the v0.3 roadmap item `archy dsm`. DSM
is the canonical industrial visualization of system coupling (Steward
1981, Eppinger & Browning, MacCormack 2006) and pairs naturally with
`propagation_cost`, which is computed from a DSM. Before building the
command, the question was whether any DSM scalar belongs in the score.

## Decision

**Ship DSM as a visualization-only output** (`archy dsm` CLI + `archy_dsm`
MCP tool with ASCII and JSON formats, grouped by community / layer /
topological order). No DSM-derived scalar lands in `archy score`, not
as an axis, and not as a parallel diagnostic. The empirics support the
roadmap shape that was already planned and rule out the two adjacent
shapes (DSM-as-axis, DSM-as-diagnostic).

This matches archy's audience principle: the DSM's value is structural
information that an agent reads positionally (where do back-edges sit,
which blocks are dense, which row dominates), not a number that
compresses that structure into a single bit of judgment.

## What this rules out

Four candidate DSM-derived scalars were tested. Two failed the OECD
discriminant-validity threshold (`|r| < 0.7` against every existing
signal); two passed discriminant validity but failed the direction-
contested test that killed call-weighted Q as an axis last week.

### The four candidates

- **`feedback`**: share of internal edges that land above the diagonal
  in the SCC-condensed topological ordering. Direct measure of how much
  of the graph violates clean DAG layering.
- **`bandwidth`**: mean `|i - j| / N` over internal edges in the same
  ordering. Captures how local dependencies are.
- **`block_comm`**: fraction of internal edges that fall inside
  Newman-community block-diagonal blocks.
- **`block_layer`**: fraction of internal edges that stay within the
  same depth-bucketed layer.

### Discriminant validity results

Pearson r of each candidate against existing axes + propagation_cost
across the 27-project bench (from `bench/dsm_results.md`):

| signal | vs modularity | vs acyclicity | vs depth | vs equality | vs complexity | vs propagation_cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| feedback | -0.072 | -0.688 | +0.605 | -0.561 | +0.149 | +0.428 |
| bandwidth | -0.080 | +0.573 | -0.110 | +0.591 | +0.045 | -0.324 |
| block_comm | +0.716 | +0.175 | -0.156 | +0.122 | -0.203 | -0.501 |
| block_layer | -0.094 | -0.771 | +0.584 | -0.606 | +0.088 | +0.548 |

`block_comm` correlates `+0.716` with `modularity`: above the OECD
threshold, redundant by construction (both compute community structure
from the same algorithm). `block_layer` correlates `-0.771` with
`acyclicity` and `+0.974` with `feedback` (see pairwise table in the
raw results): a near-duplicate of the feedback signal in disguise. Both
are dropped on validity grounds.

`feedback` and `bandwidth` pass discriminant validity. The strongest
cross-axis correlation is `feedback` vs `acyclicity` at `-0.688`, just
under threshold. Pairwise these two are also distinct (`r = -0.581`).

### Why the two surviving candidates still don't ship as scalars

Both `feedback` and `bandwidth` fail the same OECD criteria that
killed call-weighted Q as a score axis (see `CALL_WEIGHTED_Q_EMPIRICS.md`):

1. **Direction is contested across the population.** Pure DAG-shaped
   projects in the bench (`pygments`, `archy`, `fastapi`, `botocore`,
   `boto3`) score `feedback < 0.02`. Cycle-heavy projects (`msgspec`
   at 0.333, `click` at 0.231, `flask` at 0.221) score high. But
   `acyclicity` and `cycle_count` already report this, and they
   already weight tangle severity rather than raw back-edge count.
   `feedback` is a different normalization of the same underlying
   property, with no clear story for *why* the new normalization is
   better at predicting maintenance cost or defect rate.

2. **Bandwidth direction inverts intuition.** `starlette` (0.400) and
   `fastapi` (0.474) post the *highest* bandwidth values in the bench;
   `pygments` (0.476) ties them. These three are not analogously
   shaped: starlette and fastapi are well-regarded layered web
   frameworks, pygments is a wide-and-shallow plugin host. The metric
   conflates "long-range coupling" with "small number of edges spread
   across many layers," which is a different property and not one that
   maps cleanly to a refactoring action.

3. **Refactoring action is unclear or duplicative.** "Reduce feedback
   fraction" decomposes into "break import cycles" (already actionable
   from `archy cycles`) or "reorder topologically" (a no-op in source).
   "Reduce bandwidth" decomposes into "move tightly coupled modules
   adjacent in some ordering," which is not a thing engineers do.

4. **No additive interpretation as a diagnostic.** Unlike weighted Q
   where the *gap* between weighted and unweighted carried a distinct
   architectural reading, `feedback` and `bandwidth` are single-view
   scalars. There is no companion measurement to take a gap against.

## Why the DSM itself still ships

The decision is to ship DSM, not the DSM scalars. The empirics above
say that compressing a DSM into a single number throws away the
property that makes DSMs useful: *where* the entries sit. Three
concrete agent uses, none of which any single scalar captures:

- **Cycle localization.** An agent reading a DSM grouped by topological
  order sees back-edges as above-diagonal entries; the row and column
  indices name the specific modules involved. The scalar `feedback =
  0.221` for flask doesn't help; the matrix shows which 32 edges are
  the back-edges.
- **Layer violations.** Grouped by detected layer, the off-block-diagonal
  entries are exactly the cross-layer dependencies. `block_layer = 0.510`
  for flask doesn't tell an agent which dependencies violate which
  layer; the matrix does.
- **Propagation-cost transparency.** `propagation_cost` is already
  shipped (Tier 0 defect-prediction signal). It is computed from a
  DSM via reverse reach. Exposing the DSM lets an agent read the
  substrate the scalar is derived from, rather than treating the
  scalar as a black box.

These are agent-output properties (ASCII grid in a terminal context,
structured JSON for tool consumption), not number-in-a-score
properties. The roadmap framing was right; this empirical study just
confirms there's no shortcut through scalar promotion.

## What this analysis does not settle

- The implementation choice between Steward's binary DSM and a
  numeric DSM weighted by `call_count`. The visualization can carry
  either. To be decided when implementing `archy dsm`.
- The grouping algorithm default (`community`, `layer`, or
  `topological`). All three are defensible defaults for different
  agent tasks; the implementation will likely expose all three and
  pick one as the no-arg default based on which is most legible in
  ASCII at typical project sizes.
- Whether `archy dsm` should support filtering by package prefix to
  keep large projects (dagster at 5690 modules, django at 2904)
  legible. The bench shape suggests yes; the exact UX is for the
  implementation PR.

## Setup details

For reproducibility (matches the `CALL_WEIGHTED_Q_EMPIRICS.md` setup):

- 27 projects pinned in `bench/projects.yaml`.
- Internal-only subgraph (external nodes excluded, matching
  `propagation_cost`'s definition).
- Topological ordering uses NetworkX SCC condensation with
  alphabetical tie-breaking inside each SCC, so the DSM is
  deterministic even when cycles exist.
- Community detection uses NetworkX `greedy_modularity_communities`
  (Clauset-Newman-Moore greedy), the same algorithm `compute_modularity`
  uses.
- Depth bucketing computes the longest-path layer index on the SCC
  condensation; nodes in the same SCC share a depth, matching the
  `depth` axis's underlying computation.
