from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from archy.cli import _parse_edge_spec, main


def _make_two_module_project(tmp_path: Path, *, cyclic: bool) -> Path:
    """`pkg.a` imports `pkg.b`; `pkg.b` imports `pkg.a` only when cyclic.

    Underlies both `_make_cyclic_project` and `_make_acyclic_project`,
    which keep descriptive call-site names while sharing the layout.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n" if cyclic else "")
    return tmp_path


def _make_cyclic_project(tmp_path: Path) -> Path:
    return _make_two_module_project(tmp_path, cyclic=True)


def _make_acyclic_project(tmp_path: Path) -> Path:
    return _make_two_module_project(tmp_path, cyclic=False)


def _make_empty_package_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
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


def test_cycles_min_size_rejects_invalid_values(tmp_path: Path):
    project = _make_cyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--min-size", "0"])
    assert result.exit_code != 0
    assert "--min-size must be >= 1; got 0" in result.output


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


def test_check_reports_coverage_on_a_clean_pass(tmp_path: Path):
    """A pass is exactly when the reader needs to know the rules could fire."""
    project = _make_layered_project(tmp_path, with_violation=False)
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 0
    assert "layer coverage:" in result.output


def test_check_says_so_when_the_config_governs_nothing(tmp_path: Path):
    """The silent-failure case: rules naming a package the tree does not have.

    Previously this printed "0 of 0 modules (100%)", which is the exact failure
    the coverage line exists to prevent.
    """
    project = tmp_path / "proj"
    (project / "app").mkdir(parents=True)
    (project / "app" / "__init__.py").write_text("")
    (project / "app" / "main.py").write_text("x = 1\n")
    (project / "archy.yaml").write_text(
        "layers:\n  routes:\n    modules: ['routes.**']\nforbid: []\n"
    )
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 0
    assert "NO modules under the declared root packages" in result.output
    assert "100%" not in result.output


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
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nsdp:\n  enabled: true\n")
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
    _make_empty_package_project(tmp_path)
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
    assert "complexity:" in result.output


def test_score_json_output(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["score", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "overall" in payload
    assert set(payload["components"]) == {
        "modularity",
        "acyclicity",
        "depth",
        "equality",
        "complexity",
    }
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


def test_trend_last_rejects_invalid_values(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["trend", str(project), "--last", "0"])
    assert result.exit_code != 0
    assert "--last must be >= 1; got 0" in result.output


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


def test_score_strict_tolerance_rejects_out_of_range_values(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(
        main,
        ["score", str(project), "--strict", "--strict-tolerance", "-0.5"],
    )
    assert result.exit_code != 0
    assert "--strict-tolerance must be in [0, 1]; got -0.5" in result.output


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


def _make_libs_to_routers_chain(tmp_path: Path) -> None:
    """`app.routers.user` imports `app.libs.db`.

    Shared by `archy impact` and `archy affected` CLI tests that need a
    two-module chain to exercise reverse-dependency traversal.
    """
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


def test_impact_lists_transitive_dependents(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    args = ["impact", str(tmp_path), "--file", "app/libs/db.py", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["changed"] == ["app.libs.db"]
    assert payload["impacted"] == ["app.routers.user"]
    assert payload["unresolved"] == []


def test_impact_max_chains_rejects_zero(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    args = ["impact", str(tmp_path), "--file", "app/libs/db.py", "--max-chains", "0"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "--max-chains must be negative (for all) or positive (for a limit); got 0" in (
        result.output
    )


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_hotspots_top_rejects_invalid_values(tmp_path: Path, bad: str):
    # --top was unvalidated: 0 showed nothing and -1 sliced off the
    # lowest-score row while mislabeling the count. The guard runs before the
    # git check, so a non-git tmp dir still exercises it.
    _make_empty_package_project(tmp_path)
    result = CliRunner().invoke(main, ["hotspots", str(tmp_path), "--top", bad])
    assert result.exit_code != 0
    assert f"--top must be >= 1; got {bad}" in result.output


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_what_to_refactor_next_top_rejects_invalid_values(tmp_path: Path, bad: str):
    _make_empty_package_project(tmp_path)
    result = CliRunner().invoke(main, ["what-to-refactor-next", str(tmp_path), "--top", bad])
    assert result.exit_code != 0
    assert f"--top must be >= 1; got {bad}" in result.output


@pytest.mark.parametrize("bad", ["1.5", "-0.1"])
def test_what_to_refactor_next_min_risk_rejects_out_of_range(tmp_path: Path, bad: str):
    _make_empty_package_project(tmp_path)
    result = CliRunner().invoke(main, ["what-to-refactor-next", str(tmp_path), "--min-risk", bad])
    assert result.exit_code != 0
    assert "--min-risk must be in [0, 1]" in result.output


def test_affected_classifies_tests_and_modules_json(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_db.py").write_text("from app.libs.db import x\n")

    args = ["affected", str(tmp_path), "app/libs/db.py", "--json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changed"] == ["app.libs.db"]
    assert payload["impacted_modules"] == ["app.routers.user"]
    assert payload["impacted_tests"] == ["tests.test_db"]
    assert payload["depth"] == 5


def _make_app_with_test_module(tmp_path: Path) -> None:
    """Minimal app/core.py + tests/test_core.py importing it.

    Shared between the `archy affected` CLI tests that need a single
    test module mapped to a single source module.
    """
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_core.py").write_text("from app.core import x\n")


def test_affected_quiet_emits_test_file_paths(tmp_path: Path):
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "app/core.py", "--quiet"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line]
    assert len(lines) == 1
    assert lines[0].endswith("tests/test_core.py")


def test_affected_stdin_reads_paths(tmp_path: Path):
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "--stdin", "--json"]
    result = CliRunner().invoke(main, args, input="app/core.py\n")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["impacted_tests"] == ["tests.test_core"]


def test_affected_json_and_quiet_mutually_exclusive(tmp_path: Path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")

    args = ["affected", str(tmp_path), "app/core.py", "--json", "--quiet"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_affected_depth_rejects_invalid_values(tmp_path: Path):
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "app/core.py", "--depth", "0"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "--depth must be >= 1; got 0" in result.output


def test_affected_filter_with_regex_metachars_does_not_crash(tmp_path: Path):
    # Regression for #170 (F3): a `--filter` containing regex metachars that
    # are not glob operators must be treated as literal text, not raise an
    # uncaught re.error (the old escape allowlist omitted `[`/`]`).
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "app/core.py", "--filter", "[", "--json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert result.exception is None
    payload = json.loads(result.output)
    # `[` matches nothing here, so the test module is classified as a module.
    assert payload["impacted_tests"] == []


def test_affected_merges_stdin_and_positional_files(tmp_path: Path):
    # Regression for #170 (F9): --stdin and positional FILES are merged, not
    # mutually exclusive. Both sources should appear in `changed`.
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")
    (pkg / "other.py").write_text("")

    args = ["affected", str(tmp_path), "app/core.py", "--stdin", "--json"]
    result = CliRunner().invoke(main, args, input="app/other.py\n")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changed"] == ["app.core", "app.other"]


def test_contracts_missing_config_exits_one(tmp_path: Path):
    # Regression for #170 (F8): missing contracts config exits 1 (like
    # check/score gate failures), not 2, and prints a clean message rather
    # than a traceback.
    _make_empty_package_project(tmp_path)
    result = CliRunner().invoke(main, ["contracts", str(tmp_path)])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)


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


# --- dsm command --------------------------------------------------------------


def _make_three_module_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.c import other\n")
    (pkg / "c.py").write_text("")
    return tmp_path


def test_dsm_ascii_output_contains_module_names(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--group", "topological"])
    assert result.exit_code == 0
    assert "pkg.a" in result.output
    assert "pkg.b" in result.output
    assert "pkg.c" in result.output


def test_dsm_json_output_is_valid_json(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["n"] >= 3
    assert "cells" in payload
    assert "ordering" in payload


def test_dsm_focus_filter_narrows_output(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(
        main,
        ["dsm", str(project), "--focus", "pkg.b", "--format", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    names = set(payload["ordering"])
    assert names == {"pkg.a", "pkg.b", "pkg.c"}


def test_dsm_package_filter_excludes_other_packages(tmp_path: Path):
    pkg = tmp_path / "pkg"
    other = tmp_path / "other"
    pkg.mkdir()
    other.mkdir()
    (pkg / "__init__.py").write_text("")
    (other / "__init__.py").write_text("")
    (pkg / "a.py").write_text("")
    (other / "b.py").write_text("")
    result = CliRunner().invoke(
        main, ["dsm", str(tmp_path), "--package", "pkg", "--format", "json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert all(n.startswith("pkg") for n in payload["ordering"])


def test_dsm_diff_against_baseline_snapshot(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    baseline = tmp_path / "before.json"
    before = CliRunner().invoke(
        main, ["dsm", str(project), "--format", "json", "--group", "topological"]
    )
    assert before.exit_code == 0
    baseline.write_text(before.output)
    # Introduce a back-edge.
    (project / "pkg" / "c.py").write_text("from pkg.a import _\n")
    result = CliRunner().invoke(
        main,
        [
            "dsm",
            str(project),
            "--group",
            "topological",
            "--diff",
            str(baseline),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    diff_payload = json.loads(result.output)
    assert diff_payload["new_back_edges"], "the c->a edit must surface as a new back-edge"


def test_dsm_diff_missing_baseline_errors_cleanly(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--diff", str(tmp_path / "nope.json")])
    assert result.exit_code != 0
    assert "no DSM snapshot" in result.output or "no DSM snapshot" in (result.stderr or "")


def test_dsm_ascii_rejects_oversized_with_helpful_message(tmp_path: Path):
    pkg = tmp_path / "big"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(20):
        (pkg / f"m{i}.py").write_text("")
    result = CliRunner().invoke(main, ["dsm", str(tmp_path), "--max-nodes", "5"])
    assert result.exit_code == 0
    assert "exceeds max_nodes" in result.output
    assert "--focus" in result.output


def test_dsm_rejects_negative_focus_depth(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--focus-depth", "-1"])
    assert result.exit_code != 0
    assert "--focus-depth must be >= 0; got -1" in result.output


def test_dsm_rejects_invalid_max_nodes(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--max-nodes", "0"])
    assert result.exit_code != 0
    assert "--max-nodes must be >= 1; got 0" in result.output


def test_index_sync_reports_stats_and_caches(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    first = CliRunner().invoke(main, ["index", "sync", str(project)])
    assert first.exit_code == 0
    assert "reparsed" in first.output
    assert (project / ".archy" / "index.db").exists()
    # Second sync reuses the cache: nothing reparsed.
    second = CliRunner().invoke(main, ["index", "sync", str(project)])
    assert second.exit_code == 0
    assert "0 reparsed" in second.output


def test_index_clear_removes_db(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    CliRunner().invoke(main, ["index", "sync", str(project)])
    result = CliRunner().invoke(main, ["index", "clear", str(project)])
    assert result.exit_code == 0
    assert not (project / ".archy" / "index.db").exists()
    # Clearing again is a clean no-op.
    again = CliRunner().invoke(main, ["index", "clear", str(project)])
    assert again.exit_code == 0
    assert "no cache" in again.output


def test_parse_edge_spec_valid():
    assert _parse_edge_spec("pkg.a:pkg.b") == ("pkg.a", "pkg.b")


@pytest.mark.parametrize("spec", ["pkg.a", "a:", ":b", "a:b:c", "C:\\x:C:\\y"])
def test_parse_edge_spec_rejects_bad_input(spec: str):
    # Includes the documented Windows-drive-path limitation: use MCP {from,to}.
    with pytest.raises(click.BadParameter):
        _parse_edge_spec(spec)


def test_simulate_cli_predicts_cycle(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)  # pkg.a -> pkg.b
    result = CliRunner().invoke(main, ["simulate", str(project), "--add", "pkg.b:pkg.a"])
    assert result.exit_code == 0
    assert "no files written" in result.output
    assert "new cycle" in result.output
    assert "pkg.a" in result.output and "pkg.b" in result.output


def test_simulate_cli_json_is_valid(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(
        main, ["simulate", str(project), "--add", "pkg.b:pkg.a", "--format", "json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "applied" in payload and "cycles" in payload and "propagation_cost" in payload


def test_simulate_cli_empty_delta_renders(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["simulate", str(project)])
    assert result.exit_code == 0
    assert "+0 / -0 edge(s)" in result.output


def test_simulate_cli_bad_spec_exits_nonzero(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["simulate", str(project), "--add", "foo"])
    assert result.exit_code != 0


# --- scan-size guard (#216) ---------------------------------------------------


def _make_many_module_project(tmp_path: Path, n: int) -> Path:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(n):
        (pkg / f"mod{i}.py").write_text("x = 1\n")
    return tmp_path


def test_score_errors_on_oversized_scan(tmp_path: Path):
    project = _make_many_module_project(tmp_path, 5)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code != 0
    assert "max_modules" in result.output
    assert "exclude" in result.output


def test_score_max_modules_zero_disables_guard(tmp_path: Path):
    project = _make_many_module_project(tmp_path, 5)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 0\n")
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code == 0


def test_score_default_limit_does_not_trip_small_project(tmp_path: Path):
    # No archy.yaml -> the default ceiling (10k) applies and a tiny project passes.
    project = _make_many_module_project(tmp_path, 5)
    result = CliRunner().invoke(main, ["score", str(project)])
    assert result.exit_code == 0


def test_index_sync_errors_on_oversized_scan(tmp_path: Path):
    # `index sync` reparses every changed file, so it gets the same backstop.
    project = _make_many_module_project(tmp_path, 5)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    result = CliRunner().invoke(main, ["index", "sync", str(project)])
    assert result.exit_code != 0
    assert "max_modules" in result.output


def test_render_dsm_writes_a_self_contained_file(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    out = tmp_path / "out" / "dsm.html"
    result = CliRunner().invoke(main, ["render", str(project), "--out", str(out)])
    assert result.exit_code == 0
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "pkg.a" in html
    assert "<script" not in html


def test_render_defaults_to_stdout(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["render", str(project)])
    assert result.exit_code == 0
    assert result.output.startswith("<!DOCTYPE html>")


def test_render_trend_reads_history(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    CliRunner().invoke(main, ["score", str(project), "--record"])
    result = CliRunner().invoke(main, ["render", str(project), "--view", "trend"])
    assert result.exit_code == 0
    assert "<h2>overall</h2>" in result.output


def test_render_trend_without_history_still_succeeds(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["render", str(project), "--view", "trend"])
    assert result.exit_code == 0
    assert "archy score --record" in result.output


def test_render_errors_instead_of_writing_an_oversized_matrix(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    out = tmp_path / "dsm.html"
    result = CliRunner().invoke(
        main, ["render", str(project), "--max-nodes", "1", "--out", str(out)]
    )
    assert result.exit_code != 0
    assert "max_nodes" in result.output
    assert not out.exists()


def test_render_rejects_invalid_last(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["render", str(project), "--view", "trend", "--last", "0"])
    assert result.exit_code != 0
    assert "--last" in result.output
