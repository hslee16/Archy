"""The work behind the graph, dsm, impact and affected tools.

archy:owns        run_affected, run_dsm, run_graph, run_graph_dump, run_graph_focus,
                  run_graph_summary, run_impact
archy:mirrored-by run_affected -> archy.mcp, archy.mcp_runs, run_dsm -> archy.mcp,
                  archy.mcp_runs, run_graph -> archy.mcp, archy.mcp_runs,
                  run_impact -> archy.mcp, archy.mcp_runs
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from archy.affected import Affected, find_affected
from archy.coupling import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_SUPPORT,
    co_changed_with,
    git_cochange,
    internal_module_paths,
)
from archy.dsm import (
    DSM,
    DSMDiff,
    DSMSummary,
    GroupBy,
    Weight,
    build_dsm,
    diff_dsm,
    read_dsm,
    summarize_dsm,
)
from archy.duplicates import (
    is_test_path,
)
from archy.gitlog import (
    resolve_changed_paths,
)
from archy.graph import (
    UnresolvedHint,
    graph_to_dict,
    hint_unresolved,
    resolve_modules,
    suggest_modules,
)
from archy.impact import DEFAULT_MAX_CHAINS, Impact, find_impact
from archy.instability import compute_instability
from archy.mcp_payloads import (
    DSMErrorPayload,
    DSMTooLargePayload,
    GraphEdge,
    GraphNode,
    GraphPayload,
    GraphSummaryEntry,
    GraphSummaryPayload,
    GraphTooLargePayload,
)
from archy.mcp_runs_core import (
    _load_graph,
    _rank_desc,
    _validate_group_by,
    _validate_response_format,
    _validate_weight,
    resolve_against,
)
from archy.reach import compute_propagation_cost
from archy.risk import compute_edit_risk

# Cell-count ceiling for a full-mode DSM dump. Mirrors archy_graph's
# max_nodes guard: above this, the full matrix is refused with a pointer to
# the concise summary / focus / package escape hatches rather than dumping
# thousands of cells into the agent's context.
DEFAULT_MAX_DSM_CELLS = 2000


def run_impact(
    path: Path,
    *,
    files: list[Path],
    max_chains: int = DEFAULT_MAX_CHAINS,
    co_change: bool = False,
) -> Impact:
    if not files:
        raise ValueError("pass at least one file (got files=[])")
    if max_chains == 0:
        raise ValueError("--max-chains must be negative (for all) or positive (for a limit); got 0")
    graph = _load_graph(path, internal_only=True)
    result = find_impact(graph, resolve_changed_paths(path, files), max_chains=max_chains)
    if not co_change or not result.changed:
        return result
    # Opt-in behavioral overlay: surface modules that co-change with the edited
    # set but have no structural edge (so the blast radius above missed them).
    # Source-only (test co-change is mostly the module's own test) and best-effort
    # (no git -> empty). Exclude modules already in `impacted` - the point is the
    # HIDDEN dependents. Only this path reads git history; resolving `files`
    # may also ask git for the work-tree root.
    source_paths = frozenset(p for p in internal_module_paths(graph) if not is_test_path(p))
    cochange = git_cochange(path, keep_paths=source_paths)
    if cochange is None:
        return result
    hints = co_changed_with(
        graph,
        cochange,
        targets=frozenset(result.changed),
        exclude=frozenset(result.impacted),
        min_support=DEFAULT_MIN_SUPPORT,
        min_confidence=DEFAULT_MIN_CONFIDENCE,
    )
    return result.model_copy(update={"co_changed": tuple(hints)})


def run_affected(
    path: Path,
    *,
    files: list[Path],
    depth: int,
    test_filter: str | None,
) -> Affected:
    if not files:
        raise ValueError("pass at least one file (got files=[])")
    graph = _load_graph(path, internal_only=True)
    resolved = resolve_changed_paths(path, files)
    return find_affected(
        graph,
        resolved,
        project_root=path,
        depth=depth,
        test_filter=test_filter,
    )


def run_graph_focus(
    path: Path,
    *,
    modules: list[str],
    depth: int,
    direction: str,
    internal_only: bool,
) -> GraphPayload:
    import networkx as nx

    if direction not in ("in", "out", "both"):
        raise ValueError(f"direction must be 'in', 'out', or 'both'; got {direction!r}")
    if depth < 0:
        raise ValueError(f"depth must be >= 0; got {depth}")

    graph = _load_graph(path, internal_only=internal_only)
    resolved, unresolved = resolve_modules(graph, modules, project_root=path)
    if not resolved:
        return _graph_payload_from(
            _empty_subgraph(graph),
            unresolved=tuple(unresolved),
            unresolved_hints=hint_unresolved(graph, unresolved, base=path),
        )

    reachable: set[str] = set(resolved)
    if direction in ("out", "both"):
        for seed in resolved:
            reachable |= set(nx.ego_graph(graph, seed, radius=depth).nodes())
    if direction in ("in", "both"):
        reverse = graph.reverse(copy=False)
        for seed in resolved:
            reachable |= set(nx.ego_graph(reverse, seed, radius=depth).nodes())

    sub = graph.subgraph(reachable).copy()
    sub.graph["root"] = graph.graph.get("root")
    sub.graph["parse_errors"] = graph.graph.get("parse_errors", ())
    return _graph_payload_from(
        sub,
        unresolved=tuple(unresolved),
        unresolved_hints=hint_unresolved(graph, unresolved, base=path),
    )


def _pagerank(graph, *, damping: float = 0.85, iterations: int = 50, tol: float = 1e-6) -> dict:
    # NetworkX 3.x's pagerank requires numpy/scipy. archy stays dependency-light,
    # so we hand-roll the power iteration. Identical formulation to the standard
    # damped random-walk PageRank with dangling-node redistribution.
    nodes = list(graph.nodes())
    n = len(nodes)
    if n == 0:
        return {}
    out_degree = {v: graph.out_degree(v) for v in nodes}
    pr = dict.fromkeys(nodes, 1.0 / n)
    teleport = (1.0 - damping) / n
    for _ in range(iterations):
        new_pr = _pagerank_step(graph, pr, out_degree, damping, teleport)
        if sum(abs(new_pr[v] - pr[v]) for v in nodes) < tol:
            return new_pr
        pr = new_pr
    return pr


def _pagerank_step(graph, pr: dict, out_degree: dict, damping: float, teleport: float) -> dict:
    """One power-iteration step: teleport, redistributed dangling mass, then each
    node's damped share along its out-edges."""
    n = len(pr)
    dangling_mass = damping * sum(pr[v] for v in pr if out_degree[v] == 0) / n
    new_pr = {v: teleport + dangling_mass for v in pr}
    for u in pr:
        if out_degree[u]:
            share = damping * pr[u] / out_degree[u]
            for v in graph.successors(u):
                new_pr[v] += share
    return new_pr


