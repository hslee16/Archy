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
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SKILL = REPO_ROOT / "skills" / "archy" / "SKILL.md"
PLUGIN_SKILL = REPO_ROOT / "plugins" / "claude" / "skills" / "archy" / "SKILL.md"
PLUGIN_MANIFEST = REPO_ROOT / "plugins" / "claude" / ".claude-plugin" / "plugin.json"
PLUGIN_LAUNCHER = REPO_ROOT / "plugins" / "claude" / "bin" / "archy-mcp.mjs"
PLUGIN_README = REPO_ROOT / "plugins" / "claude" / "README.md"

# The version specifier the launcher hands to `uvx`. Pinned in one place so the
# launcher/README cannot silently disagree (issue #150 moved the spec out of the
# manifest and into the launcher).
EXPECTED_SPEC = "archy>=0.31,<1.0"


@pytest.fixture
def node_bin() -> str:
    """Path to a Node interpreter, or skip the test if Node is unavailable.

    Node only has to exist where the launcher actually runs (CI and any machine
    that can run Claude Code), so its absence is a skip, not a failure.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH")
    return node


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
    # The last-resort branch for venvs whose archy console script is not on PATH.
    assert '"-m", "archy", "mcp"' in src


def test_plugin_readme_pins_same_spec():
    """The README must document the same spec the launcher uses, so they can't drift."""
    assert EXPECTED_SPEC in PLUGIN_README.read_text()


def test_plugin_launcher_is_valid_node_syntax(node_bin: str):
    """`node --check` the launcher so a syntax error can't ship a dead plugin."""
    result = subprocess.run(
        [node_bin, "--check", str(PLUGIN_LAUNCHER)],
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
def test_plugin_launcher_resolution_order(node_bin: str, tmp_path: Path):
    """The shim resolves archy -> uvx -> python -> actionable error, in that order.

    The whole point of #150: with a real `archy` it uses that; else `uvx` with the
    pinned spec; else `python -m archy`; with none it exits non-zero with a hint
    instead of failing silently.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").symlink_to(node_bin)

    # 1. A real `archy` wins and is called as `archy mcp`.
    archy = bin_dir / "archy"
    _fake_exe(archy, 'echo "FAKE-ARCHY [$*]"')
    r1 = _run_launcher(bin_dir)
    assert r1.returncode == 0 and "FAKE-ARCHY [mcp]" in r1.stdout, r1.stderr

    # 2. No archy, only uvx -> `uvx <spec> mcp`.
    archy.unlink()
    uvx = bin_dir / "uvx"
    _fake_exe(uvx, 'echo "FAKE-UVX [$*]"')
    r2 = _run_launcher(bin_dir)
    assert r2.returncode == 0, r2.stderr
    assert f"FAKE-UVX [{EXPECTED_SPEC} mcp]" in r2.stdout

    # 3. Only python3 -> `python3 -m archy mcp` (the last-resort fallback).
    uvx.unlink()
    py = bin_dir / "python3"
    _fake_exe(py, 'echo "FAKE-PY [$*]"')
    r3 = _run_launcher(bin_dir)
    assert r3.returncode == 0, r3.stderr
    assert "FAKE-PY [-m archy mcp]" in r3.stdout

    # 4. Nothing resolvable -> actionable error, non-zero exit (not a silent hang).
    py.unlink()
    r4 = _run_launcher(bin_dir)
    assert r4.returncode == 1
    assert "could not start" in r4.stderr and "astral.sh/uv/install.sh" in r4.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake-binary harness")
def test_plugin_launcher_skips_unlaunchable_candidate(node_bin: str, tmp_path: Path):
    """A non-executable file (or directory) named `archy` must not mask a real `uvx`.

    `which` uses a stat that requires a real, executable, non-empty file, so a
    stray `archy` directory or 0644 file on PATH is skipped rather than resolved
    and then dying at spawn with no fallthrough.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").symlink_to(node_bin)
    # A non-executable file literally named `archy` sitting earlier in resolution.
    (bin_dir / "archy").write_text("not an executable")
    (bin_dir / "archy").chmod(0o644)
    _fake_exe(bin_dir / "uvx", 'echo "FAKE-UVX [$*]"')

    result = _run_launcher(bin_dir)
    assert result.returncode == 0, result.stderr
    assert f"FAKE-UVX [{EXPECTED_SPEC} mcp]" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal + /proc-free liveness check")
def test_plugin_launcher_forwards_termination(node_bin: str, tmp_path: Path):
    """SIGTERM to the shim must kill the MCP child, not orphan it.

    Claude Code sends SIGTERM to the spawned process (the shim) on stop/restart.
    The shim must forward it so the real archy process dies; the previous version
    killed only itself and leaked a stranded child holding the old stdio pipes.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").symlink_to(node_bin)
    pidfile = tmp_path / "child.pid"
    # Absolute path to sleep: the shim runs the child with PATH=bin_dir only, so a
    # bare `sleep` would not resolve. `exec` so the recorded PID IS the long-lived
    # process the shim spawned (no intermediate shell to confuse liveness).
    sleep_bin = shutil.which("sleep")
    if sleep_bin is None:
        pytest.skip("sleep not available")
    _fake_exe(bin_dir / "archy", f'echo $$ > "{pidfile}"\nexec "{sleep_bin}" 30')

    proc = subprocess.Popen(
        ["node", str(PLUGIN_LAUNCHER)],
        env={**os.environ, "PATH": str(bin_dir)},
    )

    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _wait(predicate, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    try:
        assert _wait(lambda: pidfile.exists(), 5.0), "child never started"
        child_pid = int(pidfile.read_text().strip())
        assert _alive(child_pid)
        proc.terminate()  # SIGTERM to the shim, exactly like Claude Code
        proc.wait(timeout=5)
        assert _wait(lambda: not _alive(child_pid), 5.0), (
            "MCP child was orphaned after the shim received SIGTERM"
        )
        # The shim must die BY the signal (returncode -SIGTERM), not exit 0: its
        # handler forwards to the child, then re-raises with the default action so
        # the exit status reflects the termination.
        assert proc.returncode == -signal.SIGTERM, (
            f"shim exited {proc.returncode}, expected -{signal.SIGTERM} (died by SIGTERM)"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
