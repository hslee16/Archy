"""Renderers for `score`, `cycles`, `snapshot`, `diff` and `simulate`.

archy:mirrored-by _cycle_scan_to_dict -> archy.cli_history, archy.cli_render,
                  _cycles_to_json -> archy.cli_history, archy.cli_render,
                  _cycles_to_text -> archy.cli_history, archy.cli_render,
                  _diff_to_text -> archy.cli, archy.cli_history, archy.cli_render,
                  _score_to_text -> archy.cli_history, archy.cli_render,
                  _simulate_to_text -> archy.cli, archy.cli_history, archy.cli_render,
                  _snapshot_to_text -> archy.cli_history, archy.cli_render
"""

from __future__ import annotations

from archy.cli_render_common import (
    _added_block,
    _format_lines,
    _location,
    _score_delta_lines,
    _summary_to_text,
)
from archy.cycles import Cycle, CycleScan
from archy.diff import (
    CapturedSnapshot,
    DiffReport,
)
from archy.graph_namespace import NamespaceGap, namespace_gap_reason
from archy.score import VACUOUS_SCORE_NOTE, Score
from archy.simulate import SimulateReport


def _call_weighted_modularity_prose(s: Score) -> str:
    """One-line direction summary for the call-weighted Q diagnostic.

    The gap (weighted - unweighted) is what carries the meaning; thresholds
    chosen empirically from the 27-project bench so common projects land
    in one of the three buckets without false precision in the prose.
    """
    delta = s.inputs.raw_modularity_weighted - s.inputs.raw_modularity
    if delta > 0.05:
        return "calls amplify community structure"
    if delta < -0.05:
        return "calls cross community boundaries"
    return "calls roughly track community structure"


def _namespace_gaps_to_json(gaps: tuple[NamespaceGap, ...]) -> list[dict]:
    """Each gap with its reason, so a JSON consumer is told why and not only that (#586)."""
    return [{**g.model_dump(mode="json"), "reason": namespace_gap_reason(g)} for g in gaps]


def _namespace_gap_lines(gaps: tuple[NamespaceGap, ...]) -> list[str]:
    return [f"# warning: {namespace_gap_reason(g)}" for g in gaps]


def _score_to_dict(s: Score, namespace_gaps: tuple[NamespaceGap, ...] | None = None) -> dict:
    """`namespace_gaps` is None where the graph was not consulted, so the key is left out."""
    return {
        **(
            {}
            if namespace_gaps is None
            else {"namespace_gaps": _namespace_gaps_to_json(namespace_gaps)}
        ),
        "overall": s.overall,
        "modules_scanned": s.inputs.module_count,
        "note": VACUOUS_SCORE_NOTE if s.inputs.module_count == 0 else None,
        "components": {
            "modularity": s.modularity,
            "acyclicity": s.acyclicity,
            "depth": s.depth,
            "equality": s.equality,
            "complexity": s.complexity,
        },
        "inputs": {
            "module_count": s.inputs.module_count,
            "edge_count": s.inputs.edge_count,
            "cycle_count": s.inputs.cycle_count,
            "tangle_ratio": s.inputs.tangle_ratio,
            "max_depth": s.inputs.max_depth,
            "community_count": s.inputs.community_count,
            "raw_modularity": s.inputs.raw_modularity,
            "raw_gini": s.inputs.raw_gini,
            "propagation_cost": s.inputs.propagation_cost,
            "call_edge_count": s.inputs.call_edge_count,
            "total_calls": s.inputs.total_calls,
            "calls_per_edge": s.inputs.calls_per_edge,
            "function_count": s.inputs.function_count,
            "cc_total": s.inputs.cc_total,
            "cc_max": s.inputs.cc_max,
            "cc_mean": s.inputs.cc_mean,
            "raw_modularity_weighted": s.inputs.raw_modularity_weighted,
            "modularity_weighted_community_count": s.inputs.modularity_weighted_community_count,
        },
    }


def _score_to_text(s: Score, namespace_gaps: tuple[NamespaceGap, ...] = ()) -> str:
    vacuous = f" (vacuous: {VACUOUS_SCORE_NOTE})" if s.inputs.module_count == 0 else ""
    lines = [
        f"# archy score: {s.overall:.3f}{vacuous}",
        f"modularity:  {s.modularity:.3f}  "
        f"({s.inputs.community_count} communities, raw Q={s.inputs.raw_modularity:.3f})",
        f"  call-weighted Q={s.inputs.raw_modularity_weighted:.3f}  "
        f"({_call_weighted_modularity_prose(s)})",
        f"acyclicity:  {s.acyclicity:.3f}  "
        f"({s.inputs.cycle_count} cycles, tangle={s.inputs.tangle_ratio:.3f})",
        f"depth:       {s.depth:.3f}  (max depth {s.inputs.max_depth})",
        f"equality:    {s.equality:.3f}  (Gini={s.inputs.raw_gini:.3f})",
        f"complexity:  {s.complexity:.3f}  "
        f"({s.inputs.function_count} functions, cc_mean={s.inputs.cc_mean:.2f}, "
        f"cc_max={s.inputs.cc_max})",
        f"# graph: {s.inputs.module_count} modules, {s.inputs.edge_count} edges",
        f"# propagation_cost: {s.inputs.propagation_cost:.4f}  "
        f"(diagnostic, not in score; MacCormack reverse-reach fraction)",
        f"# calls: {s.inputs.total_calls} resolved across {s.inputs.call_edge_count} edge(s), "
        f"{s.inputs.calls_per_edge:.2f}/edge  (diagnostic, not in score)",
        *_namespace_gap_lines(namespace_gaps),
    ]
    return "\n".join(lines)


