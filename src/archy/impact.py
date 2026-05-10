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

from dataclasses import dataclass
from pathlib import Path

import networkx as nx


@dataclass(frozen=True)
class Impact:
    changed: tuple[str, ...]
    unresolved: tuple[str, ...]
    impacted: tuple[str, ...]


def find_impact(graph: nx.DiGraph, files: list[Path]) -> Impact:
    """Resolve `files` to qualnames and return everything that depends on them.

    `impacted` is the set of internal modules with a directed path to any
    changed module (via `nx.ancestors`), minus the changed set itself.
    Output tuples are sorted for deterministic JSON.
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

    return Impact(
        changed=tuple(sorted(changed)),
        unresolved=tuple(sorted(unresolved)),
        impacted=tuple(sorted(impacted)),
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
