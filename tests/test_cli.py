from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from archy.cli import main


def _make_cyclic_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n")
    return tmp_path


def _make_acyclic_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    return tmp_path


def test_cycles_text_output_lists_cycle(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project)])
    assert result.exit_code == 0
    assert "1 cycle(s) found" in result.output
    assert "pkg.a" in result.output
    assert "pkg.b" in result.output


def test_cycles_text_output_when_clean(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project)])
    assert result.exit_code == 0
    assert "No cycles found" in result.output


def test_cycles_json_output_is_valid_json(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    cycle = payload[0]
    assert sorted(cycle["modules"]) == ["pkg.a", "pkg.b"]
    edge_pairs = {(e["source"], e["target"]) for e in cycle["edges"]}
    assert edge_pairs == {("pkg.a", "pkg.b"), ("pkg.b", "pkg.a")}


def test_cycles_strict_exits_nonzero_when_cycles_present(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--strict"])
    assert result.exit_code == 1


def test_cycles_strict_exits_zero_when_clean(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--strict"])
    assert result.exit_code == 0


def test_cycles_min_size_filters_smaller_cycles(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--min-size", "3"])
    assert result.exit_code == 0
    assert "No cycles found" in result.output


def test_graph_command_still_works(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["graph", str(project), "--internal-only"])
    assert result.exit_code == 0
    assert "pkg.a" in result.output
    assert "pkg.b" in result.output
