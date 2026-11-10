"""The `snapshot`, `diff` and `simulate` commands and the strict ratchet."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import (
    _diff_to_text,
    _simulate_to_text,
    main,
)
from archy.diff import (
    CycleSetDiff,
    DiffReport,
    DiffSummary,
    ReachViolationSetDiff,
    SdpViolationSetDiff,
    ViolationSetDiff,
)
from archy.layers import LayerConfigError, ReachViolation, RequiredRule, load_config, match_layer
from archy.simulate import AppliedDelta, PropagationDelta, SimulateReport
from tests.cli_helpers import (
    _DELTA,
    _DELTA_LINES,
    _assert_one_line_overlap_error,
    _diff_json,
    _make_acyclic_project,
    _make_external_target_project,
    _make_layered_project,
    _make_overlapping_layers_project,
    _make_ratchet_project,
    _make_sdp_ratchet_project,
    _make_two_cycle_project,
    _snapshot_findings_only,
    _strict,
)


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


def test_diff_default_top_lists_more_than_one_regression(tmp_path: Path):
    """Pin the fixture the two rejection tests below rely on.

    `--top 0` producing an empty list only proves the guard works if this
    project really has regressions to hide.
    """
    project = _make_two_cycle_project(tmp_path)
    result = _diff_json(project)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["summary"]["top_regressions"]) >= 2


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_diff_top_rejects_invalid_values(tmp_path: Path, bad: str):
    # --top was unvalidated on diff, and `diff_summary.py` slices items[:top_n]:
    # 0 dropped the "top regressions" section entirely and -1 sliced off the
    # lowest-risk regression, both while exiting 0. A reader sees no
    # regressions and commits. The guard runs before the baseline is read, so
    # it fires even when the baseline path is also wrong.
    project = _make_two_cycle_project(tmp_path)
    result = _diff_json(project, "--top", bad)
    assert result.exit_code != 0
    assert f"--top must be >= 1; got {bad}" in result.output


def test_simulate_cli_predicts_cycle(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
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


def test_simulate_cli_empty_delta_is_refused(tmp_path: Path):
    """No --add/--remove must UsageError instead of coverage complete (#661)."""
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["simulate", str(project)])
    assert result.exit_code == 2
    assert "pass at least one --add or --remove" in result.output


def test_simulate_cli_with_nothing_resolved_does_not_print_a_clean_zero(tmp_path: Path):
    """`simulate` is asked "should I add this import?". A typo'd endpoint used to
    answer `overall +0.000` for an edge that was never applied (#627)."""
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["simulate", str(project), "--add", "pkg.a:pkg.bb"])
    assert result.exit_code == 0
    assert result.output.splitlines() == [
        "# simulation (no files written): +0 / -0 edge(s)",
        "  ! unresolved endpoint: pkg.bb  (did you mean pkg.b?)",
        "",
        "# summary: nothing simulated: no edge resolved, so no score, cycle or "
        "violation delta was computed",
    ]


