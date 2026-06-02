"""Pin the Claude Code plugin manifest and its bundled skill against drift.

The plugin at `plugins/claude/` ships a copy of `skills/archy/SKILL.md`
because Claude Code plugin skills must live inside the plugin directory
(per the May 2026 plugin reference). The bundled copy is meant to be
byte-identical with the top-level canonical version so updates to either
side cannot silently diverge; this test is the enforcement mechanism.

If this test fails, run:

    cp skills/archy/SKILL.md plugins/claude/skills/archy/SKILL.md

(or update both files together) to bring them back into sync.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SKILL = REPO_ROOT / "skills" / "archy" / "SKILL.md"
PLUGIN_SKILL = REPO_ROOT / "plugins" / "claude" / "skills" / "archy" / "SKILL.md"
PLUGIN_MANIFEST = REPO_ROOT / "plugins" / "claude" / ".claude-plugin" / "plugin.json"


def test_plugin_skill_matches_canonical():
    """The plugin's SKILL.md must be byte-identical to the canonical copy.

    Plugin skills can't reference files outside the plugin directory, so
    the file is duplicated. This test guarantees the two copies don't
    drift. If you intentionally update one, update both in the same PR.
    """
    assert CANONICAL_SKILL.exists(), f"canonical SKILL.md missing at {CANONICAL_SKILL}"
    assert PLUGIN_SKILL.exists(), f"plugin SKILL.md missing at {PLUGIN_SKILL}"
    assert CANONICAL_SKILL.read_bytes() == PLUGIN_SKILL.read_bytes(), (
        "skills/archy/SKILL.md and plugins/claude/skills/archy/SKILL.md "
        "have diverged. Re-copy the canonical file:\n"
        "  cp skills/archy/SKILL.md plugins/claude/skills/archy/SKILL.md\n"
        "or update both files together."
    )


def test_plugin_manifest_is_valid_json():
    """Parse the plugin manifest and pin the load-bearing fields.

    Catches accidental syntax breakage and keeps the MCP server stanza
    honest. The exact field shape (name, mcpServers) is what Claude Code
    reads at install time; if any of these change, plugin install will
    silently no-op or fail in confusing ways.
    """
    raw = PLUGIN_MANIFEST.read_text()
    manifest = json.loads(raw)
    assert manifest["name"] == "archy"
    assert "mcpServers" in manifest
    assert "archy" in manifest["mcpServers"]
    server = manifest["mcpServers"]["archy"]
    assert server["command"] == "uvx"
    assert server["args"] == ["archy>=0.28,<1.0", "mcp"]
