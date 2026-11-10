"""Assembling the import graph from parsed modules, and reading it back out.

archy:owns        assemble_graph, graph_to_dict, internal_module_count,
                  internal_subgraph, zero_modules_reason
archy:mirrored-by assemble_graph -> archy.graph, archy.index, archy.watcher,
                  bench.pattern_detection, graph_to_dict -> archy.cli_graph,
                  archy.graph, archy.mcp_runs_graph,
                  internal_module_count -> archy.cli_history, archy.graph,
                  archy.mcp_runs_check, internal_subgraph -> archy.cli_app,
                  archy.graph, archy.mcp_runs_core,
                  zero_modules_reason -> archy.cli_history, archy.graph,
                  archy.mcp_runs_check
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy.complexity import FunctionComplexity
from archy.entrypoints import derive_entrypoints
from archy.graph_discovery import _relative_paths
from archy.graph_models import Module
from archy.graph_resolve import (
    _build_alias_table,
    _build_reexport_maps,
    _resolve_call_target,
    _resolve_targets,
)
from archy.instability import compute_instability
from archy.parser import CallRef, ImportRef, ParseResult
from archy.reach import compute_propagation_cost
from archy.risk import compute_edit_risk


def assemble_graph(
    root: Path,
    modules: list[Module],
    parse_results: dict[str, ParseResult],
) -> nx.DiGraph:
    """Build the import + call graph from already-parsed modules.

    This is the resolution-and-assembly half of `build_graph`, split out so the
    cold path and the cache-backed path (`archy.index.build_graph_cached`) share
    one resolution implementation. Resolution is global (relative imports,
    re-export chains, and alias tables all need the full `parse_results` set), so
    keeping a single code path is what guarantees the cached graph is identical
    to a cold build. `parse_results` is keyed by module qualname; a module with
    no entry (its file vanished between discovery and parse) is dropped here, so
    every caller -- build_graph, build_graph_cached, the mcp watcher -- is safe
    without repeating the guard.
    """
    modules = [m for m in modules if m.qualname in parse_results]
    qualname_set = {m.qualname for m in modules}

    graph: nx.DiGraph = nx.DiGraph()
    _add_module_nodes(graph, modules, parse_results)
    resolver = _Resolver(
        qualname_set=qualname_set,
        reexport_maps=_build_reexport_maps(modules, parse_results, qualname_set),
    )
    parse_errors = _add_import_edges(graph, modules, parse_results, resolver)
    _add_call_edges(graph, modules, parse_results, resolver)

    # Freeze once per edge to preserve the public tuple without copying it per call.
    for _, _, data in graph.edges(data=True):
        if "call_lines" in data:
            data["call_lines"] = tuple(data["call_lines"])

    graph.graph["root"] = str(root)
    graph.graph["relative_paths"] = _relative_paths(root, modules)
    graph.graph["parse_errors"] = tuple(sorted(parse_errors))
    # Graph-level, not per-node: a node attribute would reach every node of
    # `archy graph --format json` for the benefit of one `required:` source.
    entrypoints, entrypoint_errors = derive_entrypoints(
        root,
        guarded=(m.qualname for m in modules if parse_results[m.qualname].has_main_guard),
        modules={m.qualname: m.path for m in modules},
    )
    graph.graph["entrypoints"] = entrypoints
    graph.graph["entrypoint_errors"] = entrypoint_errors
    return graph


def _add_module_nodes(
    graph: nx.DiGraph, modules: list[Module], parse_results: dict[str, ParseResult]
) -> None:
    for m in modules:
        cc_aggregates = _cc_aggregates(parse_results[m.qualname].functions)
        graph.add_node(
            m.qualname,
            path=str(m.path),
            is_package=m.is_package,
            external=False,
            **cc_aggregates,
        )


class _Resolver(BaseModel):
    """What import and call resolution needs beyond a module's own parse result."""

    model_config = ConfigDict(frozen=True)

    qualname_set: set[str]
    reexport_maps: dict[str, dict[str, str]]


