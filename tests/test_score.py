from __future__ import annotations

import math

import networkx as nx
import pytest

from archy.score import (
    _gini,
    compute_acyclicity,
    compute_complexity,
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
    score, n, tangle = compute_acyclicity(g)
    assert score == 1.0
    assert n == 0
    assert tangle == 0.0


def test_acyclicity_full_graph_in_cycle_is_zero():
    # All 2 nodes inside one SCC: tangle_ratio = 1.0, score = 0.
    g = _g(("a", "b"), ("b", "a"))
    score, n, tangle = compute_acyclicity(g)
    assert score == 0.0
    assert n == 1
    assert tangle == 1.0


def test_acyclicity_partial_tangle():
    # 2 of 4 nodes inside a cycle: tangle_ratio = 0.5, score = 0.5.
    g = _g(("a", "b"), ("b", "a"), ("c", "d"))
    score, n, tangle = compute_acyclicity(g)
    assert score == 0.5
    assert n == 1
    assert tangle == 0.5


def test_acyclicity_two_small_cycles():
    # 4 of 4 nodes inside cycles (two 2-node SCCs): tangle_ratio = 1.0.
    g = _g(("a", "b"), ("b", "a"), ("x", "y"), ("y", "x"))
    score, n, tangle = compute_acyclicity(g)
    assert score == 0.0
    assert n == 2
    assert tangle == 1.0


def test_acyclicity_small_cycle_in_large_graph():
    # 9-node DAG chain (n0..n8) plus a 2-node cycle ({a, b}).
    # Tangle ratio = 2/11; the headline win over the old 1/(1+N)
    # formula, which would have given 0.5 regardless of graph size.
    edges = [(f"n{i}", f"n{i + 1}") for i in range(8)]
    edges.extend([("a", "b"), ("b", "a")])
    g = _g(*edges)
    score, n, tangle = compute_acyclicity(g)
    assert tangle == pytest.approx(2 / 11)
    assert score == pytest.approx(1 - 2 / 11)
    assert n == 1


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


# --- complexity ---------------------------------------------------------------


def test_complexity_no_functions_is_vacuously_one():
    # No functions => no CC signal => cannot pull the geomean down.
    assert compute_complexity(cc_mean=0.0, function_count=0) == 1.0


def test_complexity_floor_cc_mean_of_one_is_one():
    # cc_mean=1 means every function has exactly one branch-free path:
    # the theoretical floor; the axis cannot do better.
    assert compute_complexity(cc_mean=1.0, function_count=100) == 1.0


def test_complexity_ceiling_cc_mean_of_six_is_zero():
    assert compute_complexity(cc_mean=6.0, function_count=100) == 0.0


def test_complexity_above_ceiling_clamps_to_zero():
    assert compute_complexity(cc_mean=12.5, function_count=100) == 0.0


def test_complexity_linear_midpoint():
    # cc_mean=3.5 sits at (3.5 - 1) / 5 = 0.5, so the axis returns 1 - 0.5 = 0.5.
    assert compute_complexity(cc_mean=3.5, function_count=100) == pytest.approx(0.5)


def test_complexity_bench_anchor_points():
    # Anchors from RESEARCH_METRICS.md sec 17. Verify the formula matches
    # what the docs claim so a future normalization tweak can't silently
    # invalidate the published interpretation bands.
    # mkdocs: cc_mean 1.77 -> 0.846
    assert compute_complexity(cc_mean=1.77, function_count=1_277) == pytest.approx(0.846)
    # archy: cc_mean 3.73 -> 0.454
    assert compute_complexity(cc_mean=3.73, function_count=157) == pytest.approx(0.454)
    # msgspec: cc_mean 5.33 -> 0.134
    assert compute_complexity(cc_mean=5.33, function_count=63) == pytest.approx(0.134)


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
    edges_cyclic = [*edges_clean, ("c", "a")]  # introduces one 3-node cycle
    clean = compute_score(_g(*edges_clean))
    cyclic = compute_score(_g(*edges_cyclic))
    # 3 of 8 nodes inside the cycle: tangle_ratio = 0.375, acyclicity = 0.625.
    assert cyclic.acyclicity == pytest.approx(0.625)
    assert clean.acyclicity == 1.0
    # Cycles definitely lower the overall score because acyclicity drops.
    assert cyclic.overall < clean.overall


def test_compute_score_geometric_mean_combines_multiplicatively():
    # Sanity check that overall is the geometric mean of the five
    # sub-metrics (complexity promoted from diagnostic in v0.20),
    # not their arithmetic mean.
    s = compute_score(_g(("a", "b")))
    expected = (s.modularity * s.acyclicity * s.depth * s.equality * s.complexity) ** 0.2
    assert s.overall == pytest.approx(expected)


def test_compute_score_all_components_in_unit_interval():
    g = _g(("a", "b"), ("b", "c"), ("c", "a"), ("d", "a"))
    s = compute_score(g)
    for value in (s.overall, s.modularity, s.acyclicity, s.depth, s.equality, s.complexity):
        assert 0.0 <= value <= 1.0
        assert math.isfinite(value)


def test_cc_aggregates_roll_up_from_node_attrs():
    # Synthesize a graph with explicit per-node CC attributes; the score
    # roll-up reads these without re-parsing anything, so a unit test on
    # ScoreInputs can verify the math without going through tree-sitter.
    g = nx.DiGraph()
    g.add_node("m1", external=False, function_count=2, cc_sum=5, cc_max=4)
    g.add_node("m2", external=False, function_count=3, cc_sum=6, cc_max=3)
    g.add_node("m3", external=False, function_count=0, cc_sum=0, cc_max=0)
    g.add_edge("m1", "m2")
    s = compute_score(g)
    assert s.inputs.function_count == 5
    assert s.inputs.cc_total == 11
    assert s.inputs.cc_max == 4
    assert s.inputs.cc_mean == pytest.approx(11 / 5)


def test_cc_aggregates_with_no_functions_are_zero():
    g = nx.DiGraph()
    g.add_node("m", external=False)
    s = compute_score(g)
    assert s.inputs.function_count == 0
    assert s.inputs.cc_total == 0
    assert s.inputs.cc_max == 0
    assert s.inputs.cc_mean == 0.0
