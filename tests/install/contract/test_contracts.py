"""Layer 4: parse each emitted config back with the client's expected parser.

Catches "writer emits something the client would reject" without running the
client. Every assertion parses the bytes (JSON / TOML / YAML) rather than
string-matching, so cosmetic formatting changes don't cause false failures but a
structural break does.
"""

from __future__ import annotations

import json
import sys

import pytest
import yaml

from archy.install.base import Scope, apply_plan, permission_patterns
from archy.install.registry import get_adapter
from archy.install.writer import DryRunWriteSystem

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


def _emit(adapter_id: str, simulate_os, scope: Scope = Scope.GLOBAL) -> dict[str, str]:
    """Render an adapter on a faked Linux box; map basename -> content."""
    fake = simulate_os("linux")
    adapter = get_adapter(adapter_id)
    ws = DryRunWriteSystem()
    plan = adapter.plan(scope, project_root=fake.home / "proj", seed_permissions=True)
    apply_plan(plan, ws)
    return {r.path.name: r.content for r in ws.records}


def _assert_uvx_archy(entry: dict) -> None:
    assert entry["command"] == "uvx"
    assert entry["args"] == ["archy", "mcp"]


def test_claude_mcp_parses_as_json(simulate_os):
    files = _emit("claude", simulate_os)
    obj = json.loads(files[".claude.json"])
    _assert_uvx_archy(obj["mcpServers"]["archy"])


def test_claude_permissions_parses_with_all_tools(simulate_os):
    files = _emit("claude", simulate_os)
    obj = json.loads(files["settings.json"])
    assert set(permission_patterns()).issubset(set(obj["permissions"]["allow"]))


def test_cursor_mcp_parses_as_json(simulate_os):
    files = _emit("cursor", simulate_os)
    obj = json.loads(files["mcp.json"])
    _assert_uvx_archy(obj["mcpServers"]["archy"])


def test_codex_config_parses_as_toml(simulate_os):
    files = _emit("codex", simulate_os)
    data = tomllib.loads(files["config.toml"])
    _assert_uvx_archy(data["mcp_servers"]["archy"])


def test_opencode_config_parses_as_json(simulate_os):
    files = _emit("opencode", simulate_os)
    obj = json.loads(files["opencode.json"])
    entry = obj["mcp"]["archy"]
    assert entry["type"] == "local"
    assert entry["command"] == ["uvx", "archy", "mcp"]
    assert entry["enabled"] is True


def test_continue_block_parses_as_yaml(simulate_os):
    files = _emit("continue", simulate_os)
    doc = yaml.safe_load(files["archy.yaml"])
    assert doc["schema"] == "v1"
    server = doc["mcpServers"][0]
    assert server["command"] == "uvx"
    assert server["args"] == ["archy", "mcp"]


@pytest.mark.parametrize("adapter_id", ["claude", "cursor", "codex", "opencode", "continue"])
def test_instruction_file_is_nonempty_text(adapter_id, simulate_os):
    files = _emit(adapter_id, simulate_os)
    rule_names = {"CLAUDE.md", "AGENTS.md", "archy.mdc", "archy.md"}
    instruction_files = [c for name, c in files.items() if name in rule_names]
    assert instruction_files, f"{adapter_id} emitted no instruction file"
    assert all("archy" in c for c in instruction_files)
