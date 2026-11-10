"""Strongly-connected-component cycle detection over an import graph.

archy:owns        Cycle, CycleEdge, CycleScan, find_cycles, scan_cycles
archy:mirrored-by CycleScan -> archy.cli_render_history, archy.mcp,
                  archy.mcp_runs_check, find_cycles -> archy.diff, archy.score,
                  bench.acyclicity_dilution, bench.delta_direction,
                  scan_cycles -> archy.cli_history, archy.mcp_runs_check
"""

from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict


class CycleEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    lines: tuple[int, ...]
    # Repo-relative path of `source`'s file, where `lines` are (#522). "" when the
    # graph cannot say; the default keeps an old snapshot loading.
    path: str = ""


class Cycle(BaseModel):
    model_config = ConfigDict(frozen=True)

    modules: tuple[str, ...]
    edges: tuple[CycleEdge, ...]
    # How many of `edges` exist only through `if TYPE_CHECKING:` imports. A cycle
    # whose count is not zero may be breakable by leaving those edges out, which
    # is what `exclude_type_checking` does (#520).
    type_checking_edges: int = 0


class CycleScan(BaseModel):
    """`find_cycles` plus what excluding TYPE_CHECKING-only edges removed.

    `excluded_type_checking_edges` counts the TYPE_CHECKING-only edges that sat
    inside a cycle of the full graph. It is 0 when nothing was excluded, and it
    is the only trace of an exclusion that removed every cycle, so a clean
    result that is clean because of the flag says so.
    """

    model_config = ConfigDict(frozen=True)

    cycles: tuple[Cycle, ...]
    excluded_type_checking_edges: int = 0


def scan_cycles(
    graph: nx.DiGraph, *, min_size: int = 2, exclude_type_checking: bool = False
) -> CycleScan:
    """`find_cycles`, optionally leaving out edges that exist only under TYPE_CHECKING.

    With the flag off this is `find_cycles` plus a zero. With it on, cycles are
    found on a copy of the graph without the TYPE_CHECKING-only edges, and the
    count of the ones that sat inside a cycle of the full graph is reported.
    An edge any runtime import or call also supports is never excluded.
    """
    full = find_cycles(graph, min_size=min_size)
    if not exclude_type_checking:
        return CycleScan(cycles=tuple(full))
    kept = graph.copy()
    kept.remove_edges_from(
        [(u, v) for u, v, only in graph.edges(data="type_checking_only", default=False) if only]
    )
    return CycleScan(
        cycles=tuple(find_cycles(kept, min_size=min_size)),
        excluded_type_checking_edges=sum(c.type_checking_edges for c in full),
    )


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
                            path=graph.graph.get("relative_paths", {}).get(u, ""),
                        )
                    )
        edges.sort(key=lambda e: (e.source, e.target))
        guarded = sum(
            1 for e in edges if graph[e.source][e.target].get("type_checking_only", False)
        )
        cycles.append(Cycle(modules=modules, edges=tuple(edges), type_checking_edges=guarded))
    cycles.sort(key=lambda c: (-len(c.modules), c.modules[0]))
    return cycles
