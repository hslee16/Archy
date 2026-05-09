from __future__ import annotations

from pathlib import Path

import networkx as nx

from archy.cycles import find_cycles
from archy.graph import build_graph


def _g(*edges: tuple[str, str, tuple[int, ...]]) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for u, v, lines in edges:
        g.add_edge(u, v, lines=lines)
    return g


def test_no_cycles_on_dag():
    g = _g(("a", "b", (1,)), ("b", "c", (2,)))
    assert find_cycles(g) == []


def test_two_module_cycle():
    g = _g(("a", "b", (1,)), ("b", "a", (2,)))
    [cycle] = find_cycles(g)
    assert cycle.modules == ("a", "b")
    assert {(e.source, e.target) for e in cycle.edges} == {("a", "b"), ("b", "a")}


def test_three_module_cycle():
    g = _g(("a", "b", (1,)), ("b", "c", (2,)), ("c", "a", (3,)))
    [cycle] = find_cycles(g)
    assert cycle.modules == ("a", "b", "c")
    assert len(cycle.edges) == 3


def test_disjoint_cycles_sorted_by_size():
    g = _g(
        ("a", "b", (1,)),
        ("b", "c", (2,)),
        ("c", "a", (3,)),
        ("x", "y", (10,)),
        ("y", "x", (11,)),
    )
    cycles = find_cycles(g)
    assert [len(c.modules) for c in cycles] == [3, 2]
    assert cycles[0].modules == ("a", "b", "c")
    assert cycles[1].modules == ("x", "y")


def test_dag_nodes_not_reported():
    g = _g(
        ("a", "b", (1,)),
        ("b", "a", (2,)),
        ("a", "outside", (3,)),
    )
    [cycle] = find_cycles(g)
    assert cycle.modules == ("a", "b")
    # The outside-the-cycle edge is excluded.
    assert all(e.target != "outside" for e in cycle.edges)


def test_min_size_filter():
    g = _g(("a", "b", (1,)), ("b", "a", (2,)))
    assert find_cycles(g, min_size=3) == []


def test_self_loop_excluded_at_default_min_size():
    g: nx.DiGraph = nx.DiGraph()
    g.add_edge("a", "a", lines=(1,))
    assert find_cycles(g) == []


def test_edge_lines_preserved():
    g = _g(("a", "b", (5, 9)), ("b", "a", (12,)))
    [cycle] = find_cycles(g)
    by_pair = {(e.source, e.target): e.lines for e in cycle.edges}
    assert by_pair[("a", "b")] == (5, 9)
    assert by_pair[("b", "a")] == (12,)


def test_real_project_with_cycle(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n")
    g = build_graph(tmp_path)
    cycles = find_cycles(g)
    assert any(c.modules == ("pkg.a", "pkg.b") for c in cycles)


def test_real_project_dag_clean(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    g = build_graph(tmp_path)
    assert find_cycles(g) == []


def test_cycle_excludes_attached_dag_branches():
    g = _g(
        ("entry", "a", (1,)),
        ("a", "b", (2,)),
        ("b", "a", (3,)),
        ("a", "leaf", (4,)),
    )
    [cycle] = find_cycles(g)
    assert cycle.modules == ("a", "b")
    pairs = {(e.source, e.target) for e in cycle.edges}
    assert pairs == {("a", "b"), ("b", "a")}
    # neither the entry-edge nor the leaf-edge participates
    assert ("entry", "a") not in pairs
    assert ("a", "leaf") not in pairs


def test_cycle_includes_self_loop_on_member():
    g = _g(("a", "b", (1,)), ("b", "a", (2,)), ("a", "a", (5,)))
    [cycle] = find_cycles(g)
    assert cycle.modules == ("a", "b")
    pairs = {(e.source, e.target) for e in cycle.edges}
    assert ("a", "a") in pairs


def test_same_sized_cycles_sorted_by_qualname():
    g = _g(
        ("z1", "z2", (1,)),
        ("z2", "z1", (2,)),
        ("a1", "a2", (3,)),
        ("a2", "a1", (4,)),
    )
    cycles = find_cycles(g)
    assert [c.modules[0] for c in cycles] == ["a1", "z1"]


def test_min_size_one_includes_singletons():
    g: nx.DiGraph = nx.DiGraph()
    g.add_node("isolated")
    g.add_edge("a", "b", lines=(1,))
    g.add_edge("b", "a", lines=(2,))
    cycles = find_cycles(g, min_size=1)
    sizes = sorted(len(c.modules) for c in cycles)
    # 1 SCC of size 2, plus 3 singleton SCCs (a, b are *in* the size-2 SCC,
    # not singletons; only "isolated" is a singleton SCC).
    assert sizes == [1, 2]


def test_aggregated_lines_preserved_in_cycle_edges():
    g: nx.DiGraph = nx.DiGraph()
    g.add_edge("a", "b", lines=(1, 4, 9))
    g.add_edge("b", "a", lines=(11,))
    [cycle] = find_cycles(g)
    by_pair = {(e.source, e.target): e.lines for e in cycle.edges}
    assert by_pair[("a", "b")] == (1, 4, 9)


def test_real_project_two_independent_cycles(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n")
    (pkg / "x.py").write_text("from pkg.y import thing\n")
    (pkg / "y.py").write_text("from pkg.x import other\n")
    g = build_graph(tmp_path)
    cycles = find_cycles(g)
    pair_modules = sorted(c.modules for c in cycles)
    assert pair_modules == [("pkg.a", "pkg.b"), ("pkg.x", "pkg.y")]


def test_real_project_reexport_mediated_cycle(tmp_path: Path):
    # Even with re-export resolution, a genuine cycle through __init__.py
    # is still a cycle: pkg/__init__.py imports a name from pkg.a, pkg.a
    # imports back from pkg (which after re-export resolution still resolves
    # to a *different* source module — so a→b cycle should appear iff the
    # re-export points at b).
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .a import Foo\n")
    (pkg / "a.py").write_text("from pkg import Bar\nclass Foo: ...\n")
    (pkg / "b.py").write_text("class Bar: ...\n")
    # Make Bar live in pkg.b via __init__.py re-export — but we already only
    # have one re-export of Foo. So `from pkg import Bar` falls back to `pkg`.
    # That manufactures the pkg → pkg.a → pkg cycle we expect to detect.
    g = build_graph(tmp_path)
    cycles = find_cycles(g)
    cycle_module_sets = [set(c.modules) for c in cycles]
    assert any({"pkg", "pkg.a"}.issubset(m) for m in cycle_module_sets)
