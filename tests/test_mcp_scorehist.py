"""The score, trend, snapshot and diff tools and the history they read."""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.diff import DiffReport, take_snapshot, write_snapshot
from archy.graph import build_graph, internal_subgraph
from archy.mcp import (
    DiffErrorPayload,
    create_server,
)
from archy.mcp_payloads import ForbiddenEdge
from archy.mcp_runs import (
    run_diff,
    run_score,
    run_snapshot,
    run_trend,
)
from tests.mcp_helpers import (
    _add_cycle_and_resnapshot,
    _list_tools,
    _myapp_core_cli,
    _seed_score_history,
    _write_core_cli_forbid_config,
    _write_cyclic_ratchet_project,
    _write_registry_project,
)
from tests.mcp_helpers import call_tool as _call_tool


def test_all_tools_declare_read_only_annotations_except_snapshot():
    # Every archy tool but one is read-only structural analysis. Declaring the
    # hints explicitly lets trusted clients auto-approve calls instead of
    # prompting on every read (MCP tool-annotations, 2025-03-26 spec); the
    # cautious default for an un-annotated tool is destructive/non-idempotent/
    # open-world. `archy_snapshot` is the exception: it overwrites
    # .archy/baseline.json, and an auto-approved overwrite is how a new cycle
    # would get grandfathered past a baseline gate (#521).
    server = create_server()
    tools = _list_tools(server)
    assert tools, "expected registered tools"
    names = {tool.name for tool in tools}
    assert "archy_snapshot" in names
    for tool in tools:
        # Read the wire form: mcp 2.0 renamed these attributes to snake_case
        # while keeping the aliases, so `ann.readOnlyHint` breaks across the
        # major but the emitted JSON does not.
        ann = tool.model_dump(by_alias=True)["annotations"]
        assert ann is not None, f"{tool.name} has no annotations"
        writes = tool.name == "archy_snapshot"
        assert ann["readOnlyHint"] is not writes, f"{tool.name} readOnlyHint"
        assert ann["destructiveHint"] is writes, f"{tool.name} destructiveHint"
        assert ann["idempotentHint"] is True, f"{tool.name} not idempotentHint"
        assert ann["openWorldHint"] is False, f"{tool.name} not openWorldHint=False"


def test_error_model_tier2_raises_tier3_returns_in_band(acyclic_project: Path):
    # The #229 recovery contract at the wire: a tier-2 usage error (invalid
    # argument value) surfaces as isError (call_tool raises ToolError), while a
    # tier-3 recoverable condition (no archy.yaml, no baseline) comes back as a
    # normal in-band result the agent branches on.
    from archy.mcp_compat import ToolError

    server = create_server()
    path = str(acyclic_project)

    # Tier 2: bad argument value -> isError.
    with pytest.raises(ToolError):
        _call_tool(server, "archy_graph", {"path": path, "response_format": "xml"})
    with pytest.raises(ToolError):
        _call_tool(server, "archy_score", {"path": path, "view": "sideways"})

    # Tier 3: recoverable preconditions -> in-band result (no raise).
    for name in ("archy_check", "archy_diff"):
        _content, structured = _call_tool(server, name, {"path": path})
        assert isinstance(structured, dict)
        inner = structured.get("result", structured)
        assert "error" in inner, f"{name} tier-3 result should carry an in-band error field"


def test_score_history_view_rejects_a_nonexistent_project_path(tmp_path: Path):
    # `view='history'` returns before the score scan. The guard has to run
    # first, or this branch still accepts a path the CLI would reject.
    missing = tmp_path / "no-such-project"
    assert not missing.exists()

    from archy.mcp_compat import ToolError

    with pytest.raises(ToolError):
        _call_tool(
            create_server(),
            "archy_score",
            {"path": str(missing), "view": "history"},
        )

    assert not missing.exists()


def test_score_rejects_a_file_as_the_project_path(tmp_path: Path):
    # click.Path(file_okay=False). A file used to score 1.0 via the cold path
    # after mkdir failed, with the file left in place.
    project = tmp_path / "not-a-directory"
    project.write_text("x = 1\n")

    from archy.mcp_compat import ToolError

    with pytest.raises(ToolError):
        _call_tool(create_server(), "archy_score", {"path": str(project)})

    assert project.read_text() == "x = 1\n"
    assert not (tmp_path / ".archy").exists()


