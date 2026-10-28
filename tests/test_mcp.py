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
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Annotated, cast, get_args, get_origin, get_type_hints

import pytest
from pydantic import BaseModel

from archy.diff import DiffReport, take_snapshot, write_snapshot
from archy.dsm import DSM, DSMSummary
from archy.graph import build_graph, internal_subgraph
from archy.layers import LayerConfigError
from archy.mcp import (
    CheckErrorPayload,
    CheckPayload,
    ContractsPayload,
    DiffErrorPayload,
    DSMErrorPayload,
    DSMTooLargePayload,
    ForbiddenEdge,
    GraphPayload,
    GraphSummaryPayload,
    GraphTooLargePayload,
    _advertise_serialization_output_schemas,
    _run_affected,
    _run_brief,
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
from archy.scan_config import graph_kwargs
from archy.simulate import EdgeSpec, SimulateReport


def _internal_graph(root: Path):
    # Build the same internal-only graph the MCP layer hands to callers,
    # for parity assertions against graph_to_dict.
    g = build_graph(root)
    external = {n for n, d in g.nodes(data=True) if d.get("external")}
    g.remove_nodes_from(external)
    return g


def _list_tools(server):
    return asyncio.run(server.list_tools())


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


def test_all_tools_declare_human_friendly_title():
    # The 2025-06-18 revision added a display `title` so `name` stays a
    # programmatic id. Assert every tool sets a non-empty title distinct from
    # its name.
    server = create_server()
    tools = _list_tools(server)
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
    tools = _list_tools(server)
    for tool in tools:
        schema = _output_schema(tool)
        assert schema is not None, f"{tool.name} has no outputSchema"
        assert schema.get("type") == "object", f"{tool.name} outputSchema is not an object"


def _return_models(fn) -> set[type[BaseModel]]:
    found: set[type[BaseModel]] = set()

    def walk(tp: object) -> None:
        if tp is None or tp is type(None):
            return
        origin = get_origin(tp)
        if origin is Annotated:
            walk(get_args(tp)[0])
            return
        if origin is not None:
            for arg in get_args(tp):
                walk(arg)
            return
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            if tp in found:
                return
            found.add(tp)
            for field in tp.model_fields.values():
                walk(field.annotation)

    walk(get_type_hints(fn).get("return"))
    return found


def _as_object_dict(node: object) -> dict[str, object] | None:
    if isinstance(node, dict):
        return cast("dict[str, object]", node)
    return None


def _schema_properties_named(schema: dict[str, object], model_name: str) -> set[str]:
    """Property names of every schema object titled or `$defs`-keyed as `model_name`."""
    found: set[str] = set()

    def walk(node: object) -> None:
        mapping = _as_object_dict(node)
        if mapping is None:
            if isinstance(node, list):
                for value in node:
                    walk(value)
            return
        defs = _as_object_dict(mapping.get("$defs"))
        if defs is not None:
            sub = _as_object_dict(defs.get(model_name))
            props = _as_object_dict(None if sub is None else sub.get("properties"))
            if props is not None:
                found.update(props)
        titled = _as_object_dict(mapping.get("properties"))
        if mapping.get("title") == model_name and titled is not None:
            found.update(titled)
        for value in mapping.values():
            walk(value)

    walk(schema)
    return found


def test_output_schema_advertises_every_computed_field():
    server = create_server()
    schemas = {tool.name: _output_schema(tool) for tool in asyncio.run(server.list_tools())}
    seen: set[tuple[str, str]] = set()
    missing: list[str] = []
    for tool in server._tool_manager.list_tools():
        schema = schemas[tool.name]
        assert schema is not None, tool.name
        for model in _return_models(tool.fn):
            props = _schema_properties_named(schema, model.__name__)
            for field_name in model.model_computed_fields:
                seen.add((model.__name__, field_name))
                if field_name not in props:
                    missing.append(f"{tool.name} {model.__name__}.{field_name}")

    assert ("Impact", "empty_caveat") in seen
    assert ("Affected", "empty_caveat") in seen
    assert ("StrictVerdict", "passed") in seen
    assert ("StrictVerdict", "reason") in seen
    assert ("ConventionsReport", "gate_modules") in seen
    assert ("LayerCoverage", "layers_present") in seen
    assert missing == []


def test_serialization_schema_replaces_a_cached_validation_schema():
    server = create_server()
    tool = next(item for item in server._tool_manager.list_tools() if item.name == "archy_impact")
    tool.__dict__["output_schema"] = {"type": "object", "properties": {}}
    _advertise_serialization_output_schemas(server)
    listed = next(item for item in asyncio.run(server.list_tools()) if item.name == "archy_impact")
    schema = _output_schema(listed)
    assert schema is not None
    assert "empty_caveat" in _schema_properties_named(schema, "Impact")


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
        ("archy_brief", {}, True),  # BaseModel return (BriefPayload)
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


# Extra arguments each tool needs besides `path`. The set must match the
# registered tools: a new tool that forgets `_project_dir` has to land here
# or `test_project_path_guard_covers_every_registered_tool` fails, and then
# the parametrized call below fails if the guard is not actually used.
_PROJECT_PATH_ARGS: dict[str, dict] = {
    "archy_score": {},
    "archy_cycles": {},
    "archy_check": {},
    "archy_impact": {"files": ["pkg/a.py"]},
    "archy_snapshot": {},
    "archy_diff": {},
    "archy_graph": {},
    "archy_what_to_refactor_next": {},
    "archy_dsm": {},
    "archy_simulate": {},
    "archy_duplicates": {},
    "archy_conventions": {},
    "archy_module_view": {"module": "pkg.a"},
    "archy_brief": {},
}


@pytest.mark.parametrize("name", sorted(_PROJECT_PATH_ARGS))
def test_mcp_tool_rejects_a_nonexistent_project_path(tmp_path: Path, name: str):
    """A typo path fails the call and is not created on disk (#552).

    The fixture is absent before the call. The CLI already rejects that with
    click.Path(exists=True, file_okay=False). These tools used to pass the
    string through, and open_index's mkdir(parents=True) created the path and
    `.archy/index.db`, then scored the empty tree as 1.0.
    """
    missing = tmp_path / "no-such-project"
    assert not missing.exists()

    from archy.mcp_compat import ToolError

    # mcp 2.x wraps the ValueError as ToolError("Error executing tool <name>")
    # without the underlying message; 1.x keeps it. Assert the raise + no mkdir.
    with pytest.raises(ToolError):
        _call_tool(
            create_server(),
            name,
            {"path": str(missing), **_PROJECT_PATH_ARGS[name]},
        )

    assert not missing.exists()
    assert not (missing / ".archy" / "index.db").exists()


def test_project_path_guard_covers_every_registered_tool():
    server = create_server()
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert set(_PROJECT_PATH_ARGS) == names


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
    # Exactly `fan` non-zero DSM cells and no cycles, so the oversized-matrix
    # guard tests have a predictable cell count to trip against.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    leaves = "".join(f"from pkg.m{i} import x\n" for i in range(fan))
    (pkg / "a.py").write_text(leaves)
    for i in range(fan):
        (pkg / f"m{i}.py").write_text("")
    return tmp_path


def _myapp_core_cli(tmp_path: Path, *, api: str = "", runner: str = "") -> Path:
    """A `myapp` package with `core.api` and `cli.runner`, returning the root.

    The three call sites differ only in which leaf imports the other, so that
    is the parameter; everything else was copied three times (#411).
    """
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for sub in ("core", "cli"):
        (pkg / sub).mkdir()
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "core" / "api.py").write_text(api)
    (pkg / "cli" / "runner.py").write_text(runner)
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
    # Pin the counts the fixture actually produces: `pkg`, `pkg.a` and `pkg.b`
    # fall into two communities. `group_count == len(groups)` held for any
    # graph, since `summarize_dsm` sets both from one iterable (#441).
    assert result.module_count == 3
    assert [(g.label, g.size) for g in result.groups] == [("Community-0", 2), ("Community-1", 1)]
    assert result.group_count == 2


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


