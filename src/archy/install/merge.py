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
from archy.install.writer import InstallError

JsonObj = dict[str, object]


def _mcp_entry() -> JsonObj:
    """The `{command, args}` stanza, fresh dict each call so callers can mutate."""
    return {"command": MCP_COMMAND, "args": list(MCP_ARGS)}


def _dump_json(obj: JsonObj) -> str:
    # No sort_keys: a round-trip must preserve the user's existing key order
    # rather than reshuffle their config. Trailing newline keeps git/POSIX happy.
    return json.dumps(obj, indent=2) + "\n"


def _load_json_obj(existing: str | None) -> JsonObj:
    if not existing or not existing.strip():
        return {}
    loaded = json.loads(existing)
    if not isinstance(loaded, dict):
        raise ValueError("expected a JSON object at the top level")
    return cast(JsonObj, loaded)


def _load_toml_obj(existing: str | None) -> JsonObj:
    """Parse a TOML document to a dict (empty when the file is absent/blank)."""
    if not existing or not existing.strip():
        return {}
    return cast(JsonObj, tomllib.loads(existing))


def _ensure_dict(obj: JsonObj, key: str) -> JsonObj:
    """Return ``obj[key]`` as a dict, creating it only when the key is absent.

    Mutates ``obj`` in place, so the caller's reference stays valid. Used to
    walk-or-create the nested config tables every JSON merge below shares. A
    key present with a non-dict value (e.g. ``mcpServers`` set to a string) is a
    malformed config: we raise rather than silently overwrite it, so the user
    keeps their data and gets an actionable error instead of silent loss (#169).
    JSON ``null`` is treated as absent and (re)created.
    """
    sub = obj.get(key)
    if sub is None:
        sub = {}
        obj[key] = sub
    elif not isinstance(sub, dict):
        raise InstallError(
            f"invalid config: {key!r} must be an object/table, "
            f"found {type(sub).__name__}; fix or remove it before installing."
        )
    return cast(JsonObj, sub)


def render_json_mcp(existing: str | None, *, servers_key: str = "mcpServers") -> str:
    """Merge `<servers_key>.archy = {command, args}` into a JSON config.

    Covers Claude (`~/.claude.json`), Cursor (`mcp.json`), opencode
    (`opencode.json`), and Continue, which all key MCP servers off the same
    object shape (`mcpServers` for most; opencode overrides via ``servers_key``).
    """
    obj = _load_json_obj(existing)
    _ensure_dict(obj, servers_key)[SERVER_KEY] = _mcp_entry()
    return _dump_json(obj)


def render_opencode_mcp(existing: str | None) -> str:
    """opencode's `mcp.archy` stanza uses ``type`` + ``command`` (list) + enabled.

    opencode does not use the `mcpServers` shape; its schema is
    ``mcp.<name> = {type: "local", command: [...], enabled: true}``.
    """
    obj = _load_json_obj(existing)
    _ensure_dict(obj, "mcp")[SERVER_KEY] = {
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
    perms = _ensure_dict(obj, "permissions")
    allow = perms.get("allow")
    if allow is None:
        allow = []
        perms["allow"] = allow
    elif not isinstance(allow, list):
        # A non-list `allow` is malformed (Claude documents it as an array).
        # Normalizing it to [] would silently drop the user's value and, worse,
        # leave uninstall unable to restore it (the strip path only edits a
        # list), breaking the round-trip. Fail fast instead (#169).
        raise InstallError(
            f"invalid config: permissions.allow must be a list, "
            f"found {type(allow).__name__}; fix or remove it before installing."
        )
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
    data = _load_toml_obj(existing)
    _ensure_dict(data, "mcp_servers")[SERVER_KEY] = {
        "command": MCP_COMMAND,
        "args": list(MCP_ARGS),
    }
    return tomli_w.dumps(data)


# --- inverse operations (uninstall) ---------------------------------------
# Each strip_* is the inverse of the matching render_* above: it removes only
# archy's contribution and leaves everything else byte-for-byte. They are
# idempotent (stripping an already-clean config is a no-op) and never delete the
# file; deleting archy-owned files (Continue's block, the .mdc/.md rule files) is
# the adapter's job via the dedicated delete action.


def _drop_server(obj: JsonObj, table_key: str) -> None:
    """Remove ``archy`` from ``obj[table_key]`` if both the table and key exist."""
    table = obj.get(table_key)
    if isinstance(table, dict):
        cast(JsonObj, table).pop(SERVER_KEY, None)


def strip_json_mcp(existing: str | None, *, servers_key: str = "mcpServers") -> str:
    """Inverse of :func:`render_json_mcp`: drop the archy server, keep the rest."""
    obj = _load_json_obj(existing)
    _drop_server(obj, servers_key)
    return _dump_json(obj)


def strip_opencode_mcp(existing: str | None) -> str:
    """Inverse of :func:`render_opencode_mcp`."""
    obj = _load_json_obj(existing)
    _drop_server(obj, "mcp")
    return _dump_json(obj)


def strip_toml_mcp(existing: str | None) -> str:
    """Inverse of :func:`render_toml_mcp`."""
    data = _load_toml_obj(existing)
    _drop_server(data, "mcp_servers")
    return tomli_w.dumps(data)


def strip_claude_permissions(existing: str | None) -> str:
    """Inverse of :func:`render_claude_permissions`: drop only archy's patterns."""
    obj = _load_json_obj(existing)
    permissions = obj.get("permissions")
    if isinstance(permissions, dict):
        allow = cast(JsonObj, permissions).get("allow")
        if isinstance(allow, list):
            archy = set(permission_patterns())
            cast(JsonObj, permissions)["allow"] = [
                p for p in cast(list[object], allow) if p not in archy
            ]
    return _dump_json(obj)
