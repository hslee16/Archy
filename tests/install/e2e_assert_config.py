"""Post-install assertion for the gated E2E workflow (layer 5).

Run as `python tests/install/e2e_assert_config.py <adapter_id>` after a real
`archy install`. Resolves the adapter's real global config path on this OS,
parses it with the client's expected parser, and asserts archy is wired in.
Exits non-zero (with a clear message) on any miss. Needs no API key, so it
gives the E2E job a meaningful assertion even when client secrets are absent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import yaml

from archy.install.base import Scope
from archy.install.registry import get_adapter

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def _load(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".toml":
        return cast("dict[str, object]", tomllib.loads(text))
    if path.suffix in {".yaml", ".yml"}:
        return cast("dict[str, object]", yaml.safe_load(text))
    return cast("dict[str, object]", json.loads(text))


def _has_archy(adapter_id: str, data: dict[str, object]) -> bool:
    if adapter_id == "opencode":
        return "archy" in cast("dict[str, object]", data.get("mcp", {}))
    if adapter_id == "codex":
        return "archy" in cast("dict[str, object]", data.get("mcp_servers", {}))
    if adapter_id == "continue":
        servers = cast("list[dict[str, object]]", data.get("mcpServers", []))
        return any(s.get("name") == "archy" for s in servers)
    return "archy" in cast("dict[str, object]", data.get("mcpServers", {}))


def main(adapter_id: str) -> int:
    adapter = get_adapter(adapter_id)
    path = adapter.config_paths(Scope.GLOBAL)[0]
    if not path.exists():
        print(f"FAIL: {adapter_id} config not found at {path}")
        return 1
    data = _load(path)
    if not _has_archy(adapter_id, data):
        print(f"FAIL: {adapter_id} config at {path} has no archy MCP server")
        return 1
    print(f"OK: {adapter_id} wired archy into {path}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: e2e_assert_config.py <adapter_id>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
