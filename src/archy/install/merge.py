"""Pure render functions that merge archy's stanza into an existing config.

Every function here takes the *current* file content (``None`` when the file
does not exist) and returns the new content as a string. They never touch the
filesystem, so they are what snapshot tests freeze (layer 2) and contract tests
parse back (layer 4). The cardinal rule from the testing spec: no hand-coded
JSON/TOML strings, always parse-then-reserialize, so a user's unrelated config
survives untouched and a second run is a byte-for-byte no-op (idempotency).
"""

from __future__ import annotations

import json
import sys
from typing import cast

import tomli_w
import yaml

# 3.11+ ships `tomllib`; the `tomli` backport covers archy's 3.10 floor. The
# version guard (rather than try/except) is what lets the type checker narrow:
# it assumes the 3.10 floor, so it resolves the `tomli` branch.
if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10 CI
    import tomli as tomllib

from archy.install.base import (
    MCP_ARGS,
    MCP_COMMAND,
    SERVER_KEY,
    permission_patterns,
)

JsonObj = dict[str, object]


def _mcp_entry() -> JsonObj:
    """The `{command, args}` stanza, fresh dict each call so callers can mutate."""
    return {"command": MCP_COMMAND, "args": list(MCP_ARGS)}


def _dump_json(obj: JsonObj) -> str:
    # indent=2, no key sorting (preserve the user's ordering), trailing newline.
    return json.dumps(obj, indent=2) + "\n"


def _load_json_obj(existing: str | None) -> JsonObj:
    if not existing or not existing.strip():
        return {}
    loaded = json.loads(existing)
    if not isinstance(loaded, dict):
        raise ValueError("expected a JSON object at the top level")
    return cast(JsonObj, loaded)


def render_json_mcp(existing: str | None, *, servers_key: str = "mcpServers") -> str:
    """Merge `<servers_key>.archy = {command, args}` into a JSON config.

    Covers Claude (`~/.claude.json`), Cursor (`mcp.json`), opencode
    (`opencode.json`), and Continue, which all key MCP servers off the same
    object shape (`mcpServers` for most; opencode overrides via ``servers_key``).
    """
    obj = _load_json_obj(existing)
    servers = obj.get(servers_key)
    if not isinstance(servers, dict):
        servers = {}
        obj[servers_key] = servers
    cast(JsonObj, servers)[SERVER_KEY] = _mcp_entry()
    return _dump_json(obj)


def render_opencode_mcp(existing: str | None) -> str:
    """opencode's `mcp.archy` stanza uses ``type`` + ``command`` (list) + enabled.

    opencode does not use the `mcpServers` shape; its schema is
    ``mcp.<name> = {type: "local", command: [...], enabled: true}``.
    """
    obj = _load_json_obj(existing)
    mcp = obj.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
        obj["mcp"] = mcp
    cast(JsonObj, mcp)[SERVER_KEY] = {
        "type": "local",
        "command": [MCP_COMMAND, *MCP_ARGS],
        "enabled": True,
    }
    return _dump_json(obj)


def render_continue_yaml(existing: str | None) -> str:
    """Continue's native MCP block file (YAML).

    Continue reads each MCP server from its own standalone file under
    ``.continue/mcpServers/``. The documented native format is a YAML block with
    top-level ``name`` / ``version`` / ``schema`` and an ``mcpServers`` array; a
    JSON copy from another tool is only loosely "compatible", so we emit the
    fully specified YAML shape. This is a dedicated archy file we own outright,
    so ``existing`` is ignored and overwriting is inherently idempotent.
    """
    doc = {
        "name": SERVER_KEY,
        "version": "0.0.1",
        "schema": "v1",
        "mcpServers": [
            {
                "name": SERVER_KEY,
                "type": "stdio",
                "command": MCP_COMMAND,
                "args": list(MCP_ARGS),
            }
        ],
    }
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def render_claude_permissions(existing: str | None) -> str:
    """Merge archy's tool patterns into Claude's `permissions.allow` array.

    Order-preserving and de-duplicating: existing entries keep their position,
    and only archy patterns not already present are appended. The plugin
    manifest cannot seed this (Claude Code plugin constraint), which is the gap
    this closes.
    """
    obj = _load_json_obj(existing)
    permissions = obj.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        obj["permissions"] = permissions
    perms = cast(JsonObj, permissions)
    allow = perms.get("allow")
    if not isinstance(allow, list):
        allow = []
        perms["allow"] = allow
    allow_list = cast(list[object], allow)
    have = set(allow_list)
    for pattern in permission_patterns():
        if pattern not in have:
            allow_list.append(pattern)
            have.add(pattern)
    return _dump_json(obj)


def render_toml_mcp(existing: str | None) -> str:
    """Merge `[mcp_servers.archy]` into Codex's `config.toml`.

    Codex keys MCP servers under the snake_case `mcp_servers` table. Existing
    tables and scalars are preserved by round-tripping through tomllib/tomli-w.
    """
    data: JsonObj = cast(JsonObj, tomllib.loads(existing)) if existing and existing.strip() else {}
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcp_servers"] = servers
    cast(JsonObj, servers)[SERVER_KEY] = {
        "command": MCP_COMMAND,
        "args": list(MCP_ARGS),
    }
    return tomli_w.dumps(data)