def _add_import_edges(
    graph: nx.DiGraph,
    modules: list[Module],
    parse_results: dict[str, ParseResult],
    resolver: _Resolver,
) -> list[str]:
    """Add one edge per resolved import; return the modules that had parse errors."""
    parse_errors: list[str] = []
    for m in modules:
        result = parse_results[m.qualname]
        if result.has_errors:
            parse_errors.append(m.qualname)
        _add_module_import_edges(graph, m, result, resolver)
    return parse_errors


def _add_module_import_edges(
    graph: nx.DiGraph, m: Module, result: ParseResult, resolver: _Resolver
) -> None:
    for ref in result.imports:
        for target in _resolve_targets(ref, m, resolver.qualname_set, resolver.reexport_maps):
            if target not in graph:
                graph.add_node(target, external=True)
            _add_or_extend_edge(graph, m.qualname, target, ref)


def _add_call_edges(
    graph: nx.DiGraph,
    modules: list[Module],
    parse_results: dict[str, ParseResult],
    resolver: _Resolver,
) -> None:
    # Second pass: call edges. Calls only become an edge if their leftmost
    # identifier resolves through the source module's import alias table, so
    # we depend on the import pass having populated qualname_set + reexport
    # routing first. Call edges land on the longest internal qualname prefix
    # of (alias_target + chain), which can be deeper than the import edge
    # (e.g. `import pkg; pkg.sub.foo()` adds a call edge to pkg.sub even
    # when imports only edged to pkg) - that depth differential is the
    # whole point of LocAgent's invoke-edges signal.
    for m in modules:
        result = parse_results[m.qualname]
        if result.calls:
            _add_module_call_edges(graph, m, result, resolver)


def _add_module_call_edges(
    graph: nx.DiGraph, m: Module, result: ParseResult, resolver: _Resolver
) -> None:
    alias_table = _build_alias_table(
        result.imports, m, resolver.qualname_set, resolver.reexport_maps
    )
    for call in result.calls:
        target = _resolve_call_target(call, alias_table, resolver.qualname_set)
        if target is None or target == m.qualname:
            continue
        if target not in graph:
            graph.add_node(target, external=True)
        _add_or_extend_call_edge(graph, m.qualname, target, call)


def graph_to_dict(graph: nx.DiGraph) -> dict:
    """Serialize a graph to the JSON shape emitted by `archy graph --format json`.

    Per-node `instability` (Martin's `I`), `propagation_cost` (MacCormack
    reverse-reach fraction), and `edit_risk` (geometric-mean composite of
    propagation cost, normalized fan-in, and instability) are attached to
    internal nodes only; external modules have no meaningful values within
    the project. When called on a subgraph (e.g. by `archy_graph_focus`),
    values are computed relative to that subgraph's scope, not the full
    project. For the canonical project-wide propagation cost, read
    `archy score`'s `inputs.propagation_cost`.
    """
    instability = compute_instability(graph)
    _, propagation_cost = compute_propagation_cost(graph)
    edit_risk = compute_edit_risk(graph)
    return {
        "root": graph.graph.get("root"),
        "parse_errors": list(graph.graph.get("parse_errors", ())),
        "nodes": [
            {
                "id": n,
                **d,
                **({"instability": instability[n]} if n in instability else {}),
                **({"propagation_cost": propagation_cost[n]} if n in propagation_cost else {}),
                **({"edit_risk": edit_risk[n]} if n in edit_risk else {}),
            }
            for n, d in sorted(graph.nodes(data=True))
        ],
        "edges": [
            {"source": u, "target": v, **d}
            for u, v, d in sorted(graph.edges(data=True), key=lambda e: (e[0], e[1]))
        ],
    }


def internal_subgraph(graph: nx.DiGraph) -> nx.DiGraph:
    """A copy of `graph` with external nodes removed.

    Score, cycles and the DSM all want internal-only input, and the CLI and MCP
    boundaries each need to derive one alongside the full graph (required-reach
    rules may name an external package, so the externals have to survive
    somewhere). Shared so the definition of "external" lives in one place: both
    boundaries strip on the same rule, and a future change to how nodes are
    classified cannot land in one and miss the other.
    """
    internal = graph.copy()
    internal.remove_nodes_from([n for n, d in graph.nodes(data=True) if d.get("external")])
    return internal


