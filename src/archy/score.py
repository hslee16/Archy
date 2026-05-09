"""Composite architecture quality score over an import graph.

Four sub-metrics, each in [0, 1], aggregated via geometric mean so a
weak score on any axis pulls the overall down hard:

* modularity: Newman's Q over a greedy community partition,
              normalized as (Q + 0.5) / 1.5 to map [-0.5, 1.0] -> [0, 1].
* acyclicity: 1 / (1 + cycle_count) where cycle_count counts SCCs of size >= 2.
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

from dataclasses import dataclass

import networkx as nx

from archy.cycles import find_cycles


@dataclass(frozen=True)
class ScoreInputs:
    module_count: int
    edge_count: int
    cycle_count: int
    max_depth: int
    community_count: int
    raw_modularity: float
    raw_gini: float


@dataclass(frozen=True)
class Score:
    overall: float
    modularity: float
    acyclicity: float
    depth: float
    equality: float
    inputs: ScoreInputs


def compute_score(graph: nx.DiGraph) -> Score:
    mod, communities, raw_q = compute_modularity(graph)
    acy, cycle_count = compute_acyclicity(graph)
    dep, max_depth = compute_depth(graph)
    eq, raw_gini = compute_equality(graph)
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
            max_depth=max_depth,
            community_count=communities,
            raw_modularity=raw_q,
            raw_gini=raw_gini,
        ),
    )


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


def compute_acyclicity(graph: nx.DiGraph) -> tuple[float, int]:
    cycles = find_cycles(graph, min_size=2)
    return 1.0 / (1 + len(cycles)), len(cycles)


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
    # Standard sorted formula:
    #   G = sum_{i=1..n} (2i - n - 1) * x_i / (n * sum(x_i))
    # Returns a value in [0, 1) for non-degenerate inputs (i.e., sum > 0).
    sorted_v = sorted(values)
    n = len(sorted_v)
    total = sum(sorted_v)
    if total == 0 or n == 0:
        return 0.0
    weighted = sum((2 * i - n - 1) * x for i, x in enumerate(sorted_v, start=1))
    return weighted / (n * total)
