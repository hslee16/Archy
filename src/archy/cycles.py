"""Strongly-connected-component cycle detection over an import graph."""

from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict


class CycleEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    lines: tuple[int, ...]


class Cycle(BaseModel):
    model_config = ConfigDict(frozen=True)

    modules: tuple[str, ...]
    edges: tuple[CycleEdge, ...]


def find_cycles(graph: nx.DiGraph, *, min_size: int = 2) -> list[Cycle]:
    """Return cycles in the graph, sorted largest-first then by qualname.

    A cycle is either a strongly-connected component of size >= min_size or
    a singleton SCC whose only node has a self-edge. Self-loops are always
    real cycles (a module importing itself), so we report them regardless
    of min_size; the gate only suppresses incidental singletons (DAG nodes
    that happen to be their own SCC because they have no inbound mutual
    relationship).
    """
    cycles: list[Cycle] = []
    for component in nx.strongly_connected_components(graph):
        size = len(component)
        if size == 1:
            node = next(iter(component))
            if not graph.has_edge(node, node):
                continue
        elif size < min_size:
            continue
        modules = tuple(sorted(component))
        component_set = set(component)
        edges: list[CycleEdge] = []
        for u in modules:
            for v in graph.successors(u):
                if v in component_set:
                    data = graph[u][v]
                    edges.append(
                        CycleEdge(
                            source=u,
                            target=v,
                            lines=tuple(data.get("lines", ())),
                        )
                    )
        edges.sort(key=lambda e: (e.source, e.target))
        cycles.append(Cycle(modules=modules, edges=tuple(edges)))
    cycles.sort(key=lambda c: (-len(c.modules), c.modules[0]))
    return cycles
