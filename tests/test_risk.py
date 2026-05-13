from __future__ import annotations

import networkx as nx

from archy.risk import compute_edit_risk


def _g(*edges: tuple[str, str], external: tuple[str, ...] = ()) -> nx.DiGraph:
    g = nx.DiGraph()
    for u, v in edges:
        g.add_node(u, external=u in external)
        g.add_node(v, external=v in external)
        g.add_edge(u, v)
    return g


def test_empty_graph_returns_empty():
    g = nx.DiGraph()
    assert compute_edit_risk(g) == {}


def test_single_internal_node_returns_empty():
    # Composite is undefined with one internal module; we return {} rather
    # than guess so callers don't see noise.
    g = nx.DiGraph()
    g.add_node("solo", external=False)
    assert compute_edit_risk(g) == {}


def test_stable_sink_has_zero_risk():
    # `b` is a pure sink: I(b) = 0 -> composite kills to 0 regardless of
    # high fan-in. Documents the bias: SDP-stable modules are intentionally
    # low edit-risk because they're not supposed to change.
    g = _g(("a", "b"), ("c", "b"))
    risk = compute_edit_risk(g)
    assert risk["b"] == 0.0


def test_pure_source_has_zero_risk():
    # `a` imports `b` but no one imports `a`: fan_in=0 -> composite=0.
    g = _g(("a", "b"))
    risk = compute_edit_risk(g)
    assert risk["a"] == 0.0


def test_central_volatile_module_has_nonzero_risk():
    # `m` depends on `dep` (instability rises) and is imported by `x`, `y`, `z`
    # (fan_in and propagation_cost rise). All three components non-zero -> risk > 0.
    g = _g(
        ("x", "m"),
        ("y", "m"),
        ("z", "m"),
        ("m", "dep"),
    )
    risk = compute_edit_risk(g)
    assert risk["m"] > 0.0
    # And it should dominate the other modules:
    assert risk["m"] == max(risk.values())


def test_external_nodes_excluded_from_normalization():
    # External nodes neither count toward N_internal nor contribute to fan_in,
    # so adding them must not change anyone's score.
    base = _g(("a", "b"), ("c", "b"), ("b", "d"))
    with_ext = _g(
        ("a", "b"),
        ("c", "b"),
        ("b", "d"),
        ("a", "ext"),
        external=("ext",),
    )
    assert compute_edit_risk(base)["b"] == compute_edit_risk(with_ext)["b"]


def test_risk_bounded_in_unit_interval():
    # All three terms are 0..1 so the geometric mean is too. Worst-case
    # densely-coupled graph still produces values <= 1.
    g = _g(
        ("a", "b"),
        ("b", "a"),
        ("a", "c"),
        ("c", "a"),
        ("b", "c"),
        ("c", "b"),
    )
    risk = compute_edit_risk(g)
    assert all(0.0 <= v <= 1.0 for v in risk.values())
