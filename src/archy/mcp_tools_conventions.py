"""MCP tool registrations for conventions and module-view.

archy:owns        register_archy_conventions, register_archy_module_view
"""

from __future__ import annotations

from archy.conventions import (
    ConventionsReport,
    ModuleView,
)
from archy.mcp_compat import Server
from archy.mcp_runs import (
    run_conventions,
    run_module_view,
)
from archy.mcp_tool_descriptions import ARCHY_CONVENTIONS_DESCRIPTION, ARCHY_MODULE_VIEW_DESCRIPTION
from archy.mcp_tools_common import _READ_ONLY_ANNOTATIONS, _project_dir


def register_archy_conventions(server: Server) -> None:
    @server.tool(
        name="archy_conventions",
        title="Report the project's house style",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_CONVENTIONS_DESCRIPTION,
    )
    def archy_conventions(
        path: str, min_family: int = 2, include_tests: bool = False
    ) -> ConventionsReport:
        return run_conventions(
            _project_dir(path), min_family=min_family, include_tests=include_tests
        )


def register_archy_module_view(server: Server) -> None:
    @server.tool(
        name="archy_module_view",
        title="Everything known about ONE module, complete and unranked",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_MODULE_VIEW_DESCRIPTION,
    )
    def archy_module_view(path: str, module: str, include_tests: bool = False) -> ModuleView:
        return run_module_view(_project_dir(path), module, include_tests=include_tests)
