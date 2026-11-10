"""The check tool: layer rules, coverage, required reach, SDP and the scan guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.layers import LayerConfigError
from archy.mcp import (
    CheckErrorPayload,
    CheckPayload,
    create_server,
)
from archy.mcp_runs import (
    run_check,
    run_cycles,
    run_simulate,
)
from tests.mcp_helpers import (
    _list_tools,
    _make_sdp_violating_project,
    _myapp_core_cli,
    _transitive_project,
    _write_core_cli_forbid_config,
    _write_registry_project,
)
from tests.mcp_helpers import call_tool as _call_tool


def test_check_passed_respects_the_layer_presence_floor(tmp_path: Path):
    """The gate has to hold on the AGENT-FACING surface, not just the CLI.

    It shipped wired into `archy check` only, so `archy_check` reported
    passed=True for the degenerate single-module solution the floor exists to
    catch: precisely the case an agent in a correction loop needs told.
    """

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "everything.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text(
        "min_layers_present: 2\n"
        "layers:\n"
        "  routes:\n    modules: ['app.routes.**']\n"
        "  services:\n    modules: ['app.services.**']\n"
        "forbid:\n  - {from: services, to: routes}\n"
    )

    payload = run_check(tmp_path, config_path=None)

    assert isinstance(payload, CheckPayload)
    assert payload.violations == ()
    assert payload.passed is False

    # Asserted on the SERIALIZED form, not attributes. FastMCP sends
    # `model_dump()`, which drops plain properties, so an attribute-only
    # assertion passed while the agent-facing payload carried no reason at all.
    dumped = payload.model_dump()
    assert dumped["passed"] is False
    assert dumped["presence_fails"] is True
    assert dumped["min_layers_present"] == 2
    assert dumped["coverage"]["layers_present"] == 0
    assert sorted(dumped["coverage"]["empty_layers"]) == ["routes", "services"]


def test_create_server_registers_expected_tools():
    server = create_server()
    tools = _list_tools(server)
    names = {t.name for t in tools}
    # The tool set is pinned exactly: adding or removing a tool without a
    # deliberate decision fails here. Folded tools live behind mode/view/param
    # switches on the survivors; see the module docstring.
    assert names == {
        "archy_score",
        "archy_cycles",
        "archy_check",
        "archy_impact",
        "archy_snapshot",
        "archy_diff",
        "archy_graph",
        "archy_what_to_refactor_next",
        "archy_dsm",
        "archy_simulate",
        "archy_duplicates",
        # `archy_conventions` reports a project's derived house style (naming
        # families, mirrored surfaces, gate inventory, model census) so an
        # agent stops re-deriving conventions by reading source.
        "archy_conventions",
        # `archy_module_view` is the same census as a LOOKUP on one module,
        # complete and unranked. Kept a separate tool rather than a
        # parameter on `archy_conventions` because a union return type makes
        # FastMCP wrap every payload under a `result` key, which would change
        # the released tool's wire format for consumers that never asked for
        # this feature.
        "archy_module_view",
        # `archy_brief` composes conventions + coverage + contracts into one
        # call. It was CLI-only until a usage signal appeared (#427).
        "archy_brief",
    }
    assert len(names) == 14


@pytest.mark.parametrize("relative", [True, False], ids=["relative", "absolute"])
def test_check_config_path_is_relative_to_project(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: bool,
):
    _myapp_core_cli(tmp_path, api="from myapp.cli import runner\n")
    _write_core_cli_forbid_config(tmp_path)
    config = tmp_path / "rules.yaml"
    (tmp_path / "archy.yaml").rename(config)
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert config.is_file()
    assert not Path(config.name).exists()

    _content, wire = _call_tool(
        create_server(),
        "archy_check",
        {"path": str(tmp_path), "config_path": config.name if relative else str(config)},
    )

    assert isinstance(wire, dict)
    result = wire["result"]
    assert result["config_path"] == str(config)
    assert result["passed"] is False
    assert [(v["source"], v["target"]) for v in result["violations"]] == [
        ("myapp.core.api", "myapp.cli.runner")
    ]


def test_run_check_payload_shape(tmp_path: Path):
    _myapp_core_cli(tmp_path, api="from myapp.cli.runner import go\n")
    _write_core_cli_forbid_config(tmp_path)
    result = run_check(tmp_path, config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is False
    [violation] = result.violations
    assert violation.rule.from_layer == "core"
    assert violation.rule.to_layer == "cli"


def test_run_check_fails_the_gate_on_a_required_reach_violation(tmp_path: Path):
    """The agent-facing surface must not report passed=true here.

    Wiring a gate to the CLI alone and not to MCP is the failure #371 hit three
    review rounds in a row: the surface agents actually call kept saying the
    codebase was fine for the exact case the check exists to catch.
    """
    result = run_check(_write_registry_project(tmp_path, bootstrap=""), config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is False
    assert result.violations == ()  # nothing forbidden; the gate failed on reach alone
    [violation] = result.required_violations
    assert violation.module == "app.commands.setup_user"


def test_run_check_required_reach_survives_model_dump(tmp_path: Path):
    """FastMCP sends `model_dump()`, so the reason has to be in the wire form.

    Asserting on the attribute would pass while the agent received `passed:
    false` with no explanation of what to fix.
    """
    result = run_check(_write_registry_project(tmp_path, bootstrap=""), config_path=None)
    assert isinstance(result, CheckPayload)
    dumped = result.model_dump()
    assert dumped["passed"] is False
    [violation] = dumped["required_violations"]
    assert violation["module"] == "app.commands.setup_user"
    assert "does not transitively reach" in violation["detail"]
    assert violation["rule"]["reason"] == "standalone entrypoints need the full mapper registry"


def test_run_check_required_reach_passes_through_the_package_init(tmp_path: Path):
    project = _write_registry_project(tmp_path, bootstrap="from app.core import model_registry\n")
    result = run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is True
    assert result.required_violations == ()


def test_run_check_entrypoints_rule_survives_model_dump(tmp_path: Path):
    """The derived reason and the rule's `within` reach the wire form."""
    project = _write_registry_project(tmp_path, bootstrap="from app.core import model_registry\n")
    (project / "app" / "worker.py").write_text('if __name__ == "__main__":\n    pass\n')
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: '@entrypoints'\n"
        "    within: 'app.**'\n"
        "    must_reach: app.core.model_registry\n"
    )
    result = run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    dumped = result.model_dump()
    assert dumped["passed"] is False
    [violation] = dumped["required_violations"]
    assert violation["module"] == "app.worker"
    assert violation["rule"]["within"] == "app.**"
    assert "It is an entrypoint" in violation["detail"]