def test_dsm_bad_baseline_returns_error_on_the_wire(acyclic_project: Path, tmp_path: Path):
    baseline = tmp_path / "bad.json"
    baseline.write_text("not json")
    result = _dsm(acyclic_project, baseline_path=str(baseline))

    assert isinstance(result, DSMErrorPayload)
    assert result.model_dump() == {
        "error": (
            f"{baseline} is not a DSM snapshot (write one with `archy dsm --format json`): "
            "Expecting value: line 1 column 1 (char 0)"
        )
    }


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


def _seed_score_history(project: Path, overall: float) -> None:
    """Write one history row so the strict gate has a previous overall to compare."""
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
            "complexity": 1.0,
        },
        "inputs": {
            "module_count": 3,
            "edge_count": 1,
            "cycle_count": 0,
            "tangle_ratio": 0.0,
            "max_depth": 1,
            "community_count": 2,
        },
    }
    history.write_text(json.dumps(row) + "\n")


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
            _run_score(
                acyclic_project,
                internal_only=True,
                record=False,
                strict=strict,
                strict_tolerance=tolerance,
            )
        assert str(caught.value) == message
        # mcp 2.x ToolError is "Error executing tool archy_score" without the
        # underlying ValueError text; 1.x keeps it. The exact message is pinned
        # on the direct _run_score raise above.
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
    _myapp_core_cli(tmp_path, api="from myapp.cli.runner import go\n")
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
    result = _run_check(project, config_path=None)
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


