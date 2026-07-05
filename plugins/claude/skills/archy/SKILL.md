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

Read the `invariant_brief` in the result before your first edit: the declared layers and the forbidden edges between them, whether the graph is currently acyclic, the baseline score per axis, and the load-bearing modules (highest `edit_risk`, treat as high blast radius). Knowing the constraints up front lets you avoid proposing a cross-layer or cycle-introducing edit in the first place, instead of finding out from the step-5 diff.

### 2. Look up impact before editing

Before modifying a module, understand what it touches.

For pure blast radius (who depends on me, transitively):

```
archy_impact(path=".", files=["src/app/db.py"])
```

`impacted` lists the transitive dependents; `chains` tells you *why* each is reachable, the shortest import path back to a changed module with the line numbers on each hop. Cite that edge when you make the edit (e.g. "preserve `billing.invoice -> auth.session -> auth.tokens`") instead of guessing which dependents matter. Chains are ranked closest-first and capped (`max_chains`, `chains_omitted` reports the remainder).

For a richer bidirectional neighborhood with import line numbers and module instability scores, pass `focus` to the graph tool (this replaces the old `archy_graph_focus`):

```
archy_graph(path=".", focus=["src/app/db.py"], depth=2, direction="both")
```

`direction` accepts `"in"` (who depends on me), `"out"` (my dependencies), or `"both"`. `depth` caps hop distance. Pass either file paths or qualnames. With `focus` set the neighborhood is already bounded, so `response_format`/`max_nodes`/`top_n` do not apply.

When the target module is unknown ("where is the gravity in this codebase"), start with the overview (the default `response_format="summary"`, which replaces the old `archy_graph_summary`):

```
archy_graph(path=".")
```

Returns top modules by fan-in, fan-out, and PageRank, plus top external dependencies. Cheap. Read this before pulling the full dump with `response_format="full"`.

Before a non-trivial edit, check whether the target is a high-risk module (the structural lens of the refactor tool, replacing the old `archy_high_risk_modules`):

```
archy_what_to_refactor_next(path=".", lens="structural", top_n=10, min_risk=0)
```

Ranks internal modules by `edit_risk = geomean(propagation_cost, normalized_fan_in, instability)`. A high score means central *and* fragile; treat such edits with extra care or scope them down. Pass `min_risk=0` to surface every module (no floor). The behavioral lens (`lens="behavioral"`, below) answers "where is the refactoring leverage" using git churn rather than structural risk.

For CI-shaped test selection (a depth-bounded variant of `archy_impact` that pre-classifies impacted modules into tests vs. other downstream code), pass `mode="affected"` (this replaces the old `archy_affected`):

```
archy_impact(path=".", files=["src/app/db.py"], mode="affected", depth=5)
```

Use this instead of the default `mode="blast"` when the question is "given this diff, which tests should I run?" rather than "what's the full blast radius?". Returns `impacted_tests` and `impacted_modules` as separate lists. Test detection defaults to pytest conventions (`test_*.py`, `*_test.py`, anything under a `tests/` directory); override with `test_filter=<recursive glob>`. The depth cap prevents a single-line edit on a monorepo from fanning out to thousands of nodes. The CLI form `git diff --name-only HEAD | archy affected . --stdin -q | xargs pytest` is the canonical CI / pre-commit shape.

For positional context (where blocks are dense, where back-edges sit, where layer leakage shows up), reach for the Design Structure Matrix:

```
archy_dsm(path=".", group_by="community")
# or: archy_dsm(path=".", focus="src.app.auth", focus_depth=1)
# or: archy_dsm(path=".", package="src.app")
```

