"""Risk-weighted ranking of `archy diff` deltas for the agent loop.

`compute_diff` returns the raw added/resolved sets plus per-component score
deltas. That's exhaustive but not actionable: an agent has to scan the lists
and re-rank them to decide what to look at first. `summarize_diff` does that
ranking once, using the current graph's `compute_edit_risk` map to weight
each delta by how central and fragile the involved modules are.

Item kinds:

* ``cycle_added`` / ``cycle_resolved`` - risk = max edit_risk over cycle members
* ``violation_added`` / ``violation_resolved`` - risk = max(source, target)
* ``sdp_violation_added`` / ``sdp_violation_resolved`` - same as violation
* ``score_component_drop`` / ``score_component_gain`` - risk = clamp(|delta| * 5, 0, 1)

Score-component items use magnitude rather than module risk because they're
project-wide signals with no module to weight against. The x5 scaler maps
a 0.20 drop to risk 1.0, which matches the empirical "big regression" floor
on the 27-project bench.
"""

from __future__ import annotations

import networkx as nx

from archy.diff import DiffReport, DiffSummary, DiffSummaryItem
from archy.risk import compute_edit_risk

_COMPONENT_NAMES = ("modularity", "acyclicity", "depth", "equality", "complexity")
_SCORE_MAGNITUDE_SCALER = 5.0
# Cycles in giant SCCs (e.g., dagster's 200+ module cycle) would dump every
# member into a single description line, making the text output unreadable.
# Truncate to a handful of members and append a "(+N more)" suffix; full
# member lists remain in `modules`.
_MAX_CYCLE_MEMBERS_IN_DESCRIPTION = 5


def summarize_diff(diff: DiffReport, graph: nx.DiGraph, *, top_n: int = 5) -> DiffSummary:
    """Rank diff items by risk and produce a structured headline.

    `graph` is the current (post-edit) graph; risk is computed once over it.
    Items are sorted by risk descending, ties broken by description for
    determinism.
    """
    risk = compute_edit_risk(graph)
    regressions = _collect_regressions(diff, risk)
    improvements = _collect_improvements(diff, risk)
    return DiffSummary(
        headline=_make_headline(diff),
        top_regressions=_rank(regressions, top_n),
        top_improvements=_rank(improvements, top_n),
    )


def _collect_regressions(diff: DiffReport, risk: dict[str, float]) -> list[DiffSummaryItem]:
    items: list[DiffSummaryItem] = []
    for cycle in diff.cycles.added:
        items.append(
            DiffSummaryItem(
                kind="cycle_added",
                risk=_max_module_risk(cycle.modules, risk),
                modules=tuple(cycle.modules),
                description=f"new cycle: {_format_modules(cycle.modules)}",
            )
        )
    for v in diff.violations.added:
        items.append(
            DiffSummaryItem(
                kind="violation_added",
                risk=_max_module_risk((v.source, v.target), risk),
                modules=(v.source, v.target),
                description=(
                    f"new layer violation: {v.source} -> {v.target} "
                    f"({v.rule.from_layer} -> {v.rule.to_layer})"
                ),
            )
        )
    for v in diff.sdp_violations.added:
        items.append(
            DiffSummaryItem(
                kind="sdp_violation_added",
                risk=_max_module_risk((v.source, v.target), risk),
                modules=(v.source, v.target),
                description=(
                    f"new SDP violation: {v.source} (I={v.source_instability:.2f}) -> "
                    f"{v.target} (I={v.target_instability:.2f})"
                ),
            )
        )
    for name in _COMPONENT_NAMES:
        delta = getattr(diff.score_delta, name)
        if delta < 0:
            items.append(
                DiffSummaryItem(
                    kind="score_component_drop",
                    risk=_score_risk(delta),
                    modules=(),
                    description=f"{name} dropped {delta:+.3f}",
                )
            )
    return items


def _collect_improvements(diff: DiffReport, risk: dict[str, float]) -> list[DiffSummaryItem]:
    items: list[DiffSummaryItem] = []
    for cycle in diff.cycles.resolved:
        items.append(
            DiffSummaryItem(
                kind="cycle_resolved",
                risk=_max_module_risk(cycle.modules, risk),
                modules=tuple(cycle.modules),
                description=f"cycle resolved: {_format_modules(cycle.modules)}",
            )
        )
    for v in diff.violations.resolved:
        items.append(
            DiffSummaryItem(
                kind="violation_resolved",
                risk=_max_module_risk((v.source, v.target), risk),
                modules=(v.source, v.target),
                description=(
                    f"layer violation resolved: {v.source} -> {v.target} "
                    f"({v.rule.from_layer} -> {v.rule.to_layer})"
                ),
            )
        )
    for v in diff.sdp_violations.resolved:
        items.append(
            DiffSummaryItem(
                kind="sdp_violation_resolved",
                risk=_max_module_risk((v.source, v.target), risk),
                modules=(v.source, v.target),
                description=f"SDP violation resolved: {v.source} -> {v.target}",
            )
        )
    for name in _COMPONENT_NAMES:
        delta = getattr(diff.score_delta, name)
        if delta > 0:
            items.append(
                DiffSummaryItem(
                    kind="score_component_gain",
                    risk=_score_risk(delta),
                    modules=(),
                    description=f"{name} improved {delta:+.3f}",
                )
            )
    return items


def _format_modules(modules: tuple[str, ...] | list[str]) -> str:
    members = list(modules)
    if len(members) <= _MAX_CYCLE_MEMBERS_IN_DESCRIPTION:
        return ", ".join(members)
    shown = members[:_MAX_CYCLE_MEMBERS_IN_DESCRIPTION]
    remaining = len(members) - _MAX_CYCLE_MEMBERS_IN_DESCRIPTION
    return f"{', '.join(shown)} (+{remaining} more)"


def _max_module_risk(modules: tuple[str, ...] | list[str], risk: dict[str, float]) -> float:
    values = [risk.get(m, 0.0) for m in modules]
    return max(values) if values else 0.0


def _score_risk(delta: float) -> float:
    magnitude = abs(delta) * _SCORE_MAGNITUDE_SCALER
    if magnitude > 1.0:
        return 1.0
    return magnitude


def _rank(items: list[DiffSummaryItem], top_n: int) -> tuple[DiffSummaryItem, ...]:
    items.sort(key=lambda i: (-i.risk, i.description))
    return tuple(items[:top_n])


def _make_headline(diff: DiffReport) -> str:
    overall = diff.score_delta.overall
    drivers: list[str] = []
    for name in _COMPONENT_NAMES:
        delta = getattr(diff.score_delta, name)
        if abs(delta) >= 0.01:
            drivers.append(f"{name} {delta:+.2f}")
    cycles_added = len(diff.cycles.added)
    cycles_resolved = len(diff.cycles.resolved)
    violations_added = len(diff.violations.added) + len(diff.sdp_violations.added)
    violations_resolved = len(diff.violations.resolved) + len(diff.sdp_violations.resolved)
    parts = [f"overall {overall:+.3f}"]
    if drivers:
        parts.append("driven by " + ", ".join(drivers[:3]))
    if cycles_added or cycles_resolved:
        parts.append(f"cycles +{cycles_added}/-{cycles_resolved}")
    if violations_added or violations_resolved:
        parts.append(f"violations +{violations_added}/-{violations_resolved}")
    return "; ".join(parts)