def test_check_contracts_reports_an_unfalsifiable_contract_as_unverified(tmp_path: Path):
    """The surface agents actually call. A contract whose pattern matches no
    module is `kept` by import-linter, so an agent reading `all_kept` concludes
    it is protected; `verified` is the field that says otherwise (#435)."""
    pkg = tmp_path / "top"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "api.py").write_text("VALUE = 1\n")
    (pkg / "store.py").write_text("from top import api\n")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        '  api:\n    modules: ["top.api.**"]\n'
        '  store:\n    modules: ["top.*.handlers"]\n'
        "forbid:\n  - {from: store, to: api}\n"
    )

    server = create_server()
    _c, wire = _call_tool(server, "archy_check", {"path": str(tmp_path), "contracts": True})

    assert isinstance(wire, dict)
    contracts = wire["result"]["contracts"]
    assert contracts["all_kept"] is True
    assert contracts["verified"] is False
    assert contracts["unverifiable"] == 1
    assert contracts["contracts"][0]["matched_nothing"] is True
    assert contracts["contracts"][0]["unmatched_expressions"] == ["top.*.handlers"]
    # #457: the run did not evaluate the rule, so it must not say it did. Same
    # rule as the CLI's `_transitive_checked`, and a verdict without a reason
    # is indistinguishable from a bug in archy.
    assert wire["result"]["transitive_checked"] is False
    assert "could not have failed" in wire["result"]["transitive_unverified_reason"]


def test_check_contracts_flag_nests_contract_results(tmp_path: Path):
    # #268: archy_check(contracts=True) folds the old archy_contracts tool,
    # nesting the transitive import-linter result under CheckPayload.contracts.
    # contracts=False (the default) leaves the field None, so a routine check
    # stays a pure direct-edge layer-rule result.
    _myapp_core_cli(tmp_path)
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
    # `all_kept` is also true of a contract that matched no module, so the
    # payload carries `verified` separately (#435). Here the contract really
    # was evaluated, so both are true and `unverifiable` is zero.
    assert result["contracts"]["verified"] is True
    assert result["contracts"]["unverifiable"] == 0
    # The other side of #457: a contract that really was evaluated still
    # reports the transitive check as done, so the flag is not just False.
    assert result["transitive_checked"] is True
    assert result["transitive_unverified_reason"] is None


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
    assert payload.score is not None
    assert payload.score.overall > 0
    assert (acyclic_project / ".archy" / "baseline.json").exists()


