"""Gemini CLI adapter.

Paths (from the Gemini CLI docs, ``tools/mcp-server.md`` and ``cli/gemini-md.md``):
- global MCP config: ``~/.gemini/settings.json`` (`mcpServers` object)
- local MCP config:  ``<project>/.gemini/settings.json``
- instructions:      ``<project>/GEMINI.md``
                     (a global install writes no block, it only clears an old one
                     from ``~/.gemini/GEMINI.md``, #560)

Gemini CLI has no permission allowlist archy seeds, so ``seed_permissions`` is
ignored. The server entry is the shared ``{command, args}`` stanza.

archy:owns        GeminiAdapter
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
from archy.install.merge import render_json_mcp, strip_json_mcp


class GeminiAdapter(AgentAdapter):
    id = "gemini"
    name = "Gemini CLI"
    cli_name = "gemini"

    def _config_dir(self, scope: Scope, project_root: Path | None) -> Path:
        if scope is Scope.LOCAL:
            return local_root(project_root) / ".gemini"
        return paths.home() / ".gemini"

    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        return [self._config_dir(scope, project_root) / "settings.json"]

    def detection_paths(self) -> list[Path]:
        return [paths.home() / ".gemini"]

    def plan(
        self,
        scope: Scope,
        *,
        project_root: Path | None = None,
        seed_permissions: bool = True,
        for_uninstall: bool = False,
    ) -> list[FileAction]:
        instructions = (
            local_root(project_root) / "GEMINI.md"
            if scope is Scope.LOCAL
            else paths.home() / ".gemini" / "GEMINI.md"
        )
        return [
            FileAction(
                path=self.config_paths(scope, project_root)[0],
                kind="mcp",
                render=render_json_mcp,
                unrender=strip_json_mcp,
            ),
            instructions_action(instructions, scope, for_uninstall=for_uninstall),
        ]
