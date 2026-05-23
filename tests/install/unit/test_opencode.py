"""opencode adapter unit tests, including the Windows AppData path split."""

from __future__ import annotations

from archy.install.adapters.opencode import OpencodeAdapter
from archy.install.base import Scope

ADAPTER = OpencodeAdapter()


def test_global_path_linux_uses_xdg_config(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.config_paths(Scope.GLOBAL) == [
        fake.home / ".config" / "opencode" / "opencode.json"
    ]


def test_global_path_windows_uses_appdata(simulate_os):
    fake = simulate_os("win32")
    assert ADAPTER.config_paths(Scope.GLOBAL) == [fake.appdata / "opencode" / "opencode.json"]


def test_local_path_is_project_root(simulate_os, tmp_path):
    simulate_os("linux")
    proj = tmp_path / "proj"
    assert ADAPTER.config_paths(Scope.LOCAL, project_root=proj) == [proj / "opencode.json"]


def test_plan_writes_opencode_json_and_agents(simulate_os):
    simulate_os("linux")
    plan = ADAPTER.plan(Scope.GLOBAL)
    assert [a.kind for a in plan] == ["mcp", "instructions"]
    assert plan[0].path.name == "opencode.json"
    assert plan[1].path.name == "AGENTS.md"


def test_detect_via_config_dir(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.detect() is False
    (fake.home / ".config" / "opencode").mkdir(parents=True)
    assert ADAPTER.detect() is True
