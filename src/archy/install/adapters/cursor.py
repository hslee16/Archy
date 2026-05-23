"""Cursor adapter.

Paths:
- global MCP config: ``~/.cursor/mcp.json`` (`mcpServers` object)
- local MCP config:  ``<project>/.cursor/mcp.json``
- instructions:      ``~/.cursor/rules/archy.mdc`` / ``<project>/.cursor/rules/archy.mdc``

Cursor has no permission allowlist, so ``seed_permissions`` is ignored.
"""

from __future__ import annotations

from pathlib import Path

from archy.install import paths
from archy.install.base import (
    AgentAdapter,
    FileAction,
    Scope,
    delete_file,
    local_root,
    upsert_instructions,
)
from archy.install.merge import render_json_mcp, strip_json_mcp


class CursorAdapter(AgentAdapter):
    id = "cursor"
    name = "Cursor"
    cli_name = "cursor"

    def _root(self, scope: Scope, project_root: Path | None) -> Path:
        if scope is Scope.LOCAL:
            return local_root(project_root) / ".cursor"
        return paths.home() / ".cursor"

    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        return [self._root(scope, project_root) / "mcp.json"]

    def detection_paths(self) -> list[Path]:
        return [paths.home() / ".cursor"]

    def mac_app_bundles(self) -> list[Path]:
        return [
            Path("/Applications/Cursor.app"),
            paths.home() / "Applications" / "Cursor.app",
        ]

    def windows_install_dirs(self) -> list[Path]:
        local = paths.local_appdata()
        if local is None:
            return []
        return [local / "Programs" / "cursor" / "Cursor.exe"]

    def plan(
        self,
        scope: Scope,
        *,
        project_root: Path | None = None,
        seed_permissions: bool = True,
    ) -> list[FileAction]:
        root = self._root(scope, project_root)
        return [
            FileAction(
                path=root / "mcp.json",
                kind="mcp",
                render=render_json_mcp,
                unrender=strip_json_mcp,
            ),
            FileAction(
                path=root / "rules" / "archy.mdc",
                kind="instructions",
                render=upsert_instructions,
                unrender=delete_file,
            ),
        ]