def internal_module_count(graph: nx.DiGraph) -> int:
    """How many nodes of `graph` are internal modules, by the same rule as `internal_subgraph`."""
    return sum(1 for _, d in graph.nodes(data=True) if not d.get("external"))


def zero_modules_reason(path: Path, verdict: str) -> str:
    """Why a scan that found no internal modules cannot support a verdict (#523).

    A pass over nothing reads exactly like a pass over a clean codebase, so every
    surface that would say "no cycles" or score 1.000 says this instead. `verdict`
    completes "nothing was ...", for example "checked" or "scored".
    """
    return f"0 internal modules scanned under {path}; nothing was {verdict}"


def _add_kind(data: dict, kind: str) -> None:
    """Record `kind` on an existing edge's `kinds`, defaulting a missing one to import.

    The default is `("import",)` for BOTH callers: an edge that exists without
    `kinds` predates call edges, so it was an import edge.
    """
    kinds = data.get("kinds") or ("import",)
    if kind not in kinds:
        data["kinds"] = (*kinds, kind)


def _add_or_extend_edge(
    graph: nx.DiGraph,
    src: str,
    dst: str,
    ref: ImportRef,
) -> None:
    if graph.has_edge(src, dst):
        data = graph[src][dst]
        data["lines"] = (*data.get("lines", ()), ref.line)
        # `any`, not first-seen: a pair written `from pkg.b import x` then
        # `from .b import y` really does contain a relative import, and which
        # of the two came first is not something a caller can predict. This
        # aggregates the way `lines` does (#450).
        data["is_relative"] = bool(data.get("is_relative", False)) or ref.is_relative
        # `and`, the opposite aggregate: one unguarded import makes the edge a
        # runtime dependency whatever else sits under TYPE_CHECKING (#520).
        data["type_checking_only"] = bool(data.get("type_checking_only", False)) and (
            ref.type_checking
        )
        _add_kind(data, "import")
    else:
        graph.add_edge(
            src,
            dst,
            is_relative=ref.is_relative,
            lines=(ref.line,),
            kinds=("import",),
            type_checking_only=ref.type_checking,
        )


def _add_or_extend_call_edge(
    graph: nx.DiGraph,
    src: str,
    dst: str,
    call: CallRef,
) -> None:
    """Attach call-site data to an edge, creating it if no import edge exists.

    Call-only edges (kinds=('call',)) appear when calls resolve to a deeper
    submodule than the import edge - e.g., `import pkg; pkg.sub.foo()`
    creates a call edge to pkg.sub on top of the import edge to pkg.
    """
    if graph.has_edge(src, dst):
        data = graph[src][dst]
        data.setdefault("call_lines", []).append(call.line)
        data["call_count"] = data.get("call_count", 0) + 1
        # A resolved call is a runtime reference, so it ends any
        # TYPE_CHECKING-only claim the import pass made for this edge (#520).
        data["type_checking_only"] = False
        _add_kind(data, "call")
    else:
        graph.add_edge(
            src,
            dst,
            is_relative=False,
            lines=(),
            kinds=("call",),
            call_lines=[call.line],
            call_count=1,
            type_checking_only=False,
        )


def _cc_aggregates(functions: tuple[FunctionComplexity, ...]) -> dict[str, int | float]:
    """Per-module CC roll-up: function count, sum, max, and mean.

    Empty / no-function modules (e.g. plain `__init__.py`, type-only stub
    modules) get function_count=0 and the rest 0; downstream consumers
    treat 0 as "no signal" rather than "perfectly simple."
    """
    if not functions:
        return {"function_count": 0, "cc_sum": 0, "cc_max": 0, "cc_mean": 0.0}
    counts = [f.cyclomatic for f in functions]
    total = sum(counts)
    return {
        "function_count": len(counts),
        "cc_sum": total,
        "cc_max": max(counts),
        "cc_mean": total / len(counts),
    }
