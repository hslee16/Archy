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


def _make_layered_project(tmp_path: Path, *, with_violation: bool) -> Path:
    """Two-layer project. core imports from cli iff with_violation=True."""
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    core = pkg / "core"
    core.mkdir()
    (core / "__init__.py").write_text("")
    cli = pkg / "cli"
    cli.mkdir()
    (cli / "__init__.py").write_text("")
    (cli / "runner.py").write_text("from myapp.core import api\n")
    if with_violation:
        (core / "api.py").write_text("from myapp.cli.runner import go\n")
    else:
        (core / "api.py").write_text("")
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    return tmp_path


def test_check_clean_project_exits_zero(tmp_path: Path):
    project = _make_layered_project(tmp_path, with_violation=False)
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 0
    assert "No layer violations" in result.output


def test_check_violations_exit_one_and_listed(tmp_path: Path):
    project = _make_layered_project(tmp_path, with_violation=True)
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 1
    assert "1 layer violation(s)" in result.output
    assert "core -> cli (forbidden)" in result.output
    assert "myapp.core.api -> myapp.cli.runner" in result.output


def test_check_json_output(tmp_path: Path):
    project = _make_layered_project(tmp_path, with_violation=True)
    result = CliRunner().invoke(main, ["check", str(project), "--format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    [violation] = payload
    assert violation["rule"] == {"from": "core", "to": "cli"}
    assert violation["source"] == "myapp.core.api"
    assert violation["target"] == "myapp.cli.runner"


def test_check_no_config_gives_clear_error(tmp_path: Path):
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    result = CliRunner().invoke(main, ["check", str(tmp_path)])
    assert result.exit_code != 0
    assert "no archy.yaml found" in result.output.lower()


def test_check_explicit_config_path(tmp_path: Path):
    project = _make_layered_project(tmp_path, with_violation=False)
    cfg = project / "archy.yaml"
    moved = tmp_path / "elsewhere.yaml"
    moved.write_text(cfg.read_text())
    cfg.unlink()
    result = CliRunner().invoke(main, ["check", str(project), "--config", str(moved)])
    assert result.exit_code == 0


def test_check_malformed_config_reports_error(tmp_path: Path):
    project = _make_layered_project(tmp_path, with_violation=False)
    (project / "archy.yaml").write_text(
        "layers:\n  core: {modules: [myapp.core.**]}\nforbid:\n  - {from: core, to: ghost}\n"
    )
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code != 0
    assert "ghost" in result.output


def test_score_text_output(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code == 0
    assert "archy score:" in result.output
    assert "modularity:" in result.output
    assert "acyclicity:" in result.output
    assert "depth:" in result.output
    assert "equality:" in result.output


def test_score_json_output(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["score", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "overall" in payload
    assert set(payload["components"]) == {"modularity", "acyclicity", "depth", "equality"}
    assert 0.0 <= payload["overall"] <= 1.0
    for v in payload["components"].values():
        assert 0.0 <= v <= 1.0


def test_score_drops_when_cycle_introduced(tmp_path: Path):
    (tmp_path / "clean").mkdir()
    (tmp_path / "cyclic").mkdir()
    clean_project = _make_acyclic_project(tmp_path / "clean")
    cyclic_project = _make_cyclic_project(tmp_path / "cyclic")
    runner = CliRunner()
    clean = json.loads(
        runner.invoke(main, ["score", str(clean_project), "--format", "json"]).output
    )
    cyclic = json.loads(
        runner.invoke(main, ["score", str(cyclic_project), "--format", "json"]).output
    )
    assert cyclic["components"]["acyclicity"] < clean["components"]["acyclicity"]
    assert cyclic["inputs"]["cycle_count"] == 1
    assert clean["inputs"]["cycle_count"] == 0
    # Tiny graphs are noisy across the other three metrics, so we only assert
    # the deterministic acyclicity drop. compute_score's overall semantics
    # are exercised in tests/test_score.py against larger fixtures.


def test_score_record_writes_history_row(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    history_path = project / ".archy" / "history.jsonl"
    assert not history_path.exists()
    result = CliRunner().invoke(main, ["score", str(project), "--record"])
    assert result.exit_code == 0
    assert history_path.exists()
    [line] = history_path.read_text().splitlines()
    payload = json.loads(line)
    assert "score" in payload
    assert "overall" in payload["score"]


def test_score_without_record_does_not_write_history(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    history_path = project / ".archy" / "history.jsonl"
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code == 0
    assert not history_path.exists()


def test_trend_empty_history(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["trend", str(project)])
    assert result.exit_code == 0
    assert "No archy score history" in result.output


def test_trend_after_recording(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["score", str(project), "--record"])
    runner.invoke(main, ["score", str(project), "--record"])
    result = runner.invoke(main, ["trend", str(project)])
    assert result.exit_code == 0
    assert "last 2 of 2" in result.output
    assert "score" in result.output


def test_trend_json_output(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["score", str(project), "--record"])
    result = runner.invoke(main, ["trend", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert "overall" in payload[0]["score"]


def test_graph_command_still_works(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["graph", str(project), "--internal-only"])
    assert result.exit_code == 0
    assert "pkg.a" in result.output
    assert "pkg.b" in result.output
