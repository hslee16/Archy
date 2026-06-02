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

from itertools import pairwise
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict

DEFAULT_MAX_CHAINS = 20


class CausalHop(BaseModel):
    """One import edge on a causal chain, with the source-file line(s)."""

    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    lines: tuple[int, ...]


class CausalChain(BaseModel):
    """Why one impacted module is reachable from a changed module.

    `via` is the shortest import path from the impacted module to the
    changed module it depends on (`[impacted, ..., changed]`); `hops`
    carries the same path one edge at a time, each with the line numbers
    where that import appears. This is the "because" an agent should cite
    when it edits the changed module: the specific edges to preserve.
    """

    model_config = ConfigDict(frozen=True)

    impacted: str
    changed: str
    via: tuple[str, ...]
    hops: tuple[CausalHop, ...]


class Impact(BaseModel):
    model_config = ConfigDict(frozen=True)

    changed: tuple[str, ...]
    unresolved: tuple[str, ...]
    impacted: tuple[str, ...]
    propagation_cost: float = 0.0
    chains: tuple[CausalChain, ...] = ()
    chains_omitted: int = 0


def find_impact(
    graph: nx.DiGraph,
    files: list[Path],
    *,
    max_chains: int = DEFAULT_MAX_CHAINS,
) -> Impact:
    """Resolve `files` to qualnames and return everything that depends on them.

    `impacted` is the set of internal modules with a directed path to any
    changed module (via `nx.ancestors`), minus the changed set itself.
    `propagation_cost` is `(|changed| + |impacted|) / N_internal`: the
    fraction of the project's internal module count that this edit set
    can reach (the two sets are disjoint by construction), a MacCormack-
    style blast-radius scalar. Output tuples are sorted for deterministic
    JSON.

    `chains` answers *why* each impacted module is in the blast radius: the
    shortest import path from it back to a changed module, with per-edge
    line numbers (the data already lives on the graph edges). The closest
    dependents (shortest paths) are the most directly coupled, so chains
    are ranked shortest-first and capped at `max_chains` (set negative for
    all); `chains_omitted` reports how many impacted modules were left out
    so the cap is never silent.
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

    chains, chains_omitted = _build_chains(graph, changed, impacted, max_chains)

    return Impact(
        changed=tuple(sorted(changed)),
        unresolved=tuple(sorted(unresolved)),
        impacted=tuple(sorted(impacted)),
        propagation_cost=propagation_cost,
        chains=chains,
        chains_omitted=chains_omitted,
    )


def _build_chains(
    graph: nx.DiGraph,
    changed: set[str],
    impacted: set[str],
    max_chains: int,
) -> tuple[tuple[CausalChain, ...], int]:
    """Shortest import path from each impacted module to a changed module.

    Runs one multi-source BFS from the changed set over the reversed graph,
    which yields, for every reachable node, the shortest path *from* the
    nearest changed module; reversing each gives the import path *to* it.
    Ranked shortest-first (ties broken by qualname) and capped.
    """
    sources = {q for q in changed if q in graph}
    if not impacted or not sources:
        return (), 0

    # On the reversed graph, a path source -> ... -> node corresponds to the
    # import path node -> ... -> source in the original (importer -> imported).
    rev_paths = nx.multi_source_dijkstra_path(graph.reverse(copy=False), sources)

    ranked = sorted(
        (m for m in impacted if m in rev_paths),
        key=lambda m: (len(rev_paths[m]), m),
    )
    selected = ranked if max_chains < 0 else ranked[:max_chains]

    chains: list[CausalChain] = []
    for m in selected:
        forward = tuple(reversed(rev_paths[m]))  # [impacted, ..., changed]
        hops = tuple(
            CausalHop(source=u, target=v, lines=_edge_lines(graph, u, v))
            for u, v in pairwise(forward)
        )
        chains.append(
            CausalChain(impacted=m, changed=forward[-1], via=forward, hops=hops)
        )
    return tuple(chains), len(ranked) - len(selected)


def _edge_lines(graph: nx.DiGraph, src: str, dst: str) -> tuple[int, ...]:
    """Import lines for the `src -> dst` edge, falling back to call sites.

    Pure call edges (e.g. `import pkg; pkg.sub.foo()`) carry no import
    `lines` but do carry `call_lines`; surface those so every hop has a
    citation rather than an empty `()`.
    """
    data = graph[src][dst]
    lines = data.get("lines") or data.get("call_lines") or ()
    return tuple(sorted(set(lines)))


def _index_by_path(graph: nx.DiGraph) -> dict[Path, str]:
    out: dict[Path, str] = {}
    for qualname, data in graph.nodes(data=True):
        if data.get("external"):
            continue
        raw = data.get("path")
        if raw:
            out[Path(raw).resolve()] = qualname
    return out
