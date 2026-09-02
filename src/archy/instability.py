"""Martin's instability metric (`I`) over an import graph.

For each internal module, `I = Ce / (Ce + Ca)` where:

* Ce (efferent coupling) = number of distinct internal modules this one
  imports. Counts how dependent the module is on others.
* Ca (afferent coupling) = number of distinct internal modules that
  import this one. Counts how depended-on the module is by others.

`I` ranges from 0 (maximally stable: nothing the module depends on can
break it because it depends on nothing) to 1 (maximally unstable: every
change to a dependency could ripple through). External edges are
deliberately excluded so the metric reflects internal architecture, not
how much third-party surface a project pulls in.

Modules with no incoming or outgoing internal edges are reported as
`I = 0.0` (vacuous; no information to compute) rather than left out so
callers can rely on a complete map.

archy:owns        compute_instability
archy:mirrored-by compute_instability -> archy.graph, archy.layers, archy.mcp,
                  archy.refactor, archy.risk
"""

from __future__ import annotations

import networkx as nx


def compute_instability(graph: nx.DiGraph) -> dict[str, float]:
    """Return Martin's `I` for every internal module in `graph`.

    Edges to external nodes (`external=True`) are excluded from both Ce
    and Ca counts so the metric is intrinsic to the project's own
    architecture.
    """
    internal = {n for n, d in graph.nodes(data=True) if not d.get("external")}
    out: dict[str, float] = {}
    for node in internal:
        ce = sum(1 for succ in graph.successors(node) if succ in internal)
        ca = sum(1 for pred in graph.predecessors(node) if pred in internal)
        total = ce + ca
        out[node] = (ce / total) if total else 0.0
    return out
