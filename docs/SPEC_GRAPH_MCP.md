# Spec: `archy_graph` MCP tool

Open roadmap item from README:

> `archy graph` MCP tool: expose the dep graph itself for agent-side reasoning

The CLI command `archy graph` already exists and emits `text` / `json` / `dot`. The work is wiring it into `src/archy/mcp.py` next to the other nine tools - but a naïve "dump the full graph JSON" tool wastes the agent's context on every call. This spec defines a small set of lenses tuned for how agents actually reason about a graph.

## Why not one big dump

A full graph for a medium project (a few hundred modules, a few thousand edges) easily blows past 100k tokens once you include node attrs (`path`, `is_package`, `external`, `instability`) and edge attrs (`lines`, `is_relative`). Agents rarely need all of it - they need:

- "what does this module depend on / who depends on it"  (the local neighborhood - the by-far most common query)
- "what's the shape of the whole project"  (a summary view, not 3000 edges)
- "give me everything so I can do my own analysis"  (rare, but the CLI already serves this)

The other dep-graph MCP servers we surveyed converge on the same split:

- **sentrux** ships a `dsm` (Dependency Structure Matrix) tool - a compact whole-graph summary, distinct from the raw-graph endpoint
- **mkearl/dependency-mcp** has `get_dependency_graph` (full) *and* `get_file_metadata` (single-file focus)
- **entrepeneur4lyf/code-graph-mcp** exposes `find_callers` / `find_callees` - neighborhood queries, not full dumps
- **oraios/serena** is the strongest signal here: "agent-first tool design… robust high-level abstractions… distinguishing it from approaches that rely on low-level concepts"

Takeaway: a `graph` namespace with three tools, not one.

## Proposed tools

### 1. `archy_graph_focus(path, modules, depth=1, direction="both")`

The primary tool. Returns a slice of the graph centered on one or more modules.

**Parameters**
- `modules: list[str]` - qualnames (e.g. `["archy.parser"]`) **or** file paths that resolve to internal modules (uses the shared `resolve_modules` helper, same path resolution as `find_impact`). Multi-seed: ego graphs are unioned across all resolved seeds; unresolved entries are returned in the `unresolved` field instead of erroring.
- `depth: int = 1` - how many hops to expand
- `direction: "in" | "out" | "both" = "both"` - `in` = who depends on me (callers), `out` = my dependencies, `both` = bidirectional neighborhood
- `internal_only: bool = True`

**Returns** - same `_graph_to_dict` shape the CLI emits, but only the subgraph reachable within `depth` hops in the requested direction(s). Each node carries `instability`; each edge carries `lines` so the agent can pinpoint the import site.

**Why this shape**: `find_impact` already gives reverse-reachability of *internal* modules; this generalizes it (forward direction, depth-limited, with edge detail). Agents use this before editing - same role as `archy_impact` but more flexible.

### 2. `archy_graph_summary(path, top_n=20)`

The whole-project lens, sized for context.

**Returns**
```
{
  "module_count": int,
  "internal_edge_count": int,
  "external_edge_count": int,
  "parse_errors": [qualname, ...],
  "top_fan_in":  [{module, in_degree, instability}, ...],   # top_n
  "top_fan_out": [{module, out_degree, instability}, ...],  # top_n
  "top_pagerank": [{module, score}, ...],                   # top_n
  "external_deps": [{name, importer_count}, ...]            # top_n
}
```

