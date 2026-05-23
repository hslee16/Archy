"""Pin the installer's tool list and permission patterns against the server.

The Claude permission seed is only correct if `base.TOOL_NAMES` matches the
tools the MCP server actually registers. This test parses the names straight
out of `src/archy/mcp.py` so the two cannot drift: add a tool to the server
without updating the installer and this fails.
"""

from __future__ import annotations

import re
from pathlib import Path

from archy.install.base import TOOL_NAMES, permission_patterns

MCP_SOURCE = Path(__file__).resolve().parents[3] / "src" / "archy" / "mcp.py"


def _server_tool_names() -> set[str]:
    text = MCP_SOURCE.read_text(encoding="utf-8")
    return set(re.findall(r'name="(archy_[a-z_]+)"', text))


def test_tool_names_match_server():
    assert set(TOOL_NAMES) == _server_tool_names()


def test_tool_names_has_no_duplicates():
    assert len(TOOL_NAMES) == len(set(TOOL_NAMES))


def test_permission_patterns_shape():
    patterns = permission_patterns()
    assert len(patterns) == len(TOOL_NAMES)
    assert all(p.startswith("mcp__archy__archy_") for p in patterns)
    assert "mcp__archy__archy_score" in patterns