def test_score_accepts_an_existing_project_directory(acyclic_project: Path):
    # Positive control for the guard. The fixture is pkg/__init__.py, a.py,
    # and b.py: three modules. A guard that rejects every path fails here.
    _content, structured = _call_tool(
        create_server(), "archy_score", {"path": str(acyclic_project)}
    )
    assert structured is not None
    assert structured["result"]["inputs"]["module_count"] == 3


def test_run_score_payload_shape(acyclic_project: Path):
    payload = run_score(
        acyclic_project,
        internal_only=True,
        record=False,
        strict=False,
        strict_tolerance=0.02,
    )
    assert payload.overall > 0
    assert payload.components.modularity is not None
    assert payload.components.acyclicity is not None
    assert payload.inputs.module_count >= 0
    assert payload.gate is None


def test_run_score_strict_includes_gate_block(acyclic_project: Path):
    payload = run_score(
        acyclic_project,
        internal_only=True,
        record=False,
        strict=True,
        strict_tolerance=0.02,
    )
    assert payload.gate is not None
    assert payload.gate.tolerance == 0.02


def test_archy_score_rejects_strict_tolerance_outside_unit_interval(acyclic_project: Path):
    """`archy_score` must reject a strict_tolerance outside [0, 1].

    The gate sets passed when delta >= -strict_tolerance. Both scores sit in
    [0, 1], so delta sits in [-1, 1]. Tolerance 5.0 makes every drop pass,
    because -1 >= -5. Tolerance -0.1 requires delta >= 0.1, so an ordinary
    drop fails. Tolerance -5 fails even when the score improves, because no
    delta can reach 5. The CLI already rejects these with _require.

    The row below sets previous to 0.99. This acyclic fixture scores below
    that, so delta is negative and still greater than -1. Tolerance 0.0 must
    fail that drop. Tolerance 1.0 must pass it.
    """
    from archy.mcp_compat import ToolError

    _seed_score_history(acyclic_project, 0.99)
    server = create_server()
    path = str(acyclic_project)

    def reject(tolerance: float, *, strict: bool) -> None:
        message = f"strict_tolerance must be in [0, 1]; got {tolerance}"
        with pytest.raises(ValueError) as caught:
            run_score(
                acyclic_project,
                internal_only=True,
                record=False,
                strict=strict,
                strict_tolerance=tolerance,
            )
        assert str(caught.value) == message
        # mcp 2.x ToolError is "Error executing tool archy_score" without the
        # underlying ValueError text; 1.x keeps it. The exact message is pinned
        # on the direct run_score raise above.
        with pytest.raises(ToolError):
            _call_tool(
                server,
                "archy_score",
                {"path": path, "strict": strict, "strict_tolerance": tolerance},
            )

    reject(5.0, strict=True)
    reject(-0.1, strict=True)
    # The CLI checks the flag even when --strict is off. MCP must do the same.
    reject(5.0, strict=False)

    for tolerance, expect_passed in ((0.0, False), (1.0, True)):
        _content, structured = _call_tool(
            server,
            "archy_score",
            {"path": path, "strict": True, "strict_tolerance": tolerance},
        )
        assert isinstance(structured, dict)
        gate = structured["result"]["gate"]
        assert gate["previous"] == 0.99
        assert gate["tolerance"] == tolerance
        # The fixture has to be a real drop that tolerance 1.0 still allows.
        assert -1.0 < gate["delta"] < 0
        assert gate["passed"] is expect_passed


def test_run_trend_payload_shape(acyclic_project: Path):
    run_score(
        acyclic_project,
        internal_only=True,
        record=True,
        strict=False,
        strict_tolerance=0.02,
    )
    [row] = run_trend(acyclic_project, last_n=10)
    assert row.timestamp
    assert row.score is not None
    assert row.score.overall > 0


