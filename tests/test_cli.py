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
    assert isinstance(payload, dict)
    [violation] = payload["violations"]
    assert violation["rule"] == {"from": "core", "to": "cli"}
    assert violation["source"] == "myapp.core.api"
    assert violation["target"] == "myapp.cli.runner"
    assert payload["sdp_violations"] == []


def _make_sdp_violating_project(tmp_path: Path) -> Path:
    # See test_run_check_reports_sdp_violations_when_enabled in test_mcp.py
    # for the I calculation; the a -> b edge is the SDP violation.
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from myapp.b import thing\n")
    (pkg / "b.py").write_text("from myapp import y1, y2, y3\n")
    for name in ("y1", "y2", "y3"):
        (pkg / f"{name}.py").write_text("")
    for name in ("x1", "x2", "x3"):
        (pkg / f"{name}.py").write_text("from myapp.a import thing\n")
    return tmp_path


def test_check_sdp_error_mode_fails_gate(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\nsdp:\n  enabled: true\n"
    )
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 1
    assert "SDP violation" in result.output


def test_check_sdp_warn_mode_reports_but_passes(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\nsdp:\n  enabled: true\n  mode: warn\n"
    )
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 0
    assert "SDP violation" in result.output
    assert "sdp.mode=warn" in result.output


def test_check_sdp_warn_mode_still_fails_on_layer_violation(tmp_path: Path):
    # warn mode must not soften unrelated layer-rule failures.
    project = _make_layered_project(tmp_path, with_violation=True)
    cfg = project / "archy.yaml"
    cfg.write_text(cfg.read_text() + "sdp:\n  enabled: true\n  mode: warn\n")
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 1


def test_check_sdp_invalid_mode_clear_error(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\nsdp:\n  enabled: true\n  mode: lenient\n"
    )
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code != 0
    assert "sdp.mode" in result.output


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


def test_score_strict_with_no_history_passes(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["score", str(project), "--strict"])
    assert result.exit_code == 0
    assert "no prior score recorded" in result.output


def test_score_strict_passes_when_unchanged(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["score", str(project), "--record"])
    result = runner.invoke(main, ["score", str(project), "--strict"])
    assert result.exit_code == 0
    assert "strict: PASS" in result.output


def _seed_history(project: Path, overall: float) -> None:
    """Write a synthetic history row with a chosen `overall`.

    Geometric-mean composites are noisy on tiny fixtures - small structural
    changes can move overall in either direction - so we seed history
    directly to test the gate decision in isolation from the score.
    """
    history = project / ".archy" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": "2026-05-09T13:45:07Z",
        "commit": "deadbee",
        "branch": "main",
        "score": {
            "overall": overall,
            "modularity": 0.5,
            "acyclicity": 1.0,
            "depth": 0.5,
            "equality": 0.5,
        },
        "inputs": {
            "module_count": 3,
            "edge_count": 1,
            "cycle_count": 0,
            "max_depth": 1,
            "community_count": 2,
        },
    }
    history.write_text(json.dumps(row) + "\n")


