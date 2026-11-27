"""Refusals that could be deleted with the suite still green (#704).

The directory ones carry weight: without them a mistyped path is created on disk
(the cache's parent) and the empty tree scores as a perfect project (#552).
Named `test_mcp_*` so the mcp 2.x CI job runs it too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.mcp import create_server
from archy.mcp_compat import ToolError
from tests.mcp_helpers import _list_tools
from tests.mcp_helpers import call_tool as _call_tool

# A value for each argument, besides `path`, that some tool requires.
_REQUIRED = {"module": "pkg", "files": ["pkg/a.py"]}


def _tools() -> dict[str, dict]:
    """Every tool, with the arguments it requires besides `path`."""
    found = {}
    for tool in _list_tools(create_server()):
        schema = tool.model_dump(by_alias=True)["inputSchema"]
        assert "path" in schema.get("required", []), f"{tool.name} takes no path"
        found[tool.name] = {k: _REQUIRED[k] for k in schema["required"] if k != "path"}
    return found


@pytest.fixture
def project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("X = 1\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("tool", sorted(_tools()))
def test_every_tool_refuses_a_directory_that_does_not_exist(tool: str, tmp_path: Path):
    missing = tmp_path / "typo"

    with pytest.raises(ToolError) as caught:
        _call_tool(create_server(), tool, {"path": str(missing), **_tools()[tool]})

    assert str(caught.value).endswith(f"Directory '{missing}' does not exist.")
    assert not missing.exists()


@pytest.mark.parametrize("tool", sorted(_tools()))
def test_every_tool_refuses_a_file_given_as_the_project(tool: str, project: Path):
    file = project / "pkg" / "a.py"

    with pytest.raises(ToolError) as caught:
        _call_tool(create_server(), tool, {"path": str(file), **_tools()[tool]})

    assert str(caught.value).endswith(f"Directory '{file}' is a file.")


def test_a_negative_graph_depth_is_refused_and_zero_is_not(project: Path):
    arguments = {"path": str(project), "focus": ["pkg.a"]}

    with pytest.raises(ToolError) as caught:
        _call_tool(create_server(), "archy_graph", {**arguments, "depth": -1})

    assert str(caught.value).endswith("depth must be >= 0; got -1")
    _content, payload = _call_tool(create_server(), "archy_graph", {**arguments, "depth": 0})
    assert payload is not None
