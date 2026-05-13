# Agent feedback loop

archy turns a Python codebase's structural health into a number that an
AI agent can act on between edits, the way sentrux does for Rust. This
doc describes the recommended loop. The same playbook is also exposed
as the `loop` prompt on the MCP server, so an agent connected to
`archy mcp` can pull it on demand.

## The loop

1. **Snapshot** at session start so you have a baseline.

   - CLI: `archy snapshot .`
   - MCP: `archy_snapshot(path)`

   Captures the current score, cycle list, and layer-violation list to
   `.archy/baseline.json`. Write-once; the file is overwritten on each
   call so a session always starts from a known point.

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

   And when you don't yet know which module to look at, the top-N
   overview tool answers "where is the gravity in this codebase":

   - MCP: `archy_graph_summary(path)`

3. **Edit** the code as you normally would.

4. **Diff** after editing to see what got better, what got worse, and
   exactly which cycles or layer rules changed.

   - CLI: `archy diff .`
   - MCP: `archy_diff(path)`

   Returns per-component score deltas plus cycles and violations
   `added` / `resolved` since the baseline.

5. If `score_delta.overall` dropped or `cycles.added` /
   `violations.added` are non-empty, the change introduced a
   regression. Inspect the named modules, fix or revert, then loop
   back to step 4. Recurse until the diff is clean.

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

The detailed failure-mode-to-capability mapping and the implied roadmap priority order live in [`RESEARCH_METRICS.md` §14c](RESEARCH_METRICS.md).
