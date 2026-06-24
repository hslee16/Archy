# Agent feedback loop

archy turns a Python codebase's structural health into a number that an
AI agent can act on between edits, the way sentrux does for Rust. This
doc describes the recommended loop. The same playbook is also exposed
as the `loop` prompt on the MCP server, so an agent connected to
`archy mcp` can pull it on demand.

A persistent parse cache (`.archy/index.db`), kept warm by a background file
watcher inside `archy mcp`, keeps every call cheap: warm graph builds are a
few seconds even on 10k+ module repos, because only files whose content
changed are re-parsed. That economics is the point of the loop below: consult
archy on *each* edit to keep your working surface relevant (impact before,
diff after), not just once at the start and end. Freshness is automatic: every
tool re-syncs changed files on demand, so a result is never stale; the
`archy_status` tool reports `last_synced_at` and whether the watcher is running
if you want to confirm it explicitly.

## The loop

1. **Snapshot** at session start so you have a baseline.

   - CLI: `archy snapshot .`
   - MCP: `archy_snapshot(path)`

   Captures the current score, cycle list, and layer-violation list to
   `.archy/baseline.json`. Write-once; the file is overwritten on each
   call so a session always starts from a known point. The MCP tool also
   returns an `invariant_brief` (declared layers, forbidden edges, the
   acyclic invariant, baseline score per axis, and the load-bearing /
   highest-`edit_risk` modules) so the agent is told the constraints up
   front and can avoid a cross-layer or cycle-introducing edit before
   making it, not just catch it in the step-4 diff.

2. **Look up impact** before editing a module so you know who breaks
   if the change is wrong.

   - CLI: `archy impact . --file src/foo.py`
   - MCP: `archy_impact(path, files=[<path>])`

   Returns the set of internal modules that transitively import the
   given file(s). Useful for sizing a refactor before you commit to it.

   For a bounded, bidirectional neighborhood that also includes the
   module's *forward* dependencies (and import line numbers on each
   edge), reach for `archy_graph_focus` instead:

   - CLI: not exposed; use `archy graph` and grep
   - MCP: `archy_graph_focus(path, modules=[<file or qualname>])`

   And when you don't yet know which module to look at, the graph tool
   answers "where is the gravity in this codebase" with a top-N overview
   (summary by default; pass `response_format="full"` only when you
   actually need the whole node/edge dump):

   - MCP: `archy_graph(path)`  (or the equivalent `archy_graph_summary(path)`)

   Before a non-trivial edit (refactor, public-surface change, anything
   touching more than a handful of files), check whether your target
   sits in the project's central-and-fragile zone:

   - MCP: `archy_high_risk_modules(path)`

   Returns the top-N modules by `edit_risk`: the geometric mean of
   propagation cost, normalized fan-in, and Martin's instability. Each
   entry breaks the composite back out into its components so you can
   see *why* a module ranks high. If your target is on the list, scope
   down or pause for review before proceeding.

   The churn-aware sibling answers "where is the refactoring leverage?"
   rather than "is this edit dangerous?":

   - MCP: `archy_hotspots(path)`

   Returns the top-N modules by `cc_sum x git-commit-count` (Tornhill /
   CodeScene's "Code Red"); each entry is `{module, path, cc_sum, churn,
   score}`. Needs git history; if the project isn't under git, returns
   an empty list plus a `note` so you can pivot back to
   `archy_high_risk_modules`. Most useful when planning a refactoring
   sprint, less useful for a single targeted edit.

   To answer "what should I refactor first?" without calling both tools
   and merging by hand, use the fused list:

   - MCP: `archy_what_to_refactor_next(path)`

   It sums each lens's normalized score (CC x churn and edit-risk) into a
   `priority`, so a module flagged by *both* generally outranks a comparable
   single-lens one - though a dominant single-lens signal (a giant hotspot
   at the import-graph leaves) can still rank first; each entry names which
   `lenses` fired plus a one-line `rationale`. Without
   git it degrades to the structural lens alone. An empty list plus a
   `note` is a real answer - nothing is both complex+churned and nothing
   is central+fragile above the floor - so take "nothing to prioritize"
   at face value rather than lowering the bar to manufacture a target.

   When you need *where*, not *how much*, reach for the Design Structure
   Matrix:

   - CLI: `archy dsm . --group community` (orientation in an unfamiliar
     repo) or `archy dsm . --focus <module>` (read the module's row and
     column to see who you depend on and who depends on you)
   - MCP: `archy_dsm(path, group_by="community"|"layer"|"topological",
     focus=<qualname>, package=<prefix>)` returns a compact summary
     (block structure, counts, back-edges) by default; pass
     `response_format="full"` to read the positional matrix cell-by-cell.

   The DSM is structured context an agent reads positionally rather
   than a number to act on: block-diagonal blocks under community
   grouping name the top-level decomposition; above-diagonal entries
   under topological ordering localize back-edges to specific module
   pairs; off-block entries under layer grouping show which
   dependencies cross declared layers. Save the JSON output as a DSM
   snapshot before editing so step 4 can diff against it. See
   [`docs/research/DSM_EMPIRICS.md`](research/DSM_EMPIRICS.md) for why DSM ships as a
   visualization rather than a score axis.

   If the edit changes imports, simulate the edge delta first to predict
   its structural consequence before writing anything:

   - CLI: `archy simulate . --add app.a:app.b --remove app.c:app.d`
   - MCP: `archy_simulate(path, add=[{from, to}], remove=[...])`

   It applies the delta to an in-memory copy of the graph and returns the
   would-be cycles, layer/SDP violations, per-axis score delta, and
   blast-radius change, no file written. If the simulation shows a new
   cycle, reshape the plan before editing instead of catching it in the
   diff. Empirically the prediction matches the post-edit diff exactly
   when the import maps 1:1 to the named edge (~96% of single-line
   imports; importing a submodule also pulls in its ancestor packages,
   so include those edges to model it exactly). See
   [`docs/research/SIMULATE_ORACLE_EMPIRICS.md`](research/SIMULATE_ORACLE_EMPIRICS.md).

3. **Edit** the code as you normally would.

4. **Diff** after editing to see what got better, what got worse, and
   exactly which cycles or layer rules changed.

   - CLI: `archy diff .`
   - MCP: `archy_diff(path)`

   Returns a risk-weighted `summary` (headline + `top_regressions` /
   `top_improvements`) plus per-component score deltas and the full
   cycles and violations `added` / `resolved` lists. Read
   `summary.headline` first, then walk `summary.top_regressions` in
   order; each item carries a 0-1 `risk` weight derived from
   `compute_edit_risk`, so the most central-and-fragile breakage
   surfaces first, plus a `prompt` that reframes the delta as the
   judgment question to answer ("Acyclicity dropped because
   `a -> b -> a` is now a cycle; intended, or invert an edge?"), so you
   act on the decision rather than re-deriving it from numbers. The raw
   blocks remain available when you need the full list, but the summary
   is the right starting point.

   If `acyclicity` dropped or `cycles.added` is non-empty, the
   companion DSM diff names the specific edge that closed the cycle:

   - CLI: `archy dsm . --group topological --diff <path/to/dsm.json>`
   - MCP: `archy_dsm(path, group_by="topological",
     baseline_path=<path>)`

   The `new_back_edges` field on `DSMDiff` lists every source-target
   pair that turned into a back-edge in the new ordering, which is the
   exact information needed to pick which import to remove or invert.

5. If `summary.headline` shows `overall -...` or `summary.top_regressions`
   is non-empty, the change introduced a regression. Inspect the named
   modules in order (highest `risk` first), fix or revert, then loop
   back to step 4. Recurse until `top_regressions` is empty.

## When to use which gate

`archy score --strict` is a one-shot gate against the last recorded
run in `.archy/history.jsonl`, not against a session baseline. It's
the right tool for CI and pre-commit hooks. The snapshot/diff loop is
the right tool for the *active editing* phase, when you want to see
deltas after every change without polluting trend history.

Both are stateless from archy's perspective: the score history and
the snapshot file are the only state, and both live under `.archy/`
in the project being analyzed.

## Worked example (terminal)

```text
$ archy snapshot .
# baseline written to .archy/baseline.json
# score: 0.638  cycles: 0  violations: 0