def test_run_check_missing_config_returns_in_band(tmp_path: Path):
    # Tier-3 recoverable precondition: no archy.yaml -> in-band CheckErrorPayload
    # (isError:false), NOT a raise, so the agent can branch and create a config.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    result = run_check(tmp_path, config_path=None)
    assert isinstance(result, CheckErrorPayload)
    assert "archy.yaml" in result.error


def test_run_check_malformed_config_raises(tmp_path: Path):
    # Tier-2 usage error: a broken archy.yaml cannot be checked against, so it
    # raises (-> isError:true at the wire), distinct from the no-config case.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (tmp_path / "archy.yaml").write_text("layers: [not a mapping\n")
    with pytest.raises(LayerConfigError):
        run_check(tmp_path, config_path=None)


def test_run_check_reports_sdp_violations_when_enabled(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\nsdp:\n  enabled: true\n  tolerance: 0.0\n"
    )
    result = run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is False
    assert result.violations == ()
    [violation] = [v for v in result.sdp_violations if v.source == "myapp.a"]
    assert violation.target == "myapp.b"


def test_run_check_skips_sdp_when_disabled(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\n")
    result = run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is True
    assert result.sdp_violations == ()


def test_run_check_warn_mode_reports_violations_but_passes(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\nsdp:\n  enabled: true\n  mode: warn\n"
    )
    result = run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    # Violations still reported, but passed=True so CI/agents can adopt SDP
    # without it being a hard gate yet.
    assert result.passed is True
    assert any(v.source == "myapp.a" for v in result.sdp_violations)


def test_archy_simulate_refuses_an_empty_delta_on_the_wire(acyclic_project: Path):
    """MCP tool boundary carries the refusal as ToolError (#661 / #636)."""
    from archy.mcp_compat import ToolError

    message = "pass at least one add or remove edge"
    with pytest.raises(ValueError, match=message):
        run_simulate(acyclic_project, add=[], remove=[])
    with pytest.raises(ToolError, match=message):
        _call_tool(create_server(), "archy_simulate", {"path": str(acyclic_project)})


def test_archy_yaml_exclude_plumbed_through_mcp(tmp_path: Path):
    # Verifies the MCP path honors `exclude:` (CLI tests cover the same
    # config for the CLI path; this protects the agent-facing surface).
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "real.py").write_text("import os\n")
    gen = pkg / "baml_client"
    gen.mkdir()
    (gen / "__init__.py").write_text("from myapp.baml_client.b import x\n")
    (gen / "b.py").write_text("from myapp.baml_client import other\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nexclude: [baml_client]\n")
    assert run_cycles(tmp_path, min_size=2, internal_only=True) == []