def run_graph_summary(path: Path, *, top_n: int) -> GraphSummaryPayload:
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1; got {top_n}")

    graph = _load_graph(path, internal_only=False)
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]
    internal_set = set(internal)

    internal_view = graph.subgraph(internal)
    instability = compute_instability(internal_view)
    _, propagation_cost = compute_propagation_cost(internal_view)
    edit_risk = compute_edit_risk(internal_view)

    internal_edge_count = internal_view.number_of_edges()
    external_edge_count = sum(
        1 for u, v in graph.edges() if u in internal_set and v not in internal_set
    )

    fan_in = _rank_desc((n, internal_view.in_degree(n)) for n in internal)
    fan_out = _rank_desc((n, internal_view.out_degree(n)) for n in internal)

    pagerank = _pagerank(internal_view)
    pr_sorted = _rank_desc(pagerank.items())

    risk_sorted = _rank_desc(edit_risk.items())

    external_counts: dict[str, int] = {}
    for _, v in graph.edges():
        if v not in internal_set and graph.nodes[v].get("external"):
            external_counts[v] = external_counts.get(v, 0) + 1
    ext_sorted = _rank_desc(external_counts.items())

    def _entries(
        pairs: list[tuple[str, float | int]],
        *,
        with_internal_metrics: bool,
    ) -> tuple[GraphSummaryEntry, ...]:
        return tuple(
            GraphSummaryEntry(
                module=name,
                value=float(value),
                instability=instability.get(name) if with_internal_metrics else None,
                propagation_cost=propagation_cost.get(name) if with_internal_metrics else None,
                edit_risk=edit_risk.get(name) if with_internal_metrics else None,
            )
            for name, value in pairs[:top_n]
        )

    return GraphSummaryPayload(
        module_count=len(internal),
        internal_edge_count=internal_edge_count,
        external_edge_count=external_edge_count,
        parse_errors=tuple(graph.graph.get("parse_errors", ())),
        top_fan_in=_entries(list(fan_in), with_internal_metrics=True),
        top_fan_out=_entries(list(fan_out), with_internal_metrics=True),
        top_pagerank=_entries(list(pr_sorted), with_internal_metrics=True),
        top_edit_risk=_entries(list(risk_sorted), with_internal_metrics=True),
        external_deps=_entries(list(ext_sorted), with_internal_metrics=False),
    )


def run_graph_dump(
    path: Path,
    *,
    internal_only: bool,
    max_nodes: int,
) -> GraphPayload | GraphTooLargePayload:
    if max_nodes < 1:
        raise ValueError(f"max_nodes must be >= 1; got {max_nodes}")
    graph = _load_graph(path, internal_only=internal_only)
    node_count = graph.number_of_nodes()
    if node_count > max_nodes:
        return GraphTooLargePayload(
            error=(
                f"graph has {node_count} nodes (> max_nodes={max_nodes}). "
                "Use archy_graph(focus=[...]) for a local slice or "
                "archy_graph(response_format='summary') for a top-N overview, or "
                "call archy_graph again with a higher max_nodes if you really "
                "want the full dump."
            ),
            node_count=node_count,
            max_nodes=max_nodes,
        )
    return _graph_payload_from(graph)