Returns a compact summary by default (block structure, counts, back-edges, cross-block coupling); pass `response_format="full"` for the full positional matrix (ordered row/col list plus a sparse cell list, grouped into block-diagonal blocks; refused over `DEFAULT_MAX_DSM_CELLS` cells). `group_by="community"` orients in an unfamiliar codebase via Newman-community blocks; `"layer"` makes cross-layer dependencies visible as off-block entries; `"topological"` puts cycles above the diagonal so back-edges localize to specific module pairs. `weight="calls"` exposes `call_count` instead of binary edge presence. Pass `focus=<qualname>` to keep just the focus + its N-hop neighborhood, or `package=<prefix>` to scope to a single subpackage. The DSM is *visualization-only*, never a score input; agents read it positionally, not as a number ([`docs/research/DSM_EMPIRICS.md`](https://github.com/hslee16/Archy/blob/main/docs/research/DSM_EMPIRICS.md) for why).

For refactor priority across the whole codebase, the behavioral lens ranks files by `cc_sum * git_commit_count` (Tornhill / CodeScene's "Code Red"; replaces the old `archy_hotspots`):

```
archy_what_to_refactor_next(path=".", lens="behavioral", top_n=20, since=None)
```

The top of the list is where refactoring effort pays back the most. `since` is passed straight to `git log --since`; use it for "what should I refactor right now" recency-weighted views. Off git, the behavioral lens cannot run (its `note` points at `lens="structural"` as the git-free alternative).

To get both refactor-priority lenses fused in one call instead of running the behavioral and structural lenses separately and merging by hand, use the default `lens="fused"`:

```
archy_what_to_refactor_next(path=".", top_n=10, since=None, min_risk=0.15)
```

Sums the behavioral lens (CC x churn hotspots) and the structural lens (edit-risk: central+fragile) into a `priority`, so a module flagged by *both* generally outranks a comparable single-lens one (a dominant single-lens signal, like a giant hotspot at the import-graph leaves, can still rank first). Each entry lists which `lenses` fired and a one-line `rationale`. Without git, the fused ranking is structural-only (`git_available=false`). An empty `priorities` plus a `note` is a real answer: nothing is both complex+churned and nothing is central+fragile above `min_risk`, so there is nothing for these two lenses to prioritize.

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

Returns per-axis score deltas plus the cycles and violations `added` / `resolved` since the snapshot, and a risk-weighted `summary`. Read `summary.top_regressions` first: each item carries a `prompt` that frames the delta as a judgment question with its cause ("Acyclicity dropped because `models -> services -> models` now form an import cycle. Intended, or should an edge be inverted?"), so you act on the decision rather than re-reading raw numbers.

Decision rule:

- `score_delta.overall < 0` OR `cycles.added` non-empty OR `violations.added` non-empty OR `sdp_violations.added` non-empty → regression. Surface the named modules to the user, propose a fix or revert, and re-diff after the correction. Do not commit until the diff is clean unless the user explicitly accepts the regression.
- `score_delta.overall >= 0` AND no additions on any of those fields → safe to proceed.

Loop back to step 4 after each correction.

When `score_delta.acyclicity` drops or `cycles.added` is non-empty, follow with a DSM diff to localize the offending edge. Save the full DSM JSON before editing (`archy_dsm(path=".", group_by="topological", response_format="full")` and redirect to a file -- a diff baseline needs the full matrix, not the summary), then diff against it after:

```
archy_dsm(path=".", group_by="topological", baseline_path=".archy/dsm-before.json")
```

Returns a `DSMDiff` whose `new_back_edges` lists each `source -> target` pair the edit turned into a back-edge in the new ordering. That is the exact information needed to choose which import to remove or invert.

## Tool reference

| Tool | Signature (defaults shown) | Use when |
|---|---|---|
| `archy_snapshot` | `(path)` | Once at session start. Writes `.archy/baseline.json`. |
| `archy_diff` | `(path)` | After every edit. Compares current state to the snapshot. |
| `archy_impact` | `(path, files: list[str], mode="blast", max_chains=..., depth=5, test_filter=None)` | `mode="blast"` (default): sizing a refactor or removal by transitive reverse-dependents (with `chains`). `mode="affected"` (replaces `archy_affected`): CI-shaped impact, returns modules pre-classified into `impacted_tests` and `impacted_modules`, depth-capped for monorepos. Use `affected` for "which tests should I run for this diff?". |
| `archy_simulate` | `(path, add: list[{from,to}]=None, remove=None)` | Predict an import-edge change *before writing it*: would-be cycles, layer violations, score + blast-radius delta. Reshape a plan that introduces a cycle before editing. |
| `archy_graph` | `(path, response_format="summary", focus=None, depth=2, direction="both", internal_only=True, max_nodes=500, top_n=20)` | No `focus`: `response_format="summary"` (default) = top-N overview by fan-in / fan-out / PageRank (replaces `archy_graph_summary`); `response_format="full"` = full dump, refused over `max_nodes`. With `focus=[...]` (replaces `archy_graph_focus`): bounded local neighborhood with edges + line numbers; `depth`/`direction` apply and `response_format`/`max_nodes`/`top_n` do not. |
| `archy_what_to_refactor_next` | `(path, lens="fused", top_n=10, since=None, min_risk=0.15)` | "What should I refactor first?" `lens="fused"` (default) sums hotspots (CC x churn) and edit-risk (central+fragile) into a `priority`. `lens="behavioral"` (replaces `archy_hotspots`): CC x churn only, needs git. `lens="structural"` (replaces `archy_high_risk_modules`): edit-risk only, git-free; pass `min_risk=0` for no floor. Each entry names the firing `lenses` + a `rationale`. |
| `archy_check` | `(path, config_path=None)` | After import changes. Direct-edge layer + SDP rules from `archy.yaml`. |
| `archy_contracts` | `(path, config_path=None)` | Transitive layer enforcement via import-linter. Requires `archy[contracts]`. |
| `archy_cycles` | `(path, min_size=2, internal_only=True)` | Standalone cycle listing (Tarjan SCCs + self-loops). |
| `archy_score` | `(path, internal_only=True, record=False, strict=False, strict_tolerance=0.02)` | Composite five-axis quality score (modularity, acyclicity, depth, equality, complexity). Exposes a call-weighted Newman Q diagnostic alongside the unweighted modularity axis. `record=True` appends to `.archy/history.jsonl` (replaces `archy_record_baseline` for the start-of-session entry); `strict=True` fails on regression beyond tolerance. |
| `archy_trend` | `(path, last_n=10)` | Recent score history (oldest-first). |
| `archy_dsm` | `(path, response_format="summary", group_by="community", weight="imports", focus=None, focus_depth=1, package=None, baseline_path=None)` | Design Structure Matrix view of the import graph. `response_format="summary"` (default) = compact block structure + counts + back-edges; `response_format="full"` = full matrix (refused over `DEFAULT_MAX_DSM_CELLS` cells). `group_by` is `community` / `layer` / `topological`. Narrow large projects with `focus` + `focus_depth` or `package`. When `baseline_path` is provided, returns a `DSMDiff` (regardless of `response_format`) whose `new_back_edges` flags cycles the edit just introduced. Visualization-only; not part of any score. |
| `archy_duplicates` | `(path, response_format="summary", min_nodes=30, top_n=20, members=2)` | Two-tier duplicate-function clusters (identical normalized body shape): `duplicates` (likely-real, investigate) + `variants` (demoted likely-intentional clusters - same-class / boilerplate / test / vendored, each with a `variant_reason`). Within `duplicates`, `exact=true` marks byte-identical (Type-1) clusters, the highest-confidence "definitely refactor these" subset. Advisory surfacer, not a score axis; refactorability is a semantic call you make (~50% primary precision, ~63% on the exact subset - ~74% on non-test source). `response_format="summary"` (default) = ranking fields + one sample member per cluster; `"full"` = full member lists. |

The MCP server also exposes a `loop` **prompt** containing the canonical playbook in archy's own words. Fetch it via `prompts/get name="loop"` for the always-current version.

**Error model (recovery contract).** A failure is one of two kinds. A **usage error** (bad argument value like `response_format="xml"` or `last_n=0`, a malformed `archy.yaml`, or a project over the scan ceiling) comes back as `isError: true` (a raised error); fix the call or the environment. A **recoverable condition** comes back as a normal `isError: false` result you branch on: either a payload with an `error` field and no success data (no baseline → `DiffErrorPayload`, output too large → `*TooLargePayload`, no `archy.yaml` → `CheckErrorPayload`), or an advisory field on a still-valid result (`available=false`, `note`, `git_available`). So `archy_check` on a project with no config returns an in-band `CheckErrorPayload`, not an error: create a config or move on.

## Decision rules

**Which check tool to run after an import edit:**

- Project has `archy.yaml` and no `.importlinter` → `archy_check`
- Project has `.importlinter` (or `archy[contracts]` installed and the user wants transitive enforcement) → `archy_check` AND `archy_contracts`
- Failed direct-edge check (`archy_check`) → fix or revert before continuing
- Passed direct-edge check but `archy_contracts` fails → an indirect path violates the rules; do not weaken the rule, restructure the path

**Which graph tool to reach for:**

- "What breaks if I remove this?" → `archy_impact` (full blast radius, unbounded)
- "If I add/remove this import, what breaks, before I write it?" → `archy_simulate` (would-be cycles / violations / score delta, no file written)
- "Which tests should I run for this diff / PR?" → `archy_impact(mode="affected")` (depth-capped, tests vs. modules separated)
- "What does this module depend on?" → `archy_graph(focus=[...], direction="out")`
- "Who uses this and what edges?" → `archy_graph(focus=[...], direction="both")` (carries import line numbers)
- "Where should I start reading this codebase?" → `archy_graph` (default summary)
- "I really need the whole graph" → `archy_graph(response_format="full")` (bump `max_nodes` only after the default summary shows the project fits)
- "Is this module dangerous to edit?" → `archy_what_to_refactor_next(lens="structural")` (structural; no git required)
- "Where should I focus refactoring effort?" → `archy_what_to_refactor_next(lens="behavioral")` (CC x git churn; needs a git repo)

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
archy_graph(path=".", focus=["src/app/auth.py"], depth=2, direction="both")
archy_impact(path=".", files=["src/app/auth.py"])
# Read both. Decide on scope. Edit.
archy_check(path=".")
archy_contracts(path=".")  # if archy[contracts] available
archy_diff(path=".")
```

### Adding a new module

```
archy_graph(path=".", top_n=15)
# Edit: create the module and import it from one or two callers.
archy_check(path=".")  # confirms the new edges don't cross layers
archy_diff(path=".")   # confirms score did not regress
```

### Investigating a reported cycle

```
archy_cycles(path=".")
# Identify the SCC.
archy_dsm(path=".", group_by="topological")
# The default summary lists `back_edges` directly: the (source, target)
# pairs that point against the topological order = the edges closing the
# cycle. (Use response_format="full" to read the matrix positionally.)
archy_graph(path=".", focus=[<one back-edge source from the DSM>], depth=2, direction="both")
# Read import line numbers from the edges; choose the edge to break.
```

### Orienting in an unfamiliar codebase

```
archy_graph(path=".", top_n=20)
# Top fan-in / fan-out / PageRank modules. Names the hubs.
archy_dsm(path=".", group_by="community")
# The default summary names the Newman-community blocks and their sizes
# plus cross-block coupling = the top-level decomposition. Drop to
# response_format="full" to read row density inside a block (the central
# module of that block); off-block entries name the cross-cluster bridges.
```

### Finding what to refactor next

```
archy_what_to_refactor_next(path=".", top_n=10)
# One fused list: both-lens modules (a CC x churn hotspot AND central+fragile)
# rank first. Read each entry's `lenses` and `rationale`. An empty list with a
# `note` means there is genuinely nothing to prioritize - take it at face value.
archy_graph(path=".", focus=[<top entry>], depth=1, direction="both")
# Decide whether the right move is "extract some functions" (CC-driven)
# or "split the module" (structure-driven), then snapshot and edit.
```

To inspect a single lens directly: `archy_what_to_refactor_next(lens="behavioral")` for behavioral leverage (CC x churn; needs git, supports `since="3 months ago"` for recency-weighted views) or `archy_what_to_refactor_next(lens="structural")` for structural danger (edit-risk; no git required).

### Assessing edit risk before touching a module

```
archy_what_to_refactor_next(path=".", lens="structural", top_n=10, min_risk=0)
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