def test_snapshot_brief_mirrors_score_and_acyclic_invariant(acyclic_project: Path):
    """The brief must carry the numbers a caller would get by scoring the
    project themselves, not merely the numbers of the payload it was built from.

    `brief.overall == payload.score.overall` and
    `brief.acyclic is (payload.cycles == ())` are both direct copies made inside
    `_build_invariant_brief`, so they held whatever those numbers were, correct
    or not; the docstring conceded as much (#441). The right-hand side is
    derived independently here, by building the graph and calling
    `compute_score` directly, which is the shape a parity assertion needs.
    """
    from archy.score import compute_score

    payload = _run_snapshot(acyclic_project)
    brief = payload.invariant_brief
    independent = compute_score(build_graph(acyclic_project))

    assert brief.acyclic is True
    assert brief.overall == pytest.approx(independent.overall)
    assert brief.components.modularity == pytest.approx(independent.modularity)
    assert brief.components.complexity == pytest.approx(independent.complexity)


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
    _myapp_core_cli(tmp_path, runner="from myapp.core.api import go\n")
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
    [rule] = _run_snapshot(project).invariant_brief.model_dump()["required_reach"]
    assert rule["source"] == "@entrypoints"
    assert rule["within"] == "app.**"


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
    tools = _list_tools(server)
    tool = next(t for t in tools if t.name == "archy_simulate")
    edge_spec = tool.model_dump(by_alias=True)["inputSchema"]["$defs"]["EdgeSpec"]["properties"]
    # The wire contract the agent sees must be {from, to}, not {from_, to}.
    assert set(edge_spec) == {"from", "to"}


def test_run_diff_without_baseline_returns_error(acyclic_project: Path):
    result = _run_diff(acyclic_project)
    assert isinstance(result, DiffErrorPayload)
    assert "no baseline" in result.error


def test_run_diff_corrupt_baseline_returns_error_on_the_wire(acyclic_project: Path):
    target = acyclic_project / ".archy" / "baseline.json"
    target.parent.mkdir(parents=True)
    target.write_text("not json\n")

    dumped = _run_diff(acyclic_project).model_dump()

    assert dumped == {
        "error": (
            f"{target} is not a readable archy snapshot; re-run `archy snapshot`: "
            "invalid JSON (Expecting value: line 1 column 1 (char 0))"
        )
    }


def test_run_diff_after_snapshot_reports_zero_delta(acyclic_project: Path):
    _run_snapshot(acyclic_project)
    result = _run_diff(acyclic_project)
    assert isinstance(result, DiffReport)
    assert result.score_delta is not None
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
    """`mode='blast'` (the default) answers "what breaks if I edit this", and a
    chain is the "because": the import path from an impacted module back to the
    changed one.

    `pkg.a` does `from pkg.b import thing` on line 1, so editing `pkg/b.py`
    reaches `pkg.a` in exactly one hop. The old test asserted only
    `isinstance(result, Impact)`, which the `-> Impact` annotation guarantees on
    every return path, so an entirely empty `Impact` passed it (#441).
    """
    result = _run_impact(acyclic_project, files=[Path("pkg/b.py")])

    assert result.changed == ("pkg.b",)
    assert result.impacted == ("pkg.a",)
    assert result.chains_omitted == 0
    [chain] = result.chains
    assert chain.impacted == "pkg.a"
    assert chain.via == ("pkg.a", "pkg.b")
    assert [(h.source, h.target, h.lines) for h in chain.hops] == [("pkg.a", "pkg.b", (1,))]

    # The old comment claimed this covered `mode='blast'` routing, but
    # `_run_impact` has no `mode` parameter, so nothing here went through it.
    # Route through the tool so the claim the test makes is one it tests.
    _, structured = _call_tool(
        create_server(),
        "archy_impact",
        {"path": str(acyclic_project), "files": ["pkg/b.py"], "mode": "blast"},
    )
    assert structured is not None
    routed = structured["result"]
    assert routed["impacted"] == ["pkg.a"]
    assert routed["chains"][0]["via"] == ["pkg.a", "pkg.b"]


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
    (pkg / "d.py").write_text("from pkg.b import w\n")

    payload = _run_graph_focus(
        tmp_path,
        modules=["pkg.a", "pkg.d"],
        depth=1,
        direction="out",
        internal_only=True,
    )
    edges = {(e.source, e.target): e for e in payload.edges}

    a_b = edges[("pkg.a", "pkg.b")]
    # Two import sites on one pair, one of them relative, so `is_relative` is
    # True here by `any` (#450) and False on the all-absolute `pkg.d` pair.
    # Both values, not `in (True, False)`, which is true of any bool (#441).
    assert a_b.lines == (2, 3)
    assert a_b.is_relative is True
    assert edges[("pkg.d", "pkg.b")].is_relative is False


