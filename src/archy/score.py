"""Composite architecture quality score over an import graph.

Five sub-metrics, each in [0, 1], aggregated via geometric mean so a
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
* complexity: 1 - clamp((cc_mean - 1) / 8, 0, 1), where cc_mean is the
              mean per-function McCabe cyclomatic complexity across all
              internal modules. cc_mean=1 yields 1.0; cc_mean>=9 yields
              0.0; the 27-project bench (RESEARCH_METRICS.md sec 17)
              sits in [1.77, 5.33] which maps to [0.90, 0.46]. Promoted
              from diagnostic to score axis in v0.20; the v0.20 divisor
              of /5 was widened to /8 in v0.23 after the original
              calibration under-
              ranged real-world repos whose cc_mean lands in [6, 9)
              (e.g. validator/parser-heavy backends), driving the whole
              geomean to 0 on a single axis. Projects with fewer than
              20 functions return 1.0 vacuously: cc_mean is statistically
              unstable on tiny function counts and a single branchy
              dispatcher can dominate the mean.

The model and original four formulas follow sentrux's
quality-signal-design.md. sentrux ships a different fifth metric
(redundancy: dead + duplicate function ratio); archy defers redundancy
because its static computation is fragile under dynamic dispatch,
decorators, and `if __name__ == "__main__":` gates. See
docs/LEARNINGS.md for the comparison.
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
    # in v0.16 - shipped as a diagnostic first per the MacCormack v0.13.3
    # precedent. The v0.20 axis-promotion review (docs/AXIS_REVIEW.md)
    # decided against promoting `calls_per_edge` to a score axis on
    # directionality / actionability / discriminant-validity grounds.
    call_edge_count: int = 0
    total_calls: int = 0
    calls_per_edge: float = 0.0
    # Call-weighted Newman Q as a parallel diagnostic next to the
    # unweighted modularity score. Shipped per docs/CALL_WEIGHTED_Q_EMPIRICS.md
    # (Path B: parallel diagnostic, not axis replacement). The gap between
    # `raw_modularity` and `raw_modularity_weighted` is the load-bearing
    # signal: it detects mismatch between the import-graph decomposition
    # and the call-graph decomposition. See `compute_modularity_weighted`.
    raw_modularity_weighted: float = 0.0
    modularity_weighted_community_count: int = 0
    # Cyclomatic complexity diagnostics (v0.17, diagnostic). Per-function
    # CC aggregated to the project level. Not folded into the four-axis
    # geometric mean: ships diagnostic-first, same precedent as call edges
    # (v0.16) and propagation cost (v0.13.3). The long-term target for the
    # equality axis is gini(per_function_cc) instead of gini(out_degree);
    # the underlying data lives here so a future promotion has empirical
    # ground to stand on.
    function_count: int = 0
    cc_total: int = 0
    cc_max: int = 0
    cc_mean: float = 0.0


class Score(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: float
    modularity: float
    acyclicity: float
    depth: float
    equality: float
    complexity: float
    inputs: ScoreInputs


def compute_score(graph: nx.DiGraph) -> Score:
    mod, communities, raw_q = compute_modularity(graph)
    acy, cycle_count, tangle_ratio = compute_acyclicity(graph)
    dep, max_depth = compute_depth(graph)
    eq, raw_gini = compute_equality(graph)
    propagation_cost, _ = compute_propagation_cost(graph)
    call_edge_count, total_calls, calls_per_edge = _call_stats(graph)
    function_count, cc_total, cc_max, cc_mean = _cc_stats(graph)
    cpx = compute_complexity(cc_mean, function_count)
    _, communities_weighted, raw_q_weighted = compute_modularity_weighted(graph)
    overall = (mod * acy * dep * eq * cpx) ** 0.2
    return Score(
        overall=overall,
        modularity=mod,
        acyclicity=acy,
        depth=dep,
        equality=eq,
        complexity=cpx,
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
            function_count=function_count,
            cc_total=cc_total,
            cc_max=cc_max,
            cc_mean=cc_mean,
            raw_modularity_weighted=raw_q_weighted,
            modularity_weighted_community_count=communities_weighted,
        ),
    )


def _cc_stats(graph: nx.DiGraph) -> tuple[int, int, int, float]:
    """Project-wide CC roll-up: (function_count, cc_total, cc_max, cc_mean).

    Computed off the per-node `function_count` / `cc_sum` / `cc_max`
    attributes attached by `build_graph` so we don't re-parse anything.
    External modules have no CC data; they're skipped via the missing-key
    default rather than a node-type check, which keeps subgraph callers
    (e.g. archy_graph_focus) honest.
    """
    n = 0
    total = 0
    max_cc = 0
    for _, data in graph.nodes(data=True):
        cnt = data.get("function_count", 0)
        if cnt == 0:
            continue
        n += cnt
        total += data.get("cc_sum", 0)
        node_max = data.get("cc_max", 0)
        if node_max > max_cc:
            max_cc = node_max
    mean = total / n if n else 0.0
    return n, total, max_cc, mean


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


def compute_modularity_weighted(graph: nx.DiGraph) -> tuple[float, int, float]:
    """Call-weighted Newman Q diagnostic. Returns (normalized_score, n_communities, raw_Q).

    Tuple order matches ``compute_modularity`` so the two functions are
    drop-in interchangeable at call sites.

    Identical to ``compute_modularity`` except edges are weighted by
    ``call_count``; edges without resolved calls (import-only edges) get
    weight 1 rather than 0 so every structural edge stays in the
    community-detection computation. The greedy algorithm is rerun under
    the weighted graph; the resulting partition is independent of the
    unweighted partition.

    The gap between the unweighted and weighted raw Q is the load-bearing
    signal: a wider positive gap means call traffic amplifies the
    structural community shape; a negative gap means call traffic crosses
    community boundaries. See ``docs/CALL_WEIGHTED_Q_EMPIRICS.md`` for the
    empirical justification of this shape over axis replacement.

    Done on a copy so the input graph is never mutated; the per-edge ``_w``
    fallback (call_count else 1) is a deliberate weight policy choice
    described in the bench script and the empirics doc.
    """
    if graph.number_of_edges() == 0 or graph.number_of_nodes() < 2:
        return 1.0, max(graph.number_of_nodes(), 1), 1.0
    weighted = graph.copy()
    for _, _, data in weighted.edges(data=True):
        cc = data.get("call_count", 0)
        data["_w"] = cc if cc > 0 else 1
    communities = list(nx.community.greedy_modularity_communities(weighted, weight="_w"))
    raw_q = float(nx.community.modularity(weighted, communities, weight="_w"))
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


SMALL_PROJECT_FUNCTION_THRESHOLD = 20


def compute_complexity(cc_mean: float, function_count: int) -> float:
    """Map mean per-function cyclomatic complexity onto [0, 1].

    Linear: ``1 - clamp((cc_mean - 1) / 8, 0, 1)``. cc_mean=1 (the
    theoretical floor: every function has exactly one branch-free
    path) maps to 1.0; cc_mean=9 and above map to 0.0; the typical
    Python project sits in the [2, 5] band which maps roughly linearly
    to [0.875, 0.5].

    Anchor points from the 27-project benchmark (RESEARCH_METRICS.md
    sec 17): mkdocs (1.77) -> 0.904; archy (3.73) -> 0.659; msgspec
    (5.33) -> 0.459.

    Vacuous cases (return 1.0):
    - No functions at all (e.g., a project of only empty ``__init__.py``
      files): there is no complexity signal to measure.
    - Fewer than 20 functions: cc_mean is statistically unstable on
      tiny inputs; one branchy dispatcher in a 10-function module can
      pull the mean above 4 even when the rest is healthy. Mirrors the
      empty-input convention the other axes use.
    """
    if function_count < SMALL_PROJECT_FUNCTION_THRESHOLD:
        return 1.0
    excess = (cc_mean - 1.0) / 8.0
    clamped = max(0.0, min(1.0, excess))
    return 1.0 - clamped


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