def test_mcp_check_trips_scan_size_guard(tmp_path: Path):
    # `run_check` builds its scan arguments from the config it loaded, not via
    # `graph_kwargs`, so the guard needs pinning on this path separately.
    from archy.graph import ScanTooLargeError

    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(5):
        (pkg / f"mod{i}.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    with pytest.raises(ScanTooLargeError):
        run_check(tmp_path, config_path=None)


def test_mcp_check_applies_config_roots(tmp_path: Path):
    """`roots: [app]` is what makes the `app.*` patterns match, on the MCP path too.

    Run both ways: without `roots:` the namespace-package modules are bare
    `libs.db` / `routers.user`, the rule matches nothing and the check passes. A
    fixture where that first arm also failed would not be testing `roots:`.
    """
    app = tmp_path / "app"
    (app / "routers").mkdir(parents=True)
    (app / "libs").mkdir(parents=True)
    (app / "libs" / "db.py").write_text("from app.routers.user import handler\n")
    (app / "routers" / "user.py").write_text("")
    rules = (
        "layers:\n"
        "  routers: {modules: [app.routers.**]}\n"
        "  libs: {modules: [app.libs.**]}\n"
        "forbid:\n"
        "  - {from: libs, to: routers}\n"
    )
    config = tmp_path / "archy.yaml"

    config.write_text(rules)
    without = run_check(tmp_path, config_path=None)
    assert isinstance(without, CheckPayload)
    assert without.violations == ()

    config.write_text(rules + "roots: [app]\n")
    with_roots = run_check(tmp_path, config_path=None)
    assert isinstance(with_roots, CheckPayload)
    assert [(v.source, v.target) for v in with_roots.violations] == [
        ("app.libs.db", "app.routers.user")
    ]


def test_mcp_check_respects_config_exclude(tmp_path: Path):
    """MCP check must honor exclude when scanning forbidden imports."""
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

    config.write_text(rules)
    without_exclude = run_check(project, config_path=None)

    assert isinstance(without_exclude, CheckPayload)
    assert without_exclude.passed is False
    assert [(v.source, v.target) for v in without_exclude.violations] == [
        ("myapp.core.generated.bad", "myapp.cli.runner")
    ]

    config.write_text(rules + "exclude: [generated]\n")
    with_exclude = run_check(project, config_path=None)

    assert isinstance(with_exclude, CheckPayload)
    assert with_exclude.passed is True
    assert with_exclude.violations == ()


def test_check_payload_carries_the_exact_pattern_hint(tmp_path: Path):
    """Part 2 on the agent-facing surface, asserted on the SERIALIZED form.

    `model_dump()` drops properties, and an agent reading `passed=true` with an
    empty `violations` list has no other way to learn that the config governs
    nothing and why.
    """

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

    payload = run_check(tmp_path, config_path=None)

    assert isinstance(payload, CheckPayload)
    assert payload.violations == ()
    dumped = payload.model_dump()
    assert dumped["coverage"]["governs_no_edges"] is True
    hints = {h["layer"]: h for h in dumped["coverage"]["exact_pattern_hints"]}
    assert hints["store"]["pattern"] == "app.store"
    assert hints["store"]["suggestion"] == "app.store.**"
    assert "app.store.repository" in hints["store"]["unlayered_descendants"]


def test_check_says_it_only_saw_direct_edges(tmp_path: Path):
    """#343: `passed=true` must not be silent about what it looked at.

    `archy check` sees DIRECT edges, so `store -> common -> api` passes it and
    fails `contracts`. The CLI got the qualified verdict in v0.45/v0.46 and this
    surface did not, leaving an agent unable to tell "checked transitively and
    clean" from "never looked" on exactly the case archy claims as its
    differentiator.
    """

    payload = run_check(_transitive_project(tmp_path), config_path=None)

    assert isinstance(payload, CheckPayload)
    # The direct pass is genuinely clean; that is the whole trap.
    assert payload.passed is True
    # FastMCP sends model_dump(), so the wire format is what has to carry this.
    wire = payload.model_dump()
    assert wire["transitive_checked"] is False
    assert "forbid rule" in wire["transitive_unverified_reason"]
    assert "contracts=True" in wire["transitive_unverified_reason"]


def test_check_stays_quiet_when_there_is_nothing_to_verify(tmp_path: Path):
    """No `forbid` rule means no unproven rule. A reason printed on every clean
    run is one a reader learns to skip."""

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "api.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text("layers:\n  api:\n    modules: ['app.api']\n")

    payload = run_check(tmp_path, config_path=None)

    assert isinstance(payload, CheckPayload)
    assert payload.model_dump()["transitive_unverified_reason"] is None


def test_check_transitive_checked_needs_a_verdict_not_a_request(tmp_path: Path, monkeypatch):
    """Asking for contracts and having import-linter turn out to be missing
    leaves the rules exactly as unverified as not asking, so
    transitive_checked=True there would reintroduce the bug the field closes.

    The REASON has to move with the flag. `run_check` computes its reason before
    contracts are attempted, so it names contracts=True; replaying that at a
    caller who just passed contracts=True and watched it fail sends them round a
    loop they have already been through. Goes through the registered tool, not
    the helpers, because the fallback that produced the stale reason lived in the
    tool body where a helper-level test could not see it.
    """
    import archy.contracts

    def _boom(*args, **kwargs):
        raise archy.contracts.ContractsNotAvailable("import-linter is not installed")

    monkeypatch.setattr(archy.contracts, "run_contracts", _boom)

    server = create_server()
    _c, out = _call_tool(
        server, "archy_check", {"path": str(_transitive_project(tmp_path)), "contracts": True}
    )

    assert isinstance(out, dict)
    result = out["result"]
    assert result["contracts"]["available"] is False
    assert result["transitive_checked"] is False
    reason = result["transitive_unverified_reason"]
    # Names the real cause, not the flag the caller already passed and watched fail.
    assert "import-linter is not installed" in reason
    assert "produced no transitive verdict" in reason


def test_score_gate_passes_at_exactly_the_tolerance_and_fails_just_past_it(tmp_path: Path):
    """Previous 0.75 to current 0.5 is a drop of exactly 0.25 (all three are binary fractions)."""
    from archy.history import HistoryRow
    from archy.history import append as append_history
    from archy.mcp_runs_check import _score_gate

    history = tmp_path / ".archy" / "history.jsonl"
    append_history(
        history,
        HistoryRow(
            timestamp="2026-01-01T00:00:00Z",
            commit="abc",
            branch="main",
            overall=0.75,
            modularity=0.5,
            acyclicity=1.0,
            depth=0.5,
            equality=0.5,
            complexity=0.5,
            module_count=3,
            edge_count=2,
            cycle_count=0,
            tangle_ratio=0.0,
            max_depth=2,
            community_count=1,
        ),
    )

    at_tolerance = _score_gate(history, 0.5, 0.25)
    past_tolerance = _score_gate(history, 0.5, 0.125)

    assert at_tolerance.delta == -0.25
    assert at_tolerance.passed is True
    assert past_tolerance.passed is False


@pytest.mark.parametrize(
    "tool_name",
    sorted(
        {
            "archy_score",
            "archy_cycles",
            "archy_check",
            "archy_impact",
            "archy_snapshot",
            "archy_diff",
            "archy_graph",
            "archy_what_to_refactor_next",
            "archy_dsm",
            "archy_simulate",
            "archy_duplicates",
            "archy_conventions",
            "archy_module_view",
            "archy_brief",
        }
    ),
)
def test_all_tools_forbid_additional_properties(tool_name: str):
    """Every advertised inputSchema must reject undeclared properties (#662)."""
    server = create_server()
    tool = next(t for t in _list_tools(server) if t.name == tool_name)
    schema = tool.model_dump(by_alias=True)["inputSchema"]
    assert schema.get("additionalProperties") is False
