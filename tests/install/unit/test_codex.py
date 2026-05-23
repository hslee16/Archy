"""Codex CLI adapter unit tests."""

from __future__ import annotations

from archy.install.adapters.codex import CodexAdapter
from archy.install.base import Scope

ADAPTER = CodexAdapter()


def test_global_paths(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.config_paths(Scope.GLOBAL) == [fake.home / ".codex" / "config.toml"]


def test_local_uses_project_codex_dir(simulate_os, tmp_path):
    simulate_os("linux")
    proj = tmp_path / "proj"
    # Project-scoped config.toml lives under <project>/.codex (trusted projects).
    assert ADAPTER.config_paths(Scope.LOCAL, project_root=proj) == [proj / ".codex" / "config.toml"]


def test_global_plan_writes_toml_and_agents_md(simulate_os):
    fake = simulate_os("linux")
    plan = ADAPTER.plan(Scope.GLOBAL)
    assert [a.kind for a in plan] == ["mcp", "instructions"]
    assert plan[0].path == fake.home / ".codex" / "config.toml"
    assert plan[1].path == fake.home / ".codex" / "AGENTS.md"


def test_local_agents_md_is_at_repo_root(simulate_os, tmp_path):
    simulate_os("linux")
    proj = tmp_path / "proj"
    plan = ADAPTER.plan(Scope.LOCAL, project_root=proj)
    # Project AGENTS.md is at the repo root, not under .codex/.
    assert plan[1].path == proj / "AGENTS.md"


def test_detect_via_dotcodex(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.detect() is False
    (fake.home / ".codex").mkdir()
    assert ADAPTER.detect() is True
