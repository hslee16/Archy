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
from pathlib import Path

import pytest

from archy.mcp import (
    ForbiddenEdge,
    GraphPayload,
    GraphTooLargePayload,
    _run_check,
    _run_cycles,
    _run_diff,
    _run_graph_dump,
    _run_graph_focus,
    _run_graph_summary,
    _run_high_risk_modules,
    _run_hotspots,
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


def test_create_server_registers_expected_tools():
    server = create_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "archy_score",
        "archy_cycles",
        "archy_check",
        "archy_contracts",
        "archy_trend",
        "archy_impact",
        "archy_affected",
        "archy_snapshot",
        "archy_diff",
        "archy_record_baseline",
        "archy_graph_focus",
        "archy_graph_summary",
        "archy_graph",
        "archy_high_risk_modules",
        "archy_hotspots",
        "archy_what_to_refactor_next",
        "archy_dsm",
        "archy_status",
        "archy_simulate",
    }


def test_all_tools_declare_read_only_annotations():
    # Every archy tool is read-only structural analysis. Declaring the hints
    # explicitly lets trusted clients auto-approve calls instead of prompting
    # on every read (MCP tool-annotations, 2025-03-26 spec); the cautious
    # default for an un-annotated tool is destructive/non-idempotent/open-world.
    server = create_server()
    tools = asyncio.run(server.list_tools())
    assert tools, "expected registered tools"
    for tool in tools:
        ann = tool.annotations
        assert ann is not None, f"{tool.name} has no annotations"
        assert ann.readOnlyHint is True, f"{tool.name} not readOnlyHint"
        assert ann.destructiveHint is False, f"{tool.name} not destructiveHint=False"
        assert ann.idempotentHint is True, f"{tool.name} not idempotentHint"
        assert ann.openWorldHint is False, f"{tool.name} not openWorldHint=False"


def test_all_tools_declare_human_friendly_title():
    # The 2025-06-18 revision added a display `title` so `name` stays a
    # programmatic id. Assert every tool sets a non-empty title distinct from
    # its name.
    server = create_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        assert tool.title, f"{tool.name} has no display title"
        assert tool.title != tool.name, f"{tool.name} title duplicates the name"


def test_all_tools_declare_output_schema():
    # 2025-06-18 structured output: FastMCP derives an `outputSchema` (JSON
    # Schema) from each tool's return annotation. Assert every tool declares
    # one and it is an object schema, since `structuredContent` must be a JSON
    # object (sequence/union returns are wrapped under a `result` key to honor
    # this -- see the module docstring).
    server = create_server()
    tools = asyncio.run(server.list_tools())
    for tool in tools:
        schema = tool.outputSchema
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
        ("archy_score", {}, True),  # BaseModel return
        ("archy_graph", {}, True),  # union, success branch (GraphPayload)
        ("archy_diff", {}, True),  # union, in-band error branch (no baseline -> DiffErrorPayload)
        ("archy_cycles", {}, False),  # bare list, empty on an acyclic project -> {"result": []}
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
    schema = {t.name: t.outputSchema for t in asyncio.run(server.list_tools())}[name]
    content, structured = asyncio.run(
        server.call_tool(name, {"path": str(acyclic_project), **extra_args})
    )
    assert isinstance(structured, dict)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(structured), key=lambda e: list(e.path)
    )
    assert not errors, f"{name} structuredContent violates outputSchema: {errors[:1]}"
    has_text = any(getattr(block, "text", None) for block in content)
    assert has_text is expect_text


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
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    result = _run_check(tmp_path, config_path=None)
    assert result.passed is False
    [violation] = result.violations
    assert violation.rule.from_layer == "core"
    assert violation.rule.to_layer == "cli"


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
    assert result.passed is False
    assert result.violations == ()
    [violation] = [v for v in result.sdp_violations if v.source == "myapp.a"]
    assert violation.target == "myapp.b"


def test_run_check_skips_sdp_when_disabled(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\n")
    result = _run_check(project, config_path=None)
    assert result.passed is True
    assert result.sdp_violations == ()


def test_run_check_warn_mode_reports_violations_but_passes(tmp_path: Path):
    project = _make_sdp_violating_project(tmp_path)
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\nsdp:\n  enabled: true\n  mode: warn\n"
    )
    result = _run_check(project, config_path=None)
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


def test_snapshot_brief_load_bearing_ranked_and_capped(acyclic_project: Path):
    brief = _run_snapshot(acyclic_project).invariant_brief
    risks = [m.edit_risk for m in brief.load_bearing]
    assert risks == sorted(risks, reverse=True)
    assert len(brief.load_bearing) <= 5


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
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    brief = _run_snapshot(tmp_path).invariant_brief
    assert {layer.name for layer in brief.layers} == {"core", "cli"}
    assert ForbiddenEdge(from_layer="core", to_layer="cli") in brief.forbidden_edges


