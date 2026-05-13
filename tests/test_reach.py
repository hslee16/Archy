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


def test_project_value_equals_mean_of_per_module():
    # Algebraic identity: project_pc = mean(per_module_pc).
    g = _g(("a", "b"), ("b", "c"), ("a", "d"), ("d", "c"))
    pc, per_module = compute_propagation_cost(g)
    assert pc == pytest.approx(sum(per_module.values()) / len(per_module))


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
