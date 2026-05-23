"""Continue adapter.

Continue reads each MCP server from its own block file under a ``mcpServers``
directory, and rules from a ``rules`` directory:

- global MCP config: ``~/.continue/mcpServers/archy.yaml``
- local MCP config:  ``<project>/.continue/mcpServers/archy.yaml``
- instructions:      ``~/.continue/rules/archy.md`` / ``<project>/.continue/rules/archy.md``

Continue is a VS Code / JetBrains extension with no headless mode, so it is
excluded from the E2E layer (layer 5); layers 1-4 cover it. No permission
allowlist, so ``seed_permissions`` is ignored.
"""

from __future__ import annotations

from pathlib import Path

from archy.install import paths
from archy.install.base import (
    AgentAdapter,
    FileAction,
    Scope,
    upsert_instructions,
)
from archy.install.merge import render_continue_yaml


class ContinueAdapter(AgentAdapter):
    id = "continue"
    name = "Continue"
    cli_name = "cn"

    def _root(self, scope: Scope, project_root: Path | None) -> Path:
        if scope is Scope.LOCAL:
            return (project_root or Path.cwd()) / ".continue"
        return paths.home() / ".continue"

    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        return [self._root(scope, project_root) / "mcpServers" / "archy.yaml"]

    def detection_paths(self) -> list[Path]:
        return [paths.home() / ".continue"]

    def windows_install_dirs(self) -> list[Path]:
        local = paths.local_appdata()
        if local is None:
            return []
        # Continue ships inside VS Code; treat a per-user VS Code install as a
        # weak presence signal when nothing stronger matched.
        return [local / "Programs" / "Microsoft VS Code"]

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
                path=root / "mcpServers" / "archy.yaml",
                kind="mcp",
                render=render_continue_yaml,
            ),
            FileAction(
                path=root / "rules" / "archy.md",
                kind="instructions",
                render=upsert_instructions,
            ),
        ]
