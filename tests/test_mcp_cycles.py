"""The cycles and simulate tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.mcp import (
    create_server,
)
from archy.mcp_runs import (
    run_cycles,
    run_simulate,
)
from archy.simulate import EdgeSpec, SimulateReport
from tests.mcp_helpers import _guarded_cycle_project, _list_tools
from tests.mcp_helpers import call_tool as _call_tool


def test_archy_cycles_rejects_min_size_below_one(acyclic_project: Path):
    """MCP archy_cycles must reject min_size < 1 the way the CLI does.

    CLI `cycles --min-size` uses `_require(min_size >= 1, ...)`. Without the
    matching MCP guard, min_size=0 / -5 silently return the cycle list.
    """
    from archy.mcp_compat import ToolError

    server = create_server()
    path = str(acyclic_project)

    for bad in (0, -5):
        message = f"min_size must be >= 1; got {bad}"
        with pytest.raises(ValueError) as caught:
            run_cycles(acyclic_project, min_size=bad, internal_only=True)
        assert str(caught.value) == message
        with pytest.raises(ToolError):
            _call_tool(server, "archy_cycles", {"path": path, "min_size": bad})

    # Boundary that must still succeed (acyclic project -> empty list).
    assert run_cycles(acyclic_project, min_size=1, internal_only=True) == []
    _content, structured = _call_tool(server, "archy_cycles", {"path": path, "min_size": 1})
    assert structured == {"result": []}


def test_run_cycles_payload_shape(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n")
    result = run_cycles(tmp_path, min_size=2, internal_only=True)
    assert isinstance(result, list)  # exclusion is off, so the bare-list shape
    [cycle] = result
    assert sorted(cycle.modules) == ["pkg.a", "pkg.b"]
    edge = cycle.edges[0]
    assert edge.source and edge.target


def test_run_simulate_parses_from_alias(acyclic_project: Path):
    # `from` is a Python keyword; the wire field must be the alias `from`, not
    # `from_`. If the alias breaks, agents cannot call the tool at all.
    payload = run_simulate(
        acyclic_project,
        add=[EdgeSpec.model_validate({"from": "pkg.b", "to": "pkg.a"})],
        remove=[],
    )
    assert isinstance(payload, SimulateReport)
    # pkg.a imports pkg.b; adding pkg.b -> pkg.a closes a cycle.
    assert payload.cycles.added


def test_run_simulate_refuses_an_empty_delta(acyclic_project: Path):
    """Direct helper refuses empty add/remove (#661)."""
    with pytest.raises(ValueError, match="pass at least one add or remove edge"):
        run_simulate(acyclic_project, add=[], remove=[])


def test_simulate_tool_input_schema_uses_from_to_aliases():
    server = create_server()
    tools = _list_tools(server)
    tool = next(t for t in tools if t.name == "archy_simulate")
    edge_spec = tool.model_dump(by_alias=True)["inputSchema"]["$defs"]["EdgeSpec"]["properties"]
    # The wire contract the agent sees must be {from, to}, not {from_, to}.
    assert set(edge_spec) == {"from", "to"}


def test_simulate_tool_reports_that_nothing_was_simulated(acyclic_project: Path):
    """On the wire: the verdict and the hint reach the agent, not just the model."""
    _content, structured = _call_tool(
        create_server(),
        "archy_simulate",
        {"path": str(acyclic_project), "add": [{"from": "pkg.a", "to": "pkg.bb"}]},
    )
    assert structured is not None
    applied = structured["applied"]
    assert applied["coverage"] == "none"
    assert applied["skipped"] == ["add pkg.a -> pkg.bb"]
    assert applied["unresolved_hints"] == [{"ref": "pkg.bb", "did_you_mean": ["pkg.b"]}]


def test_archy_cycles_exclude_type_checking_reports_the_excluded_edge(tmp_path: Path):
    """Asserted on the wire form: the object shape appears only with the flag on (#520)."""
    project = _guarded_cycle_project(tmp_path)
    server = create_server()

    _content, plain = _call_tool(server, "archy_cycles", {"path": str(project)})
    assert plain is not None
    [cycle] = plain["result"]  # the bare list, as before the flag existed
    assert cycle["type_checking_edges"] == 1

    _content, excluded = _call_tool(
        server, "archy_cycles", {"path": str(project), "exclude_type_checking": True}
    )
    assert excluded is not None
    payload = excluded.get("result", excluded)
    assert payload == {"cycles": [], "excluded_type_checking_edges": 1, "namespace_gaps": []}


def test_archy_cycles_follows_the_archy_yaml_key_and_the_argument_overrides_it(tmp_path: Path):
    project = _guarded_cycle_project(tmp_path)
    (project / "archy.yaml").write_text("exclude_type_checking_imports: true\n")
    server = create_server()

    _content, by_config = _call_tool(server, "archy_cycles", {"path": str(project)})
    assert by_config is not None
    assert by_config.get("result", by_config)["excluded_type_checking_edges"] == 1

    _content, overridden = _call_tool(
        server, "archy_cycles", {"path": str(project), "exclude_type_checking": False}
    )
    assert overridden is not None
    assert len(overridden["result"]) == 1