def test_graph_focus_edge_attributes_survive_model_dump(tmp_path: Path):
    """The serialized form is what FastMCP actually sends, and it is where a
    derived value silently goes missing (AGENTS.md). The flag an agent reads
    off the wire has to be the aggregated one, not the create-branch one."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("\nfrom pkg.b import x\nfrom .b import y\n")
    (pkg / "b.py").write_text("")

    payload = _run_graph_focus(
        tmp_path, modules=["pkg.a"], depth=1, direction="out", internal_only=True
    )
    wire = payload.model_dump()

    edge = next(e for e in wire["edges"] if (e["source"], e["target"]) == ("pkg.a", "pkg.b"))
    assert edge["lines"] == (2, 3)
    assert edge["is_relative"] is True


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


def test_archy_yaml_roots_plumbed_through_mcp(tmp_path: Path):
    # The MCP twin of test_cli's namespace-package roots test. Without it the
    # MCP path could drop `roots:` with every test still green (#395).
    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    (app / "libs" / "db.py").write_text("import os\n")
    (app / "main.py").write_text("from app.libs.db import x\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nroots: [app]\n")
    payload = _run_graph_dump(tmp_path, internal_only=True, max_nodes=100)
    assert isinstance(payload, GraphPayload)
    assert {"app.main", "app.libs.db"} <= {n.id for n in payload.nodes}


@pytest.mark.parametrize("error", [sqlite3.OperationalError("locked"), PermissionError("ro")])
def test_warm_paths_fall_back_cold_when_the_cache_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
):
    # The index is an optimization, never a dependency: a cache that cannot open
    # (read-only filesystem, locked database) must still yield an answer (#395).
    import archy.mcp as mcp_module

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg import b\n")
    (pkg / "b.py").write_text("")
    # Config must survive the fallback too: the cold path still honors exclude.
    (pkg / "generated").mkdir()
    (pkg / "generated" / "__init__.py").write_text("")
    (pkg / "generated" / "stub.py").write_text("")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nexclude: [generated]\n")

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr(mcp_module, "_manager_for", broken)
    kwargs = graph_kwargs(tmp_path)
    graph = mcp_module._build_graph(tmp_path, **kwargs)
    assert graph.has_edge("pkg.a", "pkg.b")
    assert "pkg.generated.stub" not in graph
    modules, _ = mcp_module._parse_project(tmp_path, **kwargs)
    qualnames = {m.qualname for m in modules}
    assert {"pkg.a", "pkg.b"} <= qualnames
    assert "pkg.generated.stub" not in qualnames


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    """Run one git command in `repo`.

    `env` exists for the tests that need to backdate a commit; passing None
    inherits, which is what every other caller wants.
    """
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


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
    # `shown` is `len(entries)` and `priorities` IS `entries`, so comparing them
    # was an identity of the constructor (#441). The fixture has exactly two
    # modules carrying functions, both under the top_n of 20, so both are shown.
    assert payload.total == 2
    assert payload.shown == 2
    assert [e.module for e in payload.priorities] == ["pkg.hot", "pkg.cold"]
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
    # Only `pkg.hot` clears both floors, so the fused ranking is one row long.
    # `shown == len(priorities)` was an identity of the constructor (#441).
    assert payload.total == 1
    assert payload.shown == 1
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
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "old init", env=old_env)
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


def test_mcp_check_trips_scan_size_guard(tmp_path: Path):
    # `_run_check` builds its scan arguments from the config it loaded, not via
    # `graph_kwargs`, so the guard needs pinning on this path separately.
    from archy.graph import ScanTooLargeError

    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(5):
        (pkg / f"mod{i}.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    with pytest.raises(ScanTooLargeError):
        _run_check(tmp_path, config_path=None)


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
    without = _run_check(tmp_path, config_path=None)
    assert isinstance(without, CheckPayload)
    assert without.violations == ()

    config.write_text(rules + "roots: [app]\n")
    with_roots = _run_check(tmp_path, config_path=None)
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
    without_exclude = _run_check(project, config_path=None)

    assert isinstance(without_exclude, CheckPayload)
    assert without_exclude.passed is False
    assert [(v.source, v.target) for v in without_exclude.violations] == [
        ("myapp.core.generated.bad", "myapp.cli.runner")
    ]

    config.write_text(rules + "exclude: [generated]\n")
    with_exclude = _run_check(project, config_path=None)

    assert isinstance(with_exclude, CheckPayload)
    assert with_exclude.passed is True
    assert with_exclude.violations == ()


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


def _write_cyclic_ratchet_project(tmp_path: Path) -> Path:
    """`pkg.a <-> pkg.b` plus acyclic `pkg.d`, `pkg.e`, with a committed
    findings-only baseline written at that state."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import pkg.b\n")
    (pkg / "b.py").write_text("import pkg.a\n")
    (pkg / "d.py").write_text("")
    (pkg / "e.py").write_text("")
    snap = take_snapshot(internal_subgraph(build_graph(tmp_path)))
    write_snapshot(snap, tmp_path / "archy-baseline.json", findings_only=True)
    return tmp_path


