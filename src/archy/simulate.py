"""Counterfactual pre-edit consequence check (`archy_simulate`).

Given the current import graph plus a proposed *edge delta* (imports to add,
imports to remove between existing modules), return the structural consequence
**before any file is written**: new/resolved cycles, new topological back-edges,
new layer/SDP violations, per-axis score delta, and the change in blast radius.
No files are touched.

This is almost entirely *composition*: the delta is applied to an in-memory copy
of the graph, then the existing snapshot/diff/DSM/propagation machinery runs
against that hypothetical graph. The only new logic is resolving + applying the
delta and packaging the result. See `docs/SPEC_SIMULATE.md`.

Why a synthetic edge with `lines=()` is safe (the validation oracle in the spec
depends on it): cycle identity is `frozenset(modules)`, violation identity is
`(from_layer, to_layer, source, target)`, and the DSM gives every import edge a
constant weight of 1.0. None of those read `lines`, so the simulated and real
graphs agree on every field the report compares.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field

from archy.diff import (
    CycleSetDiff,
    ScoreDelta,
    SdpViolationSetDiff,
    ViolationSetDiff,
    compute_diff,
    take_snapshot,
)
from archy.diff_summary import DiffSummary, summarize_diff
from archy.dsm import build_dsm, diff_dsm
from archy.graph import resolve_modules
from archy.reach import compute_propagation_cost


class EdgeSpec(BaseModel):
    """An edge in the proposed delta, as the caller names it (pre-resolution).

    `from` / `to` are module qualnames or file paths. `from` is a Python
    keyword, so the field is `from_` with the wire alias `from`.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    from_: str = Field(alias="from")
    to: str

    def as_pair(self) -> tuple[str, str]:
        return (self.from_, self.to)


class EdgeRef(BaseModel):
    """A directed dependency `source -> target` (source imports target)."""

    model_config = ConfigDict(frozen=True)

    source: str
    target: str


class AppliedDelta(BaseModel):
    """What the simulation actually did, echoed back so nothing is silent."""

    model_config = ConfigDict(frozen=True)

    added_edges: tuple[EdgeRef, ...]
    removed_edges: tuple[EdgeRef, ...]
    unresolved: tuple[str, ...]
    no_op_adds: tuple[EdgeRef, ...]
    no_op_removes: tuple[EdgeRef, ...]
    rejected: tuple[str, ...]


class PropagationDelta(BaseModel):
    """Blast-radius (MacCormack propagation cost) before vs after the delta."""

    model_config = ConfigDict(frozen=True)

    before: float
    after: float
    delta: float


class SimulateReport(BaseModel):
    """Consequence of a hypothetical edge delta. Mirrors the `archy_diff` shape.

    `score_delta` / `cycles` / `violations` / `sdp_violations` / `summary` are
    the same fields `archy_diff` returns, computed against the hypothetical
    graph. `new_back_edges` and `propagation_cost` add the topological-ordering
    and blast-radius views. Nothing here was written to disk.
    """

    model_config = ConfigDict(frozen=True)

    applied: AppliedDelta
    score_delta: ScoreDelta
    cycles: CycleSetDiff
    violations: ViolationSetDiff
    sdp_violations: SdpViolationSetDiff
    new_back_edges: tuple[EdgeRef, ...]
    propagation_cost: PropagationDelta
    summary: DiffSummary


def find_simulate(
    graph: nx.DiGraph,
    *,
    add: list[tuple[str, str]],
    remove: list[tuple[str, str]],
    config_path: Path | None = None,
    project_root: Path | None = None,
) -> SimulateReport:
    """Apply `add`/`remove` edge specs to a copy of `graph` and report the diff.

    `add` / `remove` are `(source, target)` pairs of qualnames or file paths.
    `graph` should be the internal-only graph (the same one `archy_diff` uses),
    so the result is directly comparable to a post-edit diff.
    """
    applied = _resolve_delta(graph, add=add, remove=remove, project_root=project_root)

    hypo = graph.copy()
    for edge in applied.added_edges:
        if not hypo.has_edge(edge.source, edge.target):
            hypo.add_edge(edge.source, edge.target, kinds=("import",), lines=())
    for edge in applied.removed_edges:
        if hypo.has_edge(edge.source, edge.target):
            hypo.remove_edge(edge.source, edge.target)

    before = take_snapshot(graph, config_path=config_path)
    after = take_snapshot(hypo, config_path=config_path)
    report = compute_diff(before, after)
    summary = summarize_diff(report, hypo, hypothetical=True)

    prop_before = compute_propagation_cost(graph)[0]
    prop_after = compute_propagation_cost(hypo)[0]

    return SimulateReport(
        applied=applied,
        score_delta=report.score_delta,
        cycles=report.cycles,
        violations=report.violations,
        sdp_violations=report.sdp_violations,
        new_back_edges=_new_back_edges(graph, hypo),
        propagation_cost=PropagationDelta(
            before=prop_before,
            after=prop_after,
            delta=prop_after - prop_before,
        ),
        summary=summary,
    )


