"""A PEP 420 namespace monorepo must not report a clean result over a graph it never joined (#586).

`acme.core.leak` and `acme.web.views` import each other, but with no `roots:` the
scan names the modules `core.leak` and `web.views` and every `acme` import
resolves to one external node, so `cycles` said "No cycles found" and `score`
said 1.000. Each surface now names the namespace directory and the `roots:` that
fixes it. Controls cover the cases that must stay silent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.cycles import CycleScan
from archy.graph import build_graph
from archy.graph_namespace import find_namespace_gaps
from archy.mcp import create_server
from archy.mcp_runs import run_check, run_cycles, run_score
from tests.mcp_helpers import call_tool

NAMESPACE_DIRS = ["packages/core/src/acme", "packages/web/src/acme"]


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _uv_workspace(root: Path) -> Path:
    """The ticket's fixture: three distributions, two of them contributing to `acme`."""
    _write(root, "packages/core/src/acme/core/__init__.py")
    _write(root, "packages/core/src/acme/core/util.py", "def f():\n    return 1\n")
    _write(root, "packages/core/src/acme/core/leak.py", "from acme.web import views\n")
    _write(root, "packages/web/src/acme/web/__init__.py")
    _write(root, "packages/web/src/acme/web/views.py", "from acme.core import util, leak\n")
    _write(root, "packages/svc/src/svc/__init__.py")
    _write(root, "packages/svc/src/svc/main.py", "X = 1\n")
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    project = _uv_workspace(tmp_path)
    # Reach the branch before asserting the verdict: an `acme` import that
    # resolved, or an `acme` directory that gained an `__init__.py`, would make
    # every assertion below vacuous.
    graph = build_graph(project)
    assert graph.nodes["acme"]["external"] is True
    assert any(not graph.nodes[p]["external"] for p in graph.predecessors("acme"))
    assert not list(project.glob("packages/*/src/acme/__init__.py"))
    assert not graph.has_edge("web.views", "core.leak")
    return project


def _cycles_text(project: Path) -> str:
    result = CliRunner().invoke(main, ["cycles", str(project)])
    assert result.exit_code == 0, result.output
    return result.output


def test_detector_names_the_directories_and_the_packages_in_them(workspace: Path):
    (gap,) = find_namespace_gaps(build_graph(workspace))
    assert gap.name == "acme"
    assert list(gap.directories) == NAMESPACE_DIRS
    assert gap.packages == ("core", "web")
    assert gap.importers == 2  # core.leak and web.views


def test_cycles_text_names_the_gap_and_the_roots_to_set(workspace: Path):
    out = _cycles_text(workspace)
    assert "# warning: 2 modules import `acme`" in out
    assert f"roots: [{', '.join(NAMESPACE_DIRS)}]" in out


def test_cycles_json_carries_the_reason_not_a_bare_empty_list(workspace: Path):
    result = CliRunner().invoke(main, ["cycles", str(workspace), "--format", "json"])
    payload = json.loads(result.stdout)
    assert payload["cycles"] == []
    (gap,) = payload["namespace_gaps"]
    assert gap["name"] == "acme"
    assert gap["directories"] == NAMESPACE_DIRS
    assert "roots:" in gap["reason"]


def test_cycles_strict_still_passes_because_a_gap_is_a_warning(workspace: Path):
    """The ticket asks for a warning; refusing would fail repos that shadow a name on purpose."""
    result = CliRunner().invoke(main, ["cycles", str(workspace), "--strict"])
    assert result.exit_code == 0, result.output
    assert "# warning:" in result.output


def test_score_text_and_json_carry_the_gap(workspace: Path):
    text = CliRunner().invoke(main, ["score", str(workspace)]).output
    assert text.splitlines()[-1].startswith("# warning: 2 modules import `acme`")
    payload = json.loads(
        CliRunner().invoke(main, ["score", str(workspace), "--format", "json"]).stdout
    )
    assert [g["name"] for g in payload["namespace_gaps"]] == ["acme"]


def test_mcp_cycles_returns_the_object_shape_with_the_gap(workspace: Path):
    result = run_cycles(workspace, min_size=2, internal_only=True)
    assert isinstance(result, CycleScan)
    assert result.model_dump()["namespace_gaps"][0]["name"] == "acme"
    wire = call_tool(create_server(), "archy_cycles", {"path": str(workspace)})
    assert "acme" in json.dumps(wire, default=str)


def test_score_payload_model_dump_carries_the_gap(workspace: Path):
    """Assert the serialized form: FastMCP sends model_dump()."""
    payload = run_score(
        workspace, internal_only=True, record=False, strict=False, strict_tolerance=0.02
    ).model_dump()
    assert payload["namespace_gaps"][0]["name"] == "acme"
    assert payload["namespace_gaps"][0]["directories"] == tuple(NAMESPACE_DIRS)


