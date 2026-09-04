"""MCP-specific tests.

Underlying analysis behavior (cycle detection, score computation, layer
violations, etc.) is covered by test_cli.py and the per-module unit
suites. This file only verifies what the MCP layer adds:

- the registered tool surface (names) the agent sees
- the dict shapes each tool returns, since agents read them by key
- exclude/roots config plumbing through the MCP path (not just CLI)
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from archy.dsm import DSM, DSMSummary
from archy.layers import LayerConfigError
from archy.mcp import (
    CheckErrorPayload,
    CheckPayload,
    DSMTooLargePayload,
    ForbiddenEdge,
    GraphPayload,
    GraphSummaryPayload,
    GraphTooLargePayload,
    _run_affected,
    _run_check,
    _run_cycles,
    _run_diff,
    _run_dsm,
    _run_graph,
    _run_graph_dump,
    _run_graph_focus,
    _run_graph_summary,
    _run_impact,
    _run_score,
    _run_simulate,
    _run_snapshot,
    _run_trend,
    _run_what_to_refactor_next,
    create_server,
)
from archy.simulate import EdgeSpec, SimulateReport


def _internal_graph(root: Path):
    # Build the same internal-only graph the MCP layer hands to callers,
    # for parity assertions against graph_to_dict.
    from archy.graph import build_graph

    g = build_graph(root)
    external = {n for n, d in g.nodes(data=True) if d.get("external")}
    g.remove_nodes_from(external)
    return g


@pytest.fixture
def acyclic_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    return tmp_path


@pytest.fixture
def project_with_caller(tmp_path: Path) -> Path:
    # Shared by direction='in'/'out' tests: pkg.a imports pkg.b (so pkg.a has
    # a downstream dependency), and pkg.c imports pkg.a (so pkg.a has an
    # upstream caller). Anchoring focus on pkg.a then exercises both halves.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("from pkg.a import other\n")
    return tmp_path


def test_check_passed_respects_the_layer_presence_floor(tmp_path: Path):
    """The gate has to hold on the AGENT-FACING surface, not just the CLI.

    It shipped wired into `archy check` only, so `archy_check` reported
    passed=True for the degenerate single-module solution the floor exists to
    catch: precisely the case an agent in a correction loop needs told.
    """
    from archy.mcp import CheckPayload, _run_check

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

    payload = _run_check(tmp_path, config_path=None)

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
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    # v0.36 (#227): 13 tools after the consolidation. The 6 removed tools
    # (archy_affected, archy_record_baseline, archy_graph_focus,
    # archy_graph_summary, archy_high_risk_modules, archy_hotspots) are folded
    # into survivors via mode/lens/param switches; see the module docstring.
    # archy_duplicates (#242) added the 14th. v0.41 (#265): 11 after the v2
    # consolidation folded archy_trend into archy_score(view="history") (#266),
    # demoted archy_status to the CLI `archy index status` (#267), and folded
    # archy_contracts into archy_check(contracts=True) (#268).
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
        # `archy_conventions` is the 12th: it reports a project's derived house
        # style (naming families, mirrored surfaces, gate inventory, model
        # census) so an agent stops re-deriving conventions by reading source.
        "archy_conventions",
        # `archy_module_view` is the 13th: the same census as a LOOKUP on one
        # module, complete and unranked. Kept a separate tool rather than a
        # parameter on `archy_conventions` because a union return type makes
        # FastMCP wrap every payload under a `result` key, which would change
        # the released tool's wire format for consumers that never asked for
        # this feature.
        "archy_module_view",
    }
    assert len(names) == 13


def test_all_tools_declare_read_only_annotations():
    # Every archy tool is read-only structural analysis. Declaring the hints
    # explicitly lets trusted clients auto-approve calls instead of prompting
    # on every read (MCP tool-annotations, 2025-03-26 spec); the cautious
    # default for an un-annotated tool is destructive/non-idempotent/open-world.
    server = create_server()
    tools = asyncio.run(server.list_tools())
    assert tools, "expected registered tools"
    for tool in tools:
        # Read the wire form: mcp 2.0 renamed these attributes to snake_case
        # while keeping the aliases, so `ann.readOnlyHint` breaks across the
        # major but the emitted JSON does not.
        ann = tool.model_dump(by_alias=True)["annotations"]
        assert ann is not None, f"{tool.name} has no annotations"
        assert ann["readOnlyHint"] is True, f"{tool.name} not readOnlyHint"
        assert ann["destructiveHint"] is False, f"{tool.name} not destructiveHint=False"
        assert ann["idempotentHint"] is True, f"{tool.name} not idempotentHint"
        assert ann["openWorldHint"] is False, f"{tool.name} not openWorldHint=False"


def test_all_tools_declare_human_friendly_title():
    # The 2025-06-18 revision added a display `title` so `name` stays a
    # programmatic id. Assert every tool sets a non-empty title distinct from
    # its name.
    server = create_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.title, f"{tool.name} has no display title"
        assert tool.title != tool.name, f"{tool.name} title duplicates the name"


def _call_tool(server, name: str, args: dict) -> tuple[list, dict | None]:
    """Call a tool in-process and return `(content, structured)` on either SDK major.

    mcp 1.x returns that pair directly; 2.0 returns a `CallToolResult` instead.
    Normalized here rather than at each call site, and `structuredContent` is read
    from the wire form because 2.0 also renamed the attribute (see
    `archy.mcp_compat`).

    These in-process calls poke the SDK's own surface, which is why they are the
    only part of this file a major bump touches. The protocol-level contract is
    covered by `tests/test_mcp_protocol.py`, which speaks stdio and passes
    unchanged on both majors.
    """
    result = asyncio.run(server.call_tool(name, args))
    if isinstance(result, tuple):  # mcp 1.x
        return result
    return result.content, result.model_dump(by_alias=True).get("structuredContent")


def _output_schema(tool) -> dict | None:
    """Read `outputSchema` from the wire form, not the Python attribute.

    mcp 2.0 renamed every model attribute to snake_case (`outputSchema` ->
    `output_schema`) while keeping the serialization aliases identical, so
    attribute access breaks across the major and the protocol does not. Dumping
    by alias is both version-agnostic and closer to what a client actually
    parses. See `archy.mcp_compat`.
    """
    return tool.model_dump(by_alias=True).get("outputSchema")


def test_all_tools_declare_output_schema():
    # 2025-06-18 structured output: the SDK derives an `outputSchema` (JSON
    # Schema) from each tool's return annotation. Assert every tool declares
    # one and it is an object schema, since `structuredContent` must be a JSON
    # object (sequence/union returns are wrapped under a `result` key to honor
    # this -- see the module docstring).
    server = create_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        schema = _output_schema(tool)
        assert schema is not None, f"{tool.name} has no outputSchema"
        assert schema.get("type") == "object", f"{tool.name} outputSchema is not an object"


@pytest.mark.parametrize(
    # One case per wrapping rule the structured-output contract has to honor.
    # expect_text is the BC guidance that a `TextContent` block accompanies
    # `structuredContent`; it is False only for an empty bare-sequence return,
    # which FastMCP serializes to zero content blocks (the documented benign
    # edge in the module docstring).
    ("name", "extra_args", "expect_text"),
    [
        ("archy_score", {}, True),  # BaseModel return (default view="current")
        (
            "archy_score",
            {"view": "history"},
            False,
        ),  # union bare-list branch, empty -> {"result": []}
        ("archy_graph", {}, True),  # union, default summary branch (GraphSummaryPayload)
        ("archy_graph", {"response_format": "full"}, True),  # union, full branch (GraphPayload)
        ("archy_dsm", {}, True),  # union, default summary branch (DSMSummary)
        ("archy_dsm", {"response_format": "full"}, True),  # union, full branch (DSM)
        ("archy_diff", {}, True),  # union, in-band error branch (no baseline -> DiffErrorPayload)
        ("archy_check", {}, True),  # union, tier-3 no-config branch (CheckErrorPayload)
        ("archy_cycles", {}, False),  # bare list, empty on an acyclic project -> {"result": []}
        ("archy_impact", {"files": ["pkg/a.py"]}, True),  # union, blast branch (Impact)
        ("archy_impact", {"files": ["pkg/a.py"], "mode": "affected"}, True),  # affected branch
        ("archy_conventions", {}, True),  # BaseModel return (ConventionsReport)
    ],
)
def test_tool_result_conforms_to_output_schema(
    acyclic_project: Path, name: str, extra_args: dict, expect_text: bool
):
    # A client that validates `structuredContent` against a declared
    # `outputSchema` MUST NOT reject archy's results -- including the in-band
    # `*ErrorPayload` branch of a union return, which is an `anyOf` member of
    # the schema, not a separate error channel.
    from jsonschema import Draft202012Validator

    server = create_server()
    schema = {t.name: _output_schema(t) for t in asyncio.run(server.list_tools())}[name]
    content, structured = _call_tool(server, name, {"path": str(acyclic_project), **extra_args})
    assert isinstance(structured, dict)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(structured), key=lambda e: list(e.path)
    )
    assert not errors, f"{name} structuredContent violates outputSchema: {errors[:1]}"
    has_text = any(getattr(block, "text", None) for block in content)
    assert has_text is expect_text


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


# --- #226 concise-by-default response shaping ---------------------------------


def _dsm(
    path: Path,
    response_format: str = "summary",
    *,
    group_by: str = "community",
    weight: str = "imports",
    focus: str | None = None,
    focus_depth: int = 1,
    package: str | None = None,
    baseline_path: str | None = None,
):
    return _run_dsm(
        path,
        response_format=response_format,
        group_by=group_by,
        weight=weight,
        focus=focus,
        focus_depth=focus_depth,
        package=package,
        baseline_path=baseline_path,
    )


def _hub_project(tmp_path: Path, spokes: int = 3) -> Path:
    # pkg.hub imports pkg.dep (non-zero instability) and is imported by `spokes`
    # peers (high fan-in), so it dominates the edit-risk composite. Shared by the
    # structural-lens and structural-only tests.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "dep.py").write_text("")
    (pkg / "hub.py").write_text("from pkg.dep import thing\n")
    for i in range(spokes):
        (pkg / f"s{i}.py").write_text("from pkg.hub import x\n")
    return tmp_path


def _fan_out_project(tmp_path: Path, fan: int) -> Path:
    # pkg.a imports `fan` leaf modules -> `fan` cells, no cycle.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    leaves = "".join(f"from pkg.m{i} import x\n" for i in range(fan))
    (pkg / "a.py").write_text(leaves)
    for i in range(fan):
        (pkg / f"m{i}.py").write_text("")
    return tmp_path


def _write_core_cli_forbid_config(root: Path) -> None:
    # The canonical two-layer archy.yaml shared by the check / snapshot / fold
    # tests: a `core` layer forbidden from reaching `cli`. Kept as one helper so
    # the layer/forbid shape lives in a single place.
    (root / "archy.yaml").write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )


@pytest.mark.parametrize("runner", [_run_graph, _run_dsm])
def test_response_format_rejects_unknown_value(acyclic_project: Path, runner):
    # Both heavy tools validate the enum up front, before any graph work.
    kwargs = (
        dict(internal_only=True, max_nodes=500, top_n=20)
        if runner is _run_graph
        else dict(
            group_by="community",
            weight="imports",
            focus=None,
            focus_depth=1,
            package=None,
            baseline_path=None,
        )
    )
    with pytest.raises(ValueError, match="response_format must be"):
        runner(acyclic_project, response_format="xml", **kwargs)


def test_dsm_default_is_compact_summary(acyclic_project: Path):
    # Default (no response_format) is the concise summary: counts + block
    # structure, and crucially NOT the full cell list.
    result = _dsm(acyclic_project)
    assert isinstance(result, DSMSummary)
    assert not hasattr(result, "cells")
    assert result.module_count >= 2
    assert result.group_count == len(result.groups)


def test_dsm_full_returns_matrix_with_cells(acyclic_project: Path):
    result = _dsm(acyclic_project, response_format="full")
    assert isinstance(result, DSM)
    assert result.cells  # pkg.a -> pkg.b is one non-empty cell


def test_dsm_summary_localizes_back_edges(tmp_path: Path):
    # A 2-cycle produces a back-edge (source later than target in the ordering);
    # the summary surfaces both the count and the (source, target) name pair.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n")
    summary = _dsm(tmp_path, group_by="topological")
    assert summary.back_edge_count >= 1
    assert summary.back_edges
    src, tgt = summary.back_edges[0]
    assert {src, tgt} == {"pkg.a", "pkg.b"}


def test_dsm_full_oversized_returns_too_large(tmp_path: Path, monkeypatch):
    # The full matrix is capped; over the ceiling it refuses with an explicit
    # indicator pointing at the summary / focus / package escape hatches.
    monkeypatch.setattr("archy.mcp.DEFAULT_MAX_DSM_CELLS", 2)
    project = _fan_out_project(tmp_path, fan=5)  # 5 cells > cap of 2
    result = _dsm(project, response_format="full")
    assert isinstance(result, DSMTooLargePayload)
    assert result.cell_count == 5
    assert result.max_cells == 2


def test_dsm_diff_ignores_response_format(acyclic_project: Path, tmp_path: Path):
    # A baseline diff is a deliberate, already-compact comparison; summary mode
    # must not suppress it.
    from archy.dsm import DSMDiff, build_dsm, write_dsm
    from archy.mcp import _load_graph

    baseline = tmp_path / "baseline.json"
    write_dsm(build_dsm(_load_graph(acyclic_project, internal_only=False)), baseline)
    result = _dsm(acyclic_project, response_format="summary", baseline_path=str(baseline))
    assert isinstance(result, DSMDiff)


def test_graph_default_is_summary(acyclic_project: Path):
    result = _run_graph(
        acyclic_project, response_format="summary", internal_only=True, max_nodes=500, top_n=20
    )
    assert isinstance(result, GraphSummaryPayload)


def test_graph_full_returns_dump(acyclic_project: Path):
    result = _run_graph(
        acyclic_project, response_format="full", internal_only=True, max_nodes=500, top_n=20
    )
    assert isinstance(result, GraphPayload)
    assert result.nodes


def test_create_server_registers_loop_prompt():
    server = create_server()
    prompts = asyncio.run(server.list_prompts())
    assert any(p.name == "loop" for p in prompts)


def test_run_score_payload_shape(acyclic_project: Path):
    payload = _run_score(
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
    payload = _run_score(
        acyclic_project,
        internal_only=True,
        record=False,
        strict=True,
        strict_tolerance=0.02,
    )
    assert payload.gate is not None
    assert payload.gate.tolerance == 0.02


def test_run_cycles_payload_shape(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n")
    [cycle] = _run_cycles(tmp_path, min_size=2, internal_only=True)
    assert sorted(cycle.modules) == ["pkg.a", "pkg.b"]
    edge = cycle.edges[0]
    assert edge.source and edge.target


def test_run_check_payload_shape(tmp_path: Path):
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    core = pkg / "core"
    core.mkdir()
    (core / "__init__.py").write_text("")
    (core / "api.py").write_text("from myapp.cli.runner import go\n")
    cli = pkg / "cli"
    cli.mkdir()
    (cli / "__init__.py").write_text("")
    (cli / "runner.py").write_text("")
    _write_core_cli_forbid_config(tmp_path)
    result = _run_check(tmp_path, config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is False
    [violation] = result.violations
    assert violation.rule.from_layer == "core"
    assert violation.rule.to_layer == "cli"


def _write_registry_project(tmp_path: Path, *, bootstrap: str) -> Path:
    """The reported incident, reduced. See test_cli.py::_make_registry_project."""
    app = tmp_path / "app"
    (app / "core").mkdir(parents=True)
    (app / "commands").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "core" / "__init__.py").write_text("")
    (app / "core" / "model_registry.py").write_text("REGISTRY = {}\n")
    (app / "commands" / "__init__.py").write_text(bootstrap)
    (app / "commands" / "setup_user.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: 'app.commands.*'\n"
        "    must_reach: app.core.model_registry\n"
        "    reason: standalone entrypoints need the full mapper registry\n"
    )
    return tmp_path


def test_run_check_fails_the_gate_on_a_required_reach_violation(tmp_path: Path):
    """The agent-facing surface must not report passed=true here.

    Wiring a gate to the CLI alone and not to MCP is the failure #371 hit three
    review rounds in a row: the surface agents actually call kept saying the
    codebase was fine for the exact case the check exists to catch.
    """
    result = _run_check(_write_registry_project(tmp_path, bootstrap=""), config_path=None)
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
    result = _run_check(_write_registry_project(tmp_path, bootstrap=""), config_path=None)
    assert isinstance(result, CheckPayload)
    dumped = result.model_dump()
    assert dumped["passed"] is False
    [violation] = dumped["required_violations"]
    assert violation["module"] == "app.commands.setup_user"
    assert "does not transitively reach" in violation["detail"]
    assert violation["rule"]["reason"] == "standalone entrypoints need the full mapper registry"


def test_run_check_required_reach_passes_through_the_package_init(tmp_path: Path):
    project = _write_registry_project(tmp_path, bootstrap="from app.core import model_registry\n")
    result = _run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is True
    assert result.required_violations == ()


def test_run_check_missing_config_returns_in_band(tmp_path: Path):
    # Tier-3 recoverable precondition: no archy.yaml -> in-band CheckErrorPayload
    # (isError:false), NOT a raise, so the agent can branch and create a config.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    result = _run_check(tmp_path, config_path=None)
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
        _run_check(tmp_path, config_path=None)


def test_check_contracts_flag_nests_contract_results(tmp_path: Path):
    # #268: archy_check(contracts=True) folds the old archy_contracts tool,
    # nesting the transitive import-linter result under CheckPayload.contracts.
    # contracts=False (the default) leaves the field None, so a routine check
    # stays a pure direct-edge layer-rule result.
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for sub in ("core", "cli"):
        (pkg / sub).mkdir()
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "core" / "api.py").write_text("")
    (pkg / "cli" / "runner.py").write_text("")
    _write_core_cli_forbid_config(tmp_path)

    server = create_server()
    path = str(tmp_path)

    # CheckPayload | CheckErrorPayload is a union return, so FastMCP wraps it
    # under a top-level `result` key (see the module docstring's wrapping rules).
    _c, plain = _call_tool(server, "archy_check", {"path": path})
    assert isinstance(plain, dict)
    assert plain["result"]["passed"] is True
    assert plain["result"]["contracts"] is None

    _c2, withc = _call_tool(server, "archy_check", {"path": path, "contracts": True})
    assert isinstance(withc, dict)
    result = withc["result"]
    assert result["passed"] is True
    # import-linter is a dev dependency (see tests/test_contracts.py), so the
    # fold runs the real contract derived from the archy.yaml `forbid` rule and
    # keeps it clean here.
    assert isinstance(result["contracts"], dict)
    assert result["contracts"]["available"] is True
    assert result["contracts"]["all_kept"] is True


def _make_sdp_violating_project(tmp_path: Path) -> Path:
    # a is depended on by x1/x2/x3 (Ca=3) and depends on b (Ce=1) -> I(a)=0.25.
    # b is depended on by a (Ca=1) and depends on y1/y2/y3 (Ce=3) -> I(b)=0.75.
    # The a -> b edge is stable importing less-stable: an SDP violation.
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from myapp.b import thing\n")
    (pkg / "b.py").write_text("from myapp import y1, y2, y3\n")
    (pkg / "y1.py").write_text("")
    (pkg / "y2.py").write_text("")
    (pkg / "y3.py").write_text("")
    (pkg / "x1.py").write_text("from myapp.a import thing\n")
    (pkg / "x2.py").write_text("from myapp.a import thing\n")
    (pkg / "x3.py").write_text("from myapp.a import thing\n")
    return tmp_path


def test_run_check_reports_sdp_violations_when_enabled(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\nsdp:\n  enabled: true\n  tolerance: 0.0\n"
    )
    result = _run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is False
    assert result.violations == ()
    [violation] = [v for v in result.sdp_violations if v.source == "myapp.a"]
    assert violation.target == "myapp.b"


def test_run_check_skips_sdp_when_disabled(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\n")
    result = _run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    assert result.passed is True
    assert result.sdp_violations == ()


def test_run_check_warn_mode_reports_violations_but_passes(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\nsdp:\n  enabled: true\n  mode: warn\n"
    )
    result = _run_check(project, config_path=None)
    assert isinstance(result, CheckPayload)
    # Violations still reported, but passed=True so CI/agents can adopt SDP
    # without it being a hard gate yet.
    assert result.passed is True
    assert any(v.source == "myapp.a" for v in result.sdp_violations)


def test_run_trend_payload_shape(acyclic_project: Path):
    _run_score(
        acyclic_project,
        internal_only=True,
        record=True,
        strict=False,
        strict_tolerance=0.02,
    )
    [row] = _run_trend(acyclic_project, last_n=10)
    assert row.timestamp
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
        _run_trend(acyclic_project, last_n=bad)


def test_run_impact_payload_shape(tmp_path: Path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "lib.py").write_text("")
    (pkg / "main.py").write_text("from app.lib import x\n")
    result = _run_impact(tmp_path, files=[Path("app/lib.py")])
    assert result.changed == ("app.lib",)
    assert result.impacted == ("app.main",)


def test_run_snapshot_writes_baseline_and_returns_payload(acyclic_project: Path):
    payload = _run_snapshot(acyclic_project)
    assert payload.baseline_path.endswith("baseline.json")
    assert payload.score.overall > 0
    assert (acyclic_project / ".archy" / "baseline.json").exists()


def test_snapshot_brief_mirrors_score_and_acyclic_invariant(acyclic_project: Path):
    payload = _run_snapshot(acyclic_project)
    brief = payload.invariant_brief
    # The brief is a recombination of the snapshot's own numbers, not a
    # second computation, so it must agree with the payload exactly.
    assert brief.acyclic is (payload.cycles == ())
    assert brief.acyclic is True
    assert brief.overall == payload.score.overall
    assert brief.components.modularity == payload.score.modularity
    assert brief.components.complexity == payload.score.complexity


def test_snapshot_brief_load_bearing_ranked_and_capped(tmp_path: Path):
    """The brief carries the five riskiest modules, highest first.

    Needs a project the ranking and the cap can both bite on. `acyclic_project`
    is neither: `compute_edit_risk` is 0.0 for all three of its modules, and a
    list of equal values is sorted in BOTH directions, so inverting the key
    passed; three modules also cannot overflow a cap of five, so raising the cap
    to a million passed too (#440). This chain-plus-fan-in tree has eight
    modules with five distinct risks, so both assertions have something to fail
    on, and the fixture preconditions are asserted before the outcome is.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m1.py").write_text("")
    (pkg / "m2.py").write_text("from pkg.m1 import a\n")
    (pkg / "m3.py").write_text("from pkg.m2 import a\nfrom pkg.m1 import b\n")
    (pkg / "m4.py").write_text("from pkg.m3 import a\n")
    (pkg / "m5.py").write_text("from pkg.m4 import a\nfrom pkg.m2 import c\n")
    (pkg / "m6.py").write_text("from pkg.m5 import a\n")
    (pkg / "m7.py").write_text("from pkg.m6 import a\nfrom pkg.m3 import d\n")

    from archy.graph import build_graph
    from archy.risk import compute_edit_risk

    all_risks = compute_edit_risk(build_graph(tmp_path))
    assert len(all_risks) == 8  # more modules than the cap, so the cap truncates
    assert len(set(all_risks.values())) > 2  # distinct risks, so a ranking is visible

    brief = _run_snapshot(tmp_path).invariant_brief
    risks = [m.edit_risk for m in brief.load_bearing]
    assert risks == sorted(risks, reverse=True)
    assert len(brief.load_bearing) == 5
    # pkg, pkg.m1 and pkg.m7 all score 0.0, so the cap has to drop them, not
    # three arbitrary modules.
    assert [m.module for m in brief.load_bearing] == [
        "pkg.m3",
        "pkg.m2",
        "pkg.m4",
        "pkg.m5",
        "pkg.m6",
    ]


def test_snapshot_brief_reports_layers_and_forbidden_edges(tmp_path: Path):
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    core = pkg / "core"
    core.mkdir()
    (core / "__init__.py").write_text("")
    (core / "api.py").write_text("")
    cli = pkg / "cli"
    cli.mkdir()
    (cli / "__init__.py").write_text("")
    (cli / "runner.py").write_text("from myapp.core.api import go\n")
    _write_core_cli_forbid_config(tmp_path)
    brief = _run_snapshot(tmp_path).invariant_brief
    assert {layer.name for layer in brief.layers} == {"core", "cli"}
    assert ForbiddenEdge(from_layer="core", to_layer="cli") in brief.forbidden_edges


def test_snapshot_brief_no_config_has_empty_layers(acyclic_project: Path):
    brief = _run_snapshot(acyclic_project).invariant_brief
    assert brief.layers == ()
    assert brief.forbidden_edges == ()
    assert brief.required_reach == ()


def test_snapshot_brief_reports_required_reach_rules(tmp_path: Path):
    """Prevention, not correction: a missing import looks like nothing at all,
    so an agent that is not told the rule up front cannot infer it from code."""
    project = _write_registry_project(tmp_path, bootstrap="")
    brief = _run_snapshot(project).invariant_brief
    dumped = brief.model_dump()["required_reach"]
    assert dumped == (
        {
            "source": "app.commands.*",
            "must_reach": "app.core.model_registry",
            "reason": "standalone entrypoints need the full mapper registry",
        },
    )


def test_run_simulate_parses_from_alias(acyclic_project: Path):
    # `from` is a Python keyword; the wire field must be the alias `from`, not
    # `from_`. If the alias breaks, agents cannot call the tool at all.
    payload = _run_simulate(
        acyclic_project,
        add=[EdgeSpec.model_validate({"from": "pkg.b", "to": "pkg.a"})],
        remove=[],
    )
    assert isinstance(payload, SimulateReport)
    # pkg.a imports pkg.b; adding pkg.b -> pkg.a closes a cycle.
    assert payload.cycles.added


def test_simulate_tool_input_schema_uses_from_to_aliases():
    server = create_server()
    tools = asyncio.run(server.list_tools())
    tool = next(t for t in tools if t.name == "archy_simulate")
    edge_spec = tool.model_dump(by_alias=True)["inputSchema"]["$defs"]["EdgeSpec"]["properties"]
    # The wire contract the agent sees must be {from, to}, not {from_, to}.
    assert set(edge_spec) == {"from", "to"}


def test_run_diff_without_baseline_returns_error(acyclic_project: Path):
    from archy.mcp import DiffErrorPayload

    result = _run_diff(acyclic_project)
    assert isinstance(result, DiffErrorPayload)
    assert "no baseline" in result.error


def test_run_diff_after_snapshot_reports_zero_delta(acyclic_project: Path):
    from archy.diff import DiffReport

    _run_snapshot(acyclic_project)
    result = _run_diff(acyclic_project)
    assert isinstance(result, DiffReport)
    assert result.score_delta.overall == 0.0
    assert result.cycles.added == ()
    assert result.cycles.resolved == ()


def test_graph_focus_default_returns_local_neighborhood(acyclic_project: Path):
    payload = _run_graph_focus(
        acyclic_project,
        modules=["pkg.a"],
        depth=1,
        direction="both",
        internal_only=True,
    )
    assert isinstance(payload, GraphPayload)
    ids = {n.id for n in payload.nodes}
    # pkg.a is the seed; pkg.b is reached via out-edge; pkg (the package) is
    # reached via the reverse direction since pkg/__init__.py is a node too.
    assert "pkg.a" in ids
    assert "pkg.b" in ids
    edge_pairs = {(e.source, e.target) for e in payload.edges}
    assert ("pkg.a", "pkg.b") in edge_pairs


def test_graph_focus_direction_out_excludes_callers(project_with_caller: Path):
    payload = _run_graph_focus(
        project_with_caller,
        modules=["pkg.a"],
        depth=1,
        direction="out",
        internal_only=True,
    )
    ids = {n.id for n in payload.nodes}
    assert "pkg.a" in ids and "pkg.b" in ids
    assert "pkg.c" not in ids


def test_graph_focus_direction_in_excludes_dependencies(project_with_caller: Path):
    payload = _run_graph_focus(
        project_with_caller,
        modules=["pkg.a"],
        depth=1,
        direction="in",
        internal_only=True,
    )
    ids = {n.id for n in payload.nodes}
    assert "pkg.a" in ids and "pkg.c" in ids
    assert "pkg.b" not in ids


def test_graph_focus_resolves_file_paths(acyclic_project: Path):
    payload = _run_graph_focus(
        acyclic_project,
        modules=["pkg/a.py"],
        depth=0,
        direction="both",
        internal_only=True,
    )
    ids = {n.id for n in payload.nodes}
    assert ids == {"pkg.a"}
    assert payload.unresolved == ()


def test_graph_focus_reports_unresolved(acyclic_project: Path):
    payload = _run_graph_focus(
        acyclic_project,
        modules=["pkg.a", "nonexistent.module", "missing/file.py"],
        depth=0,
        direction="both",
        internal_only=True,
    )
    assert {n.id for n in payload.nodes} == {"pkg.a"}
    assert set(payload.unresolved) == {"nonexistent.module", "missing/file.py"}


def test_graph_focus_multi_seed_unions_ego_graphs(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import x\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("from pkg.d import y\n")
    (pkg / "d.py").write_text("")

    payload = _run_graph_focus(
        tmp_path,
        modules=["pkg.a", "pkg.c"],
        depth=1,
        direction="out",
        internal_only=True,
    )
    ids = {n.id for n in payload.nodes}
    assert {"pkg.a", "pkg.b", "pkg.c", "pkg.d"} <= ids


def test_graph_focus_validates_direction(acyclic_project: Path):
    with pytest.raises(ValueError, match="direction"):
        _run_graph_focus(
            acyclic_project,
            modules=["pkg.a"],
            depth=1,
            direction="sideways",
            internal_only=True,
        )


def test_graph_summary_top_n_ranking(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hub.py").write_text("")  # imported by many
    (pkg / "a.py").write_text("from pkg.hub import x\n")
    (pkg / "b.py").write_text("from pkg.hub import y\n")
    (pkg / "c.py").write_text("from pkg.hub import z\nfrom pkg.a import w\n")

    summary = _run_graph_summary(tmp_path, top_n=3)
    assert summary.module_count == 5  # pkg, pkg.hub, pkg.a, pkg.b, pkg.c
    # hub has the highest fan-in (a, b, c all import it).
    assert summary.top_fan_in[0].module == "pkg.hub"
    assert summary.top_fan_in[0].value >= 3
    # c has the highest out-degree (imports two things).
    assert summary.top_fan_out[0].module == "pkg.c"
    assert len(summary.top_pagerank) <= 3


def test_graph_summary_counts_external_deps(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import requests\nimport json\n")
    (pkg / "b.py").write_text("import requests\n")

    summary = _run_graph_summary(tmp_path, top_n=10)
    ext_names = {e.module for e in summary.external_deps}
    assert "requests" in ext_names
    requests_entry = next(e for e in summary.external_deps if e.module == "requests")
    assert requests_entry.value >= 2


def test_graph_summary_validates_top_n(acyclic_project: Path):
    with pytest.raises(ValueError, match="top_n"):
        _run_graph_summary(acyclic_project, top_n=0)


def test_graph_dump_matches_cli_json(acyclic_project: Path):
    """Parity between the MCP dump and `archy graph --format json`.

    What this does and does not make independent, stated precisely because the
    first rewrite overclaimed it. The earlier version compared the payload
    against `graph_to_dict(g)` computed in the test, which proved nothing
    (#439). Shelling out to the CLI makes the GRAPH CONSTRUCTION independent:
    a fresh subprocess doing its own `build_graph` against the cached manager's.
    It does NOT make the SERIALIZATION independent, because `cli.graph` and
    `mcp._graph_payload_from` both still call `graph_to_dict`, so a consistent
    regression inside that function moves both sides together and passes here.
    `tests/test_graph.py::test_graph_to_dict_shape_is_pinned_by_hand` is what
    covers that, against a dict written out by hand.
    """
    cli = subprocess.run(
        [sys.executable, "-m", "archy.cli", "graph", str(acyclic_project), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli.returncode == 0, cli.stderr
    expected = json.loads(cli.stdout[cli.stdout.index("{") :])

    payload = _run_graph_dump(acyclic_project, internal_only=True, max_nodes=500)

    assert isinstance(payload, GraphPayload)
    assert payload.root == expected["root"]
    # The CLI dump keeps external nodes; the MCP call above asked for internal
    # only, so compare against the internal subset rather than the whole thing.
    internal = {n["id"] for n in expected["nodes"] if not n.get("external")}
    assert {n.id for n in payload.nodes} == internal
    assert {(e.source, e.target) for e in payload.edges} == {
        (e["source"], e["target"])
        for e in expected["edges"]
        if e["source"] in internal and e["target"] in internal
    }
    # A fixture with no edges would make the comparison above vacuous.
    assert payload.edges


def test_graph_dump_refuses_oversized_graph(acyclic_project: Path):
    payload = _run_graph_dump(acyclic_project, internal_only=True, max_nodes=1)
    assert isinstance(payload, GraphTooLargePayload)
    assert payload.max_nodes == 1
    assert payload.node_count > 1
    assert "archy_graph(focus=" in payload.error


@pytest.mark.parametrize("bad", [0, -1, -500])
def test_graph_dump_validates_max_nodes(acyclic_project: Path, bad: int):
    # A non-positive max_nodes used to fall through to the size guard and
    # return a confusing GraphTooLargePayload; reject it up front instead.
    with pytest.raises(ValueError, match="max_nodes"):
        _run_graph_dump(acyclic_project, internal_only=True, max_nodes=bad)


def test_manager_cache_key_distinguishes_config(tmp_path: Path):
    # Regression for the stale-manager blocker: the key must fold in the
    # graph-building kwargs, not just the root, or a config change is ignored.
    from archy.mcp import _manager_cache_key

    base = _manager_cache_key(tmp_path, {})
    same = _manager_cache_key(tmp_path, {"extra_roots": ()})
    diff_roots = _manager_cache_key(tmp_path, {"extra_roots": ("src",)})
    diff_ignored = _manager_cache_key(tmp_path, {"ignored_dirs": frozenset({"build"})})

    # Empty kwargs and explicit defaults collapse to the same key (one manager).
    assert base == same
    # A different config yields a different key (a fresh manager).
    assert diff_roots != base
    assert diff_ignored != base
    assert diff_roots != diff_ignored


def test_manager_for_reuses_or_evicts_by_config(tmp_path: Path):
    from archy.mcp import _MANAGERS, _manager_for

    created = []

    def make(**kwargs):
        # Track every manager the moment it is created so a mid-test failure
        # still tears down its watcher + connection in the finally block.
        manager = _manager_for(tmp_path, **kwargs)
        created.append(manager)
        return manager

    try:
        m1 = make(extra_roots=("src",))
        m1_again = make(extra_roots=("src",))
        # Same config -> same cached manager (no new watcher/connection).
        assert m1 is m1_again

        m2 = make(extra_roots=("lib",))
        # Different config -> distinct manager, and the superseded one is evicted
        # so a root never accumulates managers (one live config at a time).
        # Scope the count to this root: other tests leak managers into the
        # module-global for other roots.
        assert m2 is not m1
        root_key = str(tmp_path.resolve())
        same_root = [k for k in _MANAGERS if k[0] == root_key]
        assert len(same_root) == 1
        assert _MANAGERS[same_root[0]] is m2
    finally:
        for manager in set(created):
            manager.stop()
        _MANAGERS.clear()


def test_refactor_structural_lens_ranks_central_volatile_first(tmp_path: Path):
    # v0.36 (#227): the structural lens replaces archy_high_risk_modules. `pkg.hub`
    # is imported by three peers (high fan-in) AND itself imports a downstream dep
    # (non-zero instability), so it dominates the edit-risk composite. min_risk=0
    # restores the old high-risk behavior (no floor).
    _hub_project(tmp_path, spokes=3)

    payload = _run_what_to_refactor_next(
        tmp_path, lens="structural", top_n=5, since=None, min_risk=0.0
    )
    top = payload.priorities[0]
    assert top.module == "pkg.hub"
    assert top.lenses == ("edit_risk",)
    assert 0.0 < top.edit_risk <= 1.0
    assert top.fan_in == 3
    assert top.instability > 0.0
    assert top.propagation_cost > 0.0


def test_refactor_structural_lens_skips_git(tmp_path: Path):
    # The structural lens never consults git, so it works (and reports
    # git_available=False) even with no repository present.
    _hub_project(tmp_path, spokes=1)

    payload = _run_what_to_refactor_next(
        tmp_path, lens="structural", top_n=5, since="2025-01-01", min_risk=0.0
    )
    assert payload.git_available is False
    assert payload.priorities
    assert all(e.lenses == ("edit_risk",) for e in payload.priorities)


def test_refactor_structural_lens_top_n_caps_results(tmp_path: Path):
    _hub_project(tmp_path, spokes=4)

    payload = _run_what_to_refactor_next(
        tmp_path, lens="structural", top_n=2, since=None, min_risk=0.0
    )
    assert len(payload.priorities) <= 2
    # total reports the size of the candidate pool, not the slice.
    assert payload.total >= len(payload.priorities)


def test_graph_tool_focus_routes_to_subgraph(acyclic_project: Path):
    # v0.36 (#227): archy_graph(focus=...) replaces archy_graph_focus. With focus
    # set the tool returns a bounded GraphPayload subgraph, ignoring
    # response_format/max_nodes/top_n.
    payload = _run_graph(
        acyclic_project,
        response_format="summary",
        focus=["pkg.a"],
        depth=1,
        direction="both",
        internal_only=True,
        max_nodes=500,
        top_n=20,
    )
    assert isinstance(payload, GraphPayload)
    ids = {n.id for n in payload.nodes}
    assert {"pkg.a", "pkg.b"} <= ids


def test_graph_tool_focus_still_validates_response_format(acyclic_project: Path):
    # response_format is validated up front even when focus short-circuits the
    # summary/dump branches, so a bad enum value still raises.
    with pytest.raises(ValueError, match="response_format must be"):
        _run_graph(
            acyclic_project,
            response_format="xml",
            focus=["pkg.a"],
            depth=1,
            direction="both",
            internal_only=True,
            max_nodes=500,
            top_n=20,
        )


def test_impact_mode_blast_returns_chains(acyclic_project: Path):
    # mode='blast' (the default) returns the Impact shape with chains.
    result = _run_impact(acyclic_project, files=[Path("pkg/b.py")])
    from archy.impact import Impact

    assert isinstance(result, Impact)


def test_impact_mode_affected_returns_test_split(tmp_path: Path):
    # v0.36 (#227): archy_impact(mode='affected') replaces archy_affected and
    # returns the CI-shaped tests/modules split.
    from archy.affected import Affected

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "b.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("from pkg.a import thing\n")

    result = _run_affected(tmp_path, files=[Path("pkg/a.py")], depth=5, test_filter=None)
    assert isinstance(result, Affected)
    assert "pkg.tests.test_a" in set(result.impacted_tests) or any(
        "test_a" in t for t in result.impacted_tests
    )


def test_impact_tool_validates_mode(acyclic_project: Path):
    from archy.mcp_compat import ToolError

    server = create_server()
    with pytest.raises(ToolError):
        _call_tool(
            server,
            "archy_impact",
            {"path": str(acyclic_project), "files": ["pkg/a.py"], "mode": "sideways"},
        )


def test_refactor_validates_lens(acyclic_project: Path):
    with pytest.raises(ValueError, match="lens must be"):
        _run_what_to_refactor_next(
            acyclic_project, lens="sideways", top_n=5, since=None, min_risk=0.15
        )


def test_graph_focus_preserves_edge_attributes(tmp_path: Path):
    # Spec contract: agents use edge `lines` to pinpoint import sites and
    # `is_relative` to distinguish `from .x import y` from absolute imports.
    # Both must survive subgraph extraction.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("\nfrom pkg.b import x\nfrom .b import y\n")
    (pkg / "b.py").write_text("")

    payload = _run_graph_focus(
        tmp_path,
        modules=["pkg.a"],
        depth=1,
        direction="out",
        internal_only=True,
    )
    edge = next(e for e in payload.edges if e.source == "pkg.a" and e.target == "pkg.b")
    assert edge.lines == (2, 3)
    # `is_relative` is True iff *any* of the contributing imports was relative;
    # the parser records the last-seen flag, so the assertion is just "tracked".
    assert edge.is_relative in (True, False)
    assert isinstance(edge.is_relative, bool)


def test_graph_focus_internal_only_false_keeps_external_neighbors(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import requests\n")

    payload = _run_graph_focus(
        tmp_path,
        modules=["pkg.a"],
        depth=1,
        direction="out",
        internal_only=False,
    )
    ids = {n.id for n in payload.nodes}
    assert "requests" in ids
    external_nodes = [n for n in payload.nodes if n.external]
    # External nodes have neither a filesystem path nor instability.
    assert all(n.path is None and n.instability is None for n in external_nodes)


def test_graph_focus_multi_seed_with_overlap_dedups_nodes(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.shared import x\n")
    (pkg / "b.py").write_text("from pkg.shared import y\n")
    (pkg / "shared.py").write_text("")

    payload = _run_graph_focus(
        tmp_path,
        modules=["pkg.a", "pkg.b"],
        depth=1,
        direction="out",
        internal_only=True,
    )
    ids = [n.id for n in payload.nodes]
    # pkg.shared is reachable from both seeds; must appear exactly once.
    assert ids.count("pkg.shared") == 1


def test_graph_dump_at_max_nodes_boundary_succeeds(acyclic_project: Path):
    # `> max_nodes` errors; `== max_nodes` must succeed. This guards the
    # off-by-one risk in the guardrail condition.
    exact = _internal_graph(acyclic_project).number_of_nodes()

    payload = _run_graph_dump(acyclic_project, internal_only=True, max_nodes=exact)
    assert isinstance(payload, GraphPayload)
    assert len(payload.nodes) == exact


def test_graph_summary_empty_project_does_not_divide_by_zero(tmp_path: Path):
    # No packages, no edges. Summary must return zeros instead of crashing on
    # `1 / n` in PageRank or instability.
    (tmp_path / "not_a_package.py").write_text("x = 1\n")
    summary = _run_graph_summary(tmp_path, top_n=5)
    assert summary.module_count == 0
    assert summary.internal_edge_count == 0
    assert summary.top_pagerank == ()
    assert summary.external_deps == ()


def test_graph_summary_single_node_assigns_full_pagerank_mass(tmp_path: Path):
    # One node, no edges. PageRank invariant: the unique node holds the entire
    # probability mass (the teleport + dangling-redistribution path).
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "lone.py").write_text("")

    summary = _run_graph_summary(tmp_path, top_n=5)
    pr = {e.module: e.value for e in summary.top_pagerank}
    # Two internal nodes (pkg, pkg.lone) -> 0.5 each at the fixed point.
    assert pr["pkg.lone"] == pytest.approx(0.5, abs=1e-6)
    assert sum(pr.values()) == pytest.approx(1.0, abs=1e-6)


def test_pagerank_matches_networkx_when_available(tmp_path: Path):
    # Numpy parity test: when numpy is present, nx.pagerank works and must
    # match our hand-rolled implementation to within power-iteration tolerance.
    # Gated by importorskip so the package install stays numpy-free.
    np = pytest.importorskip("numpy")  # noqa: F841
    import networkx as nx

    from archy.mcp import _pagerank

    # Hand-built graph: a small DAG with a hub, plus a dangling sink. Covers
    # the dangling-redistribution branch and a non-trivial ranking.
    g: nx.DiGraph = nx.DiGraph()
    g.add_edges_from(
        [
            ("a", "hub"),
            ("b", "hub"),
            ("c", "hub"),
            ("hub", "sink"),
            ("a", "b"),
        ]
    )
    ours = _pagerank(g)
    theirs = nx.pagerank(g, alpha=0.85, tol=1e-6, max_iter=100)
    assert set(ours) == set(theirs)
    for node in theirs:
        assert ours[node] == pytest.approx(theirs[node], abs=1e-4)


def test_pagerank_converges_early_on_stable_graph():
    # The early-return branch (delta < tol) is hit on a graph that's already
    # at its fixed point: a complete graph with uniform weights is stationary
    # under PageRank, so iteration 1 should already be within tol of iteration 0.
    import networkx as nx

    from archy.mcp import _pagerank

    g: nx.DiGraph = nx.DiGraph()
    g.add_edges_from([(u, v) for u in "abc" for v in "abc" if u != v])
    # All nodes symmetric -> uniform pagerank 1/3 each.
    pr = _pagerank(g, tol=1e-6)
    assert pr["a"] == pytest.approx(1 / 3, abs=1e-6)
    assert pr["b"] == pytest.approx(1 / 3, abs=1e-6)
    assert pr["c"] == pytest.approx(1 / 3, abs=1e-6)


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
    assert _run_cycles(tmp_path, min_size=2, internal_only=True) == []


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_git_repo(repo: Path) -> None:
    # Bare git init + the three configs every hotspot test needs (identity
    # so commits don't fail, and gpgsign=false so the CI runner without a
    # signing key still completes). Shared by every test in this file that
    # touches git history.
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _init_hotspot_repo(repo: Path) -> None:
    _init_git_repo(repo)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hot.py").write_text(
        "def f(x):\n"
        "    if x:\n"
        "        return 1\n"
        "    elif x == 2:\n"
        "        return 2\n"
        "    for y in range(x):\n"
        "        if y: pass\n"
        "    return 0\n"
    )
    (pkg / "cold.py").write_text("def g():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (pkg / "hot.py").write_text((pkg / "hot.py").read_text() + "\n# tweak\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "touch hot")


def test_refactor_behavioral_lens_shape_and_ranking(tmp_path: Path):
    # v0.36 (#227): the behavioral lens replaces archy_hotspots. It surfaces only
    # CC x churn hotspots, ranked by hotspot_score (== cc_sum * churn).
    _init_hotspot_repo(tmp_path)
    payload = _run_what_to_refactor_next(
        tmp_path, lens="behavioral", top_n=20, since=None, min_risk=0.0
    )
    assert payload.note is None
    assert payload.since is None
    assert payload.total >= 1
    assert payload.shown == len(payload.priorities)
    top = payload.priorities[0]
    assert top.module == "pkg.hot"
    # The behavioral lens keeps only rows the hotspot lens fired on (a row may
    # also carry edit_risk when it clears the structural floor too).
    assert "hotspot" in top.lenses
    assert top.hotspot_score == top.cc_sum * top.churn
    assert top.path is not None
    assert top.path.endswith("pkg/hot.py")


def test_refactor_behavioral_lens_top_n_caps_results(tmp_path: Path):
    _init_hotspot_repo(tmp_path)
    payload = _run_what_to_refactor_next(
        tmp_path, lens="behavioral", top_n=1, since=None, min_risk=0.0
    )
    assert payload.shown <= 1
    # total reports the size of the candidate pool, not the slice.
    assert payload.total >= payload.shown


def test_refactor_validates_top_n(tmp_path: Path):
    with pytest.raises(ValueError, match="top_n"):
        _run_what_to_refactor_next(tmp_path, lens="behavioral", top_n=0, since=None, min_risk=0.0)


def test_refactor_behavioral_lens_diagnostic_when_not_in_git_repo(tmp_path: Path):
    # Same Python project shape but no `git init` -> the behavioral lens must NOT
    # raise. The agent reads `note` and pivots to lens='structural' instead.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("def f():\n    if True: return 1\n")
    payload = _run_what_to_refactor_next(
        tmp_path, lens="behavioral", top_n=20, since=None, min_risk=0.0
    )
    assert payload.priorities == ()
    assert payload.total == 0
    assert payload.note is not None
    assert "not inside a git repository" in payload.note
    assert "lens='structural'" in payload.note


def test_what_to_refactor_next_fuses_both_lenses(tmp_path: Path):
    # `pkg.hot` is churned+complex (hotspot) and, by being imported by peers
    # while importing a dep, also central+fragile (edit-risk). It should rank
    # first and report both lenses fired.
    _init_git_repo(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "dep.py").write_text("")
    (pkg / "hot.py").write_text(
        "from pkg.dep import thing\n\n"
        "def f(x):\n    if x: return 1\n    elif x == 2: return 2\n    return 0\n"
    )
    (pkg / "a.py").write_text("from pkg.hot import f\n")
    (pkg / "b.py").write_text("from pkg.hot import f\n")
    (pkg / "c.py").write_text("from pkg.hot import f\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    (pkg / "hot.py").write_text((pkg / "hot.py").read_text() + "\n# tweak\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "touch hot")

    payload = _run_what_to_refactor_next(tmp_path, top_n=5, since=None, min_risk=0.1)
    assert payload.git_available is True
    assert payload.note is None
    assert payload.shown == len(payload.priorities)
    top = payload.priorities[0]
    assert top.module == "pkg.hot"
    assert top.lenses == ("hotspot", "edit_risk")
    assert "Both a complexity" in top.rationale


def test_what_to_refactor_next_structural_only_without_git(tmp_path: Path):
    # No git -> behavioral lens skipped, ranking is structural-only, and a note
    # explains the degraded mode. The list is still populated structurally.
    _hub_project(tmp_path, spokes=3)

    payload = _run_what_to_refactor_next(tmp_path, top_n=5, since=None, min_risk=0.1)
    assert payload.git_available is False
    assert payload.priorities
    assert all(e.lenses == ("edit_risk",) for e in payload.priorities)
    assert payload.note is not None
    assert "structural-only" in payload.note


def test_what_to_refactor_next_honest_null(tmp_path: Path):
    # Git present but the floor excludes every module and there is no churn yet
    # (single commit, files unchanged since) -> a real "nothing to prioritize"
    # answer with an explanatory note, not a manufactured #1.
    _init_git_repo(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")

    payload = _run_what_to_refactor_next(tmp_path, top_n=5, since=None, min_risk=1.0)
    assert payload.priorities == ()
    assert payload.total == 0
    assert payload.note is not None
    assert "nothing to prioritize" in payload.note


def test_what_to_refactor_next_validates_args(acyclic_project: Path):
    with pytest.raises(ValueError, match="top_n"):
        _run_what_to_refactor_next(acyclic_project, top_n=0, since=None, min_risk=0.15)
    with pytest.raises(ValueError, match="min_risk"):
        _run_what_to_refactor_next(acyclic_project, top_n=5, since=None, min_risk=1.5)


def test_behavioral_lens_since_propagates_to_git_churn(tmp_path: Path):
    # Verifies the `since` arg is actually plumbed into `git_churn`, not
    # silently dropped. Regression guard: if someone refactored the behavioral
    # lens and forgot to forward `since`, every other test in this file would
    # still pass.
    import os
    import subprocess

    repo = tmp_path
    _init_git_repo(repo)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # Two branchy files (cc_sum > 0 each) so both are eligible for the
    # top-K under full history.
    (pkg / "hot.py").write_text("def f(x):\n    if x: return 1\n    return 0\n")
    (pkg / "old.py").write_text("def g(x):\n    if x: return 1\n    return 0\n")

    old_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
        "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
    }
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "old init"],
        check=True,
        capture_output=True,
        env=old_env,
    )
    # Touch `hot.py` again in a "recent" commit so the since filter keeps it
    # but drops `old.py`, which only appears in the 2020 commit.
    (pkg / "hot.py").write_text((pkg / "hot.py").read_text() + "\n# tweak\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "recent tweak hot")

    full = _run_what_to_refactor_next(repo, lens="behavioral", top_n=20, since=None, min_risk=0.0)
    full_modules = {h.module for h in full.priorities}
    assert "pkg.hot" in full_modules
    assert "pkg.old" in full_modules
    hot_full = next(h for h in full.priorities if h.module == "pkg.hot")
    old_full = next(h for h in full.priorities if h.module == "pkg.old")
    assert hot_full.churn == 2
    assert old_full.churn == 1

    filtered = _run_what_to_refactor_next(
        repo, lens="behavioral", top_n=20, since="2025-01-01", min_risk=0.0
    )
    assert filtered.since == "2025-01-01"
    filtered_modules = {h.module for h in filtered.priorities}
    # `old.py` was last touched in 2020 -> dropped (churn=0 -> filtered).
    # `hot.py` still has the 2026-tweak commit -> kept with churn=1.
    assert "pkg.old" not in filtered_modules
    hot_filtered = next(h for h in filtered.priorities if h.module == "pkg.hot")
    assert hot_filtered.churn == 1


# --- scan-size guard (#216) ---------------------------------------------------


def test_mcp_load_graph_trips_scan_size_guard(tmp_path: Path):
    from archy.graph import ScanTooLargeError
    from archy.mcp import _load_graph

    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(5):
        (pkg / f"mod{i}.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    # The guard fires inside `_manager_for` before the watcher is scheduled.
    with pytest.raises(ScanTooLargeError):
        _load_graph(tmp_path, internal_only=True)


def test_conventions_tool_returns_the_derived_house_style(acyclic_project: Path):
    # The wire contract an agent reads BEFORE naming a new class: the family,
    # its home module, and the derived `concentration` that says how much to
    # trust that home. `concentration` is a `computed_field` precisely because
    # `model_dump()` drops plain properties, so its presence here is the
    # regression test for that trap.
    (acyclic_project / "pkg" / "layers.py").write_text(
        "class Violation: pass\nclass ReachViolation: pass\n"
    )
    server = create_server()
    _content, structured = _call_tool(server, "archy_conventions", {"path": str(acyclic_project)})
    assert isinstance(structured, dict)
    home = next(h for h in structured["naming"] if h["module"] == "pkg.layers")
    family = next(f for f in home["families"] if f["suffix"] == "Violation")
    assert family["home_module"] == "pkg.layers"
    assert family["concentration"] == 1.0
    assert home["total"] == 2
    # Both exit channels are on the wire, separately: an agent asking "should
    # my finding gate?" must not be handed a count of bad-input exits.
    assert structured["gates"] == []
    assert structured["errors"] == []
    assert "gate_modules" in structured
    assert "gate_codes" in structured
    assert structured["models"]["value_classes"] == 0


def test_conventions_tool_threads_include_tests_to_the_census(acyclic_project: Path):
    # The parameter has to reach the surface agents actually call. A flag wired
    # to the CLI alone is this repo's most-repeated defect (AGENTS.md), and it
    # is invisible from the CLI side, so the round trip is asserted here.
    (acyclic_project / "pkg" / "test_thing.py").write_text("class FixtureViolation: pass\n")
    server = create_server()
    _c, default = _call_tool(server, "archy_conventions", {"path": str(acyclic_project)})
    _c, with_tests = _call_tool(
        server, "archy_conventions", {"path": str(acyclic_project), "include_tests": True}
    )
    assert isinstance(default, dict) and isinstance(with_tests, dict)
    assert default["partition"]["tests"] == 1
    assert with_tests["partition"]["tests"] == 0
    assert with_tests["modules_scanned"] > default["modules_scanned"]


def test_conventions_tool_answers_a_negative_for_one_module(acyclic_project: Path):
    # The lookup has to reach the surface agents actually call. Shipped to the
    # CLI alone it would be unreachable from MCP, which is this repo's
    # most-repeated defect (AGENTS.md), and the point of the view is answering
    # in the NEGATIVE: the ranked report truncates, so absence from it proves
    # nothing, while absence from this COMPLETE list is the answer.
    (acyclic_project / "pkg" / "risk.py").write_text("from pkg.instability import inst\n")
    (acyclic_project / "pkg" / "instability.py").write_text("def inst(): ...\n")
    (acyclic_project / "pkg" / "hotspots.py").write_text("def hot(): ...\n")
    server = create_server()
    _c, view = _call_tool(
        server, "archy_module_view", {"path": str(acyclic_project), "module": "pkg.risk"}
    )
    assert isinstance(view, dict)
    assert view["module"] == "pkg.risk"
    assert view["status"] == "censused"
    assert view["imports_internal"] == ["pkg.instability"]
    assert "pkg.hotspots" not in view["imports_internal"]


def test_conventions_tool_rejects_an_unknown_module(acyclic_project: Path):
    # A verdict without a reason is not actionable: an unknown name must fail
    # loudly rather than return an empty view that reads as "imports nothing".
    from archy.mcp_compat import ToolError

    server = create_server()
    with pytest.raises(ToolError):
        _call_tool(
            server,
            "archy_module_view",
            {"path": str(acyclic_project), "module": "pkg.nope"},
        )


def test_conventions_tool_rejects_a_nonsense_min_family(acyclic_project: Path):
    # Tier-2 usage error: an invalid argument value raises rather than
    # returning a confusing empty report (#229 error model).
    from archy.mcp_compat import ToolError

    server = create_server()
    with pytest.raises(ToolError):
        _call_tool(
            server,
            "archy_conventions",
            {"path": str(acyclic_project), "min_family": 1},
        )


def test_check_payload_carries_the_exact_pattern_hint(tmp_path: Path):
    """Part 2 on the agent-facing surface, asserted on the SERIALIZED form.

    `model_dump()` drops properties, and an agent reading `passed=true` with an
    empty `violations` list has no other way to learn that the config governs
    nothing and why.
    """
    from archy.mcp import CheckPayload, _run_check

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

    payload = _run_check(tmp_path, config_path=None)

    assert isinstance(payload, CheckPayload)
    assert payload.violations == ()
    dumped = payload.model_dump()
    assert dumped["coverage"]["governs_no_edges"] is True
    hints = {h["layer"]: h for h in dumped["coverage"]["exact_pattern_hints"]}
    assert hints["store"]["pattern"] == "app.store"
    assert hints["store"]["suggestion"] == "app.store.**"
    assert "app.store.repository" in hints["store"]["unlayered_descendants"]


def _transitive_project(tmp_path: Path) -> Path:
    """`store -> common -> api`: legal on every direct edge, illegal transitively."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "api.py").write_text("x = 1\n")
    (tmp_path / "app" / "common.py").write_text("from app import api\n")
    (tmp_path / "app" / "store.py").write_text("from app import common\n")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  api:\n    modules: ['app.api']\n"
        "  common:\n    modules: ['app.common']\n"
        "  store:\n    modules: ['app.store']\n"
        "forbid:\n  - {from: store, to: api}\n"
    )
    return tmp_path


def test_check_says_it_only_saw_direct_edges(tmp_path: Path):
    """#343: `passed=true` must not be silent about what it looked at.

    `archy check` sees DIRECT edges, so `store -> common -> api` passes it and
    fails `contracts`. The CLI got the qualified verdict in v0.45/v0.46 and this
    surface did not, leaving an agent unable to tell "checked transitively and
    clean" from "never looked" on exactly the case archy claims as its
    differentiator.
    """
    from archy.mcp import CheckPayload, _run_check

    payload = _run_check(_transitive_project(tmp_path), config_path=None)

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
    from archy.mcp import CheckPayload, _run_check

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "api.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text("layers:\n  api:\n    modules: ['app.api']\n")

    payload = _run_check(tmp_path, config_path=None)

    assert isinstance(payload, CheckPayload)
    assert payload.model_dump()["transitive_unverified_reason"] is None


def test_check_transitive_checked_needs_a_verdict_not_a_request(tmp_path: Path, monkeypatch):
    """Asking for contracts and having import-linter turn out to be missing
    leaves the rules exactly as unverified as not asking, so
    transitive_checked=True there would reintroduce the bug the field closes.

    The REASON has to move with the flag. `_run_check` computes its reason before
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
