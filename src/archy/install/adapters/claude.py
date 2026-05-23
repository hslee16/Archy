"""Claude Code adapter.

The richest adapter: besides the MCP stanza it seeds the `permissions.allow`
allowlist the plugin manifest cannot write (the gap surfaced shipping the
plugin in #104), and it skips the MCP write when the archy Claude plugin is
already installed, to avoid the double-registration that produces duplicated
`mcp__archy__*` / `mcp__plugin_archy_archy__*` tools.

Paths:
- global MCP config: ``~/.claude.json`` (`mcpServers` object)
- local MCP config:  ``<project>/.mcp.json`` (Claude Code's project-scope file)
- permissions:       ``~/.claude/settings.json`` / ``<project>/.claude/settings.json``
- instructions:      ``~/.claude/CLAUDE.md`` / ``<project>/CLAUDE.md``
"""

from __future__ import annotations

import json
from pathlib import Path

from archy.install import paths
from archy.install.base import (
    AgentAdapter,
    FileAction,
    Scope,
    upsert_instructions,
)
from archy.install.merge import render_claude_permissions, render_json_mcp


class ClaudeAdapter(AgentAdapter):
    id = "claude"
    name = "Claude Code"
    cli_name = "claude"

    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        if scope is Scope.LOCAL:
            root = project_root or Path.cwd()
            return [root / ".mcp.json"]
        return [paths.home() / ".claude.json"]

    def detection_paths(self) -> list[Path]:
        home = paths.home()
        return [home / ".claude.json", home / ".claude"]

    def mac_app_bundles(self) -> list[Path]:
        return [
            Path("/Applications/Claude.app"),
            paths.home() / "Applications" / "Claude.app",
        ]

    def windows_install_dirs(self) -> list[Path]:
        local = paths.local_appdata()
        if local is None:
            return []
        return [local / "AnthropicClaude", local / "Programs" / "Claude"]

    def _permissions_path(self, scope: Scope, project_root: Path | None) -> Path:
        if scope is Scope.LOCAL:
            root = project_root or Path.cwd()
            return root / ".claude" / "settings.json"
        return paths.home() / ".claude" / "settings.json"

    def _instructions_path(self, scope: Scope, project_root: Path | None) -> Path:
        if scope is Scope.LOCAL:
            root = project_root or Path.cwd()
            return root / "CLAUDE.md"
        return paths.home() / ".claude" / "CLAUDE.md"

    def plugin_installed(self) -> bool:
        """Heuristic: is the archy Claude plugin already linked into ~/.claude?

        Scans the plugins directory shallowly for a manifest named "archy". If
        the plugin is present it already registers the MCP server and ships the
        skill, so we skip the MCP and instruction writes (but still seed
        permissions, which the plugin cannot). Best-effort: any read error means
        "not installed", since a false negative just risks a redundant stanza
        the user can remove, while a false positive would leave them unwired.
        """
        plugins_dir = paths.home() / ".claude" / "plugins"
        if not plugins_dir.exists():
            return False
        try:
            for manifest in plugins_dir.glob("*/.claude-plugin/plugin.json"):
                try:
                    if json.loads(manifest.read_text(encoding="utf-8")).get("name") == "archy":
                        return True
                except (OSError, ValueError):
                    continue
        except OSError:
            return False
        return False

    def plan(
        self,
        scope: Scope,
        *,
        project_root: Path | None = None,
        seed_permissions: bool = True,
    ) -> list[FileAction]:
        actions: list[FileAction] = []
        plugin = self.plugin_installed()
        if not plugin:
            actions.append(
                FileAction(
                    path=self.config_paths(scope, project_root)[0],
                    kind="mcp",
                    render=render_json_mcp,
                )
            )
            actions.append(
                FileAction(
                    path=self._instructions_path(scope, project_root),
                    kind="instructions",
                    render=upsert_instructions,
                )
            )
        if seed_permissions:
            actions.append(
                FileAction(
                    path=self._permissions_path(scope, project_root),
                    kind="permissions",
                    render=render_claude_permissions,
                )
            )
        return actions