def test_simulate_cli_partial_run_says_so_before_the_numbers(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    args = ["simulate", str(project), "--add", "pkg.b:pkg.a", "--add", "pkg.a:pkg.bb"]
    result = CliRunner().invoke(main, args)
    lines = result.output.splitlines()
    partial = next(i for i, line in enumerate(lines) if line.startswith("# PARTIAL"))
    summary = next(i for i, line in enumerate(lines) if line.startswith("# summary"))
    assert "add pkg.a -> pkg.bb" in lines[partial]
    assert partial < summary  # the caveat comes first
    assert "new cycle" in result.output  # and the resolved edge was still simulated


def test_simulate_cli_json_carries_the_verdict_and_the_hint(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    args = ["simulate", str(project), "--add", "pkg.a:pkg.bb", "--format", "json"]
    applied = json.loads(CliRunner().invoke(main, args).output)["applied"]
    assert applied["coverage"] == "none"
    assert applied["skipped"] == ["add pkg.a -> pkg.bb"]
    assert applied["unresolved_hints"] == [{"ref": "pkg.bb", "did_you_mean": ["pkg.b"]}]


def test_simulate_cli_bad_spec_exits_nonzero(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["simulate", str(project), "--add", "foo"])
    assert result.exit_code != 0


def test_diff_text_lists_every_score_axis_in_order():
    report = DiffReport(score_delta=_DELTA, cycles=CycleSetDiff(), violations=ViolationSetDiff())
    assert _diff_to_text(report).splitlines() == [
        "# score deltas (current - baseline):",
        *_DELTA_LINES,
        "",
        "# cycles: +0 added, -0 resolved",
        "",
        "# violations: +0 added, -0 resolved",
    ]


def test_diff_text_names_a_revived_entrypoints_rule_with_its_scope():
    """A dead rule coming back has no module, so the line names the rule, with
    `within`, or two scoped rules read alike. It is not "now reaches": nothing
    started reaching anything; the rule became checkable."""
    rule = RequiredRule(source="@entrypoints", must_reach="core.registry", within="app.**")
    report = DiffReport(
        score_delta=_DELTA,
        cycles=CycleSetDiff(),
        violations=ViolationSetDiff(),
        required_violations=ReachViolationSetDiff(
            resolved=(ReachViolation(rule=rule, module=None, detail="dead"),)
        ),
    )

    assert "  - rule can be checked again: @entrypoints within app.** must reach core.registry" in (
        _diff_to_text(report).splitlines()
    )


def test_simulate_text_lists_every_score_axis_then_blast_radius():
    report = SimulateReport(
        applied=AppliedDelta(
            added_edges=(),
            removed_edges=(),
            unresolved=(),
            no_op_adds=(),
            no_op_removes=(),
            rejected=(),
        ),
        score_delta=_DELTA,
        cycles=CycleSetDiff(),
        violations=ViolationSetDiff(),
        sdp_violations=SdpViolationSetDiff(),
        new_back_edges=(),
        propagation_cost=PropagationDelta(before=0.1, after=0.25, delta=0.15),
        summary=DiffSummary(headline="no change", top_regressions=(), top_improvements=()),
    )
    lines = _simulate_to_text(report).splitlines()
    start = lines.index("# would-be score deltas:")
    # "blast radius" is 12 characters, so the 11-wide pad adds nothing.
    assert lines[start + 1 : start + 8] == [
        *_DELTA_LINES,
        "  blast radius 0.100 -> 0.250 (+0.150)",
    ]


def test_diff_strict_passes_when_a_baseline_cycle_is_split(tmp_path: Path):
    """Acceptance: the 3-cycle becomes a 2-cycle plus an acyclic node. Paydown
    must not trip the ratchet."""
    project = _make_ratchet_project(tmp_path)
    (project / "pkg" / "b.py").write_text("import pkg.a\n")
    (project / "pkg" / "c.py").write_text("")

    result = _strict(project)

    assert result.exit_code == 0, result.output
    assert "# strict: PASS" in result.output


def test_diff_strict_fails_on_a_fresh_cycle_and_names_its_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = _make_ratchet_project(tmp_path)
    # Locations are relative to the working directory, so they paste into
    # `git blame` from where the command ran (#521).
    monkeypatch.chdir(project)
    (project / "pkg" / "d.py").write_text("x = 1\nimport pkg.e\n")
    (project / "pkg" / "e.py").write_text("import pkg.d\n")

    result = _strict(project)

    assert result.exit_code == 1, result.output
    assert "# strict: FAIL" in result.output
    assert f"pkg.d -> pkg.e at {Path('pkg', 'd.py')}:2" in result.output
    assert "1 cycle(s) and 0 violation(s) carried" in result.output


def test_diff_strict_json_carries_the_verdict(tmp_path: Path):
    project = _make_ratchet_project(tmp_path)
    (project / "pkg" / "d.py").write_text("import pkg.e\n")
    (project / "pkg" / "e.py").write_text("import pkg.d\n")

    result = _strict(project, "--format", "json")

    assert result.exit_code == 1
    strict = json.loads(result.output)["strict"]
    assert strict["passed"] is False
    assert strict["findings"][0]["modules"] == ["pkg.d", "pkg.e"]
    assert "new import cycle" in strict["reason"]


def test_diff_strict_fails_on_a_new_layer_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project = _make_layered_project(tmp_path, with_violation=False)
    # Locations are relative to the working directory, so they paste into
    # `git blame` from where the command ran (#521).
    monkeypatch.chdir(project)
    snapshot = _snapshot_findings_only(project)
    assert snapshot.exit_code == 0, snapshot.output
    (project / "myapp" / "core" / "api.py").write_text("from myapp.cli.runner import go\n")

    result = _strict(project)

    assert result.exit_code == 1, result.output
    location = f"{Path('myapp', 'core', 'api.py')}:1"
    assert (
        f"new layer violation (core -> cli): myapp.core.api -> myapp.cli.runner at {location}"
        in result.output
    )


def test_diff_strict_without_a_baseline_is_a_usage_error(tmp_path: Path):
    """`.archy/baseline.json` is what every re-snapshot overwrites, so strict
    must not fall back to it."""
    project = _make_ratchet_project(tmp_path)
    CliRunner().invoke(main, ["snapshot", str(project)])

    result = CliRunner().invoke(main, ["diff", str(project), "--strict"])

    assert result.exit_code == 2
    assert "--strict needs --baseline" in result.output


def test_findings_only_snapshot_is_stable_across_unrelated_edits(tmp_path: Path):
    """Committed and regenerated by hand, so an edit that only moves imports
    down a line must not change a byte. The fixture's edit does move lines."""
    project = _make_ratchet_project(tmp_path)
    before = (project / "base.json").read_text()
    payload = json.loads(before)
    assert "score" not in payload
    assert "lines" not in json.dumps(payload)
    assert before.endswith("\n")

    (project / "pkg" / "a.py").write_text('"""Docstring."""\n\nimport pkg.b\n')
    assert _snapshot_findings_only(project).exit_code == 0

    assert (project / "base.json").read_text() == before


def test_diff_strict_refuses_the_file_snapshot_overwrites_even_when_named(tmp_path: Path):
    """Naming `.archy/baseline.json` explicitly would let the next `archy
    snapshot` grandfather a new cycle, so strict refuses it outright."""
    project = _make_ratchet_project(tmp_path)
    CliRunner().invoke(main, ["snapshot", str(project)])
    default = project / ".archy" / "baseline.json"
    assert default.exists()

    result = CliRunner().invoke(
        main, ["diff", str(project), "--strict", "--baseline", str(default)]
    )

    assert result.exit_code == 2
    assert "the file `archy snapshot` overwrites" in result.output


def test_diff_with_a_missing_committed_baseline_says_how_to_create_it(tmp_path: Path):
    """`archy snapshot` alone writes .archy/baseline.json, which would not fix
    a missing archy-baseline.json; the hint has to name the right command."""
    project = _make_ratchet_project(tmp_path)
    missing = project / "archy-baseline.json"

    result = CliRunner().invoke(
        main, ["diff", str(project), "--strict", "--baseline", str(missing)]
    )

    assert result.exit_code == 1
    assert f"archy snapshot {project} --findings-only --out {missing}" in result.output


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        ("not json\n", "invalid JSON (Expecting value: line 1 column 1 (char 0))"),
        ("{}\n", "missing snapshot fields"),
        ("[]\n", "expected a JSON object, got list"),
        ('{"score": {}}\n', "missing 'components'"),
        (
            '{"score": {"overall": 1, "components": {"modularity": 1, "acyclicity": 1, '
            '"depth": 1, "equality": 1, "complexity": 1}, "inputs": {}}}\n',
            "score inputs do not match a snapshot",
        ),
        (b"\xff", "not valid UTF-8 (invalid start byte)"),
    ],
)
def test_diff_malformed_baseline_is_a_clean_error(
    tmp_path: Path, contents: str | bytes, reason: str
):
    project = tmp_path / "proj"
    project.mkdir()
    baseline = project / "bad.json"
    if isinstance(contents, bytes):
        baseline.write_bytes(contents)
    else:
        baseline.write_text(contents)

    # The real entry point, not CliRunner. CliRunner swallows an uncaught
    # exception into Result.exception and leaves output empty, so "Traceback"
    # would stay absent and the test would miss the bug it exists to catch.
    proc = subprocess.run(
        [
            str(Path(sys.executable).parent / "archy"),
            "diff",
            str(project),
            "--baseline",
            str(baseline),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr

    message = f"{baseline} is not a readable archy snapshot; re-run `archy snapshot`: {reason}"
    assert proc.returncode == 1
    assert "Traceback" not in output
    assert str(baseline) in output
    assert output == f"Error: {message}\n"


def test_findings_only_snapshot_is_stable_when_sdp_ratios_move(tmp_path: Path):
    """With SDP enabled, an unrelated new importer shifts instability ratios
    without changing any finding; the committed file must not change. The
    fixture really has an SDP violation whose ratio moves."""
    project = _make_sdp_ratchet_project(tmp_path)
    assert _snapshot_findings_only(project).exit_code == 0
    before = (project / "base.json").read_text()

    # An existing module gains an importer: ratios move, the module list and
    # every finding stay the same.
    (project / "pkg" / "__init__.py").write_text("import pkg.e\n")
    assert _snapshot_findings_only(project).exit_code == 0

    assert (project / "base.json").read_text() == before


def test_diff_against_a_findings_only_baseline_does_not_invent_sdp_regressions(tmp_path: Path):
    """The committed form records no SDP violations, so comparing them would
    call every current one new on an unchanged tree. The fixture has some."""
    project = _make_sdp_ratchet_project(tmp_path)
    assert _snapshot_findings_only(project).exit_code == 0

    result = _strict(project, "--format", "json")
    text = _strict(project).output

    payload = json.loads(result.output)
    assert payload["sdp_violations"] == {"added": [], "resolved": []}
    assert payload["baseline_findings_only"] is True
    assert payload["summary"]["top_regressions"] == []
    assert "# sdp: not compared" in text


def test_diff_strict_refuses_when_the_layer_rules_disappeared(tmp_path: Path):
    """The baseline recorded a violation; with archy.yaml gone no rule runs,
    and a pass would be a rule set that cannot fire reading as clean."""
    project = _make_layered_project(tmp_path, with_violation=True)
    assert _snapshot_findings_only(project).exit_code == 0
    assert json.loads((project / "base.json").read_text())["violations"]
    (project / "archy.yaml").unlink()

    result = _strict(project)

    assert result.exit_code == 1
    assert "no archy.yaml was found" in result.output


def test_diff_strict_refuses_a_hard_link_to_the_snapshot_file(tmp_path: Path):
    """A hard link keeps tracking every re-snapshot, so it is the same file."""
    project = _make_ratchet_project(tmp_path)
    CliRunner().invoke(main, ["snapshot", str(project)])
    link = project / "linked.json"
    link.hardlink_to(project / ".archy" / "baseline.json")

    result = CliRunner().invoke(main, ["diff", str(project), "--strict", "--baseline", str(link)])

    assert result.exit_code == 2
    assert "the file `archy snapshot` overwrites" in result.output


def test_diff_strict_refuses_when_layer_patterns_went_dead(tmp_path: Path):
    """archy.yaml still exists, but the layer named by the baseline violation
    now matches nothing; a new violation would otherwise pass unseen."""
    project = _make_layered_project(tmp_path, with_violation=True)
    assert _snapshot_findings_only(project).exit_code == 0
    (project / "archy.yaml").write_text(
        "layers:\n  core: {modules: [myapp.core]}\n  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n  - {from: core, to: cli}\n"
    )

    result = _strict(project)

    assert result.exit_code == 1
    assert "no longer cover 1 baseline violation(s)" in result.output
    assert f"archy snapshot {project} --findings-only --out" in result.output


def test_diff_strict_refusal_without_a_module_list_names_the_scan_path(tmp_path: Path):
    """Run from elsewhere, a path-less `archy snapshot` scans the wrong tree."""
    project = _make_ratchet_project(tmp_path)
    payload = json.loads((project / "base.json").read_text())
    del payload["modules"]
    (project / "base.json").write_text(json.dumps(payload))

    result = _strict(project)

    assert result.exit_code == 1
    assert f"archy snapshot {project} --findings-only --out {project / 'base.json'}" in (
        result.output
    )


def test_diff_strict_locations_are_relative_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Run from a parent of the scanned path, a location relative to the scan
    root (`pkg/d.py`) would not resolve for `git blame`; `sub/pkg/d.py` does."""
    (tmp_path / "sub").mkdir()
    project = _make_ratchet_project(tmp_path / "sub")
    (project / "pkg" / "d.py").write_text("import pkg.e\n")
    (project / "pkg" / "e.py").write_text("import pkg.d\n")
    monkeypatch.chdir(tmp_path)

    result = _strict(project)

    assert result.exit_code == 1
    assert f"pkg.d -> pkg.e at {Path('sub', 'pkg', 'd.py')}:1" in result.output


@pytest.mark.parametrize("extra", [(), ("--strict",)], ids=["diff", "diff-strict"])
def test_diff_reports_a_match_time_layer_error_without_a_traceback(
    tmp_path: Path, extra: tuple[str, ...]
) -> None:
    # #821: the baseline comes from a clean config, then the overlap arrives,
    # so `diff` itself is the first command to meet it.
    project = _make_overlapping_layers_project(tmp_path, with_edge=True)
    overlapping = (project / "archy.yaml").read_text()
    (project / "archy.yaml").write_text('layers:\n  core:\n    modules: ["pkg.core.**"]\n')
    baseline = tmp_path / "base.json"
    written = CliRunner().invoke(main, ["snapshot", str(project), "--out", str(baseline)])
    assert written.exit_code == 0, written.output
    (project / "archy.yaml").write_text(overlapping)
    config = load_config(project / "archy.yaml")
    with pytest.raises(LayerConfigError, match="matches multiple layers"):
        match_layer("pkg.core.x", config.layers)

    result = CliRunner().invoke(main, ["diff", str(project), "--baseline", str(baseline), *extra])

    _assert_one_line_overlap_error(result)
