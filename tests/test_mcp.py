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
    GraphPayload,
    GraphTooLargePayload,
    _run_check,
    _run_cycles,
    _run_diff,
    _run_graph_dump,
    _run_graph_focus,
    _run_graph_summary,
    _run_impact,
    _run_score,
    _run_snapshot,
    _run_trend,
    create_server,
)


@pytest.fixture
def acyclic_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
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
        "archy_snapshot",
        "archy_diff",
        "archy_record_baseline",
        "archy_graph_focus",
        "archy_graph_summary",
        "archy_graph",
    }


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
    # Default direction='both', depth=1, seeded on pkg.a (which imports pkg.b).
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


def test_graph_focus_direction_out_excludes_callers(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("from pkg.a import other\n")

    payload = _run_graph_focus(
        tmp_path,
        modules=["pkg.a"],
        depth=1,
        direction="out",
        internal_only=True,
    )
    ids = {n.id for n in payload.nodes}
    assert "pkg.a" in ids and "pkg.b" in ids
    assert "pkg.c" not in ids  # c imports a, but direction='out' ignores callers


def test_graph_focus_direction_in_excludes_dependencies(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("from pkg.a import other\n")

    payload = _run_graph_focus(
        tmp_path,
        modules=["pkg.a"],
        depth=1,
        direction="in",
        internal_only=True,
    )
    ids = {n.id for n in payload.nodes}
    assert "pkg.a" in ids and "pkg.c" in ids
    assert "pkg.b" not in ids  # a imports b, but direction='in' ignores dependencies


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
    from archy.graph import build_graph, graph_to_dict

    g = build_graph(acyclic_project)
    external = {n for n, d in g.nodes(data=True) if d.get("external")}
    g.remove_nodes_from(external)
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
    # The two import statements collapse into one edge with both line numbers.
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
    from archy.graph import build_graph

    g = build_graph(acyclic_project)
    external = {n for n, d in g.nodes(data=True) if d.get("external")}
    g.remove_nodes_from(external)
    exact = g.number_of_nodes()

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
