"""GitHub Copilot CLI adapter.

Paths (from the GitHub Docs page "Adding MCP servers for GitHub Copilot CLI"):
- global MCP config: ``~/.copilot/mcp-config.json`` (`mcpServers` object)
- local MCP config:  ``<project>/.github/mcp.json`` (shared file Copilot CLI reads; not
  ``.mcp.json``, which the Claude adapter writes a different entry into)
- instructions:      ``<project>/.github/copilot-instructions.md``
  (global ``~/.copilot/copilot-instructions.md`` is retired, #560)

Copilot CLI wants ``type: "local"`` and a ``tools`` list on every local server,
so it has its own render. It has no permission allowlist archy seeds.

This covers the Copilot CLI only. VS Code's Copilot Chat reads ``.vscode/mcp.json``
under a different top-level key (``servers``); that is not wired here.

archy:owns        CopilotAdapter
"""

from __future__ import annotations

from pathlib import Path

from archy.install import paths
from archy.install.base import (
    AgentAdapter,
    FileAction,
    Scope,
    instructions_action,
    local_root,
)
from archy.install.merge import render_copilot_mcp, strip_json_mcp


class CopilotAdapter(AgentAdapter):
    id = "copilot"
    name = "GitHub Copilot CLI"
    cli_name = "copilot"

    def _mcp_path(self, scope: Scope, project_root: Path | None) -> Path:
        if scope is Scope.LOCAL:
            return local_root(project_root) / ".github" / "mcp.json"
        return paths.home() / ".copilot" / "mcp-config.json"

    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        return [self._mcp_path(scope, project_root)]

    def detection_paths(self) -> list[Path]:
        return [paths.home() / ".copilot"]

    def plan(
        self,
        scope: Scope,
        *,
        project_root: Path | None = None,
        seed_permissions: bool = True,
        for_uninstall: bool = False,
    ) -> list[FileAction]:
        instructions = (
            local_root(project_root) / ".github" / "copilot-instructions.md"
            if scope is Scope.LOCAL
            else paths.home() / ".copilot" / "copilot-instructions.md"
        )
        return [
            FileAction(
                path=self._mcp_path(scope, project_root),
                kind="mcp",
                render=render_copilot_mcp,
                unrender=strip_json_mcp,
            ),
            instructions_action(instructions, scope, for_uninstall=for_uninstall),
        ]
