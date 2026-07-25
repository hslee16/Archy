# Spec: visualization surface (static export + terminal-native live)

Status: Branch A shipped in scoped form (`dsm` + `trend`); Branch B still
design. Tracking: [#283]; branches
[#284] (static export, first) and [#285] (terminal-native live). This spec fixes the scope, the
anti-theater gate, and the two-branch plan before any UI code lands. It is the
first archy feature whose primary user is the **human governor**, not the
agent, so the framing guardrails matter more than usual.

## 1. The gap

archy computes every structural signal worth showing and renders **none** of
it. `archy graph --format dot` emits Graphviz DOT (`cli.py`) that nothing draws;
`.archy/history.jsonl` is a per-commit time series of the five axes that nothing
plots; `watcher.py` already runs a `watchdog` observer (2s debounce) inside
`archy mcp`, so the live event stream a real-time view needs already exists.

sentrux (MIT, pure-Rust desktop GUI + hosted scanner, 52 languages) fills this
with a live interactive treemap whose files glow as the agent edits them. Their
own pitch is explicitly human-facing: the terminal took away the IDE file-tree,
so you can no longer see a cycle form or an internal module leak as it happens.

**Positioning, stated honestly.** archy's core loop is agent-first: the agent
reads JSON and self-corrects on the score. Visualization does not improve that
loop; it serves a *different* user, the human watching the agent. We are not
out-treemapping a funded Rust desktop app on breadth (52 languages, native GUI).
We win by being the free, MIT, **terminal-native**, Python/agent-first tool that
*also* renders, staying where the agent session already runs.

## 2. Anti-theater gate (normative)

A generic force-directed import graph does **not** ship as a headline. It is
pretty, low-signal, present in every tool, and already conveyed to the agent as
JSON. A visual earns a slot only when it makes a signal archy *uniquely*
computes faster to grok than text. Exactly four qualify, and every view must
encode at least one:

1. **edit-risk heatmap** - nodes colored by `edit_risk`; the hot nodes are where
   the next agent edit is dangerous.
2. **cycle tangles** - SCC membership highlighted (the acyclicity axis; near
   unreadable as a node list).
3. **DSM back-edges** - a matrix cell above the diagonal is a layer
   violation / cycle seed. The one view text genuinely cannot replace.
4. **score trajectory** - the five axes from `history.jsonl`; archy's founding
   story ("cycle count doubled over six weeks and nobody noticed").

This gate is the acceptance test for every view proposed below and any added
later.

## 3. Branch A - static export (shipped, scoped to two views)

`archy render` produces a **self-contained HTML file** (offline, no CDN, no
server). Attach to a PR, drop in docs, paste in an issue.

Views (`--view`):
- `dsm` (default): the DSM as a colored matrix in inline SVG. Highest
  signal-per-pixel. **Shipped.**
- `trend`: the five axes over `history.jsonl` as inline SVG sparklines, each
  with its numeric range and window delta. **Shipped.**
- `graph`: nodes colored by edit-risk, cycle members ringed, back-edges /
  layer violations flagged. **Deferred behind a usage signal**, see §3a.

Constraints (as built):
- **Zero runtime dep, not just no heavy one.** Both shipped views are static,
  so they need no layout engine and vendor no JavaScript at all: inline SVG and
  inline CSS, no `<script>`, no external request. That is stronger than the
  original "vendor the JS" constraint and cost nothing to reach.
- Deterministic output (byte-stable for a fixed input) so it diffs cleanly and
  snapshot-tests. No wall-clock time is embedded. Reuse the greedy
  (order-stable) grouping, never Louvain (parse-order-dependent; see
  LEARNINGS.md).
- Reads the same cached graph the MCP server builds; no re-parse cost.

**What red means is grouping-dependent, and the renderer enforces it.** The
gate credits the DSM view with "back-edges" (signal 3), but `row > col` only
means *back-edge* under a topological ordering. Under community or layer
grouping the block order is not a dependency order, so flagging `row > col`
there is noise: on archy itself it painted 168 of 243 cells red. The shipped
renderer therefore flags back-edges under `--group=topological` and
block-crossing edges under `--group=community|layer`, and says which in the
legend. A view that colors cells by a property its ordering does not encode is
theater even when the underlying number is real.

### 3a. Why the `graph` view is deferred

It is the only view that forces a vendored force-layout engine (Cytoscape.js /
d3) into the wheel, and it is the weakest of the three against §2: a node-link
diagram is present in every tool, whereas a DSM's cell positions and a score
trajectory are things text conveys poorly. Deferring it holds Branch A to the
same **usage-signal** bar that §6.3 already applies to the `archy_render` MCP
tool, and that `PATTERN_DETECTION_EMPIRICS.md`, `CONSTRAINT_AWARE_ORCHESTRATION_SYNTHESIS.md`,
and `INLOOP_PREVALENCE_EMPIRICS.md` apply to their own candidates. The human
governor is a hypothesized user archy has never observed, and unlike the
agent-facing surface there is no benchmark that can falsify a rendering's
value, so cost discipline is the only available check.

Exit condition, so the deferral does not silently become a wontfix: an outside
request for it, or evidence of `archy render` output being used in public.
Prior art for the shape when it is built: `tldr-skill` generates a
self-contained Cytoscape.js dependency graph from a Python builder with zero
LLM tokens.

## 4. Branch B - terminal-native live view (second)

`archy watch` opens a **Textual** TUI that stays in the terminal where the agent
runs. It reuses the `watcher.py` event stream: on each debounced change it
re-syncs the graph, recomputes the score, and updates in place - the score, the
newest cycles, and the just-edited hot nodes animate as the agent works. This is
the "glow when modified" behavior, terminal-native: no browser, no desktop
bundle, no new GUI toolchain.

Feasibility: Textual delta-renders dirty regions at ~120fps; `rich` (already in
the lock file) covers static tree/table fallbacks; `termaid` offers a live
reactive treemap/mermaid Textual widget if a treemap panel is wanted. `textual`
(and `termaid` if used) enter as an **optional extra** (`archy[watch]`), never a
core dep - the CLI and MCP paths must not grow a TUI dependency.

Open design points:
- Reuse the long-lived MCP watcher process, or a standalone `archy watch` that
  owns its own observer? (Leaning standalone: the human may want the view without
  an agent/MCP session attached.)
- Panels: score + five-axis bars, live cycle list, edit-risk top-N, a change
  ticker. A treemap panel is optional and gated on whether it beats the bars on
  the anti-theater test.

## 5. Non-goals

- No desktop GUI, no Electron, no hosted web service. archy stays a CLI + MCP.
- No 52-language ambition; Python-only, matching the rest of archy.
- No new score axis for "prettiness" or layout. Visualization surfaces existing
  axes; it never invents signal (the OECD non-redundancy discipline applies to
  what we *show*, not just what we *score*).

## 6. Phasing

1. Branch A `archy render` (`dsm` + `trend`), zero vendored JS, snapshot tests.
   Slots next to `archy graph`. **Done.** The `graph` view is deferred per §3a.
2. Branch B `archy watch` (Textual, optional extra), reusing the watcher stream.
3. Revisit an MCP `archy_render` tool only if agents (not humans) turn out to
   want a rendered artifact to hand back to a user - deferred until there is a
   usage signal, per the usage-first priority.
