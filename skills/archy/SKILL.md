---
name: archy
description: Track architectural health of a Python codebase via the archy CLI and MCP server. Computes a five-axis quality score (modularity, acyclicity, depth, equality, complexity), detects import cycles via Tarjan SCCs, enforces YAML layer rules directly and transitively (via import-linter), ranks refactor priority via `cyclomatic_complexity * git_churn` hotspots, surfaces high-risk modules before edits, maps `git diff` to impacted test files for CI selection, and runs a snapshot/diff feedback loop so AI-assisted edits do not silently regress structure. Use when working in a Python project that contains `archy.yaml`, when the user mentions architectural drift, import cycles, layer violations, module coupling, blast radius, refactor risk, refactor priority, hotspots, affected tests, "what depends on this", "which tests should I run", "where should I refactor first", or before any multi-file Python refactor.
license: MIT
compatibility: Requires Python 3.10+ and `pip install archy` (or `archy[contracts]` for transitive import-linter checks). The MCP server runs over stdio.
metadata:
  author: hslee16
  repository: https://github.com/hslee16/archy
  homepage: https://pypi.org/project/archy/
---

# archy

Archy turns the structural health of a Python codebase into numbers and rule violations an agent can act on between edits. This skill explains when to reach for it and how to drive its MCP tools as a tight feedback loop.

Archy keeps a persistent parse cache (`.archy/index.db`), kept warm by a background file watcher in `archy mcp`, so its tools stay cheap to call: warm graph builds take a few seconds even on 10k+ module repos because only files whose content changed are re-parsed. Lean on that. Consult archy on *each* edit to keep your working surface relevant (impact before, diff after), not only at the start and end of a task. Freshness is automatic (every tool re-syncs changed files on demand, so a result is never stale); `archy_status` reports `last_synced_at` and whether the watcher is running if you want to check.

## Prerequisites

The agent must have access to the `archy` MCP server. The user wires it up once in their MCP client config:

```json
{
  "mcpServers": {
    "archy": { "command": "archy", "args": ["mcp"] }
  }
}
```

If the `archy_*` tools below are not visible, stop and ask the user to install archy (`pip install archy`) and add the stanza above. Do not fall back to running `archy` via Bash; the MCP server is the supported integration.

## When to activate

Activate this skill when any of the following is true:

- The repository root contains `archy.yaml` (definitive signal: the project has opted in)
- The user mentions: import cycle, architectural drift, layer violation, module coupling, blast radius, refactor risk, refactor priority, dependency graph, hotspots, affected tests, "what depends on X", "which tests should I run for this PR", "is this safe to remove", or "where should I refactor first"
- An edit is about to touch more than one Python module
- An edit adds, removes, or changes an `import` statement
- The user asks for a structural review of a Python project
- The user asks about per-function cyclomatic complexity, per-module CC aggregates, or per-file refactor priority

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

Before a non-trivial edit, check whether the target is a high-risk module:

```
archy_high_risk_modules(path=".", top_n=10)
```

Ranks internal modules by `edit_risk = geomean(propagation_cost, normalized_fan_in, instability)`. A high score means central *and* fragile; treat such edits with extra care or scope them down. Pairs with `archy_hotspots` (below) which answers "where is the refactoring leverage" using git churn rather than structural risk.

For CI-shaped test selection (a depth-bounded variant of `archy_impact` that pre-classifies impacted modules into tests vs. other downstream code):

```
archy_affected(path=".", files=["src/app/db.py"], depth=5)
```

Use this instead of `archy_impact` when the question is "given this diff, which tests should I run?" rather than "what's the full blast radius?". Returns `impacted_tests` and `impacted_modules` as separate lists. Test detection defaults to pytest conventions (`test_*.py`, `*_test.py`, anything under a `tests/` directory); override with `test_filter=<recursive glob>`. The depth cap prevents a single-line edit on a monorepo from fanning out to thousands of nodes. The CLI form `git diff --name-only HEAD | archy affected . --stdin -q | xargs pytest` is the canonical CI / pre-commit shape.

For positional context (where blocks are dense, where back-edges sit, where layer leakage shows up), reach for the Design Structure Matrix:

```
archy_dsm(path=".", group_by="community")
# or: archy_dsm(path=".", focus="src.app.auth", focus_depth=1)
# or: archy_dsm(path=".", package="src.app")
```

