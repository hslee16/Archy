"""MCP tool registrations for impact, graph and dsm.

archy:owns        register_archy_dsm, register_archy_graph, register_archy_impact
"""

from __future__ import annotations

from pathlib import Path

from archy.affected import DEFAULT_DEPTH, Affected
from archy.dsm import (
    DSM,
    DSMDiff,
    DSMSummary,
)
from archy.impact import DEFAULT_MAX_CHAINS, Impact
from archy.mcp_compat import Server
from archy.mcp_payloads import (
    DSMErrorPayload,
    DSMTooLargePayload,
    GraphPayload,
    GraphSummaryPayload,
    GraphTooLargePayload,
)
from archy.mcp_runs import (
    DEFAULT_MAX_DSM_CELLS,
    run_affected,
    run_dsm,
    run_graph,
    run_impact,
    validate_impact_mode,
)
from archy.mcp_tools_common import _READ_ONLY_ANNOTATIONS, _project_dir


def register_archy_impact(server: Server) -> None:
    @server.tool(
        name="archy_impact",
        title="Compute change blast radius",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Given a list of changed file paths, return what they affect. "
            "Relative `files` are tried under `path` first; missing paths are "
            "retried at its git work-tree root and used if they exist there, inside `path`. "
            "`mode='blast'` (the DEFAULT) returns the internal modules that "
            "transitively import any of them (the blast radius): use before "
            "refactoring or removing a module to see what would break. "
            "An empty `impacted` means no STATIC importer was found, not that "
            "removal is safe: archy cannot see importlib.import_module / "
            "__import__, entry points and plugin registries, Django "
            "INSTALLED_APPS, pytest plugins, config-driven dispatch, module "
            "__getattr__ hooks or string-keyed factories, so `rg` for those "
            "before deleting (`empty_caveat` is set whenever this applies). "
            "`propagation_cost` is the MacCormack-style scalar (fraction of "
            "internal modules the edit set can reach); `chains` explains *why* "
            "each impacted module is reachable (the shortest import path back to "
            "a changed module, hop by hop with the line numbers where each "
            "import lives) so you can cite the specific edge(s) to preserve. "
            "Chains are ranked closest-first and capped at `max_chains` "
            "(negative for all); `chains_omitted` reports how many were left "
            "out. `mode='affected'` (replaces the removed archy_affected) is the "
            "CI-shaped lookup instead: it returns the impact pre-classified into "
            "`impacted_tests` and `impacted_modules`, with traversal depth-capped "
            "(`depth`, default 5) so a single-line edit doesn't fan out to "
            "thousands of nodes; an empty `impacted_tests` carries the same "
            "static-only `empty_caveat` (no test imports it statically, not "
            "that no test exercises it). Test detection uses pytest conventions "
            "(test_*.py, *_test.py, files under tests/) unless `test_filter` "
            "overrides with a recursive glob; a `test_filter` that matches no "
            "file in the graph is an error, not an empty `impacted_tests`, and "
            "ends with a `did you mean` when a directory is close. Files that "
            "resolve to no module "
            "are returned in `unresolved`, except a `.py` path that is missing "
            "inside the project, which is `deleted` with `deleted_reason` "
            "(the scan sees only the current tree, so its dependents are not "
            "selected). Internal modules only. "
            "`co_change=true` (blast mode only, opt-in) adds a `co_changed` list: "
            "modules that historically co-change with the edited set in git but "
            "have NO import/call edge to it, so the structural blast radius above "
            "misses them (the behavioral complement, `archy coupling` scoped to "
            "your edit - 'you changed X; historically Y changes with it, check "
            "Y'). Source-only and best-effort (empty without git); the only path "
            "that makes this tool read git history."
        ),
    )
    def archy_impact(
        path: str,
        files: list[str],
        mode: str = "blast",
        max_chains: int = DEFAULT_MAX_CHAINS,
        depth: int = DEFAULT_DEPTH,
        test_filter: str | None = None,
        co_change: bool = False,
    ) -> Impact | Affected:
        root = _project_dir(path)
        validate_impact_mode(mode)
        resolved = [Path(f) for f in files]
        if mode == "affected":
            return run_affected(
                root,
                files=resolved,
                depth=depth,
                test_filter=test_filter,
            )
        return run_impact(root, files=resolved, max_chains=max_chains, co_change=co_change)