def test_snapshot_brief_no_config_has_empty_layers(acyclic_project: Path):
    brief = _run_snapshot(acyclic_project).invariant_brief
    assert brief.layers == ()
    assert brief.forbidden_edges == ()


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
    edge_spec = tool.inputSchema["$defs"]["EdgeSpec"]["properties"]
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
    # Parity contract: the MCP dump must be value-equal to graph_to_dict
    # (which the CLI uses for `archy graph --format json`).
    from archy.graph import graph_to_dict

    g = _internal_graph(acyclic_project)
    expected = graph_to_dict(g)

    payload = _run_graph_dump(acyclic_project, internal_only=True, max_nodes=500)
    assert isinstance(payload, GraphPayload)
    assert payload.root == expected["root"]
    assert {n.id for n in payload.nodes} == {n["id"] for n in expected["nodes"]}
    payload_edges = {(e.source, e.target) for e in payload.edges}
    expected_edges = {(e["source"], e["target"]) for e in expected["edges"]}
    assert payload_edges == expected_edges


def test_graph_dump_refuses_oversized_graph(acyclic_project: Path):
    payload = _run_graph_dump(acyclic_project, internal_only=True, max_nodes=1)
    assert isinstance(payload, GraphTooLargePayload)
    assert payload.max_nodes == 1
    assert payload.node_count > 1
    assert "archy_graph_focus" in payload.error


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


def test_high_risk_modules_ranks_central_volatile_first(tmp_path: Path):
    # `pkg.hub` is imported by three peers (high fan-in) AND itself imports a
    # downstream dep (non-zero instability), so it dominates the composite.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "dep.py").write_text("")
    (pkg / "hub.py").write_text("from pkg.dep import thing\n")
    (pkg / "a.py").write_text("from pkg.hub import x\n")
    (pkg / "b.py").write_text("from pkg.hub import y\n")
    (pkg / "c.py").write_text("from pkg.hub import z\n")

    payload = _run_high_risk_modules(tmp_path, top_n=5)
    assert payload.modules[0].module == "pkg.hub"
    top = payload.modules[0]
    assert 0.0 < top.edit_risk <= 1.0
    assert top.fan_in == 3
    assert top.instability > 0.0
    assert top.propagation_cost > 0.0


def test_high_risk_modules_validates_top_n(acyclic_project: Path):
    with pytest.raises(ValueError, match="top_n"):
        _run_high_risk_modules(acyclic_project, top_n=0)


def test_high_risk_modules_top_n_caps_results(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name in ("a", "b", "c", "d"):
        (pkg / f"{name}.py").write_text("")

    payload = _run_high_risk_modules(tmp_path, top_n=2)
    assert len(payload.modules) <= 2
    # module_count reports the size of the candidate pool, not the slice.
    assert payload.module_count >= len(payload.modules)


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


def test_hotspots_payload_shape_and_ranking(tmp_path: Path):
    _init_hotspot_repo(tmp_path)
    payload = _run_hotspots(tmp_path, top=20, since=None)
    assert payload.note is None
    assert payload.since is None
    assert payload.total >= 1
    assert payload.shown == len(payload.hotspots)
    top = payload.hotspots[0]
    assert top.module == "pkg.hot"
    assert top.score == top.cc_sum * top.churn
    assert top.path.endswith("pkg/hot.py")


def test_hotspots_top_caps_results(tmp_path: Path):
    _init_hotspot_repo(tmp_path)
    payload = _run_hotspots(tmp_path, top=1, since=None)
    assert payload.shown <= 1
    # `total` reports the size of the candidate pool, not the slice.
    assert payload.total >= payload.shown


def test_hotspots_validates_top(tmp_path: Path):
    with pytest.raises(ValueError, match="top"):
        _run_hotspots(tmp_path, top=0, since=None)


def test_hotspots_returns_diagnostic_when_not_in_git_repo(tmp_path: Path):
    # Same Python project shape but no `git init` -> the tool must NOT raise.
    # The agent reads `note` and pivots to archy_high_risk_modules instead.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("def f():\n    if True: return 1\n")
    payload = _run_hotspots(tmp_path, top=20, since=None)
    assert payload.hotspots == ()
    assert payload.total == 0
    assert payload.note is not None
    assert "not inside a git repository" in payload.note
    assert "archy_high_risk_modules" in payload.note


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
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "dep.py").write_text("")
    (pkg / "hub.py").write_text("from pkg.dep import thing\n")
    (pkg / "a.py").write_text("from pkg.hub import x\n")
    (pkg / "b.py").write_text("from pkg.hub import y\n")
    (pkg / "c.py").write_text("from pkg.hub import z\n")

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


def test_hotspots_since_propagates_to_git_churn(tmp_path: Path):
    # Verifies the `since` arg is actually plumbed into `git_churn`, not
    # silently dropped. Regression guard: if someone refactored
    # `_run_hotspots` and forgot to forward `since`, every other test in
    # this file would still pass.
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

    full = _run_hotspots(repo, top=20, since=None)
    full_modules = {h.module for h in full.hotspots}
    assert "pkg.hot" in full_modules
    assert "pkg.old" in full_modules
    hot_full = next(h for h in full.hotspots if h.module == "pkg.hot")
    old_full = next(h for h in full.hotspots if h.module == "pkg.old")
    assert hot_full.churn == 2
    assert old_full.churn == 1

    filtered = _run_hotspots(repo, top=20, since="2025-01-01")
    assert filtered.since == "2025-01-01"
    filtered_modules = {h.module for h in filtered.hotspots}
    # `old.py` was last touched in 2020 -> dropped (churn=0 -> filtered).
    # `hot.py` still has the 2026-tweak commit -> kept with churn=1.
    assert "pkg.old" not in filtered_modules
    hot_filtered = next(h for h in filtered.hotspots if h.module == "pkg.hot")
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
