"""Design Structure Matrix (DSM): builder, renderers, and diff.

A DSM is the canonical industrial view of system coupling (Steward 1981,
Eppinger & Browning, MacCormack 2006): nodes go on both axes in a chosen
order, and the cell at (row=source, col=target) is non-empty when the
source depends on the target. Reading positionally exposes properties
that any single scalar would hide: back-edges sit above the diagonal in
topological orderings, community structure becomes block-diagonal under
community grouping, and layer violations are off-block entries under
layer grouping.

archy ships DSM as a visualization-only output (no axis, no diagnostic
scalar) per `docs/research/DSM_EMPIRICS.md`. The intended consumer is an LLM
coding agent that reads the matrix as structured context, not a human
browsing a dashboard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import networkx as nx
from pydantic import BaseModel, ConfigDict

GroupBy = Literal["community", "layer", "topological"]
Weight = Literal["imports", "calls"]


class DSMCell(BaseModel):
    """A non-empty entry in the matrix. (row, col) index into `DSM.ordering`."""

    model_config = ConfigDict(frozen=True)

    row: int
    col: int
    weight: float


class DSMGroup(BaseModel):
    """A contiguous block of `ordering` sharing a group label."""

    model_config = ConfigDict(frozen=True)

    label: str
    members: tuple[str, ...]


class DSM(BaseModel):
    """Sparse DSM. `ordering` is the row/col order; `cells` lists non-empty entries.

    `groups[i].members` is a contiguous slice of `ordering`. Cell positions
    are int indices into `ordering`, so an agent can reconstruct the dense
    matrix by walking cells and indexing by `(row, col)`.
    """

    model_config = ConfigDict(frozen=True)

    ordering: tuple[str, ...]
    groups: tuple[DSMGroup, ...]
    cells: tuple[DSMCell, ...]
    group_by: GroupBy
    weight: Weight


class DSMDiff(BaseModel):
    """Structured diff between two DSMs over the same set of nodes (intersected).

    `new_back_edges` is the load-bearing field for agent scenario 2 in
    `docs/research/DSM_EMPIRICS.md`: cells that appeared above the diagonal in
    `after` but did not exist in `before`, meaning the edit introduced a
    new back-edge in the chosen ordering.
    """

    model_config = ConfigDict(frozen=True)

    added: tuple[DSMCell, ...]
    removed: tuple[DSMCell, ...]
    weight_changed: tuple[tuple[DSMCell, DSMCell], ...]
    nodes_added: tuple[str, ...]
    nodes_removed: tuple[str, ...]
    new_back_edges: tuple[DSMCell, ...]


def build_dsm(
    graph: nx.DiGraph,
    *,
    group_by: GroupBy = "community",
    weight: Weight = "imports",
    focus: str | None = None,
    focus_depth: int = 1,
    package: str | None = None,
) -> DSM:
    """Build a DSM from an import graph.

    Operates on the internal subgraph (external nodes excluded; the
    audience is structural reasoning about your own code, not vendor
    surface). Applies `focus` and `package` filters in that order.
    """
    sub = _internal_subgraph(graph)
    if package is not None:
        sub = _filter_by_package(sub, package)
    if focus is not None:
        sub = _filter_by_focus(sub, focus, focus_depth)

    if group_by == "community":
        groups = _group_by_community(sub)
    elif group_by == "layer":
        groups = _group_by_layer(sub)
    else:
        groups = _group_by_topological(sub)

    ordering: list[str] = []
    for g in groups:
        ordering.extend(g.members)
    pos = {name: i for i, name in enumerate(ordering)}

    cells: list[DSMCell] = []
    for u, v, data in sub.edges(data=True):
        if u not in pos or v not in pos:
            continue
        if weight == "imports":
            w = 1.0
        else:
            cc = data.get("call_count", 0)
            w = float(cc) if cc > 0 else 1.0
        cells.append(DSMCell(row=pos[u], col=pos[v], weight=w))

    cells.sort(key=lambda c: (c.row, c.col))
    return DSM(
        ordering=tuple(ordering),
        groups=tuple(groups),
        cells=tuple(cells),
        group_by=group_by,
        weight=weight,
    )


def _internal_subgraph(graph: nx.DiGraph) -> nx.DiGraph:
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]
    return graph.subgraph(internal).copy()


def _filter_by_package(graph: nx.DiGraph, prefix: str) -> nx.DiGraph:
    keep = [n for n in graph.nodes() if n == prefix or n.startswith(prefix + ".")]
    return graph.subgraph(keep).copy()


def _filter_by_focus(graph: nx.DiGraph, focus: str, depth: int) -> nx.DiGraph:
    if focus not in graph:
        return graph.subgraph([]).copy()
    keep: set[str] = {focus}
    frontier: set[str] = {focus}
    for _ in range(max(depth, 0)):
        next_frontier: set[str] = set()
        for n in frontier:
            next_frontier.update(graph.successors(n))
            next_frontier.update(graph.predecessors(n))
        next_frontier -= keep
        keep.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break
    return graph.subgraph(keep).copy()


def _scc_condensation(graph: nx.DiGraph) -> tuple[list[set[str]], nx.DiGraph]:
    sccs = [set(comp) for comp in nx.strongly_connected_components(graph)]
    cond = nx.condensation(graph, scc=sccs)
    return sccs, cond


def _group_by_topological(graph: nx.DiGraph) -> list[DSMGroup]:
    """SCC-condensed topological ordering. One group per SCC (size > 1 only)."""
    if graph.number_of_nodes() == 0:
        return []
    sccs, cond = _scc_condensation(graph)
    topo = list(nx.topological_sort(cond))
    groups: list[DSMGroup] = []
    singleton_buffer: list[str] = []
    for scc_idx in topo:
        members = sorted(sccs[scc_idx])
        if len(members) == 1:
            singleton_buffer.extend(members)
            continue
        if singleton_buffer:
            groups.append(DSMGroup(label="DAG", members=tuple(singleton_buffer)))
            singleton_buffer = []
        groups.append(DSMGroup(label=f"SCC-{len(groups)}", members=tuple(members)))
    if singleton_buffer:
        groups.append(DSMGroup(label="DAG", members=tuple(singleton_buffer)))
    return groups


def _group_by_community(graph: nx.DiGraph) -> list[DSMGroup]:
    if graph.number_of_edges() == 0 or graph.number_of_nodes() < 2:
        return (
            [DSMGroup(label="Community-0", members=tuple(sorted(graph.nodes())))]
            if graph.number_of_nodes()
            else []
        )
    communities = list(nx.community.greedy_modularity_communities(graph))
    # Sort communities by size (largest first) for a stable visual layout.
    communities.sort(key=lambda c: -len(c))
    return [
        DSMGroup(label=f"Community-{i}", members=tuple(sorted(comm)))
        for i, comm in enumerate(communities)
    ]


def _group_by_layer(graph: nx.DiGraph) -> list[DSMGroup]:
    """Bucket nodes by longest-path depth in the SCC condensation."""
    if graph.number_of_nodes() == 0:
        return []
    sccs, cond = _scc_condensation(graph)
    scc_of = {n: i for i, comp in enumerate(sccs) for n in comp}
    depth: dict[int, int] = dict.fromkeys(cond.nodes(), 0)
    for scc_idx in nx.topological_sort(cond):
        for pred in cond.predecessors(scc_idx):
            if depth[pred] + 1 > depth[scc_idx]:
                depth[scc_idx] = depth[pred] + 1
    by_layer: dict[int, list[str]] = {}
    for node in graph.nodes():
        by_layer.setdefault(depth[scc_of[node]], []).append(node)
    return [
        DSMGroup(label=f"Layer-{d}", members=tuple(sorted(by_layer[d]))) for d in sorted(by_layer)
    ]


def _cell_glyph(weight: float, mode: Weight) -> str:
    if mode == "imports":
        return "X"
    w = round(weight)
    if w <= 0:
        return "."
    if w < 10:
        return str(w)
    return "+"


def render_ascii(
    dsm: DSM,
    *,
    label_width: int = 24,
    max_nodes: int = 80,
) -> str:
    """Render a DSM as ASCII. Rejects matrices wider than `max_nodes`.

    For larger graphs the caller should re-build with `focus` or
    `package` filters, or switch to JSON. The reject is deliberate:
    ASCII at 200+ columns is unreadable even with horizontal scroll.
    """
    n = len(dsm.ordering)
    if n == 0:
        return "DSM: empty graph (no internal modules after filtering).\n"
    if n > max_nodes:
        return (
            f"DSM: {n} modules exceeds max_nodes={max_nodes} for ASCII rendering. "
            "Re-run with --focus=<module>, --package=<prefix>, or --format=json.\n"
        )

    cell_lookup: dict[tuple[int, int], float] = {(c.row, c.col): c.weight for c in dsm.cells}
    boundaries = _group_boundaries(dsm)

    out: list[str] = []
    out.append(f"DSM (n={n}, group={dsm.group_by}, weight={dsm.weight})\n")
    out.append("Legend:")
    idx_pad = len(str(n))
    for i, name in enumerate(dsm.ordering, start=1):
        out.append(f"  {str(i).rjust(idx_pad)}: {name}")
    out.append("")
    if dsm.groups and dsm.group_by != "topological":
        out.append("Groups:")
        offset = 0
        for g in dsm.groups:
            lo = offset + 1
            hi = offset + len(g.members)
            out.append(f"  [{lo}-{hi}] {g.label}")
            offset = hi
        out.append("")

    cell_w = max(2, idx_pad + 1)
    label_prefix = " " * (idx_pad + 2 + label_width + 1)

    def _row_cells(cells: list[str]) -> str:
        rendered: list[str] = []
        for i, glyph in enumerate(cells):
            if i in boundaries and i != 0:
                rendered.append("|")
            rendered.append(glyph.rjust(cell_w - 1))
        return " ".join(rendered)

    header_glyphs = [str(i + 1) for i in range(n)]
    out.append(label_prefix + _row_cells(header_glyphs))

    sep_glyphs: list[str] = ["-" * (cell_w - 1) for _ in range(n)]
    out.append(label_prefix + _row_cells(sep_glyphs).replace(" ", "-").replace("|", "+"))

    for r in range(n):
        if r in boundaries and r != 0:
            out.append("")
        row_glyphs: list[str] = []
        for c in range(n):
            if r == c:
                row_glyphs.append("\\")
            elif (r, c) in cell_lookup:
                row_glyphs.append(_cell_glyph(cell_lookup[(r, c)], dsm.weight))
            else:
                row_glyphs.append(".")
        label = dsm.ordering[r]
        if len(label) > label_width:
            label = "..." + label[-(label_width - 3) :]
        prefix = f"{str(r + 1).rjust(idx_pad)} {label.ljust(label_width)} "
        out.append(prefix + _row_cells(row_glyphs))

    return "\n".join(out) + "\n"


def _group_boundaries(dsm: DSM) -> set[int]:
    """Indices at which a new group starts (excluding 0)."""
    boundaries: set[int] = set()
    offset = 0
    for g in dsm.groups:
        if offset != 0:
            boundaries.add(offset)
        offset += len(g.members)
    return boundaries


def render_json(dsm: DSM) -> dict[str, object]:
    """JSON-serializable dict. Stable key order for diff-friendly output."""
    return {
        "n": len(dsm.ordering),
        "group_by": dsm.group_by,
        "weight": dsm.weight,
        "ordering": list(dsm.ordering),
        "groups": [{"label": g.label, "members": list(g.members)} for g in dsm.groups],
        "cells": [{"row": c.row, "col": c.col, "weight": c.weight} for c in dsm.cells],
    }


def dsm_from_dict(payload: dict[str, object]) -> DSM:
    ordering = cast(list[str], payload["ordering"])
    raw_groups = cast(list[dict[str, object]], payload["groups"])
    raw_cells = cast(list[dict[str, object]], payload["cells"])
    return DSM(
        ordering=tuple(ordering),
        groups=tuple(
            DSMGroup(
                label=cast(str, g["label"]),
                members=tuple(cast(list[str], g["members"])),
            )
            for g in raw_groups
        ),
        cells=tuple(
            DSMCell(
                row=cast(int, c["row"]),
                col=cast(int, c["col"]),
                weight=cast(float, c["weight"]),
            )
            for c in raw_cells
        ),
        group_by=cast(GroupBy, payload["group_by"]),
        weight=cast(Weight, payload["weight"]),
    )


def write_dsm(dsm: DSM, path: Path) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(render_json(dsm), indent=2, sort_keys=True))


def read_dsm(path: Path) -> DSM | None:
    import json

    if not path.exists():
        return None
    return dsm_from_dict(json.loads(path.read_text()))


def diff_dsm(before: DSM, after: DSM) -> DSMDiff:
    """Diff two DSMs by (source_name, target_name).

    Operates on names rather than positions so the diff is meaningful
    when reordering changes (e.g. after community detection re-runs on
    a slightly different graph). `new_back_edges` is computed in
    `after`'s ordering: an edge is a back-edge if the source is later in
    `after.ordering` than the target.
    """
    before_edges = _name_indexed(before)
    after_edges = _name_indexed(after)
    after_pos = {n: i for i, n in enumerate(after.ordering)}

    added: list[DSMCell] = []
    removed: list[DSMCell] = []
    weight_changed: list[tuple[DSMCell, DSMCell]] = []
    new_back_edges: list[DSMCell] = []

    for key, after_cell in after_edges.items():
        if key not in before_edges:
            added.append(after_cell)
            row_name, col_name = key
            if (
                row_name in after_pos
                and col_name in after_pos
                and after_pos[row_name] > after_pos[col_name]
            ):
                new_back_edges.append(after_cell)
        else:
            before_cell = before_edges[key]
            if before_cell.weight != after_cell.weight:
                weight_changed.append((before_cell, after_cell))

    for key, before_cell in before_edges.items():
        if key not in after_edges:
            removed.append(before_cell)

    before_nodes = set(before.ordering)
    after_nodes = set(after.ordering)
    nodes_added = tuple(sorted(after_nodes - before_nodes))
    nodes_removed = tuple(sorted(before_nodes - after_nodes))

    added.sort(key=lambda c: (c.row, c.col))
    removed.sort(key=lambda c: (c.row, c.col))
    new_back_edges.sort(key=lambda c: (c.row, c.col))

    return DSMDiff(
        added=tuple(added),
        removed=tuple(removed),
        weight_changed=tuple(weight_changed),
        nodes_added=nodes_added,
        nodes_removed=nodes_removed,
        new_back_edges=tuple(new_back_edges),
    )


def _name_indexed(dsm: DSM) -> dict[tuple[str, str], DSMCell]:
    return {(dsm.ordering[c.row], dsm.ordering[c.col]): c for c in dsm.cells}


def render_diff_text(diff: DSMDiff, after: DSM) -> str:
    """Render a DSMDiff as a short agent-friendly text summary."""
    lines: list[str] = []
    lines.append(
        f"DSM diff: +{len(diff.added)} cells, -{len(diff.removed)} cells, "
        f"~{len(diff.weight_changed)} weight changes, "
        f"+{len(diff.nodes_added)} modules, -{len(diff.nodes_removed)} modules."
    )
    if diff.new_back_edges:
        lines.append("")
        lines.append(f"New back-edges ({len(diff.new_back_edges)}):")
        for cell in diff.new_back_edges:
            src = after.ordering[cell.row]
            dst = after.ordering[cell.col]
            lines.append(f"  {src} -> {dst}")
    if diff.added and not diff.new_back_edges:
        lines.append("")
        lines.append(f"Added edges ({len(diff.added)}):")
        for cell in diff.added[:10]:
            src = after.ordering[cell.row]
            dst = after.ordering[cell.col]
            lines.append(f"  {src} -> {dst}")
        if len(diff.added) > 10:
            lines.append(f"  ... and {len(diff.added) - 10} more")
    if diff.removed:
        lines.append("")
        lines.append(f"Removed edges ({len(diff.removed)}):")
        for cell in diff.removed[:10]:
            lines.append(f"  cell ({cell.row}, {cell.col})")
        if len(diff.removed) > 10:
            lines.append(f"  ... and {len(diff.removed) - 10} more")
    return "\n".join(lines) + "\n"
