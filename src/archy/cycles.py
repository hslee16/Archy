"""Strongly-connected-component cycle detection over an import graph."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx


@dataclass(frozen=True)
class CycleEdge:
    source: str
    target: str
    lines: tuple[int, ...]


@dataclass(frozen=True)
class Cycle:
    modules: tuple[str, ...]
    edges: tuple[CycleEdge, ...]


def find_cycles(graph: nx.DiGraph, *, min_size: int = 2) -> list[Cycle]:
    """Return SCCs of size >= min_size, sorted largest-first then by qualname.

    Single-module SCCs are excluded by default; raise min_size=1 to include
    them, but note that a 1-element SCC only constitutes a real cycle if the
    node has a self-loop. v1 does not synthesize self-cycles.
    """
    cycles: list[Cycle] = []
    for component in nx.strongly_connected_components(graph):
        if len(component) < min_size:
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
