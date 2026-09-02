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


def test_check_fails_when_declared_layers_are_absent(tmp_path: Path):
    """The Constraint Decay paper's other half: forbidding edges between layers
    says nothing about whether the layers exist, and a single-module solution
    satisfies every forbid rule by having no cross-layer edges at all."""
    project = tmp_path / "proj"
    (project / "app").mkdir(parents=True)
    (project / "app" / "__init__.py").write_text("")
    (project / "app" / "everything.py").write_text("x = 1\n")
    (project / "archy.yaml").write_text(
        "min_layers_present: 2\n"
        "layers:\n"
        "  routes:\n    modules: ['app.routes.**']\n"
        "  services:\n    modules: ['app.services.**']\n"
        "forbid:\n  - {from: services, to: routes}\n"
    )
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 1
    assert "No layer violations" in result.output  # no forbidden edge exists
    assert "layers present: 0 of 2" in result.output
    assert "min_layers_present is 2" in result.output


def test_check_json_explains_a_presence_failure(tmp_path: Path):
    """Exit 1 with an empty `violations` list is indistinguishable from a bug.

    A JSON consumer has no text output to fall back on, so the payload has to
    say why the gate failed.
    """
    project = tmp_path / "proj"
    (project / "app").mkdir(parents=True)
    (project / "app" / "__init__.py").write_text("")
    (project / "app" / "everything.py").write_text("x = 1\n")
    (project / "archy.yaml").write_text(
        "min_layers_present: 2\n"
        "layers:\n"
        "  routes:\n    modules: ['app.routes.**']\n"
        "  services:\n    modules: ['app.services.**']\n"
        "forbid:\n  - {from: services, to: routes}\n"
    )
    result = CliRunner().invoke(main, ["check", str(project), "--format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["violations"] == []
    assert payload["presence_fails"] is True
    assert payload["min_layers_present"] == 2
    assert payload["coverage"]["layers_present"] == 0
    assert sorted(payload["coverage"]["empty_layers"]) == ["routes", "services"]


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


def _make_registry_project(tmp_path: Path, *, bootstrap: str) -> Path:
    """The reported incident, reduced: `commands/` modules run standalone, and
    each needs `core.database.model_registry` imported before the ORM configures
    its mappers. `bootstrap` is what `commands/__init__.py` contains.
    """
    app = tmp_path / "app"
    (app / "core" / "database").mkdir(parents=True)
    (app / "commands").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "core" / "__init__.py").write_text("")
    (app / "core" / "database" / "__init__.py").write_text("")
    (app / "core" / "database" / "model_registry.py").write_text("REGISTRY = {}\n")
    (app / "commands" / "__init__.py").write_text(bootstrap)
    (app / "commands" / "setup_user.py").write_text("x = 1\n")
    (app / "commands" / "backfill.py").write_text("x = 2\n")
    (tmp_path / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: 'app.commands.*'\n"
        "    must_reach: app.core.database.model_registry\n"
        "    reason: standalone entrypoints need the full mapper registry\n"
    )
    return tmp_path


def test_check_required_reach_satisfied_through_the_package_init(tmp_path: Path):
    """One import in `commands/__init__.py` covers every command module.

    The whole point of transitive reach: this is the correct fix, and a
    direct-import rule would report both command modules as violations here.

    The exit-0 half of a negative-control pair; `..._exit_code_tracks_the_one_import`
    below removes that single line and requires exit 1. An exit 0 asserted on its
    own cannot distinguish a satisfied rule from a rule that never fired.
    """
    project = _make_registry_project(
        tmp_path, bootstrap="from app.core.database import model_registry\n"
    )
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 0
    assert "No required-reach violations (1 rule(s) checked, transitively)" in result.output


def test_check_required_reach_exit_code_tracks_the_one_import(tmp_path: Path):
    """End-to-end negative control, on real files rather than a built graph.

    Same project twice; the only difference is one line in `commands/__init__.py`.
    If the exit code does not move, the gate is decorative.
    """
    bootstrap = "from app.core.database import model_registry\n"
    with_import = _make_registry_project(tmp_path / "with_import", bootstrap=bootstrap)
    without = _make_registry_project(tmp_path / "without", bootstrap="")

    assert CliRunner().invoke(main, ["check", str(with_import)]).exit_code == 0
    assert CliRunner().invoke(main, ["check", str(without)]).exit_code == 1


def test_check_required_reach_fails_and_names_the_modules(tmp_path: Path):
    project = _make_registry_project(tmp_path, bootstrap="")
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 1
    assert "2 required-reach violation(s)" in result.output
    assert "app.commands.* must reach app.core.database.model_registry" in result.output
    assert "reason: standalone entrypoints need the full mapper registry" in result.output
    assert "app.commands.backfill does not transitively reach" in result.output
    assert "app.commands.setup_user does not transitively reach" in result.output


def test_check_json_explains_a_required_reach_failure(tmp_path: Path):
    """A JSON consumer has no text to fall back on, so the payload says why."""
    project = _make_registry_project(tmp_path, bootstrap="")
    result = CliRunner().invoke(main, ["check", str(project), "--format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["violations"] == []  # no forbidden edge exists; the gate failed anyway
    modules = [v["module"] for v in payload["required_violations"]]
    assert modules == ["app.commands.backfill", "app.commands.setup_user"]
    first = payload["required_violations"][0]
    assert first["rule"]["must_reach"] == "app.core.database.model_registry"
    assert first["rule"]["reason"] == "standalone entrypoints need the full mapper registry"
    assert "does not transitively reach" in first["detail"]


def test_check_json_carries_required_violations_when_clean(tmp_path: Path):
    project = _make_registry_project(
        tmp_path, bootstrap="from app.core.database import model_registry\n"
    )
    result = CliRunner().invoke(main, ["check", str(project), "--format", "json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["required_violations"] == []


def test_check_stays_silent_about_required_reach_when_none_declared(tmp_path: Path):
    """No `required:` in the config means no line about it, and no exit-code change."""
    project = _make_layered_project(tmp_path, with_violation=False)
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 0
    assert "required-reach" not in result.output


def test_check_fails_on_a_required_rule_that_cannot_fire(tmp_path: Path):
    """A typo'd pattern must not read as "every module satisfies it"."""
    project = _make_registry_project(tmp_path, bootstrap="")
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: 'app.commands.*'\n"
        "    must_reach: app.core.database.registry\n"  # no such module
    )
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 1
    assert "nothing matches the `must_reach` pattern" in result.output
    assert "either the pattern is wrong" in result.output


def _make_external_target_project(tmp_path: Path, *, bootstrap: str) -> Path:
    """A `required:` rule whose `must_reach` is an EXTERNAL package."""
    app = tmp_path / "app"
    (app / "commands").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "commands" / "__init__.py").write_text(bootstrap)
    (app / "commands" / "setup_user.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n  - source: 'app.commands.*'\n    must_reach: sqlalchemy\n"
    )
    return tmp_path


def test_snapshot_agrees_with_check_on_an_external_target(tmp_path: Path):
    """`check` and `snapshot` must not disagree about the same tree.

    They did: `check` keeps external nodes, but the snapshot path stripped them
    before the reach check ran, so `must_reach: sqlalchemy` reported as a dead
    rule ("cannot fire") while `check` reported it satisfied.
    """
    project = _make_external_target_project(tmp_path, bootstrap="import sqlalchemy\n")

    assert CliRunner().invoke(main, ["check", str(project)]).exit_code == 0
    assert CliRunner().invoke(main, ["snapshot", str(project)]).exit_code == 0

    baseline = json.loads((project / ".archy" / "baseline.json").read_text())
    assert baseline["required_violations"] == []


def test_diff_catches_a_removed_external_bootstrap_import(tmp_path: Path):
    """The masking this caused was worse than the wrong verdict.

    A false "dead rule" landed on BOTH sides of the diff, so deleting the
    bootstrap import could never surface as a regression -- the exact ratchet
    the feature exists to provide, silently disabled for external targets.
    """
    project = _make_external_target_project(tmp_path, bootstrap="import sqlalchemy\n")
    assert CliRunner().invoke(main, ["snapshot", str(project)]).exit_code == 0

    (project / "app" / "commands" / "__init__.py").write_text("")
    result = CliRunner().invoke(main, ["diff", str(project)])

    assert "required-reach: +1 added" in result.output
    # For an external target, "nothing matches it" IS the failure: the import
    # that satisfied the rule is gone, so the node left the graph with it.
    assert "no module imports it any more" in result.output


def test_simulate_lists_required_reach_violations_outside_the_ranked_summary(tmp_path: Path):
    """`simulate` has no --top-n, so a reach item must not depend on the top-5."""
    project = _make_registry_project(
        tmp_path, bootstrap="from app.core.database import model_registry\n"
    )
    result = CliRunner().invoke(
        main,
        [
            "simulate",
            str(project),
            "--remove",
            "app.commands:app.core.database.model_registry",
        ],
    )

    assert result.exit_code == 0
    assert "# new required-reach violations (+2):" in result.output
    assert "app.commands.setup_user does not transitively reach" in result.output


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


def _make_app_with_core_module(tmp_path: Path) -> Path:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")
    return pkg


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
    _make_app_with_core_module(tmp_path)

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
    pkg = _make_app_with_core_module(tmp_path)
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


def _make_conventions_project(tmp_path: Path) -> Path:
    """A project with one of each thing `archy conventions` reports."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "layers.py").write_text(
        "class Violation: pass\nclass ReachViolation: pass\nclass SdpViolation: pass\n"
    )
    (pkg / "cli.py").write_text(
        "import sys\n"
        "def _report_to_text(): pass\n"
        "def _report_to_json(): pass\n"
        "def run(strict):\n"
        "    if strict:\n"
        "        sys.exit(1)\n"
    )
    return tmp_path


def test_conventions_text_reports_all_four_sections(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project)])
    assert result.exit_code == 0
    for heading in ("## naming", "## surfaces", "## gates", "## models"):
        assert heading in result.output
    assert "*Violation(3)" in result.output
    assert "pkg.layers" in result.output
    assert "param:strict" in result.output
    # The gate/error split is the headline: a finding-failure exit and a
    # bad-input exit must never share a count.
    assert "## errors" in result.output
    assert "1 finding-failure exit(s)" in result.output


def test_conventions_always_exits_zero_even_with_gates_present(tmp_path: Path):
    # Advisory, not a gate: only `check`, `contracts` and the `--strict`
    # variants fail a build. Reporting that a project HAS gates must not
    # itself become one.
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project)])
    assert result.exit_code == 0


def test_conventions_json_matches_the_text_surface(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["totals"]["naming"] >= 1
    home = next(h for h in payload["naming"] if h["module"] == "pkg.layers")
    family = next(f for f in home["families"] if f["suffix"] == "Violation")
    assert family["home_module"] == "pkg.layers"
    # Derived values must survive `model_dump()` -- a plain property would be
    # silently absent here and on the MCP wire.
    assert "concentration" in family
    assert {"total", "family_count"} <= set(home)
    assert "gate_modules" in payload
    # `run` guards its exit on `strict`, a plain parameter, not a Click flag,
    # so it is a finding-failure gate; nothing here rejects bad user input.
    assert [g["function"] for g in payload["gates"]] == ["run"]
    assert payload["gates"][0]["code"] == 1
    assert payload["errors"] == []
    assert payload["models"]["dominant_base"] is None


def test_conventions_top_truncates_but_reports_the_total(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(
        main, ["conventions", str(project), "--top", "1", "--format", "json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload["naming"]) == 1
    assert payload["totals"]["naming"] >= len(payload["naming"])


def test_conventions_rejects_a_nonsense_min_family(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project), "--min-family", "1"])
    assert result.exit_code != 0
    assert "min-family" in result.output


def test_conventions_rejects_a_nonsense_top(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project), "--top", "0"])
    assert result.exit_code != 0


def test_conventions_default_output_surfaces_a_small_but_located_family(tmp_path: Path):
    # Acceptance criterion for the by-home-module shape: a 2-member family in
    # its own module must be visible with NO flags, even alongside a family
    # six times its size. Ranked flat by count it would fall below the fold.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mcp.py").write_text("".join(f"class P{i}Payload: pass\n" for i in range(13)))
    (pkg / "layers.py").write_text("class Violation: pass\nclass ReachViolation: pass\n")
    result = CliRunner().invoke(main, ["conventions", str(tmp_path)])
    assert result.exit_code == 0
    assert "pkg.layers" in result.output
    assert "*Violation(2)" in result.output


def test_conventions_separates_gates_from_user_error_exits(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text(
        "import click\n"
        "import sys\n"
        "def gate(bad):\n"
        "    if bad:\n"
        "        sys.exit(1)\n"
        "def oops(path):\n"
        "    if not path:\n"
        '        raise click.ClickException("nope")\n'
    )
    result = CliRunner().invoke(main, ["conventions", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 finding-failure exit(s)" in result.output
    assert "1 user-error exit(s)" in result.output
    assert "exit code(s): 1" in result.output
    assert "ClickException=1" in result.output


def _make_bare_pattern_project(tmp_path: Path) -> Path:
    """The walkthrough shape: bare `modules:` qualnames over a nested tree."""
    app = tmp_path / "app"
    (app / "store").mkdir(parents=True)
    (app / "api").mkdir()
    (app / "__init__.py").write_text("")
    (app / "store" / "__init__.py").write_text("")
    (app / "api" / "__init__.py").write_text("")
    (app / "api" / "context.py").write_text("")
    (app / "store" / "repository.py").write_text("from app.api.context import ctx\n")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  store:\n    modules: ['app.store']\n"
        "  api:\n    modules: ['app.api']\n"
        "forbid:\n  - {from: store, to: api}\n"
    )
    return tmp_path


def test_check_verdict_names_degenerate_coverage(tmp_path: Path):
    """A clean verdict must say nothing was LOOKED AT, not just nothing found."""
    project = _make_bare_pattern_project(tmp_path)
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code == 0
    headline = result.output.splitlines()[0]
    # The substring the walkthrough and the other CLI tests assert on survives.
    assert "No layer violations" in headline
    assert "governs 0 of 1 internal edges (0%)" in headline


def test_check_verdict_hints_at_the_bare_pattern(tmp_path: Path):
    project = _make_bare_pattern_project(tmp_path)
    result = CliRunner().invoke(main, ["check", str(project)])
    assert "layer 'store' matches app.store exactly" in result.output
    assert "app.store.repository" in result.output
    assert 'Did you mean "app.store.**"?' in result.output


def test_check_json_carries_hints_and_the_degenerate_flag(tmp_path: Path):
    project = _make_bare_pattern_project(tmp_path)
    result = CliRunner().invoke(main, ["check", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["coverage"]["governs_no_edges"] is True
    hints = {h["layer"]: h for h in payload["coverage"]["exact_pattern_hints"]}
    assert hints["store"]["suggestion"] == "app.store.**"
    assert hints["store"]["unlayered_descendants"] == ["app.store.repository"]


def test_check_verdict_unqualified_when_coverage_is_real(tmp_path: Path):
    """The clause must not fire on a config that governs its edges."""
    project = _make_layered_project(tmp_path, with_violation=False)
    result = CliRunner().invoke(main, ["check", str(project)])
    headline = result.output.splitlines()[0]
    assert headline.startswith("# No layer violations (config:")
    assert "Did you mean" not in result.output


def _invoke_json(args: list[str]) -> dict:
    """Invoke the CLI and parse the JSON document out of its output.

    The slice is load-bearing, which is why this is a helper rather than five
    copies of an idiom: this CliRunner folds stderr into `output`, so a command
    that writes an advisory there first (`# --contracts unavailable: ...`)
    prefixes the JSON with a line `json.loads` cannot parse.
    """
    out = CliRunner().invoke(main, args).output
    return json.loads(out[out.index("{") :])


def _make_uncovered_forbid_project(tmp_path: Path) -> Path:
    """A config whose layers govern a little, but not the edge that matters."""
    root = tmp_path / "proj"
    (root / "shipping" / "store").mkdir(parents=True)
    (root / "shipping" / "common").mkdir(parents=True)
    (root / "shipping" / "api").mkdir(parents=True)
    for pkg in ("", "store", "common", "api"):
        (root / "shipping" / pkg / "__init__.py").write_text("")
    (root / "shipping" / "store" / "repository.py").write_text(
        "from shipping.common.tenancy import current\n"
    )
    (root / "shipping" / "common" / "tenancy.py").write_text(
        "from shipping.api.context import ctx\n\ndef current(): return ctx\n"
    )
    (root / "shipping" / "api" / "context.py").write_text("ctx = 1\n")
    (root / "archy.yaml").write_text(
        "layers:\n"
        "  api:\n    modules: [shipping.api]\n"
        "  store:\n    modules: [shipping.store]\n"
        "forbid:\n  - {from: store, to: api}\n"
    )
    return root


def test_check_names_the_flag_that_can_settle_what_it_cannot_verify(tmp_path: Path):
    """Measured, not guessed: over a five-hour agent run `archy check` was
    invoked twenty times and `archy contracts` zero times, while the model named
    `contracts` thirty-one times in its own reasoning. Awareness was not the
    deficit, so the handoff has to come from the command already being run."""
    root = _make_uncovered_forbid_project(tmp_path)
    result = CliRunner().invoke(main, ["check", str(root)])
    assert "forbid rule" in result.output
    assert "--contracts" in result.output


def test_check_handoff_is_silent_once_contracts_was_asked_for(tmp_path: Path):
    root = _make_uncovered_forbid_project(tmp_path)
    result = CliRunner().invoke(main, ["check", str(root), "--contracts"])
    assert "evaluates them transitively" not in result.output


def test_check_handoff_is_silent_without_forbid_rules(tmp_path: Path):
    """Nothing to verify means nothing to hand off. A line printed on every run
    is one a reader learns to skip."""
    root = _make_uncovered_forbid_project(tmp_path)
    (root / "archy.yaml").write_text(
        "layers:\n  api:\n    modules: [shipping.api]\n  store:\n    modules: [shipping.store]\n"
    )
    result = CliRunner().invoke(main, ["check", str(root)])
    assert "--contracts" not in result.output


def test_check_contracts_does_not_change_the_exit_code(tmp_path: Path):
    """`--contracts` REPORTS. A flag that can turn a passing check into a failing
    one would change what a green check has always meant, and would make the
    flag unsafe to leave on in CI."""
    root = _make_uncovered_forbid_project(tmp_path)
    plain = CliRunner().invoke(main, ["check", str(root)])
    withc = CliRunner().invoke(main, ["check", str(root), "--contracts"])
    assert withc.exit_code == plain.exit_code


def test_brief_answers_the_four_questions_and_stays_small(tmp_path: Path):
    """`brief` exists because of an economic argument: reading is ~66x cheaper
    than writing on an inference box, so a briefing only pays if it is small
    enough to inject and complete enough to prevent a derivation."""
    root = _make_uncovered_forbid_project(tmp_path)
    result = CliRunner().invoke(main, ["brief", str(root)])
    assert result.exit_code == 0
    for heading in (
        "what kind of thing",
        "what must change WITH it",
        "does a new finding gate",
        "cannot see",
    ):
        assert heading in result.output
    # the co-update set and the coverage gap are the two actionable parts
    assert "layer coverage" in result.output
    assert len(result.output) < 20_000


def test_brief_is_advisory_and_never_gates(tmp_path: Path):
    root = _make_uncovered_forbid_project(tmp_path)
    assert CliRunner().invoke(main, ["brief", str(root)]).exit_code == 0


def test_check_contracts_says_why_it_has_no_verdict_on_every_surface(tmp_path: Path, monkeypatch):
    """A verdict without a reason is not actionable. When import-linter is
    absent, `contracts` missing from the JSON entirely is indistinguishable from
    a bug in archy, and a reason on stderr is carried by neither structured
    stream. The MCP surface has always reported `available`/`error` here."""
    # Local: the substitution has to land on the module object that
    # `_run_check_contracts` imports from at call time.
    import archy.contracts

    def _boom(*args, **kwargs):
        raise archy.contracts.ContractsNotAvailable("import-linter is not installed")

    monkeypatch.setattr(archy.contracts, "run_contracts", _boom)
    root = _make_uncovered_forbid_project(tmp_path)

    payload = _invoke_json(["check", str(root), "--contracts", "--format", "json"])
    assert payload["contracts"]["available"] is False
    assert "import-linter" in payload["contracts"]["error"]

    text = CliRunner().invoke(main, ["check", str(root), "--contracts"]).output
    assert "# contracts: no verdict (import-linter is not installed)" in text


def test_brief_contracts_says_why_it_has_no_verdict(tmp_path: Path, monkeypatch):
    """An unreadable contracts config is `available=True` with a reason, the way
    `mcp._run_contracts` distinguishes it from a missing dependency."""
    # Local: the substitution has to land on the module object that
    # `_run_check_contracts` imports from at call time.
    import archy.contracts

    def _boom(*args, **kwargs):
        raise archy.contracts.ContractsConfigError("no contracts config found")

    monkeypatch.setattr(archy.contracts, "run_contracts", _boom)
    root = _make_uncovered_forbid_project(tmp_path)

    payload = _invoke_json(["brief", str(root), "--contracts", "--format", "json"])
    assert payload["contracts"]["available"] is True
    assert "no contracts config" in payload["contracts"]["error"]

    text = CliRunner().invoke(main, ["brief", str(root), "--contracts"]).output
    assert "# contracts: no verdict (no contracts config found)" in text


def test_check_json_says_whether_it_looked_transitively(tmp_path: Path):
    """#343 on the CLI's machine-readable surface. The text output has qualified
    a clean verdict since v0.46; its JSON had not, so a machine reader could not
    tell "checked transitively and clean" from "never looked"."""
    root = _make_uncovered_forbid_project(tmp_path)

    payload = _invoke_json(["check", str(root), "--format", "json"])

    assert payload["violations"] == []
    assert payload["transitive_checked"] is False
    assert "forbid rule" in payload["transitive_unverified_reason"]
    assert "--contracts" in payload["transitive_unverified_reason"]


def test_check_json_reason_is_absent_without_forbid_rules(tmp_path: Path):
    root = _make_uncovered_forbid_project(tmp_path)
    (root / "archy.yaml").write_text(
        "layers:\n  api:\n    modules: [shipping.api]\n  store:\n    modules: [shipping.store]\n"
    )

    payload = _invoke_json(["check", str(root), "--format", "json"])

    assert payload["transitive_checked"] is False
    assert payload["transitive_unverified_reason"] is None


def test_check_json_keeps_a_reason_when_contracts_could_not_run(tmp_path: Path, monkeypatch):
    """The failure this PR exists to close, one level down. Requesting
    `--contracts` and getting no verdict leaves the forbid rules exactly as
    unverified as never asking, so dropping the reason there would be the same
    silent clean pass in a different disguise. It must not name `--contracts`
    back at a caller who just passed it and watched it fail."""
    import archy.contracts

    def _boom(*args, **kwargs):
        raise archy.contracts.ContractsConfigError("no contracts config found")

    monkeypatch.setattr(archy.contracts, "run_contracts", _boom)
    root = _make_uncovered_forbid_project(tmp_path)

    payload = _invoke_json(["check", str(root), "--contracts", "--format", "json"])

    assert payload["transitive_checked"] is False
    reason = payload["transitive_unverified_reason"]
    assert "no contracts config found" in reason
    assert "produced no transitive verdict" in reason