def _cycles_to_json(cycles: list[Cycle]) -> list[dict]:
    return [
        {
            "modules": list(c.modules),
            "edges": [
                {"source": e.source, "target": e.target, "lines": list(e.lines), "path": e.path}
                for e in c.edges
            ],
            "type_checking_edges": c.type_checking_edges,
        }
        for c in cycles
    ]


def _cycle_scan_to_dict(scan: CycleScan) -> dict:
    """The `cycles --exclude-type-checking` payload: the list plus what it left out.

    An object, not the bare list, because an exclusion that empties the list
    would otherwise leave no trace of itself (#520). The default output stays a
    list so existing consumers see no change.
    """
    return {
        "cycles": _cycles_to_json(list(scan.cycles)),
        "excluded_type_checking_edges": scan.excluded_type_checking_edges,
        "namespace_gaps": _namespace_gaps_to_json(scan.namespace_gaps),
    }


def _snapshot_to_text(snap: CapturedSnapshot) -> str:
    return (
        f"# score: {snap.score.overall:.3f}  "
        f"cycles: {len(snap.cycles)}  violations: {len(snap.violations)}"
    )


def _diff_to_text(result: DiffReport) -> str:
    lines = [
        *_diff_head_lines(result),
        *_diff_cycle_lines(result),
        *_diff_violation_lines(result),
        *_diff_reach_lines(result),
    ]
    if result.strict is not None:
        lines.append("")
        verdict = "PASS" if result.strict.passed else "FAIL"
        lines.append(f"# strict: {verdict}, {result.strict.reason}")
    return "\n".join(lines)


def _diff_head_lines(result: DiffReport) -> list[str]:
    lines: list[str] = []
    if result.summary is not None:
        lines.extend(_summary_to_text(result.summary))
        lines.append("")
    if result.score_delta is None:
        lines.append("# score deltas: not available (the baseline records findings only)")
    else:
        lines.extend(_score_delta_lines(result.score_delta, "# score deltas (current - baseline):"))
    if result.baseline_findings_only:
        lines.append("# sdp: not compared (the baseline records findings only)")
    return lines


def _diff_cycle_lines(result: DiffReport) -> list[str]:
    cycles = result.cycles
    lines = ["", f"# cycles: +{len(cycles.added)} added, -{len(cycles.resolved)} resolved"]
    for c in cycles.added:
        lines.append(f"  + cycle: {', '.join(c.modules)}")
    for c in cycles.resolved:
        lines.append(f"  - cycle (resolved): {', '.join(c.modules)}")
    return lines


def _diff_violation_lines(result: DiffReport) -> list[str]:
    violations = result.violations
    lines = [
        "",
        f"# violations: +{len(violations.added)} added, -{len(violations.resolved)} resolved",
    ]
    for v in violations.added:
        lines.append(
            f"  + {_location(v.path, v.lines)}{v.source} -> {v.target}  "
            f"({v.rule.from_layer} -> {v.rule.to_layer})"
        )
    for v in violations.resolved:
        rule = f"{v.rule.from_layer} -> {v.rule.to_layer}"
        lines.append(f"  - {v.source} -> {v.target}  resolved ({rule})")
    return lines


def _diff_reach_lines(result: DiffReport) -> list[str]:
    reach = result.required_violations
    # Only printed when there is something to say: a diff on a project with no
    # `required:` rules should not grow a permanent zero line.
    if not (reach.added or reach.resolved):
        return []
    lines = [
        "",
        f"# required-reach: +{len(reach.added)} added, -{len(reach.resolved)} resolved",
    ]
    for v in reach.added:
        lines.append(f"  + {v.detail}")
    for v in reach.resolved:
        if v.module is None:
            rule = f"{v.rule.describe_source()} must reach {v.rule.must_reach}"
            lines.append(f"  - rule can be checked again: {rule}")
        else:
            lines.append(f"  - {v.module} now reaches {v.rule.must_reach}")
    return lines


