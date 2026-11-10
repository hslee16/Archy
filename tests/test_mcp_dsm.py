"""The dsm tool: matrix, summary, diff and the size cap."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from archy.dsm import DSM, DSMSummary
from archy.graph import build_graph
from archy.mcp import (
    DSMErrorPayload,
    DSMTooLargePayload,
    create_server,
)
from archy.mcp_runs import (
    run_dsm,
    run_graph,
)
from tests.mcp_helpers import _dsm, _fan_out_project, _output_schema
from tests.mcp_helpers import call_tool as _call_tool


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


@pytest.mark.parametrize("runner", [run_graph, run_dsm])
def test_response_format_rejects_unknown_value(acyclic_project: Path, runner):
    # Both heavy tools validate the enum up front, before any graph work.
    kwargs = (
        dict(internal_only=True, max_nodes=500, top_n=20)
        if runner is run_graph
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
    monkeypatch.setattr("archy.mcp_runs_graph.DEFAULT_MAX_DSM_CELLS", 2)
    project = _fan_out_project(tmp_path, fan=5)  # 5 cells > cap of 2
    result = _dsm(project, response_format="full")
    assert isinstance(result, DSMTooLargePayload)
    assert result.cell_count == 5
    assert result.max_cells == 2


def test_dsm_diff_ignores_response_format(acyclic_project: Path, tmp_path: Path):
    # A baseline diff is a deliberate, already-compact comparison; summary mode
    # must not suppress it.
    from archy.dsm import DSMDiff, build_dsm, write_dsm
    from archy.mcp_runs import _load_graph

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


@pytest.mark.parametrize("relative", [True, False], ids=["relative", "absolute"])
def test_dsm_baseline_path_is_relative_to_project(
    acyclic_project: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: bool,
):
    from archy.dsm import build_dsm, write_dsm

    baseline = acyclic_project / "dsm.json"
    write_dsm(build_dsm(build_graph(acyclic_project)), baseline)
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert baseline.is_file()
    assert not Path(baseline.name).exists()

    _content, wire = _call_tool(
        create_server(),
        "archy_dsm",
        {
            "path": str(acyclic_project),
            "baseline_path": baseline.name if relative else str(baseline),
        },
    )

    assert isinstance(wire, dict)
    result = wire["result"]
    assert "error" not in result
    assert result["nodes_added"] == []
    assert result["nodes_removed"] == []
    assert result["added"] == []
    assert result["removed"] == []
    assert result["new_back_edges"] == []


def test_dsm_directory_baseline_returns_error_on_the_wire(acyclic_project: Path, tmp_path: Path):
    baseline = tmp_path / "subdir"
    baseline.mkdir()
    result = _dsm(acyclic_project, baseline_path=str(baseline))

    assert isinstance(result, DSMErrorPayload)
    assert result.model_dump() == {
        "error": (
            f"{baseline} is a directory, not a DSM snapshot "
            "(write one with `archy dsm --format json`)"
        )
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"focus": "pkg.nope"}, "focus 'pkg.nope' is not an internal module"),
        ({"package": "nope"}, "package 'nope' matches no internal module"),
    ],
)
def test_archy_dsm_refuses_a_name_that_matches_nothing(
    acyclic_project: Path, kwargs: dict, message: str
):
    """The tool used to return a clean-looking empty summary for a typo (#625)."""
    from archy.mcp_compat import ToolError

    assert _dsm(acyclic_project).module_count > 0  # the unfiltered project is not empty
    with pytest.raises(ValueError, match=message):
        _dsm(acyclic_project, **kwargs)
    # #636: expected refusals re-raise as ToolError at the tool boundary, so the
    # reason reaches agents on both mcp majors (2.x used to drop ValueError text).
    with pytest.raises(ToolError, match=message):
        _call_tool(create_server(), "archy_dsm", {"path": str(acyclic_project), **kwargs})


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"focus": "pkg.aa"}, "focus 'pkg.aa' is not an internal module; did you mean pkg.a?"),
        (
            {"package": "pk."},
            "package 'pk.' matches no internal module; did you mean pkg, pkg.a, pkg.b?",
        ),
        ({"focus": "zzz"}, "focus 'zzz' is not an internal module"),
    ],
)
def test_archy_dsm_refusal_names_the_near_misses(acyclic_project: Path, kwargs: dict, message: str):
    """The agent is told the right name, so its next call is right (#643)."""
    from archy.mcp_compat import ToolError

    with pytest.raises(ValueError) as caught:
        _dsm(acyclic_project, **kwargs)
    assert str(caught.value) == message
    with pytest.raises(ToolError, match=re.escape(message)):
        _call_tool(create_server(), "archy_dsm", {"path": str(acyclic_project), **kwargs})
    assert _dsm(acyclic_project, focus="pkg.a").module_count == 2


