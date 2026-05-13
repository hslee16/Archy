"""MacCormack propagation cost and per-module reverse-reach fractions.

For an internal subgraph with `N` modules, propagation cost is the fraction
of the system that, on average, can be affected by a change to a randomly
chosen module. Formally:

    propagation_cost = sum over internal nodes of `|reverse_closure(n)|` / N^2

where `reverse_closure(n) = ancestors(n) ∪ {n}`, the set of internal modules
that transitively import `n` (so a change to `n` could ripple to them) plus
`n` itself. External nodes are excluded because they are not subject to
edits inside the project.

Per-module value `propagation_cost[n] = |reverse_closure(n)| / N` is the
fraction of the project that depends on `n`. This is the per-module
"blast radius" for editing `n`, useful as an agent-facing diagnostic. The
project-level value equals the mean of the per-module values.

Empirically the most-validated single architectural metric in the
defect-prediction literature: multiple Spearman-significant studies
linking low propagation cost to lower bug rate and lower maintenance
cost, starting with MacCormack-Rusnak-Baldwin (2006) and replicated in
the 2026 architectural-technical-debt literature.

See `docs/RESEARCH_METRICS.md` section 3 for the relationship to Lakos's
NCCD (same metric family, different normalization).
"""

from __future__ import annotations

import networkx as nx


def compute_propagation_cost(graph: nx.DiGraph) -> tuple[float, dict[str, float]]:
    """Return (project_propagation_cost, per_module_fractions).

    Operates on internal nodes only (`external=True` nodes are excluded).
    A graph with zero internal nodes returns `(0.0, {})`; single-node
    cases return `1.0` per the natural formula because the one node IS
    the whole project.
    """
    internal = {n for n, d in graph.nodes(data=True) if not d.get("external")}
    n = len(internal)
    if n == 0:
        return 0.0, {}

    per_module: dict[str, float] = {}
    total = 0
    for node in internal:
        ancestors = nx.ancestors(graph, node) & internal
        reach = len(ancestors) + 1  # +1 for self
        per_module[node] = reach / n
        total += reach
    return total / (n * n), per_module