def test_score_view_history_routes_to_trend(acyclic_project: Path):
    # #266: archy_score(view="history") is the fold that replaced archy_trend.
    # Record a baseline, then the history view must return that row through the
    # tool (wrapped under {"result": [...]} like any bare-sequence return).
    server = create_server()
    path = str(acyclic_project)
    _call_tool(server, "archy_score", {"path": path, "record": True})
    _content, structured = _call_tool(
        server, "archy_score", {"path": path, "view": "history", "last_n": 5}
    )
    assert isinstance(structured, dict)
    [row] = structured["result"]
    assert row["timestamp"]
    assert row["score"]["overall"] > 0


@pytest.mark.parametrize("bad", [0, -1, -10])
def test_run_trend_validates_last_n(acyclic_project: Path, bad: int):
    # Previously last_n <= 0 silently returned the entire history; now it is a
    # clear validation error so an agent doesn't accidentally dump everything.
    with pytest.raises(ValueError, match="last_n"):
        run_trend(acyclic_project, last_n=bad)


def test_run_snapshot_writes_baseline_and_returns_payload(acyclic_project: Path):
    payload = run_snapshot(acyclic_project)
    assert payload.baseline_path.endswith("baseline.json")
    assert payload.score is not None
    assert payload.score.overall > 0
    assert (acyclic_project / ".archy" / "baseline.json").exists()


def test_snapshot_brief_reports_layers_and_forbidden_edges(tmp_path: Path):
    _myapp_core_cli(tmp_path, runner="from myapp.core.api import go\n")
    _write_core_cli_forbid_config(tmp_path)
    brief = run_snapshot(tmp_path).invariant_brief
    assert {layer.name for layer in brief.layers} == {"core", "cli"}
    assert ForbiddenEdge(from_layer="core", to_layer="cli") in brief.forbidden_edges


def test_snapshot_brief_no_config_has_empty_layers(acyclic_project: Path):
    brief = run_snapshot(acyclic_project).invariant_brief
    assert brief.layers == ()
    assert brief.forbidden_edges == ()
    assert brief.required_reach == ()


def test_snapshot_brief_reports_required_reach_rules(tmp_path: Path):
    """Prevention, not correction: a missing import looks like nothing at all,
    so an agent that is not told the rule up front cannot infer it from code."""
    project = _write_registry_project(tmp_path, bootstrap="")
    brief = run_snapshot(project).invariant_brief
    dumped = brief.model_dump()["required_reach"]
    assert dumped == (
        {
            "source": "app.commands.*",
            "must_reach": "app.core.model_registry",
            "reason": "standalone entrypoints need the full mapper registry",
            "within": None,
        },
    )


def test_snapshot_brief_carries_an_entrypoints_rule_scope(tmp_path: Path):
    project = _write_registry_project(tmp_path, bootstrap="")
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: '@entrypoints'\n"
        "    within: 'app.**'\n"
        "    must_reach: app.core.model_registry\n"
    )
    [rule] = run_snapshot(project).invariant_brief.model_dump()["required_reach"]
    assert rule["source"] == "@entrypoints"
    assert rule["within"] == "app.**"


def test_run_diff_without_baseline_returns_error(acyclic_project: Path):
    result = run_diff(acyclic_project)
    assert isinstance(result, DiffErrorPayload)
    assert "no baseline" in result.error


def test_run_diff_directory_baseline_returns_error_on_the_wire(acyclic_project: Path):
    # `baseline` is resolved relative to `path`, so "subdir" is a folder inside it.
    target = acyclic_project / "subdir"
    target.mkdir()

    result = run_diff(acyclic_project, baseline="subdir")

    assert isinstance(result, DiffErrorPayload)
    assert result.model_dump() == {
        "error": (
            f"{target} is not a readable archy snapshot; re-run `archy snapshot`: "
            "it is a directory, not a file"
        )
    }


def test_run_diff_corrupt_baseline_returns_error_on_the_wire(acyclic_project: Path):
    target = acyclic_project / ".archy" / "baseline.json"
    target.parent.mkdir(parents=True)
    target.write_text("not json\n")

    dumped = run_diff(acyclic_project).model_dump()

    assert dumped == {
        "error": (
            f"{target} is not a readable archy snapshot; re-run `archy snapshot`: "
            "invalid JSON (Expecting value: line 1 column 1 (char 0))"
        )
    }


