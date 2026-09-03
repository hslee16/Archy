from __future__ import annotations

import networkx as nx
import pytest

from archy.reach import compute_propagation_cost


def _g(*edges: tuple[str, str], external: tuple[str, ...] = ()) -> nx.DiGraph:
    g = nx.DiGraph()
    for u, v in edges:
        g.add_node(u, external=u in external)
        g.add_node(v, external=v in external)
        g.add_edge(u, v)
    return g


def test_empty_graph_is_zero():
    g = nx.DiGraph()
    pc, per_module = compute_propagation_cost(g)
    assert pc == 0.0
    assert per_module == {}


def test_single_node_is_one():
    # n=1, reach=1 (just self), 1/(1*1) = 1.0
    g = nx.DiGraph()
    g.add_node("a", external=False)
    pc, per_module = compute_propagation_cost(g)
    assert pc == 1.0
    assert per_module == {"a": 1.0}


def test_two_node_chain():
    # a -> b; reverse closure of a = {a} (size 1); reverse closure of b = {a, b} (size 2)
    # per_module = {a: 1/2, b: 2/2} = {a: 0.5, b: 1.0}
    # project = (1 + 2) / (2*2) = 0.75
    g = _g(("a", "b"))
    pc, per_module = compute_propagation_cost(g)
    assert per_module == {"a": 0.5, "b": 1.0}
    assert pc == 0.75


def test_completely_decoupled_modules():
    # Three modules, zero edges -> each module's reverse closure is just itself.
    # Per module = 1/3 = 0.333..., project = 3 / 9 = 0.333...
    g = nx.DiGraph()
    for n in ("a", "b", "c"):
        g.add_node(n, external=False)
    pc, per_module = compute_propagation_cost(g)
    assert pc == pytest.approx(1.0 / 3.0)
    assert per_module == {n: 1.0 / 3.0 for n in ("a", "b", "c")}


def test_external_nodes_excluded():
    # `ext` is external; only `a` and `b` count.
    # a -> b, a -> ext. Internal subgraph: a -> b.
    # Per module = {a: 0.5, b: 1.0}, project = 0.75 (same as two-node chain).
    g = _g(("a", "b"), ("a", "ext"), external=("ext",))
    pc, per_module = compute_propagation_cost(g)
    assert "ext" not in per_module
    assert per_module == {"a": 0.5, "b": 1.0}
    assert pc == 0.75


def test_diamond_reach_is_reverse_not_forward():
    """Hand-worked reverse reach on a diamond, per module and for the project.

    This replaces an assertion that `project == mean(per_module)`, which the
    implementation makes an ALGEBRAIC IDENTITY: it sets `per_module[n] = reach/n`
    and `total += reach` in one loop, then returns `total/(n*n)`. That holds for
    every possible definition of `reach`, so swapping `nx.ancestors` for
    `nx.descendants` -- inverting the metric -- left it green (#439).

    The diamond is the fixture that tells the two apart: `a` is upstream of
    everything and `c` downstream of everything, so reverse and forward reach
    give them opposite values.

        a: reaches only itself         1/4 = 0.25
        b: ancestors {a}               2/4 = 0.5
        d: ancestors {a}               2/4 = 0.5
        c: ancestors {a, b, d}         4/4 = 1.0
        project: (1+2+2+4) / 16            = 0.5625
    """
    g = _g(("a", "b"), ("b", "c"), ("a", "d"), ("d", "c"))

    pc, per_module = compute_propagation_cost(g)

    assert per_module == {"a": 0.25, "b": 0.5, "c": 1.0, "d": 0.5}
    assert pc == pytest.approx(0.5625)


def test_fully_coupled_clique_is_one():
    # If every module reaches every module, propagation cost is 1.0.
    g = nx.DiGraph()
    nodes = ["a", "b", "c"]
    for n in nodes:
        g.add_node(n, external=False)
    # Make it a directed cycle so every node reverse-reaches every node.
    g.add_edge("a", "b")
    g.add_edge("b", "c")
    g.add_edge("c", "a")
    pc, per_module = compute_propagation_cost(g)
    assert pc == 1.0
    for n in nodes:
        assert per_module[n] == 1.0


def test_dag_with_branching():
    # a -> b, a -> c, b -> d, c -> d.
    # Reverse closures (internal-only):
    #   a: {a}                 size 1
    #   b: {a, b}              size 2
    #   c: {a, c}              size 2
    #   d: {a, b, c, d}        size 4
    # Per module = {a: 0.25, b: 0.5, c: 0.5, d: 1.0}, project = 9/16 = 0.5625
    g = _g(("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"))
    pc, per_module = compute_propagation_cost(g)
    assert per_module == {"a": 0.25, "b": 0.5, "c": 0.5, "d": 1.0}
    assert pc == pytest.approx(9.0 / 16.0)
