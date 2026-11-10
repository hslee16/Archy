"""The graph, dsm, impact and affected tools."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from archy.graph import build_graph
from archy.mcp import (
    GraphPayload,
    GraphSummaryPayload,
    GraphTooLargePayload,
    create_server,
)
from archy.mcp_runs import (
    run_graph,
    run_graph_dump,
    run_graph_focus,
    run_graph_summary,
    run_snapshot,
    run_what_to_refactor_next,
)
from archy.scan_config import graph_kwargs
from tests.mcp_helpers import _hub_project, _internal_graph
from tests.mcp_helpers import call_tool as _call_tool


def test_archy_graph_under_an_ignored_ancestor_still_counts_modules(tmp_path: Path):
    """#517: MCP abs path under build/ must not report module_count 0."""
    proj = tmp_path / "build" / "proj"
    pkg = proj / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")

    _content, structured = _call_tool(
        create_server(),
        "archy_graph",
        {"path": str(proj.resolve()), "response_format": "summary"},
    )
    assert structured is not None
    assert structured["result"]["module_count"] == 3


def test_graph_default_is_summary(acyclic_project: Path):
    result = run_graph(
        acyclic_project, response_format="summary", internal_only=True, max_nodes=500, top_n=20
    )
    assert isinstance(result, GraphSummaryPayload)


def test_graph_full_returns_dump(acyclic_project: Path):
    result = run_graph(
        acyclic_project, response_format="full", internal_only=True, max_nodes=500, top_n=20
    )
    assert isinstance(result, GraphPayload)
    assert result.nodes


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

    payload = run_snapshot(acyclic_project)
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

    brief = run_snapshot(tmp_path).invariant_brief
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