def test_run_diff_after_snapshot_reports_zero_delta(acyclic_project: Path):
    run_snapshot(acyclic_project)
    result = run_diff(acyclic_project)
    assert isinstance(result, DiffReport)
    assert result.score_delta is not None
    assert result.score_delta.overall == 0.0
    assert result.cycles.added == ()
    assert result.cycles.resolved == ()


def test_run_diff_strict_needs_a_committed_baseline(tmp_path: Path):
    project = _write_cyclic_ratchet_project(tmp_path)
    run_snapshot(project)

    result = run_diff(project, strict=True)

    assert isinstance(result, DiffErrorPayload)
    assert "strict=True needs `baseline`" in result.error


def test_run_diff_strict_is_not_grandfathered_by_a_resnapshot(tmp_path: Path):
    """The #521 constraint: an agent told to snapshot at the start of each
    task re-snapshots after adding a cycle. Strict reads the committed file,
    so the new cycle still fails and is named."""
    project = _write_cyclic_ratchet_project(tmp_path)
    _add_cycle_and_resnapshot(project)

    result = run_diff(project, baseline="archy-baseline.json", strict=True)

    assert isinstance(result, DiffReport)
    dumped = result.model_dump()
    assert dumped["strict"]["passed"] is False
    assert dumped["strict"]["findings"][0]["modules"] == ("pkg.d", "pkg.e")
    assert "pkg.d -> pkg.e at" in dumped["strict"]["reason"]
    assert dumped["strict"]["baseline"] == "archy-baseline.json"


def test_run_diff_strict_passes_on_carried_findings(tmp_path: Path):
    project = _write_cyclic_ratchet_project(tmp_path)

    result = run_diff(project, baseline="archy-baseline.json", strict=True)

    assert isinstance(result, DiffReport)
    assert result.model_dump()["strict"]["passed"] is True
    assert result.score_delta is None  # findings-only baseline: no score to compare


def test_run_diff_strict_refuses_the_default_snapshot_file(tmp_path: Path):
    """The agent being gated picks the arguments: naming .archy/baseline.json
    after a re-snapshot would otherwise pass a new cycle."""
    project = _write_cyclic_ratchet_project(tmp_path)
    _add_cycle_and_resnapshot(project)

    result = run_diff(project, baseline=".archy/baseline.json", strict=True)

    assert isinstance(result, DiffErrorPayload)
    assert "the file `archy snapshot` overwrites" in result.error


def test_run_diff_missing_committed_baseline_says_it_resolves_against_path(tmp_path: Path):
    project = _write_cyclic_ratchet_project(tmp_path)

    result = run_diff(project, baseline="nope.json", strict=True)

    assert isinstance(result, DiffErrorPayload)
    assert f"archy snapshot {project} --findings-only --out {project / 'nope.json'}" in result.error
    assert "relative to `path`" in result.error


def test_run_diff_strict_refuses_when_the_layer_rules_disappeared(tmp_path: Path):
    """Same refusal as the CLI, on the surface agents call."""
    project = _write_cyclic_ratchet_project(tmp_path)
    (project / "pkg" / "a.py").write_text("import pkg.b\nimport pkg.d\n")
    (project / "archy.yaml").write_text(
        "layers:\n  low: {modules: [pkg.a]}\n  high: {modules: [pkg.d]}\n"
        "forbid:\n  - {from: low, to: high}\n"
    )
    snap = take_snapshot(
        internal_subgraph(build_graph(project)), config_path=project / "archy.yaml"
    )
    assert snap.violations, "fixture must record a layer violation"
    write_snapshot(snap, project / "archy-baseline.json", findings_only=True)
    (project / "archy.yaml").unlink()

    result = run_diff(project, baseline="archy-baseline.json", strict=True)

    assert isinstance(result, DiffReport)
    assert result.model_dump()["strict"]["passed"] is False
    assert "no archy.yaml was found" in result.model_dump()["strict"]["reason"]
