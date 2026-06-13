"""Unit tests for the pure render/merge functions.

Always parse-then-assert (never string-compare hand-written JSON/TOML), and
always assert idempotency: render twice and the second pass is a byte no-op.
"""

from __future__ import annotations

import json
import sys

import pytest
import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

from archy.install.base import permission_patterns
from archy.install.merge import (
    render_claude_permissions,
    render_continue_yaml,
    render_json_mcp,
    render_opencode_mcp,
    render_toml_mcp,
    strip_claude_permissions,
    strip_json_mcp,
    strip_opencode_mcp,
    strip_toml_mcp,
)
from archy.install.writer import InstallError


def test_json_mcp_fresh():
    obj = json.loads(render_json_mcp(None))
    assert obj["mcpServers"]["archy"] == {"command": "uvx", "args": ["archy", "mcp"]}


def test_json_mcp_preserves_unrelated_keys_and_servers():
    existing = json.dumps({"numStartups": 7, "mcpServers": {"other": {"command": "foo"}}})
    obj = json.loads(render_json_mcp(existing))
    assert obj["numStartups"] == 7
    assert obj["mcpServers"]["other"] == {"command": "foo"}
    assert obj["mcpServers"]["archy"]["command"] == "uvx"


def test_json_mcp_idempotent():
    first = render_json_mcp(None)
    assert render_json_mcp(first) == first


def test_json_mcp_rejects_non_dict_servers_key():
    # #169: a scalar where a table is expected is malformed config; raise with
    # an actionable message instead of silently overwriting the user's value.
    existing = json.dumps({"mcpServers": "oops-a-string"})
    with pytest.raises(InstallError, match=r"mcpServers.*must be an object"):
        render_json_mcp(existing)


def test_claude_permissions_rejects_non_list_allow():
    # #169: normalizing a scalar `allow` to [] would drop the user's value and
    # leave uninstall unable to restore it; fail fast instead.
    existing = json.dumps({"permissions": {"allow": "not-a-list"}})
    with pytest.raises(InstallError, match=r"permissions\.allow must be a list"):
        render_claude_permissions(existing)


def test_opencode_mcp_shape():
    obj = json.loads(render_opencode_mcp(None))
    entry = obj["mcp"]["archy"]
    assert entry["type"] == "local"
    assert entry["command"] == ["uvx", "archy", "mcp"]
    assert entry["enabled"] is True


def test_opencode_idempotent_and_preserves():
    existing = json.dumps({"mcp": {"keep": {"type": "local"}}, "theme": "dark"})
    once = render_opencode_mcp(existing)
    obj = json.loads(once)
    assert obj["theme"] == "dark"
    assert "keep" in obj["mcp"]
    assert render_opencode_mcp(once) == once


def test_continue_yaml_block_shape():
    doc = yaml.safe_load(render_continue_yaml(None))
    assert doc["name"] == "archy"
    assert doc["schema"] == "v1"
    server = doc["mcpServers"][0]
    assert server["name"] == "archy"
    assert server["type"] == "stdio"
    assert server["command"] == "uvx"
    assert server["args"] == ["archy", "mcp"]
    # Owned file: existing content is irrelevant, output is constant.
    assert render_continue_yaml("anything") == render_continue_yaml(None)


def test_claude_permissions_fresh_has_all_patterns():
    obj = json.loads(render_claude_permissions(None))
    assert obj["permissions"]["allow"] == permission_patterns()


def test_claude_permissions_merges_without_clobbering():
    existing = json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}, "model": "opus"})
    obj = json.loads(render_claude_permissions(existing))
    allow = obj["permissions"]["allow"]
    assert allow[0] == "Bash(ls:*)"
    assert set(permission_patterns()).issubset(set(allow))
    assert obj["model"] == "opus"


def test_claude_permissions_idempotent_no_duplicates():
    once = render_claude_permissions(None)
    twice = render_claude_permissions(once)
    assert once == twice
    allow = json.loads(twice)["permissions"]["allow"]
    assert len(allow) == len(set(allow))


def test_toml_mcp_fresh():
    data = tomllib.loads(render_toml_mcp(None))
    assert data["mcp_servers"]["archy"] == {"command": "uvx", "args": ["archy", "mcp"]}


def test_toml_mcp_preserves_existing_tables():
    existing = 'model = "o3"\n[mcp_servers.other]\ncommand = "foo"\n'
    data = tomllib.loads(render_toml_mcp(existing))
    assert data["model"] == "o3"
    assert data["mcp_servers"]["other"]["command"] == "foo"
    assert data["mcp_servers"]["archy"]["command"] == "uvx"


def test_toml_mcp_idempotent():
    first = render_toml_mcp(None)
    assert render_toml_mcp(first) == first


# --- inverse (strip) functions --------------------------------------------


def test_strip_json_mcp_removes_archy_keeps_others():
    installed = render_json_mcp(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    obj = json.loads(strip_json_mcp(installed))
    assert "archy" not in obj["mcpServers"]
    assert obj["mcpServers"]["other"] == {"command": "x"}


def test_strip_json_mcp_idempotent_and_safe_when_absent():
    clean = json.dumps({"mcpServers": {"other": {}}})
    once = strip_json_mcp(clean)
    assert "archy" not in json.loads(once)["mcpServers"]
    assert strip_json_mcp(once) == once


def test_strip_round_trip_restores_unrelated_config():
    original = {"numStartups": 4, "mcpServers": {"other": {"command": "x"}}}
    after = json.loads(strip_json_mcp(render_json_mcp(json.dumps(original))))
    assert after == original


def test_strip_opencode_removes_archy():
    installed = render_opencode_mcp(json.dumps({"theme": "dark"}))
    obj = json.loads(strip_opencode_mcp(installed))
    assert obj["theme"] == "dark"
    assert "archy" not in obj.get("mcp", {})


def test_strip_toml_removes_archy_keeps_others():
    installed = render_toml_mcp('[mcp_servers.other]\ncommand = "foo"\n')
    data = tomllib.loads(strip_toml_mcp(installed))
    assert "archy" not in data["mcp_servers"]
    assert data["mcp_servers"]["other"]["command"] == "foo"


def test_strip_claude_permissions_removes_only_archy_patterns():
    installed = render_claude_permissions(json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}}))
    allow = json.loads(strip_claude_permissions(installed))["permissions"]["allow"]
    assert allow == ["Bash(ls:*)"]
    assert not any(p in allow for p in permission_patterns())