# (edit some code, accidentally introduce a cycle)

$ archy diff .
# score deltas (current - baseline):
  overall     -0.220
  modularity  -0.012
  acyclicity  -0.500
  depth       +0.000
  equality    -0.014

# cycles: +1 added, -0 resolved
  + cycle: app.libs.db, app.services.auth

# violations: +0 added, -0 resolved
```

The `acyclicity` drop and the `+ cycle` line localize the regression
in one read; the agent now knows to break the cycle and re-diff.

## MCP discovery

Agents connected to `archy mcp` can fetch the playbook via the standard
MCP `prompts/list` and `prompts/get` calls:

```python
prompts = await session.list_prompts()
loop = await session.get_prompt(name="loop")
```

This is the distribution mechanism: `pip install archy` and start
`archy mcp`, and any MCP-aware agent gets both the tools and the
instructions for using them as a loop.

## Empirical backing

Why this loop exists in this shape, with citations:

- **Navigation Paradox** (Feb 2026, [arxiv:2602.20048](https://arxiv.org/html/2602.20048v1)) shows that larger LLM context windows do not eliminate the need for structural graph navigation. Failure shifts from retrieval capacity to navigational salience. The MCP `archy_graph_focus` + `archy_impact` calls in step 2 of the loop above exist specifically because long context alone is not enough; the agent has to be pointed at the architecturally critical files.
- **LocAgent ablation** (ACL 2025, [aclanthology:2025.acl-long.426](https://aclanthology.org/2025.acl-long.426/)) finds that removing graph traversal from an LLM agent significantly degrades function-level code localization. The whole-graph view alone is not enough either; bounded local neighborhoods matter, which is why `archy_graph_focus` returns a subgraph rather than the full dump.
- **Coding-agent failure-mode literature** (Columbia DAPLab, Anthropic, Stack Overflow synthesis, 2026) names "scope drift", "context exhaustion", and "cross-file reasoning failure" as recurring patterns. The snapshot/diff cycle in steps 1, 4, and 5 of the loop above is built to catch the first; the bounded `archy_graph_focus` and `archy_impact` calls in step 2 are built to catch the third.

The detailed failure-mode-to-capability mapping and the implied roadmap priority order live in [`RESEARCH_METRICS.md` §14c](research/RESEARCH_METRICS.md).
