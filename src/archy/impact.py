"""Blast-radius analysis: which internal modules depend on a changed set?

Given an import graph and a list of changed file paths, find the modules
that transitively *depend on* the changes. Useful as an agent-side
"who do I break if I edit this?" check before refactoring or removing
a module.

Resolution is graph-driven: each internal node carries a `path` attribute
(absolute path on disk), so a changed file maps to a qualname only if
that file participates in the discovered graph. Files outside the graph
(non-Python, gitignored, excluded by archy.yaml, top-level scripts not
in any package) are reported as `unresolved` rather than silently
dropped, so callers can tell why a file produced no impact.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict


class Impact(BaseModel):
    model_config = ConfigDict(frozen=True)

    changed: tuple[str, ...]
    unresolved: tuple[str, ...]
    impacted: tuple[str, ...]
    propagation_cost: float = 0.0


def find_impact(graph: nx.DiGraph, files: list[Path]) -> Impact:
    """Resolve `files` to qualnames and return everything that depends on them.

    `impacted` is the set of internal modules with a directed path to any
    changed module (via `nx.ancestors`), minus the changed set itself.
    `propagation_cost` is `|changed ∪ impacted| / N_internal`: the fraction
    of the project's internal module count that this edit set can reach,
    a MacCormack-style blast-radius scalar. Output tuples are sorted for
    deterministic JSON.
    """
    path_to_qualname = _index_by_path(graph)

    changed: set[str] = set()
    unresolved: list[str] = []
    for f in files:
        resolved = f.resolve()
        qualname = path_to_qualname.get(resolved)
        if qualname is None:
            unresolved.append(str(f))
        else:
            changed.add(qualname)

    impacted: set[str] = set()
    for q in changed:
        if q in graph:
            impacted |= nx.ancestors(graph, q)
    impacted -= changed
    impacted = {q for q in impacted if not graph.nodes[q].get("external")}

    internal_count = sum(1 for _, d in graph.nodes(data=True) if not d.get("external"))
    reachable = len(changed) + len(impacted)
    propagation_cost = (reachable / internal_count) if internal_count else 0.0

    return Impact(
        changed=tuple(sorted(changed)),
        unresolved=tuple(sorted(unresolved)),
        impacted=tuple(sorted(impacted)),
        propagation_cost=propagation_cost,
    )


def _index_by_path(graph: nx.DiGraph) -> dict[Path, str]:
    out: dict[Path, str] = {}
    for qualname, data in graph.nodes(data=True):
        if data.get("external"):
            continue
        raw = data.get("path")
        if raw:
            out[Path(raw).resolve()] = qualname
    return out