def _add_cycle_and_resnapshot(project: Path) -> None:
    """Add the `pkg.d <-> pkg.e` cycle, then re-snapshot as an agent told to
    snapshot at the start of each task would, which overwrites
    .archy/baseline.json with the new cycle already in it."""
    (project / "pkg" / "d.py").write_text("import pkg.e\n")
    (project / "pkg" / "e.py").write_text("import pkg.d\n")
    _run_snapshot(project)


def test_run_diff_strict_needs_a_committed_baseline(tmp_path: Path):
    project = _write_cyclic_ratchet_project(tmp_path)
    _run_snapshot(project)

    result = _run_diff(project, strict=True)

    assert isinstance(result, DiffErrorPayload)
    assert "strict=True needs `baseline`" in result.error


def test_run_diff_strict_is_not_grandfathered_by_a_resnapshot(tmp_path: Path):
    """The #521 constraint: an agent told to snapshot at the start of each
    task re-snapshots after adding a cycle. Strict reads the committed file,
    so the new cycle still fails and is named."""
    project = _write_cyclic_ratchet_project(tmp_path)
    _add_cycle_and_resnapshot(project)

    result = _run_diff(project, baseline="archy-baseline.json", strict=True)

    assert isinstance(result, DiffReport)
    dumped = result.model_dump()
    assert dumped["strict"]["passed"] is False
    assert dumped["strict"]["findings"][0]["modules"] == ("pkg.d", "pkg.e")
    assert "pkg.d -> pkg.e at" in dumped["strict"]["reason"]
    assert dumped["strict"]["baseline"] == "archy-baseline.json"


def test_run_diff_strict_passes_on_carried_findings(tmp_path: Path):
    project = _write_cyclic_ratchet_project(tmp_path)

    result = _run_diff(project, baseline="archy-baseline.json", strict=True)

    assert isinstance(result, DiffReport)
    assert result.model_dump()["strict"]["passed"] is True
    assert result.score_delta is None  # findings-only baseline: no score to compare


def test_run_diff_strict_refuses_the_default_snapshot_file(tmp_path: Path):
    """The agent being gated picks the arguments: naming .archy/baseline.json
    after a re-snapshot would otherwise pass a new cycle."""
    project = _write_cyclic_ratchet_project(tmp_path)
    _add_cycle_and_resnapshot(project)

    result = _run_diff(project, baseline=".archy/baseline.json", strict=True)

    assert isinstance(result, DiffErrorPayload)
    assert "the file `archy snapshot` overwrites" in result.error


def test_run_diff_missing_committed_baseline_says_it_resolves_against_path(tmp_path: Path):
    project = _write_cyclic_ratchet_project(tmp_path)

    result = _run_diff(project, baseline="nope.json", strict=True)

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

    result = _run_diff(project, baseline="archy-baseline.json", strict=True)

    assert isinstance(result, DiffReport)
    assert result.model_dump()["strict"]["passed"] is False
    assert "no archy.yaml was found" in result.model_dump()["strict"]["reason"]