Returns a structured DSM: ordered row/col list plus a sparse cell list, grouped into block-diagonal blocks. `group_by="community"` orients in an unfamiliar codebase via Newman-community blocks; `"layer"` makes cross-layer dependencies visible as off-block entries; `"topological"` puts cycles above the diagonal so back-edges localize to specific module pairs. `weight="calls"` exposes `call_count` instead of binary edge presence. Pass `focus=<qualname>` to keep just the focus + its N-hop neighborhood, or `package=<prefix>` to scope to a single subpackage. The DSM is *visualization-only*, never a score input; agents read it positionally, not as a number ([`docs/research/DSM_EMPIRICS.md`](https://github.com/hslee16/Archy/blob/main/docs/research/DSM_EMPIRICS.md) for why).

For refactor priority across the whole codebase:

```
archy_hotspots(path=".", top_n=20, since=None)
```

Ranks files by `cc_sum * git_commit_count` (Tornhill / CodeScene's "Code Red"). The top of the list is where refactoring effort pays back the most. `since` is passed straight to `git log --since`; use it for "what should I refactor right now" recency-weighted views. Falls back gracefully on non-git projects (empty list plus a note pointing at `archy_high_risk_modules` as the structural alternative).

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

Requires `pip install archy[contracts]`. A failed contract means the new import violates the declared architecture; revert or restructure rather than weakening the rule.

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

When `score_delta.acyclicity` drops or `cycles.added` is non-empty, follow with a DSM diff to localize the offending edge. Save the DSM JSON before editing (`archy_dsm(path=".", group_by="topological")` and redirect to a file), then diff against it after:

```
archy_dsm(path=".", group_by="topological", baseline_path=".archy/dsm-before.json")
```

Returns a `DSMDiff` whose `new_back_edges` lists each `source -> target` pair the edit turned into a back-edge in the new ordering. That is the exact information needed to choose which import to remove or invert.

## Tool reference

| Tool | Signature (defaults shown) | Use when |
|---|---|---|
| `archy_snapshot` | `(path)` | Once at session start. Writes `.archy/baseline.json`. |
| `archy_diff` | `(path)` | After every edit. Compares current state to the snapshot. |
| `archy_impact` | `(path, files: list[str])` | Sizing a refactor or removal by transitive reverse-dependents. |
| `archy_affected` | `(path, files: list[str], depth=5, test_filter=None)` | CI-shaped impact: given changed files, return modules pre-classified into `impacted_tests` and `impacted_modules`. Depth-capped so monorepos stay tractable. Use for "which tests should I run for this diff?". |
| `archy_graph_focus` | `(path, modules: list[str], depth=1, direction="both", internal_only=True)` | Bounded local neighborhood with edges + line numbers. |
| `archy_graph_summary` | `(path, top_n=20)` | Top-N overview by fan-in / fan-out / PageRank. |
| `archy_graph` | `(path, internal_only=True, max_nodes=500)` | Full dump. Refuses graphs over `max_nodes`; prefer focus/summary for reasoning. |
| `archy_high_risk_modules` | `(path, top_n=10, internal_only=True)` | "Is this edit dangerous?" Top-N modules by `edit_risk` (geomean of propagation cost, normalized fan-in, instability). Call before a non-trivial edit. |
| `archy_hotspots` | `(path, top_n=20, since=None)` | "Where is the refactoring leverage?" Rank files by `cc_sum * git_commit_count` (Tornhill / CodeScene's "Code Red"). Pass `since` (e.g. `"1 year ago"`) for recency-weighted views. |
| `archy_check` | `(path, config_path=None)` | After import changes. Direct-edge layer + SDP rules from `archy.yaml`. |
| `archy_contracts` | `(path, config_path=None)` | Transitive layer enforcement via import-linter. Requires `archy[contracts]`. |
| `archy_cycles` | `(path, min_size=2, internal_only=True)` | Standalone cycle listing (Tarjan SCCs + self-loops). |
| `archy_score` | `(path, internal_only=True, record=False, strict=False, strict_tolerance=0.02)` | Composite five-axis quality score (modularity, acyclicity, depth, equality, complexity). Exposes a call-weighted Newman Q diagnostic alongside the unweighted modularity axis. `record=True` appends to `.archy/history.jsonl`; `strict=True` fails on regression beyond tolerance. |
| `archy_record_baseline` | `(path, internal_only=True)` | Convenience: `archy_score(record=True)` for the start-of-session entry. |
| `archy_trend` | `(path, last_n=10)` | Recent score history (oldest-first). |
| `archy_dsm` | `(path, group_by="community", weight="imports", focus=None, focus_depth=1, package=None, baseline_path=None)` | Design Structure Matrix view of the import graph. `group_by` is `community` / `layer` / `topological`. Narrow large projects with `focus` + `focus_depth` or `package`. When `baseline_path` is provided, returns a `DSMDiff` whose `new_back_edges` flags cycles the edit just introduced. Visualization-only; not part of any score. |

The MCP server also exposes a `loop` **prompt** containing the canonical playbook in archy's own words. Fetch it via `prompts/get name="loop"` for the always-current version.

## Decision rules

**Which check tool to run after an import edit:**

- Project has `archy.yaml` and no `.importlinter` → `archy_check`
- Project has `.importlinter` (or `archy[contracts]` installed and the user wants transitive enforcement) → `archy_check` AND `archy_contracts`
- Failed direct-edge check (`archy_check`) → fix or revert before continuing
- Passed direct-edge check but `archy_contracts` fails → an indirect path violates the rules; do not weaken the rule, restructure the path

**Which graph tool to reach for:**

- "What breaks if I remove this?" → `archy_impact` (full blast radius, unbounded)
- "Which tests should I run for this diff / PR?" → `archy_affected` (depth-capped, tests vs. modules separated)
- "What does this module depend on?" → `archy_graph_focus(direction="out")`
- "Who uses this and what edges?" → `archy_graph_focus(direction="both")` (carries import line numbers)
- "Where should I start reading this codebase?" → `archy_graph_summary`
- "I really need the whole graph" → `archy_graph` (bump `max_nodes` only after `archy_graph_summary` shows the project fits)
- "Is this module dangerous to edit?" → `archy_high_risk_modules` (structural; no git required)
- "Where should I focus refactoring effort?" → `archy_hotspots` (CC x git churn; needs a git repo)

**Which DSM grouping?**

- "What are the natural top-level blocks of this codebase?" → `group_by="community"` (Newman block-diagonal cohesion)
- "Which dependencies cross declared layers?" → `group_by="layer"` (off-block entries name the violations; pair with `weight="calls"` to weight by call traffic)
- "Which edges close which cycle?" → `group_by="topological"` (back-edges appear above the diagonal within an SCC block, named by source and target)
- "Is this module's neighborhood healthy?" → any grouping with `focus=<qualname>` to keep the matrix focused on the relevant rows and columns
- "Show me only this subpackage" → any grouping with `package=<prefix>`

**Reading the score breakdown.** `archy_score` returns five axes plus a call-weighted Q diagnostic. The diagnostic appears alongside the unweighted `modularity` line: the *gap* between unweighted and call-weighted raw Q is the load-bearing signal (it detects mismatch between the import-graph community structure and the call-graph community structure). The headline `overall` is the geometric mean of the five axes only; the call-weighted Q diagnostic is for context, not score. See [`docs/research/CALL_WEIGHTED_Q_EMPIRICS.md`](https://github.com/hslee16/Archy/blob/main/docs/research/CALL_WEIGHTED_Q_EMPIRICS.md) for what the gap means in practice.

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
archy_dsm(path=".", group_by="topological")
# The SCC sits as a contiguous block on the diagonal; entries above the
# diagonal inside that block are the back-edges that close the cycle.
archy_graph_focus(path=".", modules=[<one back-edge source from the DSM>], depth=2, direction="both")
# Read import line numbers from the edges; choose the edge to break.
```

### Orienting in an unfamiliar codebase

```
archy_graph_summary(path=".", top_n=20)
# Top fan-in / fan-out / PageRank modules. Names the hubs.
archy_dsm(path=".", group_by="community")
# Block-diagonal view of the top-level decomposition. Each block is one
# Newman community; row density inside a block names the central module
# of that block; off-block entries name the cross-cluster bridges.
```

### Finding what to refactor next

```
archy_hotspots(path=".", top_n=10)
# Top of the list is the highest `cc_sum * git_commit_count` product.
# These files cost the most attention per change; refactoring them
# pays back the most.
archy_graph_focus(path=".", modules=[<top hotspot>], depth=1, direction="both")
# Decide whether the right move is "extract some functions" (CC-driven)
# or "split the module" (structure-driven), then snapshot and edit.
```

For recency-weighted hotspots ("what's hot in the last quarter"):

```
archy_hotspots(path=".", top_n=10, since="3 months ago")
```

### Assessing edit risk before touching a module

```
archy_high_risk_modules(path=".", top_n=10)
# If the module you plan to edit is in this list, scope the edit down or
# pause for review. `edit_risk` is the geometric mean of propagation
# cost, normalized fan-in, and Martin's instability; high means the
# module is both central and fragile.
```

## References

- Repository: https://github.com/hslee16/Archy
- Agent loop playbook: https://github.com/hslee16/Archy/blob/main/docs/AGENT_LOOP.md
- Score formulas (five axes + call-weighted Q diagnostic): https://github.com/hslee16/Archy/blob/main/docs/SCORING.md
- Axis review (why 5 axes, why no 6th from calls_per_edge): https://github.com/hslee16/Archy/blob/main/docs/research/AXIS_REVIEW.md
- Call-weighted Q empirical study: https://github.com/hslee16/Archy/blob/main/docs/research/CALL_WEIGHTED_Q_EMPIRICS.md
- DSM empirical study (why DSM ships as visualization, not a scalar): https://github.com/hslee16/Archy/blob/main/docs/research/DSM_EMPIRICS.md
- Layer rules and `archy.yaml` syntax: https://github.com/hslee16/Archy#layer-rules-archy-check
- Benchmarks across 27 projects: https://github.com/hslee16/Archy/blob/main/bench/results.md
