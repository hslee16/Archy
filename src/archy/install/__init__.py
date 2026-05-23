"""`archy install`: an agent-detecting MCP installer registry.

One adapter per agent client (Claude Code, Cursor, Codex CLI, opencode,
Continue). Each adapter detects whether its client is present, writes the MCP
server stanza in that client's format, drops the rules/instructions file the
client expects, and (Claude only) seeds the permission allowlist the plugin
manifest cannot. See ``docs/SPEC_INDEX_AND_INSTALL.md`` Part 4 and
``docs/SPEC_INSTALL_TESTING.md``.
"""

from __future__ import annotations

from archy.install.base import AgentAdapter, FileAction, Scope
from archy.install.registry import adapter_ids, all_adapters, get_adapter
from archy.install.runner import (
    Detection,
    InstallResult,
    detect_all,
    print_config,
    resolve_targets,
    run_install,
)
from archy.install.writer import InstallError

__all__ = [
    "AgentAdapter",
    "Detection",
    "FileAction",
    "InstallError",
    "InstallResult",
    "Scope",
    "adapter_ids",
    "all_adapters",
    "detect_all",
    "get_adapter",
    "print_config",
    "resolve_targets",
    "run_install",
]
