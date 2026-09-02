"""opencode adapter.

opencode does not use the `mcpServers` shape; its schema keys MCP servers under
``mcp.<name> = {type, command: [...], enabled}``. Config is JSON:

- global: ``~/.config/opencode/opencode.json`` (``%APPDATA%\\opencode\\opencode.json`` on Windows)
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
    local_root,
    remove_instructions,
    upsert_instructions,
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
        return paths.home() / ".config" / "opencode"

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
        for_uninstall: bool = False,  # part of the shared plan() contract; unused here
    ) -> list[FileAction]:
        root = local_root(project_root) if scope is Scope.LOCAL else self._global_dir()
        return [
            FileAction(
                path=root / "opencode.json",
                kind="mcp",
                render=render_opencode_mcp,
                unrender=strip_opencode_mcp,
            ),
            FileAction(
                path=root / "AGENTS.md",
                kind="instructions",
                render=upsert_instructions,
                unrender=remove_instructions,
            ),
        ]
