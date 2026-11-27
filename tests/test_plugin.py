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
import sys
import threading
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
EXPECTED_SPEC = "archy>=0.51,<1.0"

# First archy release that registers each tool the bundled skill still names as a
# callable. The plugin floor must be at least the newest of these; when the skill
# starts naming a new tool, add it here and raise EXPECTED_SPEC (issue #668).
TOOL_FIRST_VERSION: dict[str, tuple[int, int, int]] = {
    "archy_score": (0, 36, 0),
    "archy_cycles": (0, 36, 0),
    "archy_check": (0, 36, 0),
    "archy_impact": (0, 36, 0),
    "archy_snapshot": (0, 36, 0),
    "archy_diff": (0, 36, 0),
    "archy_simulate": (0, 29, 0),
    "archy_graph": (0, 36, 0),
    "archy_what_to_refactor_next": (0, 36, 0),
    "archy_dsm": (0, 36, 0),
    "archy_duplicates": (0, 37, 0),
    "archy_conventions": (0, 44, 0),
    "archy_module_view": (0, 45, 0),
    "archy_brief": (0, 51, 0),
}


def _spec_lower_bound(spec: str) -> tuple[int, int, int]:
    """Parse `archy>=X.Y` / `archy>=X.Y.Z` into an (X, Y, Z) tuple."""
    assert spec.startswith("archy>="), spec
    lower = spec.split(",", 1)[0].removeprefix("archy>=")
    parts = [int(p) for p in lower.split(".")]
    while len(parts) < 3:
        parts.append(0)
    assert len(parts) == 3, spec
    return parts[0], parts[1], parts[2]


def _skill_table_tools(skill_text: str) -> set[str]:
    """Tool names in the skill's MCP tool table rows (`| `archy_...` |`)."""
    names: set[str] = set()
    for line in skill_text.splitlines():
        if not line.startswith("| `archy_"):
            continue
        name = line.split("`", 2)[1]
        names.add(name)
    return names


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


@pytest.fixture
def posix_bin_dir(node_bin: str, tmp_path: Path) -> Path:
    """An empty `bin/` holding only a `node` symlink, for PATH-restricted launcher runs.

    The launcher tests run the shim with PATH set to this directory alone, so
    the only candidates it can resolve are the fakes each test drops in here.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").symlink_to(node_bin)
    return bin_dir


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
    then `uvx` (with the pinned spec), then a Python that has archy, and pin the same
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
    assert '["python3", "python"]' in src


def test_plugin_readme_pins_same_spec():
    """The README must document the same spec the launcher uses, so they can't drift."""
    assert EXPECTED_SPEC in PLUGIN_README.read_text()


def test_plugin_floor_covers_every_skill_table_tool():
    """The uvx floor must cover every tool the bundled skill's table still names.

    Mutation check: add an `archy_*` row to the skill table without updating
    TOOL_FIRST_VERSION / EXPECTED_SPEC and this fails (#668).
    """
    skill_tools = _skill_table_tools(PLUGIN_SKILL.read_text())
    assert skill_tools, "skill table parsed no tools"
    missing = sorted(skill_tools - TOOL_FIRST_VERSION.keys())
    assert missing == [], f"add first-version entries for {missing}"
    required = max(TOOL_FIRST_VERSION[name] for name in skill_tools)
    floor = _spec_lower_bound(EXPECTED_SPEC)
    assert floor >= required, (
        f"EXPECTED_SPEC floor {floor} is below newest skill-table tool {required}; "
        "raise the shim / README / EXPECTED_SPEC together"
    )
    # Extra tools in the map (historical) are fine; every current MCP registration
    # that the skill table names must be covered above.


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
        LAUNCHER_ARGV, capture_output=True, text=True, env=_launcher_env(path_dir)
    )


LAUNCHER_ARGV = ["node", str(PLUGIN_LAUNCHER)]


def _launcher_env(path_dir: Path, **extra: str) -> dict[str, str]:
    return {**os.environ, "PATH": str(path_dir), **extra}


