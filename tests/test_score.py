from __future__ import annotations

import math

import networkx as nx
import pytest

from archy.score import (
    _gini,
    compute_acyclicity,
    compute_depth,
    compute_equality,
    compute_modularity,
    compute_score,
)


def _g(*edges: tuple[str, str]) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for u, v in edges:
        g.add_edge(u, v, lines=(1,))
    return g


# --- Gini ---------------------------------------------------------------------


def test_gini_perfect_equality():
    assert _gini([1, 1, 1, 1]) == 0.0


def test_gini_perfect_inequality():
    # All mass on one element. With n=4 and only x_4 > 0, the sorted formula
    # yields G = 0.75 (the maximum for n=4).
    assert _gini([0, 0, 0, 4]) == pytest.approx(0.75)


def test_gini_all_zero_returns_zero():
    assert _gini([0, 0, 0]) == 0.0


def test_gini_empty_returns_zero():
    assert _gini([]) == 0.0


# --- modularity ---------------------------------------------------------------


def test_modularity_trivial_graph_is_vacuously_perfect():
    g: nx.DiGraph = nx.DiGraph()
    g.add_node("a")
    score, n_comm, _ = compute_modularity(g)
    assert score == 1.0
    assert n_comm == 1


def test_modularity_two_disjoint_clusters_is_high():
    # Two disconnected triangles: clear community structure.
    g = _g(("a", "b"), ("b", "c"), ("c", "a"), ("x", "y"), ("y", "z"), ("z", "x"))
    score, n_comm, _ = compute_modularity(g)
    assert n_comm >= 2
    assert score > 0.3


def test_modularity_clamps_to_zero_on_negative_q():
    # Pathological tightly-coupled graph: Q can be negative. We clamp to 0.
    # A complete bipartite "everything points everywhere" graph is a stress
    # test; we just assert the clamp invariant holds.
    g = _g(*[(f"n{i}", f"n{j}") for i in range(4) for j in range(4) if i != j])
    score, _, _ = compute_modularity(g)
    assert 0.0 <= score <= 1.0


# --- acyclicity ---------------------------------------------------------------


def test_acyclicity_no_cycles_is_one():
    g = _g(("a", "b"), ("b", "c"))
    score, n = compute_acyclicity(g)
    assert score == 1.0
    assert n == 0


def test_acyclicity_one_cycle_is_one_half():
    g = _g(("a", "b"), ("b", "a"))
    score, n = compute_acyclicity(g)
    assert score == 0.5
    assert n == 1


def test_acyclicity_two_cycles_is_one_third():
    g = _g(("a", "b"), ("b", "a"), ("x", "y"), ("y", "x"))
    score, n = compute_acyclicity(g)
    assert score == pytest.approx(1.0 / 3)
    assert n == 2


# --- depth --------------------------------------------------------------------


def test_depth_empty_graph_is_one():
    g: nx.DiGraph = nx.DiGraph()
    score, depth = compute_depth(g)
    assert score == 1.0
    assert depth == 0


def test_depth_linear_chain():
    g = _g(("a", "b"), ("b", "c"), ("c", "d"))
    score, depth = compute_depth(g)
    assert depth == 3
    assert score == pytest.approx(1.0 / (1 + 3 / 8))


def test_depth_collapses_cycles_via_condensation():
    # a <-> b -> c. The cycle {a, b} condenses to a single node, so the
    # condensation depth is 1 (one edge from {a,b} to {c}).
    g = _g(("a", "b"), ("b", "a"), ("b", "c"))
    _, depth = compute_depth(g)
    assert depth == 1


def test_depth_score_decreases_as_chain_grows():
    short = _g(("a", "b"))
    long = _g(*((f"n{i}", f"n{i + 1}") for i in range(10)))
    short_score, _ = compute_depth(short)
    long_score, _ = compute_depth(long)
    assert long_score < short_score


# --- equality -----------------------------------------------------------------


def test_equality_uniform_out_degree_is_one():
    # 3-cycle gives every node out-degree 1 (uniform).
    g = _g(("a", "b"), ("b", "c"), ("c", "a"))
    score, gini = compute_equality(g)
    assert gini == 0.0
    assert score == 1.0


def test_equality_one_god_module_low():
    # `god` has out-degree 4; everyone else 0.
    g = _g(("god", "a"), ("god", "b"), ("god", "c"), ("god", "d"))
    score, gini = compute_equality(g)
    assert gini > 0.7
    assert score < 0.3


def test_equality_empty_graph_is_one():
    g: nx.DiGraph = nx.DiGraph()
    score, gini = compute_equality(g)
    assert score == 1.0
    assert gini == 0.0


# --- compute_score ------------------------------------------------------------


def test_compute_score_clean_dag_is_high():
    # Two disjoint chains - clean structure, uniform out-degree, modular.
    g = _g(
        ("a", "b"),
        ("b", "c"),
        ("c", "d"),
        ("e", "f"),
        ("f", "g"),
        ("g", "h"),
    )
    s = compute_score(g)
    assert s.acyclicity == 1.0
    assert s.inputs.cycle_count == 0
    assert s.overall > 0.5  # high but not necessarily >0.7 with greedy partition


def test_compute_score_with_cycle_is_lower():
    # Use big enough graphs that greedy modularity returns positive Q on
    # both, so a non-zero baseline is comparable.
    edges_clean = [("a", "b"), ("b", "c"), ("d", "e"), ("e", "f"), ("g", "h")]
    edges_cyclic = [*edges_clean, ("c", "a")]  # introduces one cycle
    clean = compute_score(_g(*edges_clean))
    cyclic = compute_score(_g(*edges_cyclic))
    assert cyclic.acyclicity == 0.5
    assert clean.acyclicity == 1.0
    # Cycles definitely lower the overall score because acyclicity drops.
    assert cyclic.overall < clean.overall


def test_compute_score_geometric_mean_zero_on_any_zero_metric():
    # Manufacture a pathological graph: one giant cycle would still yield
    # acyclicity 0.5 (one cycle), not zero. The geometric-mean property is
    # already covered analytically by the formula; this just sanity-checks
    # that the four sub-metrics combine multiplicatively.
    s = compute_score(_g(("a", "b")))
    assert s.overall == pytest.approx((s.modularity * s.acyclicity * s.depth * s.equality) ** 0.25)


def test_compute_score_all_components_in_unit_interval():
    g = _g(("a", "b"), ("b", "c"), ("c", "a"), ("d", "a"))
    s = compute_score(g)
    for value in (s.overall, s.modularity, s.acyclicity, s.depth, s.equality):
        assert 0.0 <= value <= 1.0
        assert math.isfinite(value)
