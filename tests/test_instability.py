from __future__ import annotations

import networkx as nx
import pytest

from archy.instability import compute_instability
from archy.layers import find_sdp_violations


def _g(*edges: tuple[str, str], external: tuple[str, ...] = ()) -> nx.DiGraph:
    g = nx.DiGraph()
    for u, v in edges:
        g.add_node(u, external=u in external)
        g.add_node(v, external=v in external)
        g.add_edge(u, v)
    return g


def test_pure_source_is_maximally_unstable():
    # `a` imports `b` and nothing imports `a` -> I(a) = 1/(1+0) = 1.
    g = _g(("a", "b"))
    result = compute_instability(g)
    assert result["a"] == 1.0


def test_pure_sink_is_maximally_stable():
    # `b` is imported by `a` and imports nothing -> I(b) = 0/(0+1) = 0.
    g = _g(("a", "b"))
    result = compute_instability(g)
    assert result["b"] == 0.0


def test_isolated_module_reports_zero():
    g = nx.DiGraph()
    g.add_node("loner", external=False)
    result = compute_instability(g)
    # Vacuous: no edges either way. Reported as 0.0 so callers can iterate
    # the full module set without KeyError handling.
    assert result["loner"] == 0.0


def test_external_edges_do_not_affect_instability():
    # `a` imports an external module `ext`; that edge should not raise Ce.
    # Without `b`, `a` would be isolated -> I=0.
    g = _g(("a", "ext"), external=("ext",))
    result = compute_instability(g)
    assert "ext" not in result  # external nodes are excluded entirely
    assert result["a"] == 0.0


def test_mixed_module_partial_instability():
    # `b` imports `c` (Ce=1) and is imported by `a` (Ca=1) -> I(b) = 1/2.
    g = _g(("a", "b"), ("b", "c"))
    result = compute_instability(g)
    assert result["b"] == 0.5


# --- SDP violations -----------------------------------------------------------


@pytest.fixture
def sdp_violation_graph() -> nx.DiGraph:
    # `a` is heavily depended on (Ca=3, Ce=1) -> I(a) = 1/4 = 0.25
    # `b` depends on a lot (Ce=3, Ca=1) -> I(b) = 3/4 = 0.75
    # The edge `a -> b` is from a stable module to a less-stable one;
    # SDP says that's backwards.
    return _g(
        ("x1", "a"),
        ("x2", "a"),
        ("x3", "a"),
        ("a", "b"),
        ("b", "y1"),
        ("b", "y2"),
        ("b", "y3"),
    )


def test_find_sdp_violations_flags_strict_violation(sdp_violation_graph: nx.DiGraph):
    findings = find_sdp_violations(sdp_violation_graph)
    [violation] = [v for v in findings if v.source == "a" and v.target == "b"]
    assert violation.source_instability == 0.25
    assert violation.target_instability == 0.75


def test_find_sdp_violations_respects_tolerance(sdp_violation_graph: nx.DiGraph):
    # The a->b gap is 0.5; tolerance >= 0.5 silences it.
    assert all(v.source != "a" for v in find_sdp_violations(sdp_violation_graph, tolerance=0.5))


def test_find_sdp_violations_ignores_external_targets():
    # External targets have no I, so the SDP comparison cannot be made
    # and the edge is skipped.
    g = _g(("a", "ext"), external=("ext",))
    assert find_sdp_violations(g) == []
