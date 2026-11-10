"""MCP tool registrations for what-to-refactor-next and duplicates.

archy:owns        register_archy_duplicates, register_archy_what_to_refactor_next
"""

from __future__ import annotations

from archy.duplicates import (
    DEFAULT_MIN_SIZE,
)
from archy.mcp_compat import Server
from archy.mcp_payloads import (
    DuplicatesPayload,
    WhatToRefactorPayload,
)
from archy.mcp_runs import (
    run_duplicates,
    run_what_to_refactor_next,
)
from archy.mcp_tool_descriptions import (
    ARCHY_DUPLICATES_DESCRIPTION,
    ARCHY_WHAT_TO_REFACTOR_NEXT_DESCRIPTION,
)
from archy.mcp_tools_common import _READ_ONLY_ANNOTATIONS, _project_dir
from archy.refactor import DEFAULT_MIN_RISK


def register_archy_what_to_refactor_next(server: Server) -> None:
    @server.tool(
        name="archy_what_to_refactor_next",
        title="Rank refactor priorities",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_WHAT_TO_REFACTOR_NEXT_DESCRIPTION,
    )
    def archy_what_to_refactor_next(
        path: str,
        lens: str = "fused",
        top_n: int = 10,
        since: str | None = None,
        min_risk: float = DEFAULT_MIN_RISK,
    ) -> WhatToRefactorPayload:
        return run_what_to_refactor_next(
            _project_dir(path), lens=lens, top_n=top_n, since=since, min_risk=min_risk
        )


def register_archy_duplicates(server: Server) -> None:
    @server.tool(
        name="archy_duplicates",
        title="Duplicate-function clusters",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_DUPLICATES_DESCRIPTION,
    )
    def archy_duplicates(
        path: str,
        response_format: str = "summary",
        min_nodes: int = DEFAULT_MIN_SIZE,
        top_n: int = 20,
        members: int = 2,
        co_change: bool = True,
        near_miss: bool = False,
    ) -> DuplicatesPayload:
        return run_duplicates(
            _project_dir(path),
            response_format=response_format,
            min_nodes=min_nodes,
            top_n=top_n,
            members=members,
            co_change=co_change,
            near_miss=near_miss,
        )
