"""A malformed `archy.yaml` or an oversized scan reaches the agent with its reason (#793).

`LayerConfigError` and `ScanTooLargeError` subclass bare `Exception`, so on mcp 2.x,
which drops the message of any exception a tool raises, an agent saw only
`Error executing tool archy_score` for the two refusals it is most likely to hit
on a real repository. The CLI prints the reason; the tool boundary now keeps it
on both SDK majors. This file is in the `mcp 2.x compatibility` CI job's list,
because that is the only place the 2.x behaviour is exercised.

Each case first calls the loader or scan directly and asserts the TYPED error, so
the tool-level assertion cannot pass on some other refusal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from archy.graph import ScanTooLargeError
from archy.layers import LayerConfigError, load_config
from archy.mcp import create_server
from archy.mcp_compat import ToolError
from archy.mcp_runs import _load_graph

# Tools that load the config or scan the tree. `archy_diff` is deliberately absent:
# it does not scan before it looks for a baseline.
TOOLS = ["archy_score", "archy_check", "archy_cycles", "archy_graph"]


@pytest.fixture
def bad_config(tmp_path: Path) -> Path:
    """A project whose `archy.yaml` is not a valid config (`layers` is a list)."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("X = 1\n")
    (tmp_path / "archy.yaml").write_text("layers: [1, 2]\n")
    with pytest.raises(LayerConfigError, match="must be a mapping"):
        load_config(tmp_path / "archy.yaml")
    return tmp_path


@pytest.fixture
def too_big(tmp_path: Path) -> Path:
    """A project with more modules than its `max_modules` allows."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name in "abcde":
        (pkg / f"{name}.py").write_text("X = 1\n")
    (tmp_path / "archy.yaml").write_text("max_modules: 2\n")
    with pytest.raises(ScanTooLargeError):
        _load_graph(tmp_path, internal_only=True)
    return tmp_path


def _tool_error(tool: str, path: Path) -> str:
    """Call `tool` in-process and return the text of the ToolError the agent would see.

    A refusal is an exception on both SDK majors, so no result normalisation is needed.
    """
    with pytest.raises(ToolError) as caught:
        asyncio.run(create_server().call_tool(tool, {"path": str(path)}))
    return str(caught.value)


@pytest.mark.parametrize("tool", TOOLS)
def test_a_malformed_config_reaches_the_agent_with_its_reason(bad_config: Path, tool: str):
    assert "must be a mapping" in _tool_error(tool, bad_config)


@pytest.mark.parametrize("tool", TOOLS)
def test_an_oversized_scan_reaches_the_agent_with_the_fix(too_big: Path, tool: str):
    message = _tool_error(tool, too_big)
    assert "max_modules" in message and "exclude:" in message