def _fake_exe(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake-binary harness")
def test_plugin_launcher_resolution_order(posix_bin_dir: Path):
    """The shim resolves archy -> uvx -> python -> actionable error, in that order.

    The whole point of #150: with a real `archy` it uses that; else `uvx` with the
    pinned spec; else a Python with archy; with none it exits non-zero with a hint
    instead of failing silently.
    """
    # 1. A real `archy` wins and is called as `archy mcp`.
    archy = posix_bin_dir / "archy"
    _fake_exe(archy, 'echo "FAKE-ARCHY [$*]"')
    r1 = _run_launcher(posix_bin_dir)
    assert r1.returncode == 0 and "FAKE-ARCHY [mcp]" in r1.stdout, r1.stderr

    # 2. No archy, only uvx -> `uvx <spec> mcp`.
    archy.unlink()
    uvx = posix_bin_dir / "uvx"
    _fake_exe(uvx, 'echo "FAKE-UVX [$*]"')
    r2 = _run_launcher(posix_bin_dir)
    assert r2.returncode == 0, r2.stderr
    assert f"FAKE-UVX [{EXPECTED_SPEC} mcp]" in r2.stdout

    # 3. Only python3, and its probe passes -> it runs `mcp` (the last resort).
    uvx.unlink()
    py = posix_bin_dir / "python3"
    _fake_exe(py, 'echo "FAKE-PY [$*]"')
    r3 = _run_launcher(posix_bin_dir)
    assert r3.returncode == 0, r3.stderr
    assert "FAKE-PY [-E -c " in r3.stdout and r3.stdout.rstrip().endswith("main() mcp]")

    # 4. Nothing resolvable -> actionable error, non-zero exit (not a silent hang).
    py.unlink()
    r4 = _run_launcher(posix_bin_dir)
    assert r4.returncode == 1
    assert "could not start" in r4.stderr and "astral.sh/uv/install.sh" in r4.stderr


def _python_wrapper(path: Path) -> None:
    """A PATH entry that runs this test's interpreter, which has archy installed."""
    _fake_exe(path, f'exec "{sys.executable}" "$@"')


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake-binary harness")
def test_plugin_launcher_hints_when_python_lacks_archy(posix_bin_dir: Path):
    """#766: a bare python3 must not swallow the install hint."""
    _fake_exe(posix_bin_dir / "python3", 'echo "No module named archy" >&2; exit 1')

    result = _run_launcher(posix_bin_dir)

    assert result.returncode == 1
    assert "could not start" in result.stderr and "astral.sh/uv/install.sh" in result.stderr
    assert "No module named" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake-binary harness")
def test_plugin_launcher_probe_really_imports_archy(posix_bin_dir: Path):
    """A stub that always fails would satisfy any probe, even `-c pass`."""
    python = posix_bin_dir / "python3"
    # -S skips site-packages, so this real interpreter has no archy.
    _fake_exe(python, f'exec "{sys.executable}" -S "$@"')
    without = subprocess.run([str(python), "-E", "-c", "import archy"], capture_output=True)
    assert without.returncode != 0, "fixture drifted: archy imports without site-packages"

    result = _run_launcher(posix_bin_dir)

    assert result.returncode == 1
    assert "could not start" in result.stderr


def _assert_exits(pid: int, *, within: float) -> None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    os.kill(pid, signal.SIGKILL)
    pytest.fail(f"process {pid} was orphaned")


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_plugin_launcher_dies_on_a_signal_during_the_probe(posix_bin_dir: Path, tmp_path: Path):
    started = tmp_path / "probe-pid"
    # `exec` keeps the pid, so the file names the process that sleeps.
    _fake_exe(posix_bin_dir / "python3", f'echo $$ > "{started}"\nexec /bin/sleep 12')
    shim = subprocess.Popen(LAUNCHER_ARGV, env=_launcher_env(posix_bin_dir))
    try:
        deadline = time.monotonic() + 10
        while not (started.exists() and started.read_text(encoding="utf-8").endswith("\n")):
            assert time.monotonic() < deadline, "probe never started"
            time.sleep(0.02)
        shim.send_signal(signal.SIGTERM)
        # Well under the stub's sleep: a blocked shim would sit it out.
        assert shim.wait(timeout=5) == -signal.SIGTERM
        _assert_exits(int(started.read_text(encoding="utf-8")), within=5)
    finally:
        shim.kill()
        shim.wait()


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake-binary harness")
def test_plugin_launcher_tries_python_after_a_python3_without_archy(posix_bin_dir: Path):
    _fake_exe(posix_bin_dir / "python3", "exit 1")
    _fake_exe(posix_bin_dir / "python", 'echo "FAKE-PYTHON"')

    result = _run_launcher(posix_bin_dir)

    assert result.returncode == 0, result.stderr
    assert "FAKE-PYTHON" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake-binary harness")
def test_plugin_launcher_serves_mcp_through_a_real_python(posix_bin_dir: Path, tmp_path: Path):
    """End to end, from a project whose own `archy/` folder must not be executed."""
    _python_wrapper(posix_bin_dir / "python3")
    project = tmp_path / "project"
    (project / "archy").mkdir(parents=True)
    marker = tmp_path / "project-code-ran"
    (project / "archy" / "__init__.py").write_text(
        f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8"
    )
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    server = subprocess.Popen(
        LAUNCHER_ARGV,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=project,
        env=_launcher_env(posix_bin_dir, PYTHONPATH="."),
    )
    # SIGTERM, which the shim forwards, so a server that never answers ends the
    # read instead of hanging the suite or outliving it.
    watchdog = threading.Timer(60, server.terminate)
    watchdog.start()
    try:
        assert server.stdin and server.stdout
        server.stdin.write(json.dumps(request) + "\n")
        server.stdin.flush()
        response = json.loads(server.stdout.readline())
    finally:
        watchdog.cancel()
        server.terminate()
        server.communicate(timeout=30)

    assert response["result"]["serverInfo"]["name"] == "archy"
    assert not marker.exists()


def test_python_dash_m_archy_runs_the_cli():
    result = subprocess.run(
        [sys.executable, "-m", "archy", "--help"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Usage: archy ")


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake-binary harness")
def test_plugin_launcher_skips_unlaunchable_candidate(posix_bin_dir: Path):
    """A non-executable file (or directory) named `archy` must not mask a real `uvx`.

    `which` uses a stat that requires a real, executable, non-empty file, so a
    stray `archy` directory or 0644 file on PATH is skipped rather than resolved
    and then dying at spawn with no fallthrough.
    """
    # A non-executable file literally named `archy` sitting earlier in resolution.
    (posix_bin_dir / "archy").write_text("not an executable")
    (posix_bin_dir / "archy").chmod(0o644)
    _fake_exe(posix_bin_dir / "uvx", 'echo "FAKE-UVX [$*]"')

    result = _run_launcher(posix_bin_dir)
    assert result.returncode == 0, result.stderr
    assert f"FAKE-UVX [{EXPECTED_SPEC} mcp]" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal + /proc-free liveness check")
def test_plugin_launcher_forwards_termination(posix_bin_dir: Path, tmp_path: Path):
    """SIGTERM to the shim must kill the MCP child, not orphan it.

    Claude Code sends SIGTERM to the spawned process (the shim) on stop/restart.
    The shim must forward it so the real archy process dies; the previous version
    killed only itself and leaked a stranded child holding the old stdio pipes.
    """
    pidfile = tmp_path / "child.pid"
    # Absolute path to sleep: the shim runs the child with PATH=posix_bin_dir only, so a
    # bare `sleep` would not resolve. `exec` so the recorded PID IS the long-lived
    # process the shim spawned (no intermediate shell to confuse liveness).
    sleep_bin = shutil.which("sleep")
    if sleep_bin is None:
        pytest.skip("sleep not available")
    _fake_exe(posix_bin_dir / "archy", f'echo $$ > "{pidfile}"\nexec "{sleep_bin}" 30')

    proc = subprocess.Popen(
        LAUNCHER_ARGV,
        env={**os.environ, "PATH": str(posix_bin_dir)},
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