def test_the_advised_roots_make_the_cycle_visible(workspace: Path):
    """Assert the ANSWER: following the advice finds the cycle, and the warning goes away."""
    (workspace / "archy.yaml").write_text(
        "roots:\n" + "".join(f"  - {d}\n" for d in NAMESPACE_DIRS) + "  - packages/svc/src/svc\n"
    )
    out = _cycles_text(workspace)
    assert "1 cycle(s) found" in out
    assert "acme.core.leak" in out and "acme.web.views" in out
    assert "# warning:" not in out


def test_a_regular_package_is_not_a_gap(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, "pkg/a.py", "import pkg\n")
    assert find_namespace_gaps(build_graph(tmp_path)) == ()


def test_a_third_party_import_sharing_no_scanned_directory_is_not_a_gap(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, "pkg/a.py", "import requests\nimport yaml\n")
    graph = build_graph(tmp_path)
    assert graph.nodes["requests"]["external"] is True  # the import reaches the branch
    assert find_namespace_gaps(graph) == ()
    out = CliRunner().invoke(main, ["cycles", str(tmp_path)]).output
    assert out.strip() == "# No cycles found (min_size=2)."
    as_json = CliRunner().invoke(main, ["cycles", str(tmp_path), "--format", "json"])
    assert json.loads(as_json.stdout) == []


def test_a_namespace_directory_nothing_imports_is_not_a_gap(tmp_path: Path):
    _write(tmp_path, "src/acme/core/__init__.py")
    _write(tmp_path, "src/acme/core/a.py", "X = 1\n")
    assert find_namespace_gaps(build_graph(tmp_path)) == ()


def test_a_nested_regular_package_sharing_a_name_with_a_third_party_import_is_not_a_gap(
    tmp_path: Path,
):
    """`tests/yaml/` has an `__init__.py`, so it is a package, not a namespace directory."""
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, "pkg/a.py", "import yaml\n")
    _write(tmp_path, "tests/__init__.py")
    _write(tmp_path, "tests/yaml/__init__.py")
    _write(tmp_path, "tests/yaml/case.py", "X = 1\n")
    graph = build_graph(tmp_path)
    assert graph.nodes["yaml"]["external"] is True  # the import reaches the branch
    assert "tests.yaml" in graph.nodes  # a nested package of that name exists
    assert find_namespace_gaps(graph) == ()


NO_ROOTS_CONFIG = (
    "layers:\n"
    "  core: {modules: ['core.**']}\n"
    "  web: {modules: ['web.**']}\n"
    "forbid:\n"
    "  - {from: core, to: web}\n"
)


def test_check_passes_but_says_the_graph_was_not_joined(workspace: Path):
    (workspace / "archy.yaml").write_text(NO_ROOTS_CONFIG)
    result = CliRunner().invoke(main, ["check", str(workspace)])
    assert result.exit_code == 0, result.output
    assert "# warning: 2 modules import `acme`" in result.output
    as_json = CliRunner().invoke(main, ["check", str(workspace), "--format", "json"])
    assert json.loads(as_json.stdout)["namespace_gaps"][0]["name"] == "acme"


def test_check_with_the_advised_roots_fires_the_rule_the_gap_hid(workspace: Path):
    """The ticket's end state: with roots, `forbid: core -> web` fires and the warning is gone."""
    (workspace / "archy.yaml").write_text(
        "roots:\n"
        + "".join(f"  - {d}\n" for d in NAMESPACE_DIRS)
        + "layers:\n  core: {modules: ['acme.core.**']}\n  web: {modules: ['acme.web.**']}\n"
        "forbid:\n  - {from: core, to: web}\n"
    )
    result = CliRunner().invoke(main, ["check", str(workspace)])
    assert result.exit_code == 1, result.output
    assert "acme.core.leak" in result.output
    assert "# warning:" not in result.output


def test_check_payload_model_dump_carries_the_gap(workspace: Path):
    (workspace / "archy.yaml").write_text(NO_ROOTS_CONFIG)
    payload = run_check(workspace, config_path=None).model_dump()
    assert payload["passed"] is True
    assert payload["namespace_gaps"][0]["name"] == "acme"


def test_check_over_a_joined_graph_has_no_gap(tmp_path: Path):
    _write(tmp_path, "pkg/__init__.py")
    _write(tmp_path, "pkg/a.py", "import requests\n")
    (tmp_path / "archy.yaml").write_text("layers:\n  pkg: {modules: ['pkg.**']}\nforbid: []\n")
    out = CliRunner().invoke(main, ["check", str(tmp_path)]).output
    assert "# warning:" not in out
    assert run_check(tmp_path, config_path=None).model_dump()["namespace_gaps"] == ()
