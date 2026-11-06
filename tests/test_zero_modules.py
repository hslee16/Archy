"""A scan that found 0 internal modules checked nothing, and must say so (#523).

`cycles` printed "No cycles found" and `score` printed 1.000 over an empty
directory, over a flat scripts directory holding a real import cycle, and over a
config that excludes the only package. Each case is paired with a control that
scans real modules, so a guard that fires on everything goes red too.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.mcp import create_server
from archy.mcp_compat import ToolError
from archy.mcp_runs import run_cycles, run_score
from archy.score import VACUOUS_SCORE_NOTE
from tests.test_mcp import _call_tool


def _empty_dir(root: Path) -> Path:
    return root


def _scripts_cycle(root: Path) -> Path:
    """A real import cycle that scans to 0 modules: no `__init__.py`, so no package."""
    (root / "a.py").write_text("import b\n")
    (root / "b.py").write_text("import a\n")
    return root


def _excluded_package(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("X = 1\n")
    (root / "archy.yaml").write_text("exclude: ['pkg']\n")
    return root


def _real_package(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg import b\n")
    (pkg / "b.py").write_text("X = 1\n")
    return root


EMPTY_FIXTURES: list[Callable[[Path], Path]] = [_empty_dir, _scripts_cycle, _excluded_package]


@pytest.fixture(params=EMPTY_FIXTURES, ids=lambda f: f.__name__.strip("_"))
def empty_project(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    project = request.param(tmp_path)
    # Assert the fixture reaches the branch before asserting the verdict: a
    # fixture that drifts to scan a module would make every test below vacuous.
    graph = CliRunner().invoke(main, ["graph", str(project), "--internal-only"])
    assert graph.output.startswith("# 0 internal module(s)"), graph.output
    return project


@pytest.fixture
def real_project(tmp_path: Path) -> Path:
    project = _real_package(tmp_path)
    graph = CliRunner().invoke(main, ["graph", str(project), "--internal-only"])
    assert not graph.output.startswith("# 0 internal module(s)"), graph.output
    return project


def test_cycles_text_says_nothing_was_checked(empty_project: Path):
    result = CliRunner().invoke(main, ["cycles", str(empty_project)])
    assert result.exit_code == 0, result.output
    assert f"0 internal modules scanned under {empty_project}; nothing was checked" in result.output
    assert "No cycles found" not in result.output


def test_cycles_strict_refuses_with_the_reason(empty_project: Path):
    result = CliRunner().invoke(main, ["cycles", str(empty_project), "--strict"])
    assert result.exit_code == 1, result.output
    assert "nothing was checked" in result.stderr


def test_cycles_json_keeps_the_bare_list_and_warns_on_stderr(empty_project: Path):
    result = CliRunner().invoke(main, ["cycles", str(empty_project), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []
    assert "warning: 0 internal modules scanned" in result.stderr


def test_cycles_json_strict_refuses_once_not_twice(empty_project: Path):
    result = CliRunner().invoke(
        main, ["cycles", str(empty_project), "--format", "json", "--strict"]
    )
    assert result.exit_code == 1, result.output
    assert result.stderr.count("nothing was checked") == 1


def test_cycles_over_real_modules_is_unchanged(real_project: Path):
    text = CliRunner().invoke(main, ["cycles", str(real_project), "--strict"])
    assert text.exit_code == 0, text.output
    assert text.output.startswith("# No cycles found")
    as_json = CliRunner().invoke(main, ["cycles", str(real_project), "--format", "json"])
    assert json.loads(as_json.stdout) == []
    assert as_json.stderr == ""


def test_score_text_headline_is_marked_vacuous(empty_project: Path):
    result = CliRunner().invoke(main, ["score", str(empty_project)])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# archy score: 1.000 (vacuous: ")
    assert VACUOUS_SCORE_NOTE in result.output.splitlines()[0]


def test_score_json_carries_the_reason_and_keeps_overall_numeric(empty_project: Path):
    result = CliRunner().invoke(main, ["score", str(empty_project), "--format", "json"])
    payload = json.loads(result.stdout)
    assert payload["overall"] == 1.0
    assert payload["modules_scanned"] == 0
    assert payload["note"] == VACUOUS_SCORE_NOTE


@pytest.mark.parametrize("flag", ["--record", "--strict"])
def test_score_refuses_to_record_or_gate_over_nothing(empty_project: Path, flag: str):
    result = CliRunner().invoke(main, ["score", str(empty_project), flag])
    assert result.exit_code == 1, result.output
    assert "nothing was scored" in result.stderr
    assert not (empty_project / ".archy").exists()


def test_score_over_real_modules_has_no_note(real_project: Path):
    text = CliRunner().invoke(main, ["score", str(real_project)])
    assert "vacuous" not in text.output
    payload = json.loads(
        CliRunner().invoke(main, ["score", str(real_project), "--format", "json"]).stdout
    )
    assert payload["modules_scanned"] == 2 + 1  # pkg, pkg.a, pkg.b
    assert payload["note"] is None
    recorded = CliRunner().invoke(main, ["score", str(real_project), "--record"])
    assert recorded.exit_code == 0, recorded.output
    assert (real_project / ".archy" / "history.jsonl").exists()


def test_run_cycles_refuses_rather_than_return_an_empty_list(empty_project: Path):
    message = f"0 internal modules scanned under {empty_project}; nothing was checked"
    with pytest.raises(ValueError) as caught:
        run_cycles(empty_project, min_size=2, internal_only=True)
    assert str(caught.value) == message
    with pytest.raises(ToolError) as tool_caught:
        _call_tool(create_server(), "archy_cycles", {"path": str(empty_project)})
    assert message in str(tool_caught.value)


def test_run_cycles_over_real_modules_still_returns_a_list(real_project: Path):
    assert run_cycles(real_project, min_size=2, internal_only=True) == []


def test_score_payload_model_dump_carries_the_reason(empty_project: Path):
    """Assert the serialized form: FastMCP sends model_dump(), which drops a plain property."""
    payload = run_score(
        empty_project, internal_only=True, record=False, strict=False, strict_tolerance=0.02
    ).model_dump()
    assert payload["modules_scanned"] == 0
    assert payload["note"] == VACUOUS_SCORE_NOTE


@pytest.mark.parametrize("flag", ["record", "strict"])
def test_run_score_refuses_to_record_or_gate_over_nothing(empty_project: Path, flag: str):
    kwargs = {"record": False, "strict": False, "strict_tolerance": 0.02, flag: True}
    with pytest.raises(ValueError, match="nothing was scored"):
        run_score(empty_project, internal_only=True, **kwargs)
    # The MCP path keeps a parse cache under .archy/; the invariant is that no
    # history row was written.
    assert not (empty_project / ".archy" / "history.jsonl").exists()


def test_run_score_over_real_modules_has_no_note(real_project: Path):
    payload = run_score(
        real_project, internal_only=True, record=False, strict=False, strict_tolerance=0.02
    ).model_dump()
    assert payload["modules_scanned"] > 0
    assert payload["note"] is None
