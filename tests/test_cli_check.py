"""The `check` command: layer rules, coverage, required reach, SDP and the scan guards."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import (
    main,
)
from archy.layers import LayerConfigError, load_config, match_layer
from tests.cli_helpers import (
    _SHIPPING_LAYERS_NO_FORBID,
    _assert_one_line_overlap_error,
    _invoke_json,
    _make_absent_layers_project,
    _make_bare_pattern_project,
    _make_empty_package_project,
    _make_entrypoint_project,
    _make_external_target_project,
    _make_guarded_project,
    _make_layered_project,
    _make_many_module_project,
    _make_overlapping_layers_project,
    _make_registry_project,
    _make_sdp_violating_project,
    _make_uncovered_forbid_project,
    _snapshot_findings_only,
)


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
    project = _make_absent_layers_project(tmp_path)
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
    project = _make_absent_layers_project(tmp_path)
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


def test_check_respects_config_exclude(tmp_path: Path):
    """Excluding a directory must remove its forbidden imports from CLI check."""
    project = tmp_path / "project"
    core = project / "myapp" / "core"
    cli = project / "myapp" / "cli"
    generated = core / "generated"

    generated.mkdir(parents=True)
    cli.mkdir(parents=True)

    (project / "myapp" / "__init__.py").write_text("")
    (core / "__init__.py").write_text("")
    (generated / "__init__.py").write_text("")
    (cli / "__init__.py").write_text("")
    (core / "api.py").write_text("")
    (cli / "runner.py").write_text("")
    (generated / "bad.py").write_text("from myapp.cli.runner import go\n")

    rules = (
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    config = project / "archy.yaml"
    runner = CliRunner()

    config.write_text(rules)
    without_exclude = runner.invoke(main, ["check", str(project)])

    assert without_exclude.exit_code == 1, without_exclude.output
    assert "myapp.core.generated.bad -> myapp.cli.runner" in without_exclude.output

    config.write_text(rules + "exclude: [generated]\n")
    with_exclude = runner.invoke(main, ["check", str(project)])

    assert with_exclude.exit_code == 0, with_exclude.output
    assert "No layer violations" in with_exclude.output


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


def test_check_entrypoints_exit_code_tracks_the_worker_import(tmp_path: Path):
    """End-to-end on real files: the guard is parsed, the entrypoint derived,
    and one import in the worker moves the exit code."""
    fixed = _make_entrypoint_project(
        tmp_path / "fixed", worker_body="from app.core.database import model_registry\n"
    )
    broken = _make_entrypoint_project(tmp_path / "broken", worker_body="")

    assert CliRunner().invoke(main, ["check", str(fixed)]).exit_code == 0
    result = CliRunner().invoke(main, ["check", str(broken)])
    assert result.exit_code == 1
    assert "@entrypoints within app.** must reach app.core.database.model_registry:" in (
        result.output
    )
    assert "app.worker does not transitively reach" in result.output
    assert 'It is an entrypoint: top-level `if __name__ == "__main__":` guard.' in result.output
    assert "tests.test_worker" not in result.output


def test_check_of_a_subdirectory_still_sees_the_root_console_scripts(tmp_path: Path):
    """`archy check src` and `archy check .` must agree: the console script is
    declared at the repository root, above the scanned directory."""
    (tmp_path / ".git").mkdir()
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "registry.py").write_text("")
    (pkg / "cli.py").write_text("def main():\n    pass\n")
    (tmp_path / "pyproject.toml").write_text('[project.scripts]\npkg-cli = "pkg.cli:main"\n')
    (tmp_path / "archy.yaml").write_text(
        "required:\n  - source: '@entrypoints'\n    must_reach: pkg.registry\n"
    )

    for scanned in (tmp_path, tmp_path / "src"):
        result = CliRunner().invoke(main, ["check", str(scanned)])
        assert result.exit_code == 1, scanned
        assert "pkg.cli does not transitively reach" in result.output


def test_check_json_carries_the_entrypoints_rule_scope(tmp_path: Path):
    project = _make_entrypoint_project(tmp_path, worker_body="")
    result = CliRunner().invoke(main, ["check", str(project), "--format", "json"])
    assert result.exit_code == 1
    [violation] = json.loads(result.output)["required_violations"]
    assert violation["module"] == "app.worker"
    assert violation["rule"] == {
        "source": "@entrypoints",
        "within": "app.**",
        "must_reach": "app.core.database.model_registry",
        "reason": "entrypoints need the mapper registry",
    }
    assert "It is an entrypoint" in violation["detail"]


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


def test_check_errors_on_oversized_scan(tmp_path: Path):
    # `check` loads its config itself (possibly from --config) rather than
    # through `graph_kwargs`, so the guard reaches it by a separate path.
    project = _make_many_module_project(tmp_path, 5)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    result = CliRunner().invoke(main, ["check", str(project)])
    assert result.exit_code != 0
    assert "max_modules" in result.output


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


def test_check_names_the_flag_that_can_settle_what_it_cannot_verify(tmp_path: Path):
    """Measured, not guessed: over a five-hour agent run `archy check` was
    invoked twenty times and `archy contracts` zero times, while the model named
    `contracts` thirty-one times in its own reasoning. Awareness was not the
    deficit, so the handoff has to come from the command already being run."""
    root = _make_uncovered_forbid_project(tmp_path)
    result = CliRunner().invoke(main, ["check", str(root)])
    assert "forbid rule" in result.output
    assert "--contracts" in result.output


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
    (root / "archy.yaml").write_text(_SHIPPING_LAYERS_NO_FORBID)

    payload = _invoke_json(["check", str(root), "--format", "json"])

    assert payload["transitive_checked"] is False
    assert payload["transitive_unverified_reason"] is None


def test_findings_only_baseline_drops_required_reach_detail(tmp_path: Path):
    """`detail` is prose that can embed an absolute path; rows are keyed
    without it, so the committed file must not carry it."""
    project = _make_registry_project(tmp_path, bootstrap="")
    assert _snapshot_findings_only(project).exit_code == 0

    rows = json.loads((project / "base.json").read_text())["required_violations"]

    assert rows, "fixture must carry a required-reach violation"
    assert all("detail" not in row for row in rows)


def test_check_reports_a_match_time_layer_error_without_a_traceback(tmp_path: Path) -> None:
    project = _make_overlapping_layers_project(tmp_path, with_edge=False)
    config = load_config(project / "archy.yaml")
    with pytest.raises(LayerConfigError, match="matches multiple layers"):
        match_layer("pkg.core.x", config.layers)

    result = CliRunner().invoke(main, ["check", str(project)])

    _assert_one_line_overlap_error(result)


def test_snapshot_reports_a_match_time_layer_error_without_a_traceback(tmp_path: Path) -> None:
    project = _make_overlapping_layers_project(tmp_path, with_edge=True)
    config = load_config(project / "archy.yaml")
    with pytest.raises(LayerConfigError, match="matches multiple layers"):
        match_layer("pkg.core.x", config.layers)
    out = tmp_path / "snap.json"

    result = CliRunner().invoke(main, ["snapshot", str(project), "--out", str(out)])

    _assert_one_line_overlap_error(result)
    assert not out.exists()


def test_simulate_reports_a_match_time_layer_error_without_a_traceback(tmp_path: Path) -> None:
    project = _make_overlapping_layers_project(tmp_path, with_edge=True)
    config = load_config(project / "archy.yaml")
    with pytest.raises(LayerConfigError, match="matches multiple layers"):
        match_layer("pkg.core.x", config.layers)

    result = CliRunner().invoke(main, ["simulate", str(project), "--add", "pkg.core.x:pkg.a"])

    _assert_one_line_overlap_error(result)


def test_cycles_type_checking_back_import_still_fails_by_default(tmp_path: Path):
    """The flag is opt-in: with it absent the guarded cycle is still a cycle (#520)."""
    project = _make_guarded_project(tmp_path)
    result = CliRunner().invoke(main, ["cycles", str(project), "--strict"])
    assert result.exit_code == 1
    assert "1 of 2 edge(s) exist only under `if TYPE_CHECKING:`" in result.output
    assert "--exclude-type-checking" in result.output


def test_cycles_exclude_type_checking_passes_and_reports_the_excluded_edge(tmp_path: Path):
    project = _make_guarded_project(tmp_path)
    args = ["cycles", str(project), "--strict", "--exclude-type-checking"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    assert "No cycles found" in result.output
    assert "1 TYPE_CHECKING-only edge(s) excluded" in result.output


def test_cycles_default_json_stays_a_list_and_carries_the_per_cycle_count(tmp_path: Path):
    project = _make_guarded_project(tmp_path)
    payload = json.loads(
        CliRunner().invoke(main, ["cycles", str(project), "--format", "json"]).output
    )
    assert isinstance(payload, list)
    assert [c["type_checking_edges"] for c in payload] == [1]


def test_cycles_exclude_type_checking_accepts_the_qualified_spelling(tmp_path: Path):
    project = _make_guarded_project(
        tmp_path, "import typing\nif typing.TYPE_CHECKING:\n    from pkg import a\n"
    )
    args = ["cycles", str(project), "--strict", "--exclude-type-checking"]
    assert CliRunner().invoke(main, args).exit_code == 0


@pytest.mark.parametrize(
    "b_source",
    [
        # The `else:` branch runs at runtime, so it is a plain import.
        "if TYPE_CHECKING:\n    pass\nelse:\n    from pkg import a\n",
        # One unguarded import makes the edge a runtime dependency (mixed edge).
        "from pkg import a\nif TYPE_CHECKING:\n    from pkg import a as a2\n",
        # A function-local import runs on call.
        "if TYPE_CHECKING:\n    def f():\n        from pkg import a\n",
    ],
)
def test_cycles_exclude_type_checking_keeps_runtime_cycles(tmp_path: Path, b_source: str):
    project = _make_guarded_project(tmp_path, b_source)
    args = ["cycles", str(project), "--strict", "--exclude-type-checking"]
    assert CliRunner().invoke(main, args).exit_code == 1


def test_cycles_archy_yaml_key_rejects_a_non_boolean(tmp_path: Path):
    project = _make_guarded_project(tmp_path)
    (project / "archy.yaml").write_text("exclude_type_checking_imports: yes please\n")
    result = CliRunner().invoke(main, ["cycles", str(project)])
    assert result.exit_code != 0
    assert "exclude_type_checking_imports" in result.output
    assert "Traceback" not in result.output