def test_graph_focus_default_returns_local_neighborhood(acyclic_project: Path):
    payload = run_graph_focus(
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
    payload = run_graph_focus(
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
    payload = run_graph_focus(
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
    payload = run_graph_focus(
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
    payload = run_graph_focus(
        acyclic_project,
        modules=["pkg.a", "nonexistent.module", "missing/file.py"],
        depth=0,
        direction="both",
        internal_only=True,
    )
    assert {n.id for n in payload.nodes} == {"pkg.a"}
    assert set(payload.unresolved) == {"nonexistent.module", "missing/file.py"}


def test_graph_focus_suggests_the_module_for_an_unresolved_name(acyclic_project: Path):
    payload = run_graph_focus(
        acyclic_project,
        modules=["pkg.aa", "pkg/aa.py", "nothing.close"],
        depth=0,
        direction="both",
        internal_only=True,
    )
    assert {h.ref: h.did_you_mean for h in payload.unresolved_hints} == {
        "pkg.aa": ("pkg.a",),
        "pkg/aa.py": ("pkg/a.py",),
    }


def test_graph_focus_suggests_even_when_another_name_resolved(acyclic_project: Path):
    """The mixed case returns through a different branch than the all-unresolved one."""
    payload = run_graph_focus(
        acyclic_project, modules=["pkg.a", "pkg.bb"], depth=0, direction="both", internal_only=True
    )
    assert {n.id for n in payload.nodes} == {"pkg.a"}
    assert [(h.ref, h.did_you_mean) for h in payload.unresolved_hints] == [("pkg.bb", ("pkg.b",))]


def test_graph_focus_multi_seed_unions_ego_graphs(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import x\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("from pkg.d import y\n")
    (pkg / "d.py").write_text("")

    payload = run_graph_focus(
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
        run_graph_focus(
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

    summary = run_graph_summary(tmp_path, top_n=3)
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

    summary = run_graph_summary(tmp_path, top_n=10)
    ext_names = {e.module for e in summary.external_deps}
    assert "requests" in ext_names
    requests_entry = next(e for e in summary.external_deps if e.module == "requests")
    assert requests_entry.value >= 2


def test_graph_summary_validates_top_n(acyclic_project: Path):
    with pytest.raises(ValueError, match="top_n"):
        run_graph_summary(acyclic_project, top_n=0)


def test_graph_dump_matches_cli_json(acyclic_project: Path):
    """Parity between the MCP dump and `archy graph --format json`.

    What this does and does not make independent, stated precisely because the
    first rewrite overclaimed it. The earlier version compared the payload
    against `graph_to_dict(g)` computed in the test, which proved nothing
    (#439). Shelling out to the CLI makes the GRAPH CONSTRUCTION independent:
    a fresh subprocess doing its own `build_graph` against the cached manager's.
    It does NOT make the SERIALIZATION independent, because `cli.graph` and
    `mcp_runs._graph_payload_from` both still call `graph_to_dict`, so a consistent
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

    payload = run_graph_dump(acyclic_project, internal_only=True, max_nodes=500)

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
    payload = run_graph_dump(acyclic_project, internal_only=True, max_nodes=1)
    assert isinstance(payload, GraphTooLargePayload)
    assert payload.max_nodes == 1
    assert payload.node_count > 1
    assert "archy_graph(focus=" in payload.error


@pytest.mark.parametrize("bad", [0, -1, -500])
def test_graph_dump_validates_max_nodes(acyclic_project: Path, bad: int):
    # A non-positive max_nodes used to fall through to the size guard and
    # return a confusing GraphTooLargePayload; reject it up front instead.
    with pytest.raises(ValueError, match="max_nodes"):
        run_graph_dump(acyclic_project, internal_only=True, max_nodes=bad)


def test_refactor_structural_lens_ranks_central_volatile_first(tmp_path: Path):
    # v0.36 (#227): the structural lens replaces archy_high_risk_modules. `pkg.hub`
    # is imported by three peers (high fan-in) AND itself imports a downstream dep
    # (non-zero instability), so it dominates the edit-risk composite. min_risk=0
    # restores the old high-risk behavior (no floor).
    _hub_project(tmp_path, spokes=3)

    payload = run_what_to_refactor_next(
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

    payload = run_what_to_refactor_next(
        tmp_path, lens="structural", top_n=5, since="2025-01-01", min_risk=0.0
    )
    assert payload.git_available is False
    assert payload.priorities
    assert all(e.lenses == ("edit_risk",) for e in payload.priorities)


def test_refactor_structural_lens_top_n_caps_results(tmp_path: Path):
    _hub_project(tmp_path, spokes=4)

    payload = run_what_to_refactor_next(
        tmp_path, lens="structural", top_n=2, since=None, min_risk=0.0
    )
    assert len(payload.priorities) <= 2
    # total reports the size of the candidate pool, not the slice.
    assert payload.total >= len(payload.priorities)


def test_graph_tool_focus_routes_to_subgraph(acyclic_project: Path):
    # v0.36 (#227): archy_graph(focus=...) replaces archy_graph_focus. With focus
    # set the tool returns a bounded GraphPayload subgraph, ignoring
    # response_format/max_nodes/top_n.
    payload = run_graph(
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
        run_graph(
            acyclic_project,
            response_format="xml",
            focus=["pkg.a"],
            depth=1,
            direction="both",
            internal_only=True,
            max_nodes=500,
            top_n=20,
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

    payload = run_graph_focus(
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

    payload = run_graph_focus(
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

    payload = run_graph_focus(
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

    payload = run_graph_focus(
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

    payload = run_graph_dump(acyclic_project, internal_only=True, max_nodes=exact)
    assert isinstance(payload, GraphPayload)
    assert len(payload.nodes) == exact


def test_graph_summary_empty_project_does_not_divide_by_zero(tmp_path: Path):
    # No packages, no edges. Summary must return zeros instead of crashing on
    # `1 / n` in PageRank or instability.
    (tmp_path / "not_a_package.py").write_text("x = 1\n")
    summary = run_graph_summary(tmp_path, top_n=5)
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

    summary = run_graph_summary(tmp_path, top_n=5)
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

    from archy.mcp_runs import _pagerank

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

    from archy.mcp_runs import _pagerank

    g: nx.DiGraph = nx.DiGraph()
    g.add_edges_from([(u, v) for u in "abc" for v in "abc" if u != v])
    # All nodes symmetric -> uniform pagerank 1/3 each.
    pr = _pagerank(g, tol=1e-6)
    assert pr["a"] == pytest.approx(1 / 3, abs=1e-6)
    assert pr["b"] == pytest.approx(1 / 3, abs=1e-6)
    assert pr["c"] == pytest.approx(1 / 3, abs=1e-6)


def test_archy_yaml_roots_plumbed_through_mcp(tmp_path: Path):
    # The MCP twin of test_cli's namespace-package roots test. Without it the
    # MCP path could drop `roots:` with every test still green (#395).
    app = tmp_path / "app"
    (app / "libs").mkdir(parents=True)
    (app / "libs" / "db.py").write_text("import os\n")
    (app / "main.py").write_text("from app.libs.db import x\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nroots: [app]\n")
    payload = run_graph_dump(tmp_path, internal_only=True, max_nodes=100)
    assert isinstance(payload, GraphPayload)
    assert {"app.main", "app.libs.db"} <= {n.id for n in payload.nodes}


@pytest.mark.parametrize("error", [sqlite3.OperationalError("locked"), PermissionError("ro")])
def test_warm_paths_fall_back_cold_when_the_cache_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
):
    # The index is an optimization, never a dependency: a cache that cannot open
    # (read-only filesystem, locked database) must still yield an answer (#395).
    import archy.mcp_runs_core as mcp_module

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


def test_what_to_refactor_next_structural_only_without_git(tmp_path: Path):
    # No git -> behavioral lens skipped, ranking is structural-only, and a note
    # explains the degraded mode. The list is still populated structurally.
    _hub_project(tmp_path, spokes=3)

    payload = run_what_to_refactor_next(tmp_path, top_n=5, since=None, min_risk=0.1)
    assert payload.git_available is False
    assert payload.priorities
    assert all(e.lenses == ("edit_risk",) for e in payload.priorities)
    assert payload.note is not None
    assert "structural-only" in payload.note


def test_known_tool_arguments_still_run(acyclic_project: Path):
    """Valid calls must keep working after unknown-argument rejection (#662)."""
    server = create_server()
    _content, structured = _call_tool(
        server,
        "archy_graph",
        {"path": str(acyclic_project), "focus": ["pkg.a"]},
    )
    assert structured is not None
