"""Composite architecture quality score over an import graph.

Four sub-metrics, each in [0, 1], aggregated via geometric mean so a
weak score on any axis pulls the overall down hard:

* modularity: Newman's Q over a greedy community partition,
              normalized as (Q + 0.5) / 1.5 to map [-0.5, 1.0] -> [0, 1].
* acyclicity: 1 - tangle_ratio, where tangle_ratio is the fraction of nodes
              that sit inside an SCC of size >= 2 (or a self-looped singleton).
              A small isolated cycle in a large codebase is a smaller pathology
              than the same cycle dominating a small codebase; the ratio
              captures that. cycle_count is preserved as a diagnostic.
* depth:      1 / (1 + max_depth / 8) where max_depth is the longest path
              through the SCC condensation.
* equality:   1 - Gini(out-degree). Penalizes god-module topology.

The model and four formulas follow sentrux's quality-signal-design.md.
sentrux ships a fifth metric (redundancy: dead + duplicate function
ratio); archy defers it because its static computation is fragile
under dynamic dispatch, decorators, and `if __name__ == "__main__":`
gates. See docs/LEARNINGS.md for the comparison.
"""

from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy.cycles import find_cycles
from archy.reach import compute_propagation_cost


class ScoreInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    module_count: int
    edge_count: int
    cycle_count: int
    tangle_ratio: float
    max_depth: int
    community_count: int
    raw_modularity: float
    raw_gini: float
    propagation_cost: float = 0.0
    # Call-graph diagnostics. Not folded into the four-axis geometric mean
    # in v0.16 — shipped as a diagnostic first per the MacCormack v0.13.3
    # precedent. Promotion to a score axis depends on the 27-project
    # benchmark showing orthogonality to existing axes.
    call_edge_count: int = 0
    total_calls: int = 0
    calls_per_edge: float = 0.0


class Score(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: float
    modularity: float
    acyclicity: float
    depth: float
    equality: float
    inputs: ScoreInputs


def compute_score(graph: nx.DiGraph) -> Score:
    mod, communities, raw_q = compute_modularity(graph)
    acy, cycle_count, tangle_ratio = compute_acyclicity(graph)
    dep, max_depth = compute_depth(graph)
    eq, raw_gini = compute_equality(graph)
    propagation_cost, _ = compute_propagation_cost(graph)
    call_edge_count, total_calls, calls_per_edge = _call_stats(graph)
    overall = (mod * acy * dep * eq) ** 0.25
    return Score(
        overall=overall,
        modularity=mod,
        acyclicity=acy,
        depth=dep,
        equality=eq,
        inputs=ScoreInputs(
            module_count=graph.number_of_nodes(),
            edge_count=graph.number_of_edges(),
            cycle_count=cycle_count,
            tangle_ratio=tangle_ratio,
            max_depth=max_depth,
            community_count=communities,
            raw_modularity=raw_q,
            raw_gini=raw_gini,
            propagation_cost=propagation_cost,
            call_edge_count=call_edge_count,
            total_calls=total_calls,
            calls_per_edge=calls_per_edge,
        ),
    )


def _call_stats(graph: nx.DiGraph) -> tuple[int, int, float]:
    """Per-graph aggregates of call-edge data attached by the call-resolution pass.

    `call_edge_count` counts edges carrying any calls; `total_calls` sums
    every resolved call site; `calls_per_edge` averages calls over edges
    that carry at least one (not over all edges). The all-edges
    denominator would compress the signal toward zero on import-heavy
    graphs even when calls are dense on the edges that do have them.
    """
    call_edge_count = 0
    total = 0
    for _, _, data in graph.edges(data=True):
        count = data.get("call_count", 0)
        if count > 0:
            call_edge_count += 1
            total += count
    cpe = total / call_edge_count if call_edge_count else 0.0
    return call_edge_count, total, cpe


def compute_modularity(graph: nx.DiGraph) -> tuple[float, int, float]:
    """Return (normalized_score, n_communities, raw_Q).

    Normalization follows sentrux: `(Q + 0.5) / 1.5`, mapping the
    canonical Newman range [-0.5, 1.0] onto [0, 1]. Trivial graphs
    (no edges, or fewer than two nodes) return 1.0 vacuously.
    """
    if graph.number_of_edges() == 0 or graph.number_of_nodes() < 2:
        return 1.0, max(graph.number_of_nodes(), 1), 1.0
    communities = list(nx.community.greedy_modularity_communities(graph))
    raw_q = float(nx.community.modularity(graph, communities))
    normalized = max(0.0, min(1.0, (raw_q + 0.5) / 1.5))
    return normalized, len(communities), raw_q


def compute_acyclicity(graph: nx.DiGraph) -> tuple[float, int, float]:
    """Return (score, cycle_count, tangle_ratio).

    score = 1 - tangle_ratio, where tangle_ratio is the fraction of graph
    nodes that sit inside a cycle (an SCC of size >= 2 or a self-looped
    singleton). cycle_count is the number of such SCCs and is preserved
    as a diagnostic.
    """
    cycles = find_cycles(graph, min_size=2)
    n = graph.number_of_nodes()
    if n == 0:
        return 1.0, 0, 0.0
    tangled = sum(len(c.modules) for c in cycles)
    tangle_ratio = tangled / n
    return 1.0 - tangle_ratio, len(cycles), tangle_ratio


def compute_depth(graph: nx.DiGraph) -> tuple[float, int]:
    """Longest path in the SCC condensation. Cycles collapse to one node first."""
    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        return 1.0, 0
    condensation = nx.condensation(graph)
    if condensation.number_of_edges() == 0:
        return 1.0, 0
    max_depth = nx.dag_longest_path_length(condensation)
    return 1.0 / (1 + max_depth / 8), max_depth


def compute_equality(graph: nx.DiGraph) -> tuple[float, float]:
    out_degrees = [graph.out_degree(n) for n in graph.nodes()]
    if not out_degrees or sum(out_degrees) == 0:
        return 1.0, 0.0
    gini = _gini(out_degrees)
    return 1.0 - gini, gini


def _gini(values: list[int]) -> float:
    # Sorted-rank form (O(n log n)) rather than the canonical pairwise-
    # difference definition (O(n^2)); call sites pass per-module out-degrees
    # which can run into the hundreds, and the score recomputes on every
    # snapshot/diff in the agent loop.
    sorted_v = sorted(values)
    n = len(sorted_v)
    total = sum(sorted_v)
    if total == 0 or n == 0:
        return 0.0
    weighted = sum((2 * i - n - 1) * x for i, x in enumerate(sorted_v, start=1))
    return weighted / (n * total)
