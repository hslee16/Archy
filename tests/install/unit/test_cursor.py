"""Cursor adapter unit tests."""

from __future__ import annotations

from archy.install.adapters.cursor import CursorAdapter
from archy.install.base import Scope

ADAPTER = CursorAdapter()


def test_global_paths(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.config_paths(Scope.GLOBAL) == [fake.home / ".cursor" / "mcp.json"]


def test_local_paths(simulate_os, tmp_path):
    simulate_os("linux")
    proj = tmp_path / "proj"
    assert ADAPTER.config_paths(Scope.LOCAL, project_root=proj) == [proj / ".cursor" / "mcp.json"]


def test_plan_writes_mcp_and_mdc_rules(simulate_os):
    simulate_os("linux")
    plan = ADAPTER.plan(Scope.GLOBAL)
    assert [a.kind for a in plan] == ["mcp", "instructions"]
    assert plan[1].path.name == "archy.mdc"


def test_detect_via_dotcursor(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.detect() is False
    (fake.home / ".cursor").mkdir()
    assert ADAPTER.detect() is True


def test_detect_via_windows_exe(simulate_os):
    fake = simulate_os("win32")
    exe = fake.local_appdata / "Programs" / "cursor" / "Cursor.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    assert ADAPTER.detect() is True


def test_seed_permissions_flag_is_ignored(simulate_os):
    simulate_os("linux")
    a = ADAPTER.plan(Scope.GLOBAL, seed_permissions=True)
    b = ADAPTER.plan(Scope.GLOBAL, seed_permissions=False)
    assert [x.kind for x in a] == [x.kind for x in b]
