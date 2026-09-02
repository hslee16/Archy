"""Per-module edit-risk composite for agent-facing "is this edit dangerous?" checks.

The composite combines three already-computed structural signals into a single
0-1 score per internal module:

* ``propagation_cost[n]`` - fraction of the project that transitively depends
  on ``n``. Captures *downstream blast radius* (how far a bad edit spreads).
* ``fan_in_norm[n] = in_degree(n) / max(1, N_internal - 1)`` - direct importer
  count, normalized to [0, 1]. Captures *local coupling pressure* (how many
  call sites would have to be touched if the public surface changes).
* ``instability[n]`` - Martin's ``I = Ce / (Ce + Ca)``. Captures *volatility*
  (how exposed ``n`` is to upstream churn it does not control).

Composite is the geometric mean of the three:

    risk[n] = (propagation_cost[n] * fan_in_norm[n] * instability[n]) ** (1/3)

Geometric mean is intentional: all three terms must be non-trivially high for
risk to be high. This biases the score toward *central and fragile* modules
- the exact "scope drift" and "subtle regression" failure mode that the 2026
coding-agent literature names as the load-bearing risk for AI-assisted edits.
A stable load-bearing module (``I = 0``) lands at risk = 0 by design: SDP
keeps such modules from changing often, so editing them is structurally a
red flag rather than a routine risk to grade. Users who want pure blast
radius should read ``propagation_cost`` directly.

The metric is a *diagnostic*, not a score-axis. It is exposed on graph nodes,
graph summaries, and via the ``archy_high_risk_modules`` MCP tool so an agent
can consult it before a non-trivial edit. It is not folded into ``archy
score``'s overall number - that boundary is deliberate (see ``FUTURE.md``).

archy:owns        compute_edit_risk
archy:mirrored-by compute_edit_risk -> archy.diff_summary, archy.graph, archy.mcp,
                  archy.refactor
"""

from __future__ import annotations

import networkx as nx

from archy.instability import compute_instability
from archy.reach import compute_propagation_cost


def compute_edit_risk(graph: nx.DiGraph) -> dict[str, float]:
    """Return the per-module edit-risk composite for every internal module.

    Operates only on internal nodes (``external=True`` nodes are excluded
    from both the per-module map and the fan-in normalization denominator).
    A graph with fewer than two internal nodes returns an empty dict: the
    composite has no meaningful values when there are no peers to import
    from or be imported by.
    """
    internal = {n for n, d in graph.nodes(data=True) if not d.get("external")}
    n_internal = len(internal)
    if n_internal < 2:
        return {}

    instability = compute_instability(graph)
    _, propagation_cost = compute_propagation_cost(graph)
    denom = n_internal - 1

    out: dict[str, float] = {}
    for node in internal:
        fan_in = sum(1 for pred in graph.predecessors(node) if pred in internal)
        fan_in_norm = fan_in / denom
        prop = propagation_cost.get(node, 0.0)
        inst = instability.get(node, 0.0)
        out[node] = (prop * fan_in_norm * inst) ** (1.0 / 3.0)
    return out
