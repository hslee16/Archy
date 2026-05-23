"""Codex CLI adapter.

Codex is the only TOML client. MCP servers live under the `mcp_servers` table:

- global config:  ``~/.codex/config.toml``  + instructions ``~/.codex/AGENTS.md``
- local config:   ``<project>/.codex/config.toml`` + instructions ``<project>/AGENTS.md``

Codex loads project-scoped ``.codex/config.toml`` only for *trusted* projects,
and reads project ``AGENTS.md`` from the repo root (not under ``.codex/``).
"""

from __future__ import annotations

from pathlib import Path

from archy.install import paths
from archy.install.base import (
    AgentAdapter,
    FileAction,
    Scope,
    remove_instructions,
    upsert_instructions,
)
from archy.install.merge import render_toml_mcp, strip_toml_mcp


class CodexAdapter(AgentAdapter):
    id = "codex"
    name = "Codex CLI"
    cli_name = "codex"

    def _codex_dir(self, scope: Scope, project_root: Path | None) -> Path:
        if scope is Scope.LOCAL:
            return (project_root or Path.cwd()) / ".codex"
        return paths.home() / ".codex"

    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        return [self._codex_dir(scope, project_root) / "config.toml"]

    def _instructions_path(self, scope: Scope, project_root: Path | None) -> Path:
        # Project AGENTS.md lives at the repo root, not under .codex/.
        if scope is Scope.LOCAL:
            return (project_root or Path.cwd()) / "AGENTS.md"
        return paths.home() / ".codex" / "AGENTS.md"

    def detection_paths(self) -> list[Path]:
        return [paths.home() / ".codex"]

    def windows_install_dirs(self) -> list[Path]:
        local = paths.local_appdata()
        if local is None:
            return []
        return [local / "Programs" / "codex"]

    def plan(
        self,
        scope: Scope,
        *,
        project_root: Path | None = None,
        seed_permissions: bool = True,
    ) -> list[FileAction]:
        return [
            FileAction(
                path=self.config_paths(scope, project_root)[0],
                kind="mcp",
                render=render_toml_mcp,
                unrender=strip_toml_mcp,
            ),
            FileAction(
                path=self._instructions_path(scope, project_root),
                kind="instructions",
                render=upsert_instructions,
                unrender=remove_instructions,
            ),
        ]
