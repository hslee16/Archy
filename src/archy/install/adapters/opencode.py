"""opencode adapter.

opencode does not use the `mcpServers` shape; its schema keys MCP servers under
``mcp.<name> = {type, command: [...], enabled}``. Config is JSON:

- global: ``~/.config/opencode/opencode.json`` (``%APPDATA%\\opencode\\opencode.json`` on Windows;
  under ``$XDG_CONFIG_HOME`` instead of ``~/.config`` when it is set)
- local:  ``<project>/opencode.json``
- instructions: ``AGENTS.md`` alongside the config

No permission allowlist, so ``seed_permissions`` is ignored.

archy:owns        OpencodeAdapter
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
from archy.install.merge import render_opencode_mcp, strip_opencode_mcp


class OpencodeAdapter(AgentAdapter):
    id = "opencode"
    name = "opencode"
    cli_name = "opencode"

    def _global_dir(self) -> Path:
        if paths.is_windows():
            base = paths.appdata() or (paths.home() / "AppData" / "Roaming")
            return base / "opencode"
        return paths.xdg_config_home() / "opencode"

    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        if scope is Scope.LOCAL:
            return [local_root(project_root) / "opencode.json"]
        return [self._global_dir() / "opencode.json"]

    def detection_paths(self) -> list[Path]:
        return [self._global_dir()]

    def plan(
        self,
        scope: Scope,
        *,
        project_root: Path | None = None,
        seed_permissions: bool = True,
        for_uninstall: bool = False,
    ) -> list[FileAction]:
        root = local_root(project_root) if scope is Scope.LOCAL else self._global_dir()
        return [
            FileAction(
                path=root / "opencode.json",
                kind="mcp",
                render=render_opencode_mcp,
                unrender=strip_opencode_mcp,
            ),
            instructions_action(root / "AGENTS.md", scope, for_uninstall=for_uninstall),
        ]