def _resolve_delta(
    graph: nx.DiGraph,
    *,
    add: list[tuple[str, str]],
    remove: list[tuple[str, str]],
    project_root: Path | None,
) -> AppliedDelta:
    """Resolve raw endpoint strings to graph nodes and classify each spec.

    An edge whose endpoint matches no internal module is `unresolved`; a
    self-loop is `rejected`; an `add` of an existing edge or a `remove` of a
    missing edge is a no-op. Only genuinely applicable specs land in
    `added_edges` / `removed_edges`.

    Resolved pairs are de-duplicated within each list, and a pair that appears
    in both `add` and `remove` cancels (recorded in `rejected`, applied to
    neither). This keeps the echoed `AppliedDelta` consistent with the
    sequential apply and, critically, stops a repeated `remove` from calling
    `remove_edge` twice (which would raise).

    Self-loops are NOT rejected: archy's resolver does produce module-imports-
    itself edges (e.g. `from . import box as box` in rich's box.py), so a
    self-edge can genuinely exist and removing it must be simulable.
    """

    def _resolve_one(ref: str) -> str | None:
        res, _ = resolve_modules(graph, [ref], project_root=project_root)
        return res[0] if res else None

    def _resolve_pairs(
        specs: list[tuple[str, str]],
    ) -> tuple[list[tuple[str, str]], list[str]]:
        pairs: list[tuple[str, str]] = []
        unresolved: list[str] = []
        for src_raw, dst_raw in specs:
            src, dst = _resolve_one(src_raw), _resolve_one(dst_raw)
            if src is None:
                unresolved.append(src_raw)
            if dst is None:
                unresolved.append(dst_raw)
            if src is None or dst is None:
                continue
            pairs.append((src, dst))
        return list(dict.fromkeys(pairs)), unresolved

    add_pairs, add_unres = _resolve_pairs(add)
    remove_pairs, rem_unres = _resolve_pairs(remove)
    rejected: list[str] = []
    unresolved = add_unres + rem_unres

    # A pair in both add and remove cancels: applying both would be a no-op, and
    # the report should not claim it was added or removed.
    conflicts = set(add_pairs) & set(remove_pairs)
    for src, dst in sorted(conflicts):
        rejected.append(f"{src} -> {dst} (both add and remove)")
    add_pairs = [p for p in add_pairs if p not in conflicts]
    remove_pairs = [p for p in remove_pairs if p not in conflicts]

    existing = set(graph.edges())
    added, no_op_adds, removed, no_op_removes = [], [], [], []
    for src, dst in add_pairs:
        ref = EdgeRef(source=src, target=dst)
        (no_op_adds if (src, dst) in existing else added).append(ref)
    for src, dst in remove_pairs:
        ref = EdgeRef(source=src, target=dst)
        (removed if (src, dst) in existing else no_op_removes).append(ref)

    return AppliedDelta(
        added_edges=tuple(added),
        removed_edges=tuple(removed),
        unresolved=tuple(unresolved),
        no_op_adds=tuple(no_op_adds),
        no_op_removes=tuple(no_op_removes),
        rejected=tuple(rejected),
    )


def _new_back_edges(before: nx.DiGraph, after: nx.DiGraph) -> tuple[EdgeRef, ...]:
    """Edges that became topological back-edges, as `source -> target` names.

    `DSMDiff.new_back_edges` are positional `(row, col)` cells into the after-DSM
    ordering; an agent needs names, so translate them here.
    """
    before_dsm = build_dsm(before, group_by="topological")
    after_dsm = build_dsm(after, group_by="topological")
    diff = diff_dsm(before_dsm, after_dsm)
    ordering = after_dsm.ordering
    return tuple(
        EdgeRef(source=ordering[cell.row], target=ordering[cell.col])
        for cell in diff.new_back_edges
    )
