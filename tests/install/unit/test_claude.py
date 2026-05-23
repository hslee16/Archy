"""Claude adapter unit tests: paths, detection fallbacks, plugin skip, perms."""

from __future__ import annotations

import json

from archy.install.adapters.claude import ClaudeAdapter
from archy.install.base import Scope

ADAPTER = ClaudeAdapter()


def _kinds(plan):
    return [a.kind for a in plan]


def _write_plugin_manifest(home, plugin_name):
    manifest = home / ".claude" / "plugins" / plugin_name / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": plugin_name}), encoding="utf-8")


def test_global_paths(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.config_paths(Scope.GLOBAL) == [fake.home / ".claude.json"]


def test_local_paths_use_project_root(simulate_os, tmp_path):
    simulate_os("linux")
    proj = tmp_path / "proj"
    paths = ADAPTER.config_paths(Scope.LOCAL, project_root=proj)
    assert paths == [proj / ".mcp.json"]


def test_detect_via_dotclaude_dir(simulate_os):
    fake = simulate_os("linux")
    assert ADAPTER.detect() is False
    (fake.home / ".claude").mkdir()
    assert ADAPTER.detect() is True


def test_detect_via_mac_app_bundle(simulate_os, monkeypatch):
    fake = simulate_os("darwin")
    bundle = fake.home / "Applications" / "Claude.app"
    bundle.mkdir(parents=True)
    # config + cli both miss; only the bundle is present.
    assert ADAPTER.detect() is True


def test_detect_via_windows_install_dir(simulate_os):
    fake = simulate_os("win32")
    (fake.local_appdata / "AnthropicClaude").mkdir(parents=True)
    assert ADAPTER.detect() is True


def test_plan_without_plugin_writes_mcp_instructions_permissions(simulate_os):
    simulate_os("linux")  # empty home -> no plugin installed
    plan = ADAPTER.plan(Scope.GLOBAL, seed_permissions=True)
    assert _kinds(plan) == ["mcp", "instructions", "permissions"]


def test_plan_no_permissions_flag_drops_permission_action(simulate_os):
    simulate_os("linux")
    plan = ADAPTER.plan(Scope.GLOBAL, seed_permissions=False)
    assert "permissions" not in _kinds(plan)


def test_plan_skips_mcp_when_plugin_installed(simulate_os):
    fake = simulate_os("linux")
    _write_plugin_manifest(fake.home, "archy")
    assert ADAPTER.plugin_installed() is True
    plan = ADAPTER.plan(Scope.GLOBAL, seed_permissions=True)
    # Plugin already registers MCP + ships the skill; only permissions remain.
    assert _kinds(plan) == ["permissions"]


def test_plugin_detection_ignores_foreign_manifests(simulate_os):
    fake = simulate_os("linux")
    _write_plugin_manifest(fake.home, "other")
    assert ADAPTER.plugin_installed() is False


def test_permissions_render_contains_all_tools(simulate_os):
    from archy.install.base import TOOL_NAMES

    simulate_os("linux")
    plan = ADAPTER.plan(Scope.GLOBAL, seed_permissions=True)
    perm_action = next(a for a in plan if a.kind == "permissions")
    content = perm_action.render(None)
    allow = json.loads(content)["permissions"]["allow"]
    assert "mcp__archy__archy_dsm" in allow
    assert "mcp__archy__archy_status" in allow
    # One pattern per registered tool; count-agnostic so new tools don't re-break it.
    assert len(allow) == len(TOOL_NAMES)