def _simulate_to_text(result: SimulateReport) -> str:
    a = result.applied
    lines = _simulate_applied_lines(a)
    lines.append("")
    if a.coverage == "none":
        # Every number below would describe the unchanged graph: a `+0.000` that
        # reads as "this edit is free" when no edit was simulated.
        lines.append(
            "# summary: nothing simulated: no edge resolved, so no score, cycle or "
            "violation delta was computed"
        )
        return "\n".join(lines)
    if a.coverage == "partial":
        lines.append(
            f"# PARTIAL: {len(a.skipped)} spec(s) skipped, unresolved ({'; '.join(a.skipped)}); "
            "the results below cover only the edges that resolved"
        )
    lines.extend(_summary_to_text(result.summary))

    lines.append("")
    lines.extend(_score_delta_lines(result.score_delta, "# would-be score deltas:"))
    p = result.propagation_cost
    lines.append(f"  {'blast radius':11s} {p.before:.3f} -> {p.after:.3f} ({p.delta:+.3f})")
    lines.extend(_simulate_added_lines(result))
    return "\n".join(lines)


def _simulate_applied_lines(a) -> list[str]:
    lines = [
        f"# simulation (no files written): +{len(a.added_edges)} / -{len(a.removed_edges)} edge(s)"
    ]
    suggestions = {h.ref: h.did_you_mean for h in a.unresolved_hints}
    for name in a.unresolved:
        hint = f"  (did you mean {', '.join(suggestions[name])}?)" if name in suggestions else ""
        lines.append(f"  ! unresolved endpoint: {name}{hint}")
    for item in a.rejected:
        lines.append(f"  ! rejected: {item}")
    for note, edges in (
        ("no-op add (edge already exists)", a.no_op_adds),
        ("no-op remove (edge absent)", a.no_op_removes),
    ):
        for e in edges:
            lines.append(f"  ~ {note}: {e.source} -> {e.target}")
    return lines


def _simulate_added_lines(result: SimulateReport) -> list[str]:
    cycles = result.cycles.added
    back = result.new_back_edges
    violations = result.violations.added
    # Listed unconditionally, like the blocks above, NOT left to the ranked
    # summary. `simulate` has no --top-n and takes the default cap of 5, and a
    # whole-rule reach item carries risk 0.0 (it names no module to weight), so
    # it is the first thing evicted from the top-5 exactly when a simulation has
    # a lot going on. "Would removing this import break a required-reach rule?"
    # is a question simulate exists to answer; it must not depend on ranking.
    required = result.required_violations.added
    return [
        *_added_block(
            f"# new cycles (+{len(cycles)}):", [f"  + {', '.join(c.modules)}" for c in cycles]
        ),
        *_added_block(
            f"# new back-edges (+{len(back)}):", [f"  + {e.source} -> {e.target}" for e in back]
        ),
        *_added_block(
            f"# new layer violations (+{len(violations)}):",
            [
                f"  + {v.source} -> {v.target}  ({v.rule.from_layer} -> {v.rule.to_layer})"
                for v in violations
            ],
        ),
        *_added_block(
            f"# new required-reach violations (+{len(required)}):",
            [f"  + {v.detail}" for v in required],
        ),
    ]


def _cycles_to_text(
    cycles: list[Cycle],
    min_size: int,
    *,
    excluded: int | None = None,
    no_modules: str | None = None,
    namespace_gaps: tuple[NamespaceGap, ...] = (),
) -> str:
    """`excluded` is None when TYPE_CHECKING exclusion was off, else the edge count left out.

    `no_modules` is the reason a scan found nothing to check (#523); it replaces
    "No cycles found", which would read as a clean pass over nothing.
    """
    if no_modules is not None:
        return f"# {no_modules}."
    note = (
        ""
        if excluded is None
        else f"; {excluded} TYPE_CHECKING-only edge(s) excluded (--exclude-type-checking)"
    )
    warnings = _namespace_gap_lines(namespace_gaps)
    if not cycles:
        return "\n".join([f"# No cycles found (min_size={min_size}){note}.", *warnings])
    lines = [f"# {len(cycles)} cycle(s) found{note}"]
    for c in cycles:
        lines.append(f"\nCycle of {len(c.modules)} module(s):")
        for m in c.modules:
            lines.append(f"  - {m}")
        lines.append("Edges:")
        for e in c.edges:
            lines.append(
                f"  {_location(e.path, e.lines)}{e.source} -> {e.target}  {_format_lines(e.lines)}"
            )
        if excluded is None and c.type_checking_edges:
            lines.append(
                f"  # {c.type_checking_edges} of {len(c.edges)} edge(s) exist only under "
                "`if TYPE_CHECKING:`; --exclude-type-checking (or archy.yaml "
                "`exclude_type_checking_imports: true`) leaves them out."
            )
    return "\n".join([*lines, *warnings])
