"""Two files that map to one module qualname are refused, not silently merged (#531).

The graph keeps one node per qualname, so the second file used to replace the
first. `archy affected` then reported "0 test(s)" for tests that had been dropped.
Every case first asserts the FIXTURE really produces equal qualnames through the
naming helper, independently of the detection, so a layout change cannot make the
tests quietly stop exercising the collision.

Also in the `mcp 2.x compatibility` CI job's list: the tool-level case is only
meaningful where the SDK drops a bare exception's message.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.graph import (
    QualnameCollisionError,
    _find_package_roots,
    _iter_python_files,
    _qualname_for,
    build_graph,
    discover_modules,
)
from archy.index import build_graph_cached
from archy.mcp import create_server
from archy.mcp_compat import ToolError


def _qualnames(root: Path) -> list[str | None]:
    """Qualnames the naming helper assigns, with no collision check in the way."""
    roots = _find_package_roots(root, frozenset(), ())
    return [_qualname_for(f, roots) for f in _iter_python_files(root, frozenset())]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A uv-workspace: two members, each with its own `tests/` package."""
    for member in ("alpha", "beta"):
        src = tmp_path / "packages" / member / "src" / member
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("")
        (src / "core.py").write_text("X = 1\n")
        tests = tmp_path / "packages" / member / "tests"
        tests.mkdir()
        (tests / "__init__.py").write_text("")
        (tests / "test_core.py").write_text(f"from {member} import core\n")
    names = _qualnames(tmp_path)
    assert names.count("tests") == 2 and names.count("tests.test_core") == 2
    return tmp_path


@pytest.fixture
def module_and_package(tmp_path: Path) -> Path:
    """`pkg/foo.py` beside `pkg/foo/__init__.py`: both claim `pkg.foo`."""
    pkg = tmp_path / "pkg"
    (pkg / "foo").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "foo.py").write_text("import os\n")
    (pkg / "foo" / "__init__.py").write_text("import json\n")
    assert _qualnames(tmp_path).count("pkg.foo") == 2
    return tmp_path


def test_workspace_discovery_names_every_path(workspace: Path):
    with pytest.raises(QualnameCollisionError) as caught:
        discover_modules(workspace)
    assert caught.value.collisions["tests.test_core"] == [
        "packages/alpha/tests/test_core.py",
        "packages/beta/tests/test_core.py",
    ]
    assert set(caught.value.collisions) == {"tests", "tests.test_core"}
    assert "packages/alpha/tests/test_core.py" in str(caught.value)
    assert "packages/beta/tests/test_core.py" in str(caught.value)


def test_module_beside_same_named_package_is_refused(module_and_package: Path):
    with pytest.raises(QualnameCollisionError) as caught:
        build_graph(module_and_package)
    assert caught.value.collisions == {"pkg.foo": ["pkg/foo/__init__.py", "pkg/foo.py"]}


def test_cached_build_takes_the_same_route(workspace: Path):
    with pytest.raises(QualnameCollisionError):
        build_graph_cached(workspace, db_path=workspace / "index.db")


def test_affected_no_longer_prints_zero_tests(workspace: Path):
    result = CliRunner().invoke(
        main, ["affected", str(workspace), str(workspace / "packages/alpha/src/alpha/core.py")]
    )
    assert result.exit_code == 1
    assert "0 test(s)" not in result.output
    assert result.output.startswith("Error: ")
    assert "packages/alpha/tests/test_core.py" in result.output
    assert "packages/beta/tests/test_core.py" in result.output


@pytest.mark.parametrize("tool", ["archy_score", "archy_check", "archy_cycles", "archy_graph"])
def test_mcp_tool_carries_both_paths(workspace: Path, tool: str):
    # `archy_check` answers a missing config in-band, so give every tool one.
    (workspace / "archy.yaml").write_text("layers:\n  all:\n    modules: ['alpha.**']\n")
    with pytest.raises(ToolError) as caught:
        asyncio.run(create_server().call_tool(tool, {"path": str(workspace)}))
    message = str(caught.value)
    assert "packages/alpha/tests/test_core.py" in message
    assert "packages/beta/tests/test_core.py" in message


def test_a_compiled_twin_of_a_py_file_is_not_a_collision(tmp_path: Path):
    """A mypyc build leaves `foo.so` beside `foo.py`: one module, two artefacts."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "foo.py").write_text("X = 1\n")
    (pkg / "foo.cpython-310-darwin.so").write_bytes(b"")
    assert [m.qualname for m in discover_modules(tmp_path)] == ["pkg", "pkg.foo"]
