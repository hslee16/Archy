"""Tests for graph: serialize."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from archy.graph import (
    build_graph,
    graph_to_dict,
)
from tests.graph_helpers import _edge


def test_internal_edges_resolve_correctly(project: Path):
    g = build_graph(project)
    assert g.has_edge("myapp.core", "myapp.utils")
    assert g.has_edge("myapp.utils", "myapp.core")
    assert g.has_edge("myapp.cli", "myapp.core")
    assert g.has_edge("myapp.sub.leaf", "myapp.utils")


def test_internal_only_filtering(project: Path):
    g = build_graph(project)
    external = [n for n, d in g.nodes(data=True) if d.get("external")]
    assert set(external) >= {"os", "requests", "json"}


def test_graph_to_dict_preserves_edge_line_numbers(tmp_path: Path, pkg: Path):
    # Three import statements at known lines targeting pkg.b. Edge attribute
    # `lines` must collect all three so agents can pinpoint import sites.
    (pkg / "a.py").write_text(
        "from pkg.b import x\n# noise\nfrom pkg.b import y\nfrom pkg.b import z\n"
    )
    (pkg / "b.py").write_text("")

    data = graph_to_dict(build_graph(tmp_path))
    edge = _edge(data, "pkg.a", "pkg.b")
    assert edge["lines"] == (1, 3, 4)


def test_graph_to_dict_emits_instability_only_for_internal(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("import requests\n")

    data = graph_to_dict(build_graph(tmp_path))
    by_id = {n["id"]: n for n in data["nodes"]}
    # Internal nodes carry instability; the external `requests` node must not,
    # since Ce/Ca aren't meaningful for nodes outside the project.
    assert "instability" in by_id["pkg.a"]
    assert "instability" not in by_id["requests"]
    assert by_id["requests"]["external"] is True


def test_graph_to_dict_is_deterministic(tmp_path: Path, pkg: Path):
    # `abc` is external, so it is discovered last but sorts first; the imports
    # are written c-then-b so the raw edge order is reversed. Both halves of
    # the fixture exist to make the raw order differ from the sorted one:
    # without the external node, discovery already yields sorted node ids and
    # the node assertion holds with `sorted()` deleted (#440).
    (pkg / "a.py").write_text("import abc\nfrom pkg.c import x\nfrom pkg.b import y\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("")

    graph = build_graph(tmp_path)
    raw_nodes = list(graph.nodes())
    raw_edges = [(u, v) for u, v in graph.edges()]
    assert raw_nodes != sorted(raw_nodes)
    assert raw_edges != sorted(raw_edges)

    data = graph_to_dict(graph)
    node_ids = [n["id"] for n in data["nodes"]]
    assert node_ids == sorted(node_ids)
    edges = [(e["source"], e["target"]) for e in data["edges"]]
    assert edges == sorted(edges)


def test_graph_to_dict_includes_call_attrs(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("from pkg import b\nb.do()\n")
    (pkg / "b.py").write_text("def do():\n    pass\n")
    data = graph_to_dict(build_graph(tmp_path))
    edge = _edge(data, "pkg.a", "pkg.b")
    assert edge["call_count"] == 1
    assert edge["call_lines"] == (2,)
    assert "call" in edge["kinds"]


def test_cc_attrs_appear_in_graph_to_dict(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("def f(x):\n    return x and x\n")
    data = graph_to_dict(build_graph(tmp_path))
    by_id = {n["id"]: n for n in data["nodes"]}
    assert by_id["pkg.a"]["function_count"] == 1
    assert by_id["pkg.a"]["cc_max"] == 2


def test_graph_to_dict_shape_is_pinned_by_hand():
    """The wire shape as a literal, because every other test of it is relative.

    `archy graph --format json` and `mcp_runs._graph_payload_from` BOTH call this
    function, so the cross-surface parity test in `tests/test_mcp_graph.py` cannot
    catch a consistent regression here: dropping a field or renaming a key moves
    both sides together and that test still passes (#439). Nothing else pins the
    JSON contract that external consumers actually read.

    Three modules, not two, and that is load-bearing. On a two-node graph every
    `edit_risk` is structurally 0.0, because one node always has zero fan-in and
    the other zero instability, so the geometric mean always has a zero factor.
    A `compute_edit_risk` replaced by `{n: 0.0}` would have passed. The middle
    module here carries a real value.

    Every number below is worked from the formulas, not captured from a run:

        pkg.a  I = ce 1 / (ce 1 + ca 0)   = 1.0    reverse reach 1/3
        pkg.b  I = ce 1 / (ce 1 + ca 1)   = 0.5    reverse reach 2/3
        pkg.c  I = ce 0 / (ce 0 + ca 1)   = 0.0    reverse reach 3/3
        risk(b) = (2/3 * fan-in 1/2 * 0.5) ** (1/3) = 0.550321
        risk(a) = 0.0 at zero fan-in, risk(c) = 0.0 at zero instability
    """
    g = nx.DiGraph(root="/p")
    for name in ("pkg.a", "pkg.b", "pkg.c"):
        g.add_node(name, external=False, path=f"/p/{name}.py", is_package=False)
    g.add_edge("pkg.a", "pkg.b", is_relative=True, lines=(3,), kinds=("import",))
    g.add_edge("pkg.b", "pkg.c", is_relative=False, lines=(4,), kinds=("import",))

    assert graph_to_dict(g) == {
        "root": "/p",
        "parse_errors": [],
        "nodes": [
            {
                "id": "pkg.a",
                "external": False,
                "path": "/p/pkg.a.py",
                "is_package": False,
                "instability": 1.0,
                "propagation_cost": pytest.approx(1 / 3),
                "edit_risk": 0.0,
            },
            {
                "id": "pkg.b",
                "external": False,
                "path": "/p/pkg.b.py",
                "is_package": False,
                "instability": 0.5,
                "propagation_cost": pytest.approx(2 / 3),
                "edit_risk": pytest.approx(0.550321, abs=5e-7),
            },
            {
                "id": "pkg.c",
                "external": False,
                "path": "/p/pkg.c.py",
                "is_package": False,
                "instability": 0.0,
                "propagation_cost": 1.0,
                "edit_risk": 0.0,
            },
        ],
        "edges": [
            {
                "source": "pkg.a",
                "target": "pkg.b",
                "is_relative": True,
                "lines": (3,),
                "kinds": ("import",),
            },
            {
                "source": "pkg.b",
                "target": "pkg.c",
                # The second edge differs on every attribute the first sets, so a
                # serializer that echoed one edge's values onto all of them fails.
                "is_relative": False,
                "lines": (4,),
                "kinds": ("import",),
            },
        ],
    }