def test_archy_dsm_rejects_negative_focus_depth(acyclic_project: Path):
    """MCP archy_dsm must reject focus_depth < 0 the way the CLI does."""
    from archy.mcp_compat import ToolError

    server = create_server()
    path = str(acyclic_project)
    message = "focus_depth must be >= 0; got -1"
    with pytest.raises(ValueError) as caught:
        run_dsm(
            acyclic_project,
            response_format="summary",
            group_by="community",
            weight="imports",
            focus=None,
            focus_depth=-1,
            package=None,
            baseline_path=None,
        )
    assert str(caught.value) == message
    with pytest.raises(ToolError, match=message):
        _call_tool(
            server,
            "archy_dsm",
            {"path": path, "focus": "app.a", "focus_depth": -1},
        )

    # focus_depth=0 is a valid CLI bound and must still succeed.
    summary = run_dsm(
        acyclic_project,
        response_format="summary",
        group_by="community",
        weight="imports",
        focus=None,
        focus_depth=0,
        package=None,
        baseline_path=None,
    )
    assert isinstance(summary, DSMSummary)
    assert summary.module_count >= 0


@pytest.mark.parametrize(
    ("kwargs", "name"),
    [
        ({"group_by": "bogus"}, "group_by"),
        ({"weight": "bogus"}, "weight"),
    ],
)
def test_archy_dsm_rejects_an_unknown_group_by_or_weight_before_scanning(
    acyclic_project: Path, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, str], name: str
):
    """group_by/weight used to be cast() straight into build_dsm, so an invalid
    value was only caught once the DSM pydantic model was built, after a full
    scan (#645). A scan that never runs cannot raise, so a `_load_graph` that
    fails if called pins "validated up front" directly.
    """

    def boom(*args, **kwargs):
        raise AssertionError(f"{name} must be validated before the project is scanned")

    monkeypatch.setattr("archy.mcp_runs_graph._load_graph", boom)

    # Kept str-only (group_by/weight/response_format) so `**base` unifies to
    # `dict[str, str]`; mixing in the None/int fields below would widen that
    # to `str | None | int` and break every str-typed param at the call site.
    base: dict[str, str] = dict(
        response_format="summary",
        group_by="community",
        weight="imports",
    )
    base.update(kwargs)
    with pytest.raises(ValueError):
        run_dsm(
            acyclic_project,
            focus=None,
            focus_depth=1,
            package=None,
            baseline_path=None,
            **base,
        )


@pytest.mark.parametrize(
    ("kwargs", "name", "allowed"),
    [
        ({"group_by": "bogus"}, "group_by", ("community", "layer", "topological")),
        ({"weight": "bogus"}, "weight", ("imports", "calls")),
    ],
)
def test_archy_dsm_names_the_allowed_group_by_or_weight_values(
    acyclic_project: Path, kwargs: dict, name: str, allowed: tuple[str, ...]
):
    """The refusal must name the allowed values, not leak a raw pydantic error
    ("1 validation error for DSM ... https://errors.pydantic.dev/...", #645).
    """
    from archy.mcp_compat import ToolError

    with pytest.raises(ValueError) as caught:
        _dsm(acyclic_project, **kwargs)
    message = str(caught.value)
    assert "validation error" not in message
    assert "errors.pydantic.dev" not in message
    assert name in message
    for value in allowed:
        assert value in message

    with pytest.raises(ToolError) as caught_tool:
        _call_tool(create_server(), "archy_dsm", {"path": str(acyclic_project), **kwargs})
    tool_message = str(caught_tool.value)
    assert "validation error" not in tool_message
    assert "errors.pydantic.dev" not in tool_message


@pytest.mark.parametrize(
    ("tool_name", "args", "unknown"),
    [
        (
            "archy_graph",
            {"path": ".", "focus_modules": ["pkg.a"]},
            "focus_modules",
        ),
        (
            "archy_dsm",
            {"path": ".", "module": "pkg.a"},
            "module",
        ),
        (
            "archy_simulate",
            {"path": ".", "adds": []},
            "adds",
        ),
    ],
)
def test_unknown_tool_argument_is_refused(
    acyclic_project: Path, tool_name: str, args: dict, unknown: str
):
    """A misspelled or invented argument must raise ToolError naming the key (#662)."""
    from archy.mcp_compat import ToolError

    server = create_server()
    call_args = {**args, "path": str(acyclic_project)}
    with pytest.raises(ToolError, match=unknown):
        _call_tool(server, tool_name, call_args)
