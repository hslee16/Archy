"""Layer 3: real filesystem behavior on the running OS.

Uses RealWriteSystem against a faked home under tmp_path, so the writes, the
atomic os.replace, and (on Windows) file locking are all genuine. Runs on the
ubuntu/macos/windows matrix; the platform is simulated to match the real runner
so per-OS path resolution is exercised on its own OS.
"""

from __future__ import annotations

import json
import sys

import pytest

from archy.install.base import Scope
from archy.install.registry import adapter_ids
from archy.install.runner import resolve_targets, run_install, run_uninstall
from archy.install.writer import InstallError, RealWriteSystem

REAL_PLATFORM = sys.platform if sys.platform in {"linux", "darwin", "win32"} else "linux"

ALL_IDS = adapter_ids()


@pytest.fixture
def on_real_os(simulate_os):
    """Fake path roots under tmp_path but keep the *actual* OS semantics."""
    return simulate_os(REAL_PLATFORM)


@pytest.mark.parametrize("adapter_id", ALL_IDS)
def test_install_writes_files_that_exist(on_real_os, adapter_id):
    project_root = on_real_os.home / "proj"
    adapters = resolve_targets(adapter_id)
    result = run_install(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    assert result.all_paths(), f"{adapter_id} wrote nothing"
    for path in result.all_paths():
        assert path.exists()
        assert path.read_text(encoding="utf-8")
    # LOCAL scope takes a project_root and must resolve independently of the
    # GLOBAL run above, so exercise it as a separate write.
    local = run_install(
        adapters, Scope.LOCAL, project_root=project_root, write_system=RealWriteSystem()
    )
    for path in local.all_paths():
        assert path.exists()


@pytest.mark.parametrize("adapter_id", ALL_IDS)
def test_install_is_idempotent(on_real_os, adapter_id):
    adapters = resolve_targets(adapter_id)
    run_install(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    first = {p: p.read_bytes() for p in _all_files(on_real_os.home)}
    run_install(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    second = {p: p.read_bytes() for p in _all_files(on_real_os.home)}
    assert first == second


@pytest.mark.parametrize("adapter_id", ALL_IDS)
def test_no_temp_files_left_behind(on_real_os, adapter_id):
    adapters = resolve_targets(adapter_id)
    run_install(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    leftovers = [p for p in _all_files(on_real_os.home) if p.suffix == ".tmp"]
    assert leftovers == []


def test_merge_preserves_unrelated_existing_config(on_real_os):
    claude_json = on_real_os.home / ".claude.json"
    claude_json.parent.mkdir(parents=True, exist_ok=True)
    claude_json.write_text(
        json.dumps({"numStartups": 3, "mcpServers": {"other": {"command": "x"}}}),
        encoding="utf-8",
    )
    run_install(resolve_targets("claude"), Scope.GLOBAL, write_system=RealWriteSystem())
    obj = json.loads(claude_json.read_text(encoding="utf-8"))
    assert obj["numStartups"] == 3
    assert obj["mcpServers"]["other"] == {"command": "x"}
    assert obj["mcpServers"]["archy"]["command"] == "uvx"


@pytest.mark.parametrize("adapter_id", ALL_IDS)
def test_uninstall_round_trip_removes_all_archy_traces(on_real_os, adapter_id):
    adapters = resolve_targets(adapter_id)
    run_install(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    run_uninstall(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    # A leftover "archy" reference in any config would silently re-activate the
    # tool the user just removed, so uninstall must leave zero footprint.
    for path in _all_files(on_real_os.home):
        assert "archy" not in path.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("adapter_id", ALL_IDS)
def test_uninstall_is_idempotent(on_real_os, adapter_id):
    adapters = resolve_targets(adapter_id)
    run_install(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    run_uninstall(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    after_first = {p: p.read_bytes() for p in _all_files(on_real_os.home)}
    # Re-running uninstall (e.g. a package manager retrying) must never corrupt
    # an already-clean tree or raise on the files that are already gone.
    run_uninstall(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    assert {p: p.read_bytes() for p in _all_files(on_real_os.home)} == after_first


def test_uninstall_preserves_unrelated_config_and_user_instructions(on_real_os):
    claude_json = on_real_os.home / ".claude.json"
    claude_md = on_real_os.home / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_json.write_text(
        json.dumps({"numStartups": 5, "mcpServers": {"other": {"command": "x"}}}),
        encoding="utf-8",
    )
    claude_md.write_text("# My rules\n\nDo the thing.\n", encoding="utf-8")

    adapters = resolve_targets("claude")
    run_install(adapters, Scope.GLOBAL, write_system=RealWriteSystem())
    run_uninstall(adapters, Scope.GLOBAL, write_system=RealWriteSystem())

    obj = json.loads(claude_json.read_text(encoding="utf-8"))
    assert obj["numStartups"] == 5
    assert obj["mcpServers"]["other"] == {"command": "x"}
    assert "archy" not in obj["mcpServers"]
    # Removing the user's own instructions along with archy's block would be a
    # data-loss bug, so uninstall must be non-destructive to their content.
    text = claude_md.read_text(encoding="utf-8")
    assert text.startswith("# My rules")
    assert "archy" not in text.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="file locking is Windows-specific")
def test_windows_open_handle_surfaces_clear_error(on_real_os):
    target = on_real_os.home / ".cursor" / "mcp.json"
    run_install(resolve_targets("cursor"), Scope.GLOBAL, write_system=RealWriteSystem())
    # Hold an exclusive handle the way a running Electron client would.
    # Keep a real fd open so os.replace inside RealWriteSystem hits an actual
    # Windows lock, which a mock could not reproduce.
    with open(target, "r+", encoding="utf-8"):
        with pytest.raises(InstallError) as exc:
            run_install(resolve_targets("cursor"), Scope.GLOBAL, write_system=RealWriteSystem())
        assert "close" in str(exc.value).lower()


def _all_files(root):
    return sorted(p for p in root.rglob("*") if p.is_file())
