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
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SKILL = REPO_ROOT / "skills" / "archy" / "SKILL.md"
PLUGIN_SKILL = REPO_ROOT / "plugins" / "claude" / "skills" / "archy" / "SKILL.md"
PLUGIN_MANIFEST = REPO_ROOT / "plugins" / "claude" / ".claude-plugin" / "plugin.json"
PLUGIN_LAUNCHER = REPO_ROOT / "plugins" / "claude" / "bin" / "archy-mcp.mjs"

# The version specifier the launcher hands to `uvx`. Pinned in one place so the
# manifest/launcher/README cannot silently disagree (issue #150 moved the spec
# out of the manifest and into the launcher).
EXPECTED_SPEC = "archy>=0.31,<1.0"


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
    # The plugin launches through a Node shim (issue #150) so `uv` is no longer a
    # hard requirement. `node` is the one launcher Claude Code guarantees on PATH.
    assert server["command"] == "node"
    assert server["args"] == ["${CLAUDE_PLUGIN_ROOT}/bin/archy-mcp.mjs"]


def test_plugin_launcher_exists_and_pins_spec():
    """The Node launcher exists and resolves archy without a hard `uv` dependency.

    Guards the contract the manifest relies on: the shim must try a real `archy`,
    then `uvx` (with the pinned spec), then `python -m archy`, and pin the same
    version specifier the README documents. If the manifest points at this file
    it must actually be here and carry that fallback chain.
    """
    assert PLUGIN_LAUNCHER.exists(), f"plugin launcher missing at {PLUGIN_LAUNCHER}"
    src = PLUGIN_LAUNCHER.read_text()
    assert EXPECTED_SPEC in src, f"launcher must pin {EXPECTED_SPEC}"
    # The fallback chain that removes the hard `uv` requirement.
    assert 'which("archy")' in src
    assert 'which("uvx")' in src
    assert "-m" in src and "archy" in src  # python -m archy fallback


def test_plugin_launcher_is_valid_node_syntax():
    """`node --check` the launcher so a syntax error can't ship a dead plugin.

    Skips when Node is unavailable (it is on CI and on any machine that can run
    Claude Code, which is the only place the launcher executes).
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH")
    result = subprocess.run(
        [node, "--check", str(PLUGIN_LAUNCHER)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"node --check failed:\n{result.stderr}"


def _run_launcher(path_dir: Path) -> subprocess.CompletedProcess[str]:
    # Run the shim with PATH pointing only at `path_dir` (which holds a `node`
    # symlink plus whatever fake launchers the test placed there), so resolution
    # is fully controlled. os.environ is preserved for everything except PATH.
    return subprocess.run(
        ["node", str(PLUGIN_LAUNCHER)],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": str(path_dir)},
    )


def _fake_exe(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake-binary harness")
def test_plugin_launcher_resolution_order(tmp_path: Path):
    """The shim resolves archy -> uvx -> (actionable error) without a hard `uv` dep.

    The whole point of #150: with a real `archy` it uses that; with only `uvx` it
    falls through to the pinned spec; with neither it exits non-zero with a hint
    instead of failing silently.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").symlink_to(node)

    # 1. A real `archy` wins and is called as `archy mcp`.
    archy = bin_dir / "archy"
    _fake_exe(archy, 'echo "FAKE-ARCHY [$*]"')
    r1 = _run_launcher(bin_dir)
    assert r1.returncode == 0 and "FAKE-ARCHY [mcp]" in r1.stdout, r1.stderr

    # 2. No archy, only uvx -> `uvx <spec> mcp`.
    archy.unlink()
    _fake_exe(bin_dir / "uvx", 'echo "FAKE-UVX [$*]"')
    r2 = _run_launcher(bin_dir)
    assert r2.returncode == 0, r2.stderr
    assert f"FAKE-UVX [{EXPECTED_SPEC} mcp]" in r2.stdout

    # 3. Nothing resolvable -> actionable error, non-zero exit (not a silent hang).
    (bin_dir / "uvx").unlink()
    r3 = _run_launcher(bin_dir)
    assert r3.returncode == 1
    assert "could not start" in r3.stderr and "astral.sh/uv/install.sh" in r3.stderr
