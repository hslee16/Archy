---
name: archy
description: Track architectural health of a Python codebase via the archy CLI and MCP server. Computes a four-axis quality score (modularity, acyclicity, depth, equality), detects import cycles via Tarjan SCCs, enforces YAML layer rules directly and transitively (via import-linter), and runs a snapshot/diff feedback loop so AI-assisted edits do not silently regress structure. Use when working in a Python project that contains `archy.yaml`, when the user mentions architectural drift, import cycles, layer violations, module coupling, blast radius, refactor risk, "what depends on this", or before any multi-file Python refactor.
license: MIT
compatibility: Requires Python 3.10+ and `pip install archy` (or `archy[contracts]` for transitive import-linter checks). The MCP server runs over stdio.
metadata:
  author: hslee16
  repository: https://github.com/hslee16/archy
  homepage: https://pypi.org/project/archy/
---

# archy

Archy turns the structural health of a Python codebase into numbers and rule violations an agent can act on between edits. This skill explains when to reach for it and how to drive its MCP tools as a tight feedback loop.

## Prerequisites

The agent must have access to the `archy` MCP server. The user wires it up once in their MCP client config:

```json
{
  "mcpServers": {
    "archy": { "command": "archy", "args": ["mcp"] }
  }
}
```

If the `archy_*` tools below are not visible, stop and ask the user to install archy (`pip install archy`) and add the stanza above. Do not fall back to running `archy` via Bash — the MCP server is the supported integration.

## When to activate

Activate this skill when any of the following is true:

- The repository root contains `archy.yaml` (definitive signal — the project has opted in)
- The user mentions: import cycle, architectural drift, layer violation, module coupling, blast radius, refactor risk, dependency graph, "what depends on X", or "is this safe to remove"
- An edit is about to touch more than one Python module
- An edit adds, removes, or changes an `import` statement
- The user asks for a structural review of a Python project

Do *not* activate this skill for: single-file scripts, non-Python projects, code-style or lint questions (use ruff/mypy), or test failures (use the test runner).

## The loop

Use this five-step cadence for any editing session that crosses module boundaries.

### 1. Snapshot at session start

Capture the baseline once per session, before any edits:

```
archy_snapshot(path=".")
```

This writes `.archy/baseline.json` (score, cycles, layer violations). The file is overwritten on each call, so a re-snapshot mid-session discards prior context. Do not re-snapshot unless the user explicitly restarts.

### 2. Look up impact before editing

Before modifying a module, understand what it touches.

For pure blast radius (who depends on me, transitively):

```
archy_impact(path=".", files=["src/app/db.py"])
```

For a richer bidirectional neighborhood with import line numbers and module instability scores, use focus instead:

```
archy_graph_focus(path=".", modules=["src/app/db.py"], depth=1, direction="both")
```

`direction` accepts `"in"` (who depends on me), `"out"` (my dependencies), or `"both"`. `depth` caps hop distance. Pass either file paths or qualnames.

When the target module is unknown ("where is the gravity in this codebase"), start with the overview:

```
archy_graph_summary(path=".", top_n=20)
```

Returns top modules by fan-in, fan-out, and PageRank, plus top external dependencies. Cheap. Read this before reading the full graph.

### 3. Edit the code

Make the change.

### 4. Check rules after every import-touching edit

If the edit added, removed, or changed any `import` statement:

```
archy_check(path=".")
```

Returns direct layer-rule violations from `archy.yaml` plus Stable Dependencies Principle violations (when `sdp.enabled: true`). For transitive enforcement (A → B → C still counts as A reaching C), additionally run:

```
archy_contracts(path=".")
```

Requires `pip install archy[contracts]`. A failed contract means the new import violates the declared architecture — revert or restructure rather than weakening the rule.

### 5. Diff against the baseline

After the edit:

```
archy_diff(path=".")
```

Returns per-axis score deltas plus the cycles and violations `added` / `resolved` since the snapshot.

Decision rule:

- `score_delta.overall < 0` OR `cycles.added` non-empty OR `violations.added` non-empty OR `sdp_violations.added` non-empty → regression. Surface the named modules to the user, propose a fix or revert, and re-diff after the correction. Do not commit until the diff is clean unless the user explicitly accepts the regression.
- `score_delta.overall >= 0` AND no additions on any of those fields → safe to proceed.

Loop back to step 4 after each correction.

## Tool reference