PageRank is a NetworkX one-liner (already flagged in `docs/FUTURE.md` line 13 as a graph-time diagnostic). Fan-in/fan-out + instability is the cheapest possible "where is the gravity in this codebase" answer. This is what `dsm` is in sentrux, minus the full matrix (which doesn't paginate well into LLM context).

### 3. `archy_graph(path, internal_only=True, max_nodes=500)`

The escape hatch. Full graph dump, matching `archy graph --format json` exactly, with a guardrail.

- If `node_count > max_nodes`, return an error payload pointing the agent at `archy_graph_focus` / `archy_graph_summary` instead of silently truncating. Truncated graphs lie (missing edges look like absence of dependency).
- `max_nodes` is overridable so a determined caller can opt in.

## Response models

Add to `src/archy/mcp.py`:

```python
class GraphNode(BaseModel):
    id: str
    external: bool
    path: str | None = None          # internal only
    is_package: bool | None = None   # internal only
    instability: float | None = None # internal only

class GraphEdge(BaseModel):
    source: str
    target: str
    is_relative: bool
    lines: tuple[int, ...]

class GraphPayload(BaseModel):
    root: str | None
    parse_errors: tuple[str, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    unresolved: tuple[str, ...] = ()  # from focus(): refs that matched no internal module

# Separate model rather than a `truncated: bool` flag so the typed union
# (`GraphPayload | GraphTooLargePayload`) forces callers to handle the
# refusal case explicitly instead of silently treating a truncated graph
# as complete.
class GraphTooLargePayload(BaseModel):
    error: str
    node_count: int
    max_nodes: int

class GraphSummaryEntry(BaseModel):
    module: str
    value: float                     # in_degree | out_degree | pagerank | importer_count
    instability: float | None = None

class GraphSummaryPayload(BaseModel):
    module_count: int
    internal_edge_count: int
    external_edge_count: int
    parse_errors: tuple[str, ...]
    top_fan_in: tuple[GraphSummaryEntry, ...]
    top_fan_out: tuple[GraphSummaryEntry, ...]
    top_pagerank: tuple[GraphSummaryEntry, ...]
    external_deps: tuple[GraphSummaryEntry, ...]
```

Reuse `_graph_to_dict` from `cli.py` for the full-dump path - extract it into `graph.py` (or a new `graph_serialize.py`) so MCP and CLI share one serializer. Don't duplicate.

## Implementation notes

- **PageRank** - hand-rolled power iteration (~15 lines) in `mcp.py::_pagerank`. NetworkX 3.x's `pagerank` requires numpy/scipy, which archy keeps out of the runtime install. A `parity` dependency group + `pytest.importorskip("numpy")` test gates the comparison against `nx.pagerank` so we can validate correctness locally without making numpy a hard dep.
- **Module resolution for `focus`** - extract the `find_impact` file-to-qualname helper into a shared util; the focus tool needs the same logic.
- **Subgraph extraction** - `nx.ego_graph(g, module, radius=depth, undirected=False)` for `out`; reverse the graph for `in`; union the two for `both`. Don't roll your own BFS.
- **Touch tests for parity** - every shape returned by `archy_graph` must round-trip against `_graph_to_dict` output for a fixture project; this is the contract that lets agents and the CLI agree.

## Update prompt + agent loop

Add a one-liner to the `loop` MCP prompt: before editing a module, prefer `archy_graph_focus(module=…, depth=1)` over `archy_impact` when you want *both* directions or want to see edge line numbers. `archy_impact` stays as the dedicated blast-radius tool (it answers a slightly different question: transitive reverse-reachability of internal modules only, which `focus` could match with `direction="in", depth=∞` but that's not the agent's mental model).

## Out of scope (intentionally)

- **Call graph edges** - already a separate roadmap item in `FUTURE.md` line 17. When that lands, all three tools get the new edge type for free if we plumb a `kind` attr through. Don't pre-empt the design here.
- **Persistent caching** - the dependency-mcp prior art has a 1h TTL cache; archy graphs build fast enough on every call that this is premature.
- **DOT format over MCP** - the CLI keeps DOT for humans piping to graphviz; MCP clients want JSON.

## Rollout

1. Extract `_graph_to_dict` into shared module; CLI + MCP both call it.
2. Add the three tools with response models and tests.
3. Update README: tick the `archy graph` MCP roadmap box; add the three tool names to the tool list near `archy mcp`.
4. Add a CASE_STUDIES.md entry showing an agent using `focus` → edit → `archy_diff` (the existing loop with a richer pre-edit step).

## Open questions

1. Should `focus` accept a list of modules (multi-seed ego graph) for the "I'm editing three related files" case? Lean yes - cheap to add, common case.
2. Should `summary` include cycle stats inline (top-N cycles by size) or stay strictly node-centric? Cycles already have their own tool; lean toward not duplicating.
3. `max_nodes=500` is a guess. Worth benching against the 23-project benchmark to pick a number that covers >90% of real projects without overflowing typical context budgets.
