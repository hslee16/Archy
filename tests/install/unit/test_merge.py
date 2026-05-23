"""Unit tests for the pure render/merge functions.

Always parse-then-assert (never string-compare hand-written JSON/TOML), and
always assert idempotency: render twice and the second pass is a byte no-op.
"""

from __future__ import annotations

import json
import sys

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
)


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
