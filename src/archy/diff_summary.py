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
* ``required_reach_violation_added`` / ``..._resolved`` - risk = the offending
  module's edit_risk (0.0 for a whole-rule item, which names no module)
* ``score_component_drop`` / ``score_component_gain`` - risk = clamp(|delta| * 5, 0, 1)

Score-component items use magnitude rather than module risk because they're
project-wide signals with no module to weight against. The x5 scaler maps
a 0.20 drop to risk 1.0, which matches the empirical "big regression" floor
on the 27-project bench.

Each item also carries a `prompt`: the same delta reframed as the judgment
question a reviewer should answer ("new cycle a -> b; intended, or should an
edge be inverted?"). A number tells you *what changed*; the question tells
you *what to decide*. This is the lightweight human-facing layer the review
brief consumes.

archy:owns        summarize_diff
archy:mirrored-by summarize_diff -> archy.cli, archy.mcp, archy.simulate
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


def summarize_diff(
    diff: DiffReport, graph: nx.DiGraph, *, top_n: int = 5, hypothetical: bool = False
) -> DiffSummary:
    """Rank diff items by risk and produce a structured headline.

    `graph` is the current (post-edit) graph; risk is computed once over it.
    Items are sorted by risk descending, ties broken by description for
    determinism. When `hypothetical` is set (the `archy_simulate` path, where the
    delta has not been written), each item's `prompt` is phrased in conditional
    mood ("would form a cycle… Proceed?") instead of indicative past tense
    ("formed a cycle… Intended?").
    """
    risk = compute_edit_risk(graph)
    regressions = _collect_regressions(diff, risk, hypothetical)
    improvements = _collect_improvements(diff, risk, hypothetical)
    return DiffSummary(
        headline=_make_headline(diff),
        top_regressions=_rank(regressions, top_n),
        top_improvements=_rank(improvements, top_n),
    )


# Prompt wording. Indicative (a real diff, the edit happened) vs conditional
# (a simulation, the edit has not happened). Kept side by side so the two moods
# stay in sync as the vocabulary evolves.


def _cycle_added_prompt(members: str, hypothetical: bool) -> str:
    if hypothetical:
        return (
            f"Acyclicity would drop because {members} would form an import cycle. "
            "Proceed, or pick a different seam?"
        )
    return (
        f"Acyclicity dropped because {members} now form an import cycle. "
        "Intended, or should an edge be inverted or removed to break it?"
    )


def _violation_added_prompt(source: str, target: str, boundary: str, hypothetical: bool) -> str:
    if hypothetical:
        return (
            f"{source} -> {target} would cross the forbidden {boundary} boundary. "
            "Proceed, or route through an allowed seam?"
        )
    return (
        f"{source} -> {target} now crosses the forbidden {boundary} boundary. "
        "Intended, or a leak to route through an allowed seam?"
    )


def _sdp_added_prompt(
    source: str, target: str, before: float, after: float, hypothetical: bool
) -> str:
    tail = f"(I={before:.2f} -> {after:.2f})"
    if hypothetical:
        return (
            f"{source} would depend on the less-stable {target} {tail}. "
            "Proceed, or keep the dependency in the stable direction?"
        )
    return (
        f"{source} now depends on the less-stable {target} {tail}. "
        "Intended, or should the dependency follow stability?"
    )


def _reach_added_prompt(module: str, must_reach: str, reason: str, hypothetical: bool) -> str:
    """The one prompt that must carry the config author's `reason`.

    The others describe a structure the reviewer can see for themselves. This
    one reports an ABSENCE: the module stopped reaching something, and nothing
    in the diff shows why that mattered. Without the reason, the cheapest way to
    make the question go away is to delete the rule.
    """
    why = f" ({reason})" if reason else ""
    if hypothetical:
        return (
            f"{module} would stop reaching {must_reach}{why}. "
            "Proceed, or keep the import path that satisfies the rule?"
        )
    return (
        f"{module} no longer reaches {must_reach}{why}. "
        "Intended, or was a bootstrap import removed as unused?"
    )


def _reach_resolved_prompt(module: str, must_reach: str, hypothetical: bool) -> str:
    if hypothetical:
        return f"{module} would reach {must_reach}. Confirm that is the intended bootstrap path."
    return f"{module} now reaches {must_reach}. Confirm that is the intended bootstrap path."


def _reach_modules(module: str | None) -> tuple[str, ...]:
    """A reach violation names one module, or none when the whole rule is dead.

    Takes the module rather than the violation so this file needs no import of
    `archy.layers`: one more hop on the longest import chain, for a type
    annotation.
    """
    return (module,) if module else ()


def _score_drop_prompt(name: str, delta: float, hypothetical: bool) -> str:
    if hypothetical:
        return f"{name} would drop {delta:+.3f}. Acceptable, or pick a different approach?"
    return (
        f"{name} dropped {delta:+.3f}. Acceptable for this change, "
        "or a regression to address before committing?"
    )


def _cycle_resolved_prompt(members: str, hypothetical: bool) -> str:
    if hypothetical:
        return f"Cycle {members} would be resolved. Confirm that is the intended decoupling."
    return f"Cycle {members} is gone. Confirm this was the intended decoupling."


def _violation_resolved_prompt(source: str, target: str, boundary: str, hypothetical: bool) -> str:
    if hypothetical:
        return f"{source} -> {target} would no longer cross {boundary}. Confirm that is intended."
    return (
        f"{source} -> {target} no longer crosses {boundary}. "
        "Confirm the dependency was meant to be removed."
    )


def _sdp_resolved_prompt(source: str, target: str, hypothetical: bool) -> str:
    verb = "would no longer" if hypothetical else "no longer"
    return (
        f"{source} -> {target} {verb} violate the Stable Dependencies Principle. "
        "Confirm this is intended."
    )


def _score_gain_prompt(name: str, delta: float, hypothetical: bool) -> str:
    verb = "would improve" if hypothetical else "improved"
    return f"{name} {verb} {delta:+.3f}. No action needed; noted for context."


def _collect_regressions(
    diff: DiffReport, risk: dict[str, float], hypothetical: bool
) -> list[DiffSummaryItem]:
    items: list[DiffSummaryItem] = []
    for cycle in diff.cycles.added:
        members = _format_modules(cycle.modules)
        items.append(
            DiffSummaryItem(
                kind="cycle_added",
                risk=_max_module_risk(cycle.modules, risk),
                modules=tuple(cycle.modules),
                description=f"new cycle: {members}",
                prompt=_cycle_added_prompt(members, hypothetical),
            )
        )
    for v in diff.violations.added:
        boundary = f"{v.rule.from_layer} -> {v.rule.to_layer}"
        items.append(
            DiffSummaryItem(
                kind="violation_added",
                risk=_max_module_risk((v.source, v.target), risk),
                modules=(v.source, v.target),
                description=(f"new layer violation: {v.source} -> {v.target} ({boundary})"),
                prompt=_violation_added_prompt(v.source, v.target, boundary, hypothetical),
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
                prompt=_sdp_added_prompt(
                    v.source, v.target, v.source_instability, v.target_instability, hypothetical
                ),
            )
        )
    for v in diff.required_violations.added:
        modules = _reach_modules(v.module)
        items.append(
            DiffSummaryItem(
                kind="required_reach_violation_added",
                risk=_max_module_risk(modules, risk),
                modules=modules,
                description=(
                    f"new required-reach violation: {v.module or v.rule.source} no longer "
                    f"reaches {v.rule.must_reach}"
                ),
                prompt=_reach_added_prompt(
                    v.module or v.rule.source, v.rule.must_reach, v.rule.reason, hypothetical
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
                    prompt=_score_drop_prompt(name, delta, hypothetical),
                )
            )
    return items


def _collect_improvements(
    diff: DiffReport, risk: dict[str, float], hypothetical: bool
) -> list[DiffSummaryItem]:
    items: list[DiffSummaryItem] = []
    for cycle in diff.cycles.resolved:
        members = _format_modules(cycle.modules)
        items.append(
            DiffSummaryItem(
                kind="cycle_resolved",
                risk=_max_module_risk(cycle.modules, risk),
                modules=tuple(cycle.modules),
                description=f"cycle resolved: {members}",
                prompt=_cycle_resolved_prompt(members, hypothetical),
            )
        )
    for v in diff.violations.resolved:
        boundary = f"{v.rule.from_layer} -> {v.rule.to_layer}"
        items.append(
            DiffSummaryItem(
                kind="violation_resolved",
                risk=_max_module_risk((v.source, v.target), risk),
                modules=(v.source, v.target),
                description=(f"layer violation resolved: {v.source} -> {v.target} ({boundary})"),
                prompt=_violation_resolved_prompt(v.source, v.target, boundary, hypothetical),
            )
        )
    for v in diff.sdp_violations.resolved:
        items.append(
            DiffSummaryItem(
                kind="sdp_violation_resolved",
                risk=_max_module_risk((v.source, v.target), risk),
                modules=(v.source, v.target),
                description=f"SDP violation resolved: {v.source} -> {v.target}",
                prompt=_sdp_resolved_prompt(v.source, v.target, hypothetical),
            )
        )
    for v in diff.required_violations.resolved:
        modules = _reach_modules(v.module)
        items.append(
            DiffSummaryItem(
                kind="required_reach_violation_resolved",
                risk=_max_module_risk(modules, risk),
                modules=modules,
                description=(
                    f"required-reach violation resolved: {v.module or v.rule.source} now "
                    f"reaches {v.rule.must_reach}"
                ),
                prompt=_reach_resolved_prompt(
                    v.module or v.rule.source, v.rule.must_reach, hypothetical
                ),
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
                    prompt=_score_gain_prompt(name, delta, hypothetical),
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
    violations_added = (
        len(diff.violations.added)
        + len(diff.sdp_violations.added)
        + len(diff.required_violations.added)
    )
    violations_resolved = (
        len(diff.violations.resolved)
        + len(diff.sdp_violations.resolved)
        + len(diff.required_violations.resolved)
    )
    parts = [f"overall {overall:+.3f}"]
    if drivers:
        parts.append("driven by " + ", ".join(drivers[:3]))
    if cycles_added or cycles_resolved:
        parts.append(f"cycles +{cycles_added}/-{cycles_resolved}")
    if violations_added or violations_resolved:
        parts.append(f"violations +{violations_added}/-{violations_resolved}")
    return "; ".join(parts)