| Tool | Signature (defaults shown) | Use when |
|---|---|---|
| `archy_snapshot` | `(path)` | Once at session start. Writes `.archy/baseline.json`. |
| `archy_diff` | `(path)` | After every edit. Compares current state to the snapshot. |
| `archy_impact` | `(path, files: list[str])` | Sizing a refactor or removal by transitive reverse-dependents. |
| `archy_graph_focus` | `(path, modules: list[str], depth=1, direction="both", internal_only=True)` | Bounded local neighborhood with edges + line numbers. |
| `archy_graph_summary` | `(path, top_n=20)` | Top-N overview by fan-in / fan-out / PageRank. |
| `archy_graph` | `(path, internal_only=True, max_nodes=500)` | Full dump. Refuses graphs over `max_nodes`; prefer focus/summary for reasoning. |
| `archy_check` | `(path, config_path=None)` | After import changes. Direct-edge layer + SDP rules from `archy.yaml`. |
| `archy_contracts` | `(path, config_path=None)` | Transitive layer enforcement via import-linter. Requires `archy[contracts]`. |
| `archy_cycles` | `(path, min_size=2, internal_only=True)` | Standalone cycle listing (Tarjan SCCs + self-loops). |
| `archy_score` | `(path, internal_only=True, record=False, strict=False, strict_tolerance=0.02)` | Composite quality score. `record=True` appends to `.archy/history.jsonl`; `strict=True` fails on regression beyond tolerance. |
| `archy_record_baseline` | `(path, internal_only=True)` | Convenience: `archy_score(record=True)` for the start-of-session entry. |
| `archy_trend` | `(path, last_n=10)` | Recent score history (oldest-first). |

The MCP server also exposes a `loop` **prompt** containing the canonical playbook in archy's own words. Fetch it via `prompts/get name="loop"` for the always-current version.

## Decision rules

**Which check tool to run after an import edit:**

- Project has `archy.yaml` and no `.importlinter` → `archy_check`
- Project has `.importlinter` (or `archy[contracts]` installed and the user wants transitive enforcement) → `archy_check` AND `archy_contracts`
- Failed direct-edge check (`archy_check`) → fix or revert before continuing
- Passed direct-edge check but `archy_contracts` fails → an indirect path violates the rules; do not weaken the rule, restructure the path

**Which graph tool to reach for:**

- "What breaks if I remove this?" → `archy_impact`
- "What does this module depend on?" → `archy_graph_focus(direction="out")`
- "Who uses this and what edges?" → `archy_graph_focus(direction="both")` (carries import line numbers)
- "Where should I start reading this codebase?" → `archy_graph_summary`
- "I really need the whole graph" → `archy_graph` (bump `max_nodes` only after `archy_graph_summary` shows the project fits)

**Score vs. snapshot/diff:**

- Active editing session → snapshot + diff (no history pollution)
- CI gate or pre-commit hook → `archy_score(strict=True, record=True)` against `.archy/history.jsonl`
- Long-term trend question → `archy_trend`

**SDP (Stable Dependencies Principle) violations:**

- `mode: warn` in `archy.yaml` → report but do not block
- `mode: error` (default) → treat as a hard violation; same response as a layer-rule failure

## Common patterns

### Refactor pre-flight

```
archy_snapshot(path=".")
archy_graph_focus(path=".", modules=["src/app/auth.py"], depth=2, direction="both")
archy_impact(path=".", files=["src/app/auth.py"])
# Read both. Decide on scope. Edit.
archy_check(path=".")
archy_contracts(path=".")  # if archy[contracts] available
archy_diff(path=".")
```

### Adding a new module

```
archy_graph_summary(path=".", top_n=15)
# Edit: create the module and import it from one or two callers.
archy_check(path=".")  # confirms the new edges don't cross layers
archy_diff(path=".")   # confirms score did not regress
```

### Investigating a reported cycle

```
archy_cycles(path=".")
# Identify the SCC.
archy_graph_focus(path=".", modules=[<one node from the SCC>], depth=2, direction="both")
# Read import line numbers from the edges; choose the edge to break.
```

## References

- Repository: https://github.com/hslee16/archy
- Agent loop playbook: https://github.com/hslee16/archy/blob/main/docs/AGENT_LOOP.md
- Score formulas: https://github.com/hslee16/archy/blob/main/docs/SCORING.md
- Layer rules and `archy.yaml` syntax: https://github.com/hslee16/archy#layer-rules-archy-check
- Benchmarks across 27 projects: https://github.com/hslee16/archy/blob/main/bench/results.md