def _write_brief_project(tmp_path: Path, *, with_config: bool) -> Path:
    """Two homes with two-member suffix families each, one unlayered module.

    Two naming homes so `top_n=1` has something to drop; the unlayered module
    and the forbid rule so a direct-edge pass cannot prove the rule holds.
    """
    pkg = tmp_path / "pkg"
    (pkg / "core").mkdir(parents=True)
    (pkg / "ui").mkdir()
    for d in (pkg, pkg / "core", pkg / "ui"):
        (d / "__init__.py").write_text("")
    (pkg / "core" / "errors.py").write_text("class FooError: ...\nclass BarError: ...\n")
    (pkg / "ui" / "handlers.py").write_text("class AHandler: ...\nclass BHandler: ...\n")
    (pkg / "other.py").write_text("x = 1\n")
    if with_config:
        (tmp_path / "archy.yaml").write_text(
            "layers:\n"
            "  core: {modules: [pkg.core.**]}\n"
            "  ui: {modules: [pkg.ui.**]}\n"
            "forbid:\n"
            "  - {from: core, to: ui}\n"
        )
    return tmp_path


def test_brief_bounds_ranked_sections_and_reports_what_it_dropped(tmp_path: Path):
    project = _write_brief_project(tmp_path, with_config=False)
    full = _run_brief(project, top_n=50, with_contracts=False, include_tests=False)
    # The fixture must exercise the truncation before the outcome means anything.
    assert full.conventions.totals["naming"] >= 2

    bounded = _run_brief(project, top_n=1, with_contracts=False, include_tests=False)
    assert len(bounded.conventions.naming) == 1
    assert bounded.conventions.top_n == 1
    assert bounded.conventions.totals == full.conventions.totals
    # Survives the wire form FastMCP sends.
    assert (
        bounded.model_dump()["conventions"]["totals"]["naming"] == full.conventions.totals["naming"]
    )


def test_brief_without_a_config_has_no_coverage_and_no_reason(tmp_path: Path):
    project = _write_brief_project(tmp_path, with_config=False)
    payload = _run_brief(project, top_n=8, with_contracts=False, include_tests=False)
    assert payload.coverage is None
    assert payload.transitive_checked is False
    assert payload.transitive_unverified_reason is None


def test_brief_says_why_an_unrun_forbid_rule_is_unverified(tmp_path: Path):
    project = _write_brief_project(tmp_path, with_config=True)
    payload = _run_brief(project, top_n=8, with_contracts=False, include_tests=False)
    assert payload.coverage is not None
    assert payload.transitive_checked is False
    dumped = payload.model_dump()
    assert "1 forbid rule declared" in dumped["transitive_unverified_reason"]
    assert "archy_brief(contracts=True)" in dumped["transitive_unverified_reason"]


@pytest.mark.parametrize(
    ("contracts", "checked", "reason_has"),
    [
        (ContractsPayload(available=True, error="boom"), False, "boom"),
        (ContractsPayload(available=True, verified=True, unverifiable=0), True, None),
        (ContractsPayload(available=True, verified=False, unverifiable=2), False, "2 contract(s)"),
    ],
)
def test_brief_reads_the_contracts_verdict(
    tmp_path: Path, monkeypatch, contracts: ContractsPayload, checked: bool, reason_has
):
    monkeypatch.setattr("archy.mcp._run_contracts", lambda *a, **k: contracts)
    project = _write_brief_project(tmp_path, with_config=True)
    payload = _run_brief(project, top_n=8, with_contracts=True, include_tests=False)
    assert payload.transitive_checked is checked
    if reason_has is None:
        assert payload.transitive_unverified_reason is None
    else:
        assert reason_has in (payload.transitive_unverified_reason or "")


def test_brief_payload_has_the_cli_json_field_names(tmp_path: Path):
    # Derived independently: the CLI hand-rolls its dict, the MCP side is a model.
    from click.testing import CliRunner

    from archy.cli import main

    project = _write_brief_project(tmp_path, with_config=True)
    cli = json.loads(CliRunner().invoke(main, ["brief", str(project), "--format", "json"]).output)
    _c, wire = _call_tool(create_server(), "archy_brief", {"path": str(project)})
    assert wire is not None
    assert set(wire) == set(cli)
    assert set(wire["conventions"]) == set(cli["conventions"])
    assert wire["transitive_checked"] == cli["transitive_checked"]
    assert (wire["transitive_unverified_reason"] is None) == (
        cli["transitive_unverified_reason"] is None
    )


def test_brief_rejects_a_nonsense_top_n(tmp_path: Path):
    from archy.mcp_compat import ToolError

    project = _write_brief_project(tmp_path, with_config=False)
    with pytest.raises(ToolError):
        _call_tool(create_server(), "archy_brief", {"path": str(project), "top_n": 0})