def run_graph(
    path: Path,
    *,
    response_format: str,
    focus: list[str] | None = None,
    depth: int = 2,
    direction: str = "both",
    internal_only: bool,
    max_nodes: int,
    top_n: int,
) -> GraphSummaryPayload | GraphPayload | GraphTooLargePayload:
    """Route archy_graph between a focused subgraph, the summary, and the dump.

    `focus` (absorbing the old archy_graph_focus) takes precedence: a focused
    neighborhood is already bounded, so response_format/max_nodes/top_n do not
    apply to it. Otherwise summary is the default (top-N overview, identical to
    the old archy_graph_summary) and full is the opt-in dump, guarded by
    max_nodes. response_format is validated on every path so an invalid value
    raises even when focus is set.
    """
    _validate_response_format(response_format)
    if focus:
        return run_graph_focus(
            path,
            modules=focus,
            depth=depth,
            direction=direction,
            internal_only=internal_only,
        )
    if response_format == "summary":
        return run_graph_summary(path, top_n=top_n)
    return run_graph_dump(path, internal_only=internal_only, max_nodes=max_nodes)


def run_dsm(
    path: Path,
    *,
    response_format: str,
    group_by: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
    baseline_path: str | None,
) -> DSMSummary | DSM | DSMDiff | DSMTooLargePayload | DSMErrorPayload:
    """Route archy_dsm between the concise summary, the full matrix, and a diff.

    A diff (baseline_path set) is a deliberate, already-compact comparison
    (new_back_edges is the signal), not a dump, so response_format does not
    apply to it. The full matrix is capped at DEFAULT_MAX_DSM_CELLS cells.
    group_by and weight are validated up front too, before the scan, so a typo
    never pays for a full build only to fail on a raw pydantic error (#645).
    """
    _validate_response_format(response_format)
    if focus_depth < 0:
        raise ValueError(f"focus_depth must be >= 0; got {focus_depth}")
    _validate_group_by(group_by)
    _validate_weight(weight)
    graph = _load_graph(path, internal_only=False)
    current = build_dsm(
        graph,
        group_by=cast(GroupBy, group_by),
        weight=cast(Weight, weight),
        focus=focus,
        focus_depth=focus_depth,
        package=package,
        suggest=suggest_modules,
    )
    if baseline_path is not None:
        try:
            before = read_dsm(resolve_against(path, [Path(baseline_path)])[0])
        except ValueError as exc:
            return DSMErrorPayload(error=str(exc))
        if before is None:
            return DSMErrorPayload(error=f"no DSM snapshot at {baseline_path}")
        return diff_dsm(before, current)
    if response_format == "summary":
        return summarize_dsm(current)
    if len(current.cells) > DEFAULT_MAX_DSM_CELLS:
        return DSMTooLargePayload(
            error=(
                f"DSM has {len(current.cells)} cells (> max_cells="
                f"{DEFAULT_MAX_DSM_CELLS}). Read response_format='summary' for a "
                "compact overview, or narrow with focus=<qualname> or "
                "package=<prefix>."
            ),
            cell_count=len(current.cells),
            max_cells=DEFAULT_MAX_DSM_CELLS,
        )
    return current


def _empty_subgraph(graph):
    import networkx as nx

    empty: nx.DiGraph = nx.DiGraph()
    empty.graph["root"] = graph.graph.get("root")
    empty.graph["parse_errors"] = graph.graph.get("parse_errors", ())
    return empty


def _graph_payload_from(
    graph,
    *,
    unresolved: tuple[str, ...] = (),
    unresolved_hints: tuple[UnresolvedHint, ...] = (),
) -> GraphPayload:
    data = graph_to_dict(graph)
    nodes = tuple(
        GraphNode(
            id=n["id"],
            external=bool(n.get("external", False)),
            path=n.get("path"),
            is_package=n.get("is_package"),
            instability=n.get("instability"),
            propagation_cost=n.get("propagation_cost"),
            edit_risk=n.get("edit_risk"),
        )
        for n in data["nodes"]
    )
    edges = tuple(
        GraphEdge(
            source=e["source"],
            target=e["target"],
            is_relative=bool(e.get("is_relative", False)),
            lines=tuple(e.get("lines", ())),
        )
        for e in data["edges"]
    )
    return GraphPayload(
        root=data["root"],
        parse_errors=tuple(data["parse_errors"]),
        nodes=nodes,
        edges=edges,
        unresolved=unresolved,
        unresolved_hints=unresolved_hints,
    )
