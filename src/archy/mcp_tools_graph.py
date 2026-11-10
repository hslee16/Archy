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
    run_affected,
    run_dsm,
    run_graph,
    run_impact,
    validate_impact_mode,
)
from archy.mcp_tool_descriptions import (
    ARCHY_DSM_DESCRIPTION,
    ARCHY_GRAPH_DESCRIPTION,
    ARCHY_IMPACT_DESCRIPTION,
)
from archy.mcp_tools_common import _READ_ONLY_ANNOTATIONS, _project_dir


def register_archy_impact(server: Server) -> None:
    @server.tool(
        name="archy_impact",
        title="Compute change blast radius",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_IMPACT_DESCRIPTION,
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
        description=ARCHY_GRAPH_DESCRIPTION,
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
        description=ARCHY_DSM_DESCRIPTION,
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
