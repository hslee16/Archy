"""Unit tests for runner orchestration: target resolution, dry-run install."""

from __future__ import annotations

import pytest

from archy.install import runner
from archy.install.base import Scope
from archy.install.registry import adapter_ids
from archy.install.runner import (
    detect_all,
    print_config,
    resolve_targets,
    run_install,
    run_uninstall,
)
from archy.install.writer import DryRunWriteSystem, InstallError


def test_resolve_all_returns_every_adapter():
    assert {a.id for a in resolve_targets("all")} == set(adapter_ids())


def test_resolve_explicit_list_ignores_detection():
    adapters = resolve_targets("cursor,codex")
    assert [a.id for a in adapters] == ["cursor", "codex"]


def test_resolve_explicit_is_case_insensitive():
    assert [a.id for a in resolve_targets("CURSOR")] == ["cursor"]


def test_resolve_unknown_id_raises_install_error():
    with pytest.raises(InstallError) as exc:
        resolve_targets("cursor,bogus")
    assert "bogus" in str(exc.value)


def test_resolve_empty_raises():
    with pytest.raises(InstallError):
        resolve_targets(",")


def test_resolve_auto_uses_detection(monkeypatch):
    # Stub detect_all so resolution is tested independent of the host's real
    # installed clients; only cursor "detects" here.
    monkeypatch.setattr(
        runner,
        "detect_all",
        lambda: [
            runner.Detection(adapter=a, detected=(a.id == "cursor")) for a in runner.all_adapters()
        ],
    )
    adapters = resolve_targets("auto")
    assert [a.id for a in adapters] == ["cursor"]


def test_resolve_auto_with_nothing_detected_raises(monkeypatch):
    monkeypatch.setattr(
        runner,
        "detect_all",
        lambda: [runner.Detection(adapter=a, detected=False) for a in runner.all_adapters()],
    )
    with pytest.raises(InstallError):
        resolve_targets("auto")


def test_detect_all_covers_registry(simulate_os):
    simulate_os("linux")
    detections = detect_all()
    assert {d.adapter.id for d in detections} == set(adapter_ids())
    # simulate_os hands out an empty tmp home with no CLIs on PATH, so the
    # detection ladder must miss for every adapter.
    assert all(d.detected is False for d in detections)


def test_run_install_dry_run_records_without_writing(simulate_os):
    simulate_os("linux")
    ws = DryRunWriteSystem()
    adapters = resolve_targets("cursor")
    result = run_install(adapters, Scope.GLOBAL, write_system=ws)
    assert result.results[0].adapter_id == "cursor"
    # mcp.json only: a global install writes no rules file (#560).
    assert len(ws.records) == 1
    # The dry-run system must capture writes, never touch disk.
    assert all(not r.path.exists() for r in ws.records)


def test_print_config_returns_paths_and_content(simulate_os, tmp_path):
    simulate_os("linux")
    files = print_config("codex", Scope.GLOBAL)
    names = [p.name for p, _ in files]
    assert names == ["config.toml"]
    local = print_config("codex", Scope.LOCAL, project_root=tmp_path)
    assert [p.name for p, _ in local] == ["config.toml", "AGENTS.md"]


def test_run_uninstall_on_clean_machine_touches_nothing(simulate_os):
    # Empty fake home: nothing archy ever wrote, so uninstall is a no-op.
    simulate_os("linux")
    ws = DryRunWriteSystem()
    result = run_uninstall(resolve_targets("all"), Scope.GLOBAL, write_system=ws)
    assert result.all_paths() == []
    assert ws.removed == []
    assert ws.records == []


def test_run_install_reports_denied_claude_tools(simulate_os):
    """Install skips tools in permissions.deny and names them in result notes (#542)."""
    import json

    from archy.install.base import TOOL_NAMES, permission_patterns
    from archy.install.registry import get_adapter
    from archy.install.writer import RealWriteSystem

    fake = simulate_os("linux")
    denied = permission_patterns()[0]
    assert denied.endswith(TOOL_NAMES[0])
    settings = fake.home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"permissions": {"deny": [denied]}}, indent=2) + "\n")
    assert denied in json.loads(settings.read_text())["permissions"]["deny"]

    result = run_install(
        [get_adapter("claude")],
        Scope.GLOBAL,
        write_system=RealWriteSystem(),
    )
    allow = json.loads(settings.read_text())["permissions"]["allow"]
    deny = json.loads(settings.read_text())["permissions"]["deny"]
    assert denied in deny
    assert denied not in allow
    assert any(denied in note for note in result.all_notes())
    assert any(p in allow for p in permission_patterns() if p != denied)