def test_score_strict_fails_when_score_drops_beyond_tolerance(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    _seed_history(project, overall=0.95)
    result = CliRunner().invoke(
        main,
        ["score", str(project), "--strict", "--strict-tolerance", "0.05"],
    )
    assert result.exit_code == 1
    assert "strict: FAIL" in result.output


def test_score_strict_within_tolerance_passes(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    _seed_history(project, overall=0.95)
    result = CliRunner().invoke(
        main,
        ["score", str(project), "--strict", "--strict-tolerance", "1.0"],
    )
    assert result.exit_code == 0
    assert "strict: PASS" in result.output


def test_score_strict_passes_when_score_improves(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    _seed_history(project, overall=0.10)
    result = CliRunner().invoke(
        main,
        ["score", str(project), "--strict", "--strict-tolerance", "0.0"],
    )
    assert result.exit_code == 0
    assert "strict: PASS" in result.output
    assert "improved" in result.output


def test_score_strict_compares_before_recording(tmp_path: Path):
    # `--strict --record` together must compare against the existing last row,
    # not against the row about to be appended (which would always tie at 0).
    project = _make_acyclic_project(tmp_path)
    _seed_history(project, overall=0.95)
    history_path = project / ".archy" / "history.jsonl"
    rows_before = len(history_path.read_text().splitlines())
    result = CliRunner().invoke(
        main,
        [
            "score",
            str(project),
            "--strict",
            "--strict-tolerance",
            "0.0",
            "--record",
        ],
    )
    # `--record` appends unconditionally; the gate verdict is independent.
    assert result.exit_code == 1
    rows_after = len(history_path.read_text().splitlines())
    assert rows_after == rows_before + 1


def test_score_strict_json_includes_gate_block(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    runner.invoke(main, ["score", str(project), "--record"])
    result = runner.invoke(
        main,
        ["score", str(project), "--strict", "--format", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "gate" in payload
    assert payload["gate"]["passed"] is True
    assert payload["gate"]["tolerance"] == 0.02


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


def _make_project_with_generated_dir(tmp_path: Path) -> Path:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "real.py").write_text("import os\n")
    gen = pkg / "baml_client"
    gen.mkdir()
    (gen / "__init__.py").write_text("from myapp.baml_client.b import x\n")
    (gen / "b.py").write_text("from myapp.baml_client import other\n")
    return tmp_path


def test_exclude_drops_directory_from_graph(tmp_path: Path):
    project = _make_project_with_generated_dir(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nexclude: [baml_client]\n")
    args = ["graph", str(project), "--internal-only", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    qualnames = {n["id"] for n in payload["nodes"]}
    assert "myapp" in qualnames
    assert "myapp.real" in qualnames
    assert not any(q.startswith("myapp.baml_client") for q in qualnames)


def test_exclude_omitted_keeps_generated_dir(tmp_path: Path):
    project = _make_project_with_generated_dir(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\n")
    args = ["graph", str(project), "--internal-only", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    qualnames = {n["id"] for n in payload["nodes"]}
    assert any(q.startswith("myapp.baml_client") for q in qualnames)


def test_exclude_silences_cycles_inside_generated_dir(tmp_path: Path):
    project = _make_project_with_generated_dir(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nexclude: [baml_client]\n")
    result = CliRunner().invoke(main, ["cycles", str(project), "--strict"])
    assert result.exit_code == 0


def test_roots_makes_namespace_packages_visible(tmp_path: Path):
    # PEP 420 layout: no __init__.py at any level. archy.yaml declares
    # `roots: [app]` so descendants get qualnames rooted at app.
    app = tmp_path / "app"
    libs = app / "libs"
    libs.mkdir(parents=True)
    (libs / "db.py").write_text("import os\n")
    (app / "main.py").write_text("from app.libs.db import x\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nroots: [app]\n")

    args = ["graph", str(tmp_path), "--internal-only", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    qualnames = {n["id"] for n in payload["nodes"]}
    assert "app.main" in qualnames
    assert "app.libs.db" in qualnames


def test_impact_lists_transitive_dependents(tmp_path: Path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    libs = pkg / "libs"
    libs.mkdir()
    (libs / "__init__.py").write_text("")
    (libs / "db.py").write_text("")
    routers = pkg / "routers"
    routers.mkdir()
    (routers / "__init__.py").write_text("")
    (routers / "user.py").write_text("from app.libs.db import x\n")
    args = ["impact", str(tmp_path), "--file", "app/libs/db.py", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["changed"] == ["app.libs.db"]
    assert payload["impacted"] == ["app.routers.user"]
    assert payload["unresolved"] == []


def test_check_layer_rules_match_namespace_package_modules(tmp_path: Path):
    # Without `roots:`, app.routers.** patterns match nothing because the
    # discovered module is bare `routers.user`. With `roots: [app]`, the layer
    # rule fires and a forbidden edge surfaces as a violation.
    app = tmp_path / "app"
    routers = app / "routers"
    libs = app / "libs"
    routers.mkdir(parents=True)
    libs.mkdir(parents=True)
    (libs / "db.py").write_text("from app.routers.user import handler\n")
    (routers / "user.py").write_text("")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  routers: {modules: [app.routers.**]}\n"
        "  libs: {modules: [app.libs.**]}\n"
        "forbid:\n"
        "  - {from: libs, to: routers}\n"
        "roots: [app]\n"
    )
    result = CliRunner().invoke(main, ["check", str(tmp_path)])
    assert result.exit_code == 1
    assert "libs -> routers" in result.output