def register_archy_graph(server: Server) -> None:
    @server.tool(
        name="archy_graph",
        title="Inspect dependency graph",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Inspect the dependency graph. With no `focus`, "
            "`response_format='summary'` (the DEFAULT, replaces the removed "
            "archy_graph_summary) returns a compact top-N overview (modules by "
            "fan-in, fan-out, PageRank, and edit-risk, plus top external deps; "
            "`top_n` controls N) so a routine call doesn't dump the whole graph "
            "into context; `response_format='full'` returns "
            "the complete node/edge dump matching `archy graph --format json`, "
            "but refuses graphs larger than `max_nodes` (default 500, must be "
            ">= 1) with a GraphTooLargePayload (bump it explicitly, or narrow "
            "with `focus`). Pass `focus=[<qualname or path>]` (replaces the "
            "removed archy_graph_focus) for a bounded subgraph centered on those "
            "modules: `depth` caps hop distance and `direction` is 'in' (who "
            "depends on me), 'out' (my dependencies), or 'both'; each edge "
            "carries the source line numbers of the import statements. With "
            "`focus` set, `response_format`/`max_nodes`/`top_n` do not apply (the "
            "neighborhood is already bounded)."
        ),
    )
    def archy_graph(
        path: str,
        response_format: str = "summary",
        focus: list[str] | None = None,
        depth: int = 2,
        direction: str = "both",
        internal_only: bool = True,
        max_nodes: int = 500,
        top_n: int = 20,
    ) -> GraphSummaryPayload | GraphPayload | GraphTooLargePayload:
        return run_graph(
            _project_dir(path),
            response_format=response_format,
            focus=focus,
            depth=depth,
            direction=direction,
            internal_only=internal_only,
            max_nodes=max_nodes,
            top_n=top_n,
        )


def register_archy_dsm(server: Server) -> None:
    @server.tool(
        name="archy_dsm",
        title="Design Structure Matrix",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Design Structure Matrix view of the import graph. "
            "`response_format='summary'` (the DEFAULT) returns a compact "
            "overview -- block structure (group labels + sizes), counts, the "
            "back-edges (source later than target in the ordering = the cycle "
            "signal), and inter-block coupling -- without the full cell list, "
            "so a routine call stays small. `response_format='full'` returns "
            "the full matrix the agent reads positionally: cell (row=source, "
            "col=target) is non-empty when source imports target; it refuses "
            f"matrices with more than {DEFAULT_MAX_DSM_CELLS} cells with a "
            "DSMTooLargePayload (narrow with `focus`/`package`, or read the "
            "summary). Use `group_by='community'` for block-diagonal cohesion, "
            "`group_by='layer'` for layer-violation forensics, or "
            "`group_by='topological'` to localize back-edges. Narrow large "
            "projects with `focus` (qualname + focus_depth-hop neighborhood) or "
            "`package` (qualname prefix). When `baseline_path` (relative to `path`) is provided, "
            "returns a DSMDiff regardless of response_format (`new_back_edges` "
            "flags cycles the edit just introduced)."
        ),
    )
    def archy_dsm(
        path: str,
        response_format: str = "summary",
        group_by: str = "community",
        weight: str = "imports",
        focus: str | None = None,
        focus_depth: int = 1,
        package: str | None = None,
        baseline_path: str | None = None,
    ) -> DSMSummary | DSM | DSMDiff | DSMTooLargePayload | DSMErrorPayload:
        return run_dsm(
            _project_dir(path),
            response_format=response_format,
            group_by=group_by,
            weight=weight,
            focus=focus,
            focus_depth=focus_depth,
            package=package,
            baseline_path=baseline_path,
        )
