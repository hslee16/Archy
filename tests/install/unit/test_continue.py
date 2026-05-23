"""Continue adapter unit tests."""

from __future__ import annotations

from archy.install.adapters.continue_ import ContinueAdapter
from archy.install.base import Scope

ADAPTER = ContinueAdapter()


def test_cli_name_is_cn():
    assert ADAPTER.cli_name == "cn"


def test_global_paths(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.config_paths(Scope.GLOBAL) == [
        fake.home / ".continue" / "mcpServers" / "archy.yaml"
    ]


def test_local_paths(simulate_os, tmp_path):
    simulate_os("linux")
    proj = tmp_path / "proj"
    assert ADAPTER.config_paths(Scope.LOCAL, project_root=proj) == [
        proj / ".continue" / "mcpServers" / "archy.yaml"
    ]


def test_plan_writes_block_and_rule(simulate_os):
    simulate_os("linux")
    plan = ADAPTER.plan(Scope.GLOBAL)
    assert [a.kind for a in plan] == ["mcp", "instructions"]
    assert plan[0].path.parts[-2:] == ("mcpServers", "archy.yaml")
    assert plan[1].path.parts[-2:] == ("rules", "archy.md")


def test_detect_via_dotcontinue(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.detect() is False
    (fake.home / ".continue").mkdir()
    assert ADAPTER.detect() is True
