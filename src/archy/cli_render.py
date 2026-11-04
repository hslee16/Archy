"""Text and JSON renderers for the CLI: pure functions of a payload, no Click.

Split out of `archy.cli` (#395) so the commands read as commands and the
renderers can be tested without a Click context.

archy:owns        ContractsOutcome
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy.affected import DELETED_OR_RENAMED_REASON, Affected
from archy.contracts import (
    ContractsResult,
)
from archy.conventions import (
    ConventionsReport,
    Gate,
    ModuleView,
)
from archy.coupling import (
    CouplingPair,
)
from archy.cycles import Cycle
from archy.diff import (
    CapturedSnapshot,
    DiffReport,
    DiffSummary,
    ScoreDelta,
)
from archy.duplicates import (
    DuplicateGroup,
)
from archy.hotspots import Hotspot
from archy.impact import Impact
from archy.layers import (
    LayerConfig,
    LayerCoverage,
    ReachViolation,
    RequiredRule,
    SdpViolation,
    Violation,
    contracts_unverified,
)
from archy.refactor import (
    RefactorPriority,
)
from archy.score import Score
from archy.simulate import SimulateReport


def _format_lines(lines: tuple[int, ...]) -> str:
    label = "lines" if len(lines) > 1 else "line"
    text = ", ".join(str(n) for n in lines) or "?"
    return f"({label}: {text})"


def _gate_to_dict(gate: dict, tolerance: float) -> dict:
    delta = gate["delta"]
    return {
        "previous": gate["previous"],
        "current": gate["current"],
        "delta": delta,
        "tolerance": tolerance,
        "passed": delta is None or delta >= -tolerance,
    }


def _gate_to_text(gate: dict, tolerance: float) -> str:
    if gate["delta"] is None:
        return (
            "# strict: no prior score recorded; nothing to compare. "
            "Pass `--record` to seed the baseline."
        )
    delta = gate["delta"]
    direction = "improved" if delta >= 0 else "dropped"
    verdict = "PASS" if delta >= -tolerance else "FAIL"
    prev_label = (gate.get("previous_commit") or "?")[:7]
    return (
        f"# strict: {verdict}  "
        f"{gate['previous']:.3f} ({prev_label}) -> {gate['current']:.3f}  "
        f"({direction} {delta:+.3f}, tolerance {tolerance:.3f})"
    )


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


def _score_to_dict(s: Score) -> dict:
    return {
        "overall": s.overall,
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


def _score_to_text(s: Score) -> str:
    lines = [
        f"# archy score: {s.overall:.3f}",
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
    ]
    return "\n".join(lines)


def _violations_to_json(violations: list[Violation]) -> list[dict]:
    return [
        {
            "rule": {"from": v.rule.from_layer, "to": v.rule.to_layer},
            "source": v.source,
            "target": v.target,
            "lines": list(v.lines),
        }
        for v in violations
    ]


def _reach_violations_to_json(violations: list[ReachViolation]) -> list[dict]:
    return [
        {
            "rule": {
                "source": v.rule.source,
                "must_reach": v.rule.must_reach,
                "reason": v.rule.reason,
                "within": v.rule.within,
            },
            "module": v.module,
            "detail": v.detail,
        }
        for v in violations
    ]


def _reach_violations_to_text(
    violations: list[ReachViolation], rules: tuple[RequiredRule, ...]
) -> str:
    """Render `required:` results. Called only when the config declares rules.

    The clean line names the rule count on purpose: "no required-reach
    violations" without it reads the same whether one rule passed or twenty did.
    """
    if not violations:
        return f"# No required-reach violations ({len(rules)} rule(s) checked, transitively)."
    lines = [f"# {len(violations)} required-reach violation(s)"]
    current: tuple[str, str] | None = None
    for v in violations:
        pair = (v.rule.describe_source(), v.rule.must_reach)
        if pair != current:
            header = f"\n{v.rule.describe_source()} must reach {v.rule.must_reach}:"
            if v.rule.reason:
                header += f"\n  reason: {v.rule.reason}"
            lines.append(header)
            current = pair
        lines.append(f"  {v.detail}")
    return "\n".join(lines)


def _violations_to_text(
    violations: list[Violation], config_path: Path, coverage: LayerCoverage
) -> str:
    """The headline verdict, qualified by coverage when coverage is degenerate.

    A clean pass under a config that governs no edges is true and useless: it
    says nothing was found without saying nothing was looked at. The numbers
    were already printed one line below, where a reader who has just read
    "No layer violations" has already stopped reading. Promoting the fact into
    the verdict itself is the whole point (#362).

    The substring "No layer violations" is preserved verbatim: `bench/walkthrough.py`
    and the CLI tests assert on it, and the qualification is a clause, not a
    replacement. The exit code is untouched -- see `check`.
    """
    if not violations:
        verdict = f"# No layer violations (config: {config_path})."
        if coverage.governs_nothing:
            return (
                f"# No layer violations, but no module in the tree falls under any root "
                f"this config names, so no rule here can fire (config: {config_path})."
            )
        if coverage.governs_no_edges:
            return (
                f"# No layer violations, but this config governs "
                f"{coverage.edges_governed} of {coverage.edges_total} internal edges "
                f"(0%), so no forbid rule can fire (config: {config_path})."
            )
        return verdict
    lines = [f"# {len(violations)} layer violation(s) (config: {config_path})"]
    current_rule: tuple[str, str] | None = None
    for v in violations:
        rule_pair = (v.rule.from_layer, v.rule.to_layer)
        if rule_pair != current_rule:
            lines.append(f"\n{v.rule.from_layer} -> {v.rule.to_layer} (forbidden):")
            current_rule = rule_pair
        lines.append(f"  {v.source} -> {v.target}  {_format_lines(v.lines)}")
    return "\n".join(lines)


def _coverage_to_json(coverage: LayerCoverage) -> dict:
    return {
        "modules_total": coverage.modules_total,
        "modules_matched": coverage.modules_matched,
        "modules_in_ruled_layer": coverage.modules_in_ruled_layer,
        "module_ratio": round(coverage.module_ratio, 4),
        "edges_total": coverage.edges_total,
        "edges_governed": coverage.edges_governed,
        "edge_ratio": round(coverage.edge_ratio, 4),
        "unlayered_modules": list(coverage.unlayered_modules),
        "modules_outside_declared_roots": coverage.modules_outside_declared_roots,
        # Presence, in the payload as well as the text output. Without these a
        # JSON consumer sees exit 1 with an empty `violations` list and nothing
        # explaining why, which is indistinguishable from a bug in archy.
        "layer_sizes": dict(coverage.layer_sizes),
        "layers_present": coverage.layers_present,
        "empty_layers": list(coverage.empty_layers),
        # The degenerate-coverage verdict and its most common cause, on the
        # wire as well as in the text output. A JSON consumer seeing an empty
        # `violations` list otherwise has to re-derive "could this config have
        # said anything?" from the raw counts.
        "governs_no_edges": coverage.governs_no_edges,
        "exact_pattern_hints": [
            {
                "layer": hint.layer,
                "pattern": hint.pattern,
                "unlayered_descendants": list(hint.unlayered_descendants),
                "suggestion": hint.suggestion,
            }
            for hint in coverage.exact_pattern_hints
        ],
    }


def _presence_to_text(coverage: LayerCoverage, minimum: int | None) -> str:
    """Report empty declared layers, and whether they breach a presence floor.

    Printed even without a floor configured: a layer matching nothing is worth
    saying out loud regardless, because every rule naming it is dead.
    """
    empty = coverage.empty_layers
    if not empty and minimum is None:
        return ""
    total = len(coverage.layer_sizes)
    lines = []
    if empty:
        lines.append(
            f"#   layers present: {coverage.layers_present} of {total} declared; "
            f"empty: {', '.join(empty)}"
        )
    if minimum is not None and coverage.layers_present < minimum:
        lines.append(
            f"#   FAIL: {coverage.layers_present} layer(s) present, min_layers_present is {minimum}"
        )
    return "\n".join(lines)


def _coverage_to_text(coverage: LayerCoverage) -> str:
    """One line, always printed, including on a clean pass.

    Printed on a PASS specifically: a clean result is exactly when a reader is
    entitled to know whether the rules could have said anything at all (#362).
    """
    if coverage.governs_nothing:
        return (
            "#   layer coverage: NO modules under the declared root packages, so no rule "
            "here can ever fire"
        )
    line = (
        f"#   layer coverage: {coverage.modules_matched} of {coverage.modules_total} modules "
        f"({coverage.module_ratio:.0%}), {coverage.edges_governed} of {coverage.edges_total} "
        f"internal edges ({coverage.edge_ratio:.0%})"
    )
    unlayered = len(coverage.unlayered_modules)
    if unlayered:
        line += f"; {unlayered} module(s) match no layer (`archy check --show-unlayered`)"
    if coverage.modules_outside_declared_roots:
        line += (
            f"\n#   {coverage.modules_outside_declared_roots} scanned module(s) sit outside "
            "every declared root package and are not counted above"
        )
    return line


def _brief_to_text(report, coverage, config, contracts, *, top_n: int) -> str:
    """One screen an agent can be handed BEFORE it starts, not a report to browse.

    🔴 SIZED FOR INJECTION, WHICH IS AN ECONOMIC CLAIM, NOT AN AESTHETIC ONE.
    On the hardware this was measured on, reading runs at ~796 tok/s and writing
    at ~12 -- 66x apart. A few thousand tokens of briefing costs seconds of
    prefill; one block of the model working the same fact out for itself costs
    ~32 seconds of decode. A brief only has to prevent one block in a handful of
    runs to pay for itself, which is why this is a command rather than advice to
    run six others.

    Ordered by what a reader needs FIRST, not by what is cheapest to compute:
    what kind of thing am I adding, what do I call it, what has to change with
    it, does it gate, and what can this configuration not see. The last is the
    one an agent skips and then re-derives at length.
    """
    lines = [f"# archy brief: {report.root}"]
    p = report.partition
    if p:
        aside = ", ".join(
            x
            for x in (
                f"{p.tests} test" if p.tests else "",
                f"{p.nonsource} non-source" if p.nonsource else "",
                f"{p.shadowed} duplicating a parent" if p.shadowed else "",
            )
            if x
        )
        lines.append(
            f"#   {report.modules_scanned} module(s)"
            + (f", {report.docs_scanned} doc file(s)" if report.docs_scanned else "")
            + (f"; set aside: {aside}" if aside else "")
        )

    lines.append("")
    lines.append("## what kind of thing am I adding, and where does it go")
    kinds = [b for b in report.bases if b.count >= 3][:top_n]
    if not kinds:
        lines.append("  (no base class in this project has enough subclasses to be a convention)")
    for b in kinds:
        note = (
            ""
            if b.suffix_agreement >= 0.8
            else f"  <- name is NOT the rule ({b.suffix_agreement:.0%})"
        )
        lines.append(f"  {b.base:<26} {b.count:>3} @ {b.home_module}{note}")
        for c in b.shared_constants[:2]:
            dist = ", ".join(f"{v}x{n}" for v, n in c.distribution)
            lines.append(f"  {'':<26}     {c.name} = {dist}  ({c.setters} of {b.count} set it)")

    lines.append("")
    lines.append("## what must change WITH it")
    # 🔴 Cross-module first and never truncated below the fold: a co-update set is
    # the one thing here that is actionable rather than descriptive, and a
    # half-wired feature is this project's most-replicated defect.
    co = [x for x in report.surfaces if x.kind == "consumer"][:top_n]
    if not co:
        lines.append("  (no symbol is consumed by 2-5 internal modules)")
    for x in co:
        lines.append(f"  {x.stem:<26} {x.module} -> {', '.join(x.surfaces)}")
    for g in report.export_gaps[:top_n]:
        lines.append(
            f"  🔴 {g.export_module}: {g.family} "
            f"{g.exported}/{g.defined}, missing {', '.join(g.missing)}"
        )
    for g in report.doc_gaps[:top_n]:
        lines.append(
            f"  🔴 {g.doc_root}/: {g.family} "
            f"{g.documented}/{g.defined}, missing {', '.join(g.missing)}"
        )

    lines.append("")
    codes = ", ".join(str(c) for c in report.gate_codes) or "none literal"
    lines.append(
        f"## does a new finding gate ({len(report.gates)} "
        f"finding-failure exit(s); code(s): {codes})"
    )
    lines += [_gate_row(g) for g in report.gates[:top_n]] or [
        "  (nothing here fails a build on a finding)"
    ]

    lines.append("")
    lines.append("## what this configuration cannot see")

    def indent(block: str) -> list[str]:
        # Per LINE, not per block: these renderers emit multi-line text and a
        # single strip() left every continuation flush against the margin,
        # which reads as a new section rather than the rest of a sentence.
        return ["  " + ln.lstrip("# ").rstrip() for ln in block.split("\n") if ln.strip()]

    if coverage is None:
        lines.append("  (no archy.yaml discovered, so no layer rule governs anything)")
    else:
        lines += indent(_coverage_to_text(coverage))
        hints = _pattern_hints_to_text(coverage)
        if hints:
            lines += indent(hints)
        if config is not None:
            handoff = _contracts_handoff_to_text(config, coverage, [])
            if handoff:
                lines += indent(handoff)
    if contracts is not None:
        lines.append("")
        lines.append(_contracts_outcome_to_text(contracts))
    return "\n".join(lines)


class ContractsOutcome(BaseModel):
    """The `--contracts` addendum, including the case where it produced no verdict.

    Carries `available` and `error` so a run that could not reach a verdict still
    says why on every surface. Returning `None` instead left `check --format
    json` with no `contracts` key at all and `brief --format json` with a bare
    `null`, which is indistinguishable from a bug in archy; the reason went only
    to stderr, which neither structured stream carries. The MCP surface has
    reported `available`/`error` from the start, so this is also what keeps the
    two telling the same story.
    """

    model_config = ConfigDict(frozen=True)

    available: bool = True
    error: str | None = None
    result: ContractsResult | None = None


def _unverified_forbid_rules(
    config: LayerConfig, coverage: LayerCoverage, violations: list[Violation]
) -> str | None:
    """`"3 forbid rules declared"`, or None when nothing is left unverified.

    The guard and the pluralisation are what the text handoff and the JSON
    reason have to agree on; their tails differ because one is a `#` block for a
    reader and the other a sentence for a parser. Keeping only the tails apart
    is what stops the two disagreeing about whether there is anything to say.
    """
    if not contracts_unverified(config, coverage, violations):
        return None
    n = len(config.forbid)
    return f"{n} forbid {'rule' if n == 1 else 'rules'} declared"


def _contracts_handoff_to_text(
    config: LayerConfig, coverage: LayerCoverage, violations: list[Violation]
) -> str:
    """Name the flag that settles a `forbid` rule this run could not verify.

    🔴 THIS EXISTS BECAUSE OF A MEASUREMENT, NOT A HUNCH. Over a five-hour agent
    run on this repository, `archy check` was invoked TWENTY times and
    `archy contracts` ZERO times -- while the model named `contracts` thirty-one
    times in its own reasoning and the task statement named it too. Awareness was
    never the deficit. The model navigates by re-running the command it is
    changing, so the only handoff that lands is one printed by that command.

    Deliberately points at `--contracts` on `check` rather than at the separate
    `contracts` command: a flag on the invocation already being typed is the
    smallest discovery step available, and the same run then answers the
    question instead of ending with a suggestion.

    Silent unless it would be useful: a `forbid` rule must exist (nothing to
    verify otherwise), the direct pass must have found nothing (a reader with a
    concrete violation already has somewhere to go), and coverage must be
    incomplete enough that a path could hide in the ungoverned part. On a fully
    layered repository with every edge governed it stays quiet.
    """
    stem = _unverified_forbid_rules(config, coverage, violations)
    if stem is None:
        return ""
    return (
        f"#   {stem}, but this check governs too little of the graph to "
        "verify them.\n"
        "#   `archy check --contracts` evaluates them transitively via import-linter, which "
        "sees paths a direct-edge check cannot."
    )


def _pattern_hints_to_text(coverage: LayerCoverage) -> str:
    """Name the cause of degenerate coverage, not just the number.

    Empty when nothing was detected, so the caller can skip the echo entirely.
    """
    lines = []
    for hint in coverage.exact_pattern_hints:
        count = len(hint.unlayered_descendants)
        shown = ", ".join(hint.unlayered_descendants[:3])
        if count > 3:
            shown += f", ... (+{count - 3} more)"
        noun = "module is" if count == 1 else "modules are"
        lines.append(
            f"#   layer {hint.layer!r} matches {hint.pattern} exactly; "
            f"{count} descendant {noun} unlayered ({shown}). "
            f'Did you mean "{hint.suggestion}"?'
        )
    return "\n".join(lines)


def _sdp_violations_to_json(violations: list[SdpViolation]) -> list[dict]:
    return [
        {
            "source": v.source,
            "target": v.target,
            "source_instability": v.source_instability,
            "target_instability": v.target_instability,
            "lines": list(v.lines),
        }
        for v in violations
    ]


def _sdp_violations_to_text(violations: list[SdpViolation], tolerance: float) -> str:
    if not violations:
        return f"# No SDP violations (tolerance: {tolerance})."
    lines = [f"# {len(violations)} SDP violation(s) (tolerance: {tolerance})"]
    for v in violations:
        gap = v.target_instability - v.source_instability
        lines.append(
            f"  {v.source} (I={v.source_instability:.2f}) -> "
            f"{v.target} (I={v.target_instability:.2f}, gap={gap:+.2f})  "
            f"{_format_lines(v.lines)}"
        )
    return "\n".join(lines)


def _cycles_to_json(cycles: list[Cycle]) -> list[dict]:
    return [
        {
            "modules": list(c.modules),
            "edges": [
                {"source": e.source, "target": e.target, "lines": list(e.lines)} for e in c.edges
            ],
        }
        for c in cycles
    ]


def _snapshot_to_text(snap: CapturedSnapshot) -> str:
    return (
        f"# score: {snap.score.overall:.3f}  "
        f"cycles: {len(snap.cycles)}  violations: {len(snap.violations)}"
    )


def _score_delta_lines(delta: ScoreDelta, header: str) -> list[str]:
    """One signed line per score axis, under ``header``, for `diff` and `simulate`.

    The axes come from ``ScoreDelta``'s own fields, in declaration order, so a
    new axis shows up in both renderers without editing either (#335).
    """
    return [header] + [
        f"  {name:11s} {getattr(delta, name):+.3f}" for name in ScoreDelta.model_fields
    ]


def _diff_to_text(result: DiffReport) -> str:
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
    cycles = result.cycles
    lines.append("")
    lines.append(f"# cycles: +{len(cycles.added)} added, -{len(cycles.resolved)} resolved")
    for c in cycles.added:
        lines.append(f"  + cycle: {', '.join(c.modules)}")
    for c in cycles.resolved:
        lines.append(f"  - cycle (resolved): {', '.join(c.modules)}")
    violations = result.violations
    lines.append("")
    lines.append(
        f"# violations: +{len(violations.added)} added, -{len(violations.resolved)} resolved"
    )
    for v in violations.added:
        lines.append(f"  + {v.source} -> {v.target}  ({v.rule.from_layer} -> {v.rule.to_layer})")
    for v in violations.resolved:
        rule = f"{v.rule.from_layer} -> {v.rule.to_layer}"
        lines.append(f"  - {v.source} -> {v.target}  resolved ({rule})")
    reach = result.required_violations
    # Only printed when there is something to say: a diff on a project with no
    # `required:` rules should not grow a permanent zero line.
    if reach.added or reach.resolved:
        lines.append("")
        lines.append(
            f"# required-reach: +{len(reach.added)} added, -{len(reach.resolved)} resolved"
        )
        for v in reach.added:
            lines.append(f"  + {v.detail}")
        for v in reach.resolved:
            if v.module is None:
                rule = f"{v.rule.describe_source()} must reach {v.rule.must_reach}"
                lines.append(f"  - rule can be checked again: {rule}")
            else:
                lines.append(f"  - {v.module} now reaches {v.rule.must_reach}")
    if result.strict is not None:
        lines.append("")
        verdict = "PASS" if result.strict.passed else "FAIL"
        lines.append(f"# strict: {verdict}, {result.strict.reason}")
    return "\n".join(lines)


def _summary_to_text(summary: DiffSummary) -> list[str]:
    lines = [f"# summary: {summary.headline}"]
    if summary.top_regressions:
        lines.append("")
        lines.append("## top regressions (risk-weighted):")
        for item in summary.top_regressions:
            lines.append(f"  risk={item.risk:.2f}  {item.description}")
            lines.append(f"      ? {item.prompt}")
    if summary.top_improvements:
        lines.append("")
        lines.append("## top improvements (risk-weighted):")
        for item in summary.top_improvements:
            lines.append(f"  risk={item.risk:.2f}  {item.description}")
    return lines


def _simulate_to_text(result: SimulateReport) -> str:
    a = result.applied
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

    if result.cycles.added:
        lines.append("")
        lines.append(f"# new cycles (+{len(result.cycles.added)}):")
        for c in result.cycles.added:
            lines.append(f"  + {', '.join(c.modules)}")
    if result.new_back_edges:
        lines.append("")
        lines.append(f"# new back-edges (+{len(result.new_back_edges)}):")
        for e in result.new_back_edges:
            lines.append(f"  + {e.source} -> {e.target}")
    if result.violations.added:
        lines.append("")
        lines.append(f"# new layer violations (+{len(result.violations.added)}):")
        for v in result.violations.added:
            lines.append(
                f"  + {v.source} -> {v.target}  ({v.rule.from_layer} -> {v.rule.to_layer})"
            )
    # Listed unconditionally, like the block above, NOT left to the ranked
    # summary. `simulate` has no --top-n and takes the default cap of 5, and a
    # whole-rule reach item carries risk 0.0 (it names no module to weight), so
    # it is the first thing evicted from the top-5 exactly when a simulation has
    # a lot going on. "Would removing this import break a required-reach rule?"
    # is a question simulate exists to answer; it must not depend on ranking.
    if result.required_violations.added:
        lines.append("")
        lines.append(f"# new required-reach violations (+{len(result.required_violations.added)}):")
        for v in result.required_violations.added:
            lines.append(f"  + {v.detail}")
    return "\n".join(lines)


def _contracts_to_dict(result: ContractsResult) -> dict:
    return {
        "kept": result.kept,
        "broken": result.broken,
        "module_count": result.module_count,
        "import_count": result.import_count,
        "all_kept": result.all_kept,
        # `all_kept` is true of a contract that matched no module, so a machine
        # reader needs `verified` to tell "evaluated and held" from "could not
        # have failed" (#435).
        "verified": result.verified,
        "unverifiable": result.unverifiable,
        "contracts": [
            {
                "name": c.name,
                "type": c.contract_type,
                "kept": c.kept,
                "matched_nothing": c.matched_nothing,
                "unmatched_expressions": list(c.unmatched_expressions),
                "metadata": c.metadata,
                "warnings": list(c.warnings),
            }
            for c in result.contracts
        ],
    }


def _contracts_outcome_to_dict(outcome: ContractsOutcome) -> dict:
    payload: dict = {"available": outcome.available, "error": outcome.error}
    if outcome.result is not None:
        payload.update(_contracts_to_dict(outcome.result))
    return payload


def _contracts_outcome_to_text(outcome: ContractsOutcome) -> str:
    if outcome.result is None:
        return f"# contracts: no verdict ({outcome.error})"
    return _contracts_to_text(outcome.result)


def _contracts_to_text(result: ContractsResult) -> str:
    summary = (
        f"# contracts: {result.kept} kept, {result.broken} broken "
        f"({result.module_count} modules, {result.import_count} imports)"
    )
    if result.unverifiable:
        summary += f", {result.unverifiable} unverifiable"
    lines = [summary]
    for c in result.contracts:
        # A contract that could not have failed gets its own marker. Rendering
        # it as `OK` is the whole of #435: it reads as protection that is not
        # there, and the exit code agrees with the marker.
        marker = "??" if c.matched_nothing else ("OK" if c.kept else "X ")
        lines.append(f"{marker} {c.name}  [{c.contract_type}]")
        if c.matched_nothing:
            lines.append(
                "    ! could not have failed: no module matched, so this "
                "contract holds whatever the code does"
            )
        for expression in c.unmatched_expressions:
            lines.append(f"    ! matched no module: {expression}")
        for w in c.warnings:
            lines.append(f"    ! {w}")
        if not c.kept:
            # `metadata` is `dict[str, object]` (import-linter's per-contract-
            # type shape); cast each level explicitly at the read sites rather
            # than typing the import-linter wire format.
            chains = cast(list[dict[str, object]], c.metadata.get("invalid_chains") or [])
            for chain in chains:
                upstream = chain.get("upstream_module", "?")
                downstream = chain.get("downstream_module", "?")
                lines.append(f"    {downstream} -> {upstream}")
                paths = cast(list[list[dict[str, object]]], chain.get("chains") or [])
                for path in paths:
                    # Multi-step paths are worth showing; single-step paths
                    # are already in the line above.
                    if len(path) <= 1:
                        continue
                    nodes = [str(path[0].get("importer", "?"))]
                    nodes.extend(str(step.get("imported", "?")) for step in path)
                    lines.append(f"      via {' -> '.join(nodes)}")
    return "\n".join(lines)


def _affected_to_dict(result: Affected) -> dict:
    return {
        "changed": list(result.changed),
        "unresolved": list(result.unresolved),
        "deleted": list(result.deleted),
        "deleted_reason": result.deleted_reason,
        "impacted_modules": list(result.impacted_modules),
        "impacted_tests": list(result.impacted_tests),
        "depth": result.depth,
        "test_filter": result.test_filter,
        "empty_caveat": result.empty_caveat,
    }


def _deleted_lines(deleted: Sequence[str]) -> list[str]:
    if not deleted:
        return []
    head, _, tail = DELETED_OR_RENAMED_REASON.partition("; ")
    return [
        f"# {len(deleted)} {head} .py file(s); {tail}:",
        *(f"  ? {path}" for path in deleted),
    ]


def _unresolved_and_changed_lines(
    unresolved: Sequence[str],
    changed: Sequence[str],
    did_you_mean: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """The `did not resolve` block and the `Changed:` list shared by `impact` and `affected`."""
    suggestions = did_you_mean or {}
    lines: list[str] = []
    if unresolved:
        lines.append(
            f"# {len(unresolved)} file(s) did not resolve to a module "
            "(non-Python, excluded, or outside any package):"
        )
        lines.extend(
            f"  ? {f}"
            + (f"  (did you mean {', '.join(suggestions[f])}?)" if f in suggestions else "")
            for f in unresolved
        )
    if changed:
        lines.append("")
        lines.append("Changed:")
        lines.extend(f"  - {q}" for q in changed)
    return lines


def _affected_to_text(result: Affected) -> str:
    lines = [
        f"# depth={result.depth}, "
        f"{len(result.impacted_tests)} test(s), "
        f"{len(result.impacted_modules)} module(s) "
        f"affected by {len(result.changed)} changed module(s)"
    ]
    if result.test_filter:
        lines.append(f"# test filter: {result.test_filter}")
    lines.extend(_deleted_lines(result.deleted))
    lines.extend(_unresolved_and_changed_lines(result.unresolved, result.changed))
    if result.impacted_tests:
        lines.append("")
        lines.append("Tests to run:")
        for q in result.impacted_tests:
            lines.append(f"  - {q}")
    if result.impacted_modules:
        lines.append("")
        lines.append("Other modules touched:")
        for q in result.impacted_modules:
            lines.append(f"  - {q}")
    if result.empty_caveat:
        lines.append("")
        lines.append(f"# {result.empty_caveat}")
    return "\n".join(lines)


def _impact_to_dict(result: Impact) -> dict:
    return {
        "changed": list(result.changed),
        "unresolved": list(result.unresolved),
        "unresolved_hints": [
            {"ref": h.ref, "did_you_mean": list(h.did_you_mean)} for h in result.unresolved_hints
        ],
        "nothing_resolved": result.nothing_resolved,
        "impacted": list(result.impacted),
        "chains": [
            {
                "impacted": c.impacted,
                "changed": c.changed,
                "via": list(c.via),
                "hops": [
                    {"source": h.source, "target": h.target, "lines": list(h.lines)} for h in c.hops
                ],
            }
            for c in result.chains
        ],
        "chains_omitted": result.chains_omitted,
        "empty_caveat": result.empty_caveat,
    }


def _hotspots_note(rows: list[Hotspot], *, since: str | None, empty_window: bool) -> str | None:
    """Explain an empty hotspot list; name an empty --since window explicitly."""
    if rows:
        return None
    if empty_window and since:
        return (
            f"No commits in the --since window ({since}). "
            "Widen the window or omit --since to use full history."
        )
    return None


def _hotspots_to_dict(
    rows: list[Hotspot],
    *,
    top_n: int,
    since: str | None,
    empty_window: bool = False,
) -> dict:
    return {
        "since": since,
        "total": len(rows),
        "shown": min(top_n, len(rows)),
        "hotspots": [r.model_dump() for r in rows[:top_n]],
        "note": _hotspots_note(rows, since=since, empty_window=empty_window),
    }


def _count_header(count: int, noun: str, shown: int, since: str | None) -> str:
    """`# N <noun>; showing top M`, plus ` since <ref>` when the history was bounded."""
    header = f"# {count} {noun}; showing top {shown}"
    return f"{header} since {since}" if since else header


def _hotspots_to_text(
    rows: list[Hotspot],
    *,
    top_n: int,
    since: str | None,
    empty_window: bool = False,
) -> str:
    if not rows:
        note = _hotspots_note(rows, since=since, empty_window=empty_window)
        if note:
            return f"# {note}"
        suffix = f" since {since}" if since else ""
        return f"# No hotspots found{suffix} (need both cc_sum > 0 and churn > 0)."
    shown = rows[:top_n]
    lines = [
        _count_header(len(rows), "hotspot(s)", len(shown), since),
        "",
        "  score  churn  cc_sum  module",
    ]
    for r in shown:
        lines.append(f"  {r.score:>5}  {r.churn:>5}  {r.cc_sum:>6}  {r.module}")
    return "\n".join(lines)


def _coupling_note(
    rows: list[CouplingPair],
    *,
    min_support: int,
    since: str | None,
    empty_window: bool = False,
) -> str | None:
    """Machine-readable explanation for the empty case (mirrors _refactor_note).

    An empty list is a real answer: either the history is too short to clear
    `--min-support`, or every co-changing pair already has a structural edge (so
    the graph already captures it). Say which lever to relax rather than imply
    the project has no coupling. An empty --since window is named separately so
    it is not mistaken for "no hidden coupling"."""
    if rows:
        return None
    if empty_window and since:
        return (
            f"No commits in the --since window ({since}). "
            "Widen the window or omit --since to use full history."
        )
    suffix = f" since {since}" if since else ""
    return (
        f"No structurally-unconnected module pairs co-change above the thresholds{suffix}. "
        "Lower --min-support / --min-confidence to widen, or the graph already "
        "captures the coupling as import/call edges."
    )


def _coupling_to_dict(
    rows: list[CouplingPair],
    *,
    top_n: int,
    since: str | None,
    min_support: int,
    empty_window: bool = False,
) -> dict:
    return {
        "since": since,
        "min_support": min_support,
        "total": len(rows),
        "shown": min(top_n, len(rows)),
        "pairs": [r.model_dump() for r in rows[:top_n]],
        "note": _coupling_note(
            rows, min_support=min_support, since=since, empty_window=empty_window
        ),
    }


def _coupling_to_text(
    rows: list[CouplingPair],
    *,
    top_n: int,
    since: str | None,
    min_support: int,
    empty_window: bool = False,
) -> str:
    if not rows:
        note = _coupling_note(rows, min_support=min_support, since=since, empty_window=empty_window)
        return f"# {note}"
    shown = rows[:top_n]
    lines = [
        _count_header(len(rows), "hidden-coupling pair(s)", len(shown), since),
        "",
        "  conf  commits  modules (no import/call edge)",
    ]
    for r in shown:
        lines.append(f"  {r.confidence:>4.2f}  {r.support:>7}  {r.module_a} <-> {r.module_b}")
    return "\n".join(lines)


def _duplicates_note(
    dups: list[DuplicateGroup], *, min_nodes: int, variant_count: int = 0
) -> str | None:
    """Explain an empty primary tier (mirrors the CLI/MCP note pattern).

    Speaks to the primary "likely duplicate" tier only; if the semantic de-noiser
    demoted everything to the variant tier, say so rather than imply nothing was
    found at all."""
    if dups:
        return None
    note = (
        f"No likely-duplicate function bodies of >= {min_nodes} normalized nodes found; "
        "lower --min-nodes to widen the search."
    )
    if variant_count:
        note += (
            f" ({variant_count} likely-intentional variant(s) were found but demoted: "
            "same-class siblings, boilerplate, test/vendored copies, or independent "
            "copies that never co-change.)"
        )
    return note


def _duplicate_group_lines(group: DuplicateGroup, *, with_reason: bool) -> list[str]:
    """Render one cluster: a header row for the first member, then one row per other."""
    reason = f"  {(group.variant_reason or ''):<10}" if with_reason else ""
    pad = f"  {'':<10}" if with_reason else ""
    first, *rest = group.members
    lines = [
        f"  {group.redundancy:>6}  {group.size:>4}  {group.member_count:>5}{reason}  "
        f"{first.module}:{first.line} {first.qualified_name}"
    ]
    lines.extend(
        f"  {'':>6}  {'':>4}  {'':>5}{pad}  {m.module}:{m.line} {m.qualified_name}" for m in rest
    )
    return lines


def _duplicates_to_dict(rows: list[DuplicateGroup], *, top_n: int, min_nodes: int) -> dict:
    dups = [g for g in rows if g.category == "duplicate"]
    variants = [g for g in rows if g.category == "variant"]
    near = [g for g in rows if g.category == "near_miss"]
    return {
        "min_nodes": min_nodes,
        "total": len(dups),
        "exact_total": sum(1 for g in dups if g.exact),
        "variant_total": len(variants),
        "near_total": len(near),
        "shown": min(top_n, len(dups)),
        "duplicated_functions": sum(g.member_count for g in dups),
        "duplicates": [g.model_dump() for g in dups[:top_n]],
        "variants": [g.model_dump() for g in variants[:top_n]],
        "near_miss": [g.model_dump() for g in near[:top_n]],
        "note": _duplicates_note(dups, min_nodes=min_nodes, variant_count=len(variants)),
    }


def _section_preamble(out: list[str], header: str, columns: str) -> None:
    """Blank separator (unless first), header, blank line, then the column row."""
    if out:
        out.append("")
    out.append(header)
    out.append("")
    out.append(columns)


def _near_miss_section(out: list[str], header: str, groups: list[DuplicateGroup]) -> None:
    """Render the Type-3 near-miss tier: a `sim` column instead of redund/size."""
    _section_preamble(out, header, "   sim  count  members")
    for g in groups:
        first, *rest = g.members
        out.append(
            f"  {g.similarity or 0:>4.2f}  {g.member_count:>5}  {first.module}:{first.line} "
            f"{first.qualified_name}"
        )
        out.extend(f"  {'':>4}  {'':>5}  {m.module}:{m.line} {m.qualified_name}" for m in rest)


def _duplicates_section(
    out: list[str], header: str, groups: list[DuplicateGroup], *, with_reason: bool
) -> None:
    _section_preamble(
        out, header, f"  redund  size  count  {'reason      ' if with_reason else ''}members"
    )
    for g in groups:
        out.extend(_duplicate_group_lines(g, with_reason=with_reason))


def _duplicates_to_text(rows: list[DuplicateGroup], *, top_n: int, min_nodes: int) -> str:
    exact = [g for g in rows if g.category == "duplicate" and g.exact]
    near = [g for g in rows if g.category == "duplicate" and not g.exact]
    variants = [g for g in rows if g.category == "variant"]
    out: list[str] = []
    if exact:
        _duplicates_section(
            out,
            f"# {len(exact)} exact duplicate(s) (byte-identical; high confidence); "
            f"showing top {min(top_n, len(exact))} (min-nodes {min_nodes})",
            exact[:top_n],
            with_reason=False,
        )
    if near:
        _duplicates_section(
            out,
            f"# {len(near)} likely duplicate(s) (same shape, differ by names/values); "
            f"showing top {min(top_n, len(near))}",
            near[:top_n],
            with_reason=False,
        )
    if not exact and not near:
        out.append(f"# {_duplicates_note([], min_nodes=min_nodes, variant_count=len(variants))}")
    if variants:
        _duplicates_section(
            out,
            f"# {len(variants)} likely-intentional variant(s) (same-class / boilerplate / "
            f"test / vendored / independent; showing top {min(top_n, len(variants))})",
            variants[:top_n],
            with_reason=True,
        )
    near = [g for g in rows if g.category == "near_miss"]
    if near:
        _near_miss_section(
            out,
            f"# {len(near)} possible near-miss (Type-3, gapped) cluster(s) (LOWER confidence; "
            f"showing top {min(top_n, len(near))})",
            near[:top_n],
        )
    return "\n".join(out)


def _refactor_note(
    rows: list[RefactorPriority],
    *,
    min_risk: float,
    git_available: bool,
    no_commits: bool = False,
    empty_window: bool = False,
    since: str | None = None,
) -> str | None:
    """Machine-readable explanation for the empty / git-absent cases.

    Mirrors the `note` the MCP tool returns so the CLI JSON surface carries
    the same "nothing to prioritize" / "structural-only" signal rather than
    leaving the caller to infer it from an empty list."""
    if not rows:
        if empty_window and since and git_available:
            return (
                f"No commits in the --since window ({since}). "
                "Widen the window or omit --since to use full history."
            )
        if git_available:
            return (
                "No refactoring priorities surfaced: no file is both complex "
                "and frequently changed, and no module is central and fragile "
                f"above min_risk={min_risk}. Lower min_risk to widen the "
                "structural lens."
            )
        if no_commits:
            return (
                "No commit history yet, so the CC x churn lens was skipped and "
                f"only the structural lens ran; no module is central and fragile "
                f"above min_risk={min_risk}. Lower min_risk to widen the "
                "structural lens."
            )
        return (
            "Not a git repository, so the CC x churn lens was skipped and only "
            f"the structural lens ran; no module is central and fragile above "
            f"min_risk={min_risk}. Lower min_risk to widen the structural lens."
        )
    if not git_available:
        if no_commits:
            return (
                "No commit history yet; the CC x churn lens was skipped and "
                "this ranking is structural-only (edit-risk)."
            )
        return (
            "Not a git repository; the CC x churn lens was skipped and this "
            "ranking is structural-only (edit-risk)."
        )
    return None


def _refactor_to_dict(
    rows: list[RefactorPriority],
    *,
    top_n: int,
    since: str | None,
    min_risk: float,
    git_available: bool,
    no_commits: bool = False,
    empty_window: bool = False,
) -> dict:
    return {
        "since": since,
        "min_risk": min_risk,
        "git_available": git_available,
        "total": len(rows),
        "shown": min(top_n, len(rows)),
        "priorities": [r.model_dump() for r in rows[:top_n]],
        "note": _refactor_note(
            rows,
            min_risk=min_risk,
            git_available=git_available,
            no_commits=no_commits,
            empty_window=empty_window,
            since=since,
        ),
    }


def _refactor_to_text(
    rows: list[RefactorPriority],
    *,
    top_n: int,
    min_risk: float,
    git_available: bool,
    no_commits: bool = False,
    empty_window: bool = False,
    since: str | None = None,
) -> str:
    if not rows:
        if empty_window and since and git_available:
            return (
                f"# No commits in the --since window ({since}). "
                "Widen the window or omit --since to use full history."
            )
        if git_available:
            return (
                "# Nothing to refactor: no file is both complex and churned, and "
                f"no module is central+fragile above min_risk={min_risk}."
            )
        if no_commits:
            return (
                "# No commits yet, so only the structural lens ran; no module is "
                f"central+fragile above min_risk={min_risk}. Nothing surfaced."
            )
        return (
            "# Not a git repo, so only the structural lens ran; no module is "
            f"central+fragile above min_risk={min_risk}. Nothing surfaced."
        )
    shown = rows[:top_n]
    header = f"# {len(rows)} refactor candidate(s); showing top {len(shown)}"
    if not git_available:
        if no_commits:
            header += " (structural-only: no commits yet)"
        else:
            header += " (structural-only: not a git repo)"
    lines = [header, ""]
    for i, r in enumerate(shown, 1):
        lenses = "+".join(r.lenses)
        lines.append(f"{i}. {r.module}  [{lenses}]  priority={r.priority:.3f}")
        lines.append(f"   {r.rationale}")
    return "\n".join(lines)


def _impact_to_text(result: Impact) -> str:
    if result.nothing_resolved:
        # "0 module(s) depend on 0 changed module(s)" reads as a clean answer; no
        # file was looked up, so there is no answer.
        headline = (
            f"# nothing analysed: none of {len(result.unresolved)} file(s) resolved to a module, "
            "so no blast radius was computed"
        )
    else:
        headline = (
            f"# {len(result.impacted)} module(s) depend on {len(result.changed)} changed module(s)"
        )
    lines = [headline]
    lines.extend(
        _unresolved_and_changed_lines(
            result.unresolved,
            result.changed,
            {h.ref: h.did_you_mean for h in result.unresolved_hints},
        )
    )
    if result.impacted:
        lines.append("")
        lines.append("Impacted (transitive dependents):")
        for q in result.impacted:
            lines.append(f"  - {q}")
    if result.empty_caveat:
        lines.append("")
        lines.append(f"# {result.empty_caveat}")
    if result.chains:
        lines.append("")
        lines.append("Why (shortest import path to a changed module):")
        for c in result.chains:
            lines.append(f"  - {' -> '.join(c.via)}")
            for h in c.hops:
                lines.append(f"      {h.source} imports {h.target}  {_format_lines(h.lines)}")
        if result.chains_omitted:
            lines.append(
                f"  # {result.chains_omitted} more impacted module(s) not shown "
                "(raise --max-chains)."
            )
    return "\n".join(lines)


def _cycles_to_text(cycles: list[Cycle], min_size: int) -> str:
    if not cycles:
        return f"# No cycles found (min_size={min_size})."
    lines = [f"# {len(cycles)} cycle(s) found"]
    for c in cycles:
        lines.append(f"\nCycle of {len(c.modules)} module(s):")
        for m in c.modules:
            lines.append(f"  - {m}")
        lines.append("Edges:")
        for e in c.edges:
            lines.append(f"  {e.source} -> {e.target}  {_format_lines(e.lines)}")
    return "\n".join(lines)


def _graph_to_text(g: nx.DiGraph) -> str:
    internal = sorted(n for n, d in g.nodes(data=True) if not d.get("external"))
    lines = [f"# {len(internal)} internal module(s), {g.number_of_edges()} import edge(s)"]
    for n in internal:
        lines.append(f"{n}")
        for t in sorted(g.successors(n)):
            marker = "ext" if g.nodes[t].get("external") else "int"
            lines.append(f"  -> [{marker}] {t}")
    return "\n".join(lines)


def _conventions_to_dict(report: ConventionsReport, *, top_n: int) -> dict:
    """Hand-rolled payload, per this module's convention: the CLI decides what
    to truncate, so the JSON says how much it dropped rather than silently
    handing back a short list that looks complete. Gates and errors are never
    truncated -- the whole point of the section is the complete inventory."""
    payload = report.model_dump()
    payload["top_n"] = top_n
    payload["naming"] = [h.model_dump() for h in report.naming[:top_n]]
    payload["surfaces"] = [s.model_dump() for s in report.surfaces[:top_n]]
    payload["gates"] = [g.model_dump() for g in report.gates]
    payload["errors"] = [g.model_dump() for g in report.errors]
    payload["bases"] = [b.model_dump() for b in report.bases[:top_n]]
    payload["registries"] = [
        {**r.model_dump(), "literal_names": list(r.literal_names[:6])}
        for r in report.registries[:top_n]
    ]
    # Export gaps are never truncated: the section exists to be complete, for
    # the same reason gates are. A truncated list of "things you forgot to
    # wire up" is worse than none, because it reads as a finished checklist.
    payload["export_gaps"] = [g.model_dump() for g in report.export_gaps]
    payload["doc_gaps"] = [g.model_dump() for g in report.doc_gaps]
    payload["totals"] = {
        "naming": len(report.naming),
        "surfaces": len(report.surfaces),
        "gates": len(report.gates),
        "errors": len(report.errors),
        "bases": len(report.bases),
        "registries": len(report.registries),
        "export_gaps": len(report.export_gaps),
        "doc_gaps": len(report.doc_gaps),
    }
    return payload


def _gate_row(gate: Gate) -> str:
    code = "?" if gate.code is None else str(gate.code)
    where = f"{gate.module}:{gate.function}"
    suffix = "  [command]" if gate.is_command else ""
    call = f"{gate.kind}({code})"
    return f"  {where:<34} {call:<18} {gate.control}{suffix}"


def _module_view_to_text(view: ModuleView) -> str:
    """Terse and COMPLETE. Every list is the whole list; see ModuleView."""
    lines = [f"# {view.module}", f"  status        {view.status}"]
    # Classes and functions separately, each with a count. Completeness is the
    # point of this view, so nothing is dropped -- but a test module defines a
    # hundred `test_*` functions, and one undifferentiated wall is the same
    # delivery failure in a new place. A count lets a reader see the shape
    # before deciding to read the list.
    for label, items in (
        ("classes", view.classes),
        ("functions", view.functions),
        ("imports", view.imports_internal),
        ("imported by", view.imported_by),
        ("exports", view.exports if view.exports is not None else ()),
        ("name families", view.suffix_families),
    ):
        if label == "exports" and view.exports is None:
            lines.append(f"  {label:<13} (no __all__ and no explicit re-exports)")
            continue
        # "(none)" is a real answer here and must not be silence: it is what
        # makes "does this import that" answerable in the negative.
        count = f" ({len(items)})" if len(items) > 6 else ""
        lines.append(f"  {label + count:<13} {', '.join(items) if items else '(none)'}")
    if view.gates:
        lines.append("  exits")
        lines += [_gate_row(g) for g in view.gates]
    if view.census_complete:
        lines.append("  🔴 every list above is COMPLETE for this module; absence is an answer")
    else:
        skipped = view.unparsed_modules
        detail = (
            "; ".join(f"{u.module} ({u.path}): {u.reason} [Python {u.python}]" for u in skipped)
            or "(unparsed modules not listed)"
        )
        lines.append(
            "  ⚠ lists may be INCOMPLETE: "
            f"{len(skipped)} module(s) could not be parsed and were skipped: "
            f"{detail}"
        )
    return "\n".join(lines)


def _shown(total: int, top_n: int) -> str:
    """`(150; showing 12, --top 150 for the rest)`, or just `(12)` when nothing is hidden.

    🔴 A reader who is told "showing 12" and not told how to see the other 138
    cannot tell a missing fact from an unranked one, so absence from the list
    proves nothing to them. Measured: 24 pieces of real agent reasoning, all
    chosen because this command could in principle answer them, scored 0 -- and
    both blind readers named truncation, not the analysis, as the reason. The
    number was computed and then withheld. Printing the way to ask for it costs
    one clause.
    """
    if total <= top_n:
        return f"({total})"
    return f"({total}; showing {top_n}, --top {total} for the rest)"


def _conventions_to_text(report: ConventionsReport, *, top_n: int) -> str:
    """Terse on purpose: an agent pays per token to read this."""
    m = report.models
    lines = [
        f"# {report.root}: {report.modules_scanned} module(s) parsed"
        + (
            f", {report.modules_unparsed} unparsed ("
            + ", ".join(u.module for u in report.unparsed_modules)
            + ")"
            if report.modules_unparsed
            else ""
        )
        + (f", {report.docs_scanned} doc file(s)" if report.docs_scanned else ""),
        *(
            [
                "# set aside: "
                + ", ".join(
                    part
                    for part in (
                        f"{report.partition.tests} test" if report.partition.tests else "",
                        f"{report.partition.nonsource} non-source"
                        if report.partition.nonsource
                        else "",
                        f"{report.partition.shadowed} in {', '.join(report.partition.shadow_roots)}"
                        " (duplicates its parent)"
                        if report.partition.shadowed
                        else "",
                    )
                    if part
                )
                # 🔴 Say how to get them back. Setting tests aside is right when a
                # vendored copy or a fixture pile would otherwise win every count,
                # and wrong when the question IS about tests -- "where do helpers
                # live in this test file, and what are they called" is a real
                # convention this command silently cannot answer. One of the 24
                # scored derivations asked exactly that.
                + ("  (--include-tests to census them)" if report.partition.tests else "")
            ]
            if report.partition
            and (report.partition.tests or report.partition.shadowed or report.partition.nonsource)
            else []
        ),
        "",
        f"## naming - by home module {_shown(len(report.naming), top_n)}",
    ]
    if not report.naming:
        lines.append("  (none: no class-name suffix is shared by enough definitions)")
    for home in report.naming[:top_n]:
        families = " ".join(f"*{f.suffix}({f.count})" for f in home.families)
        lines.append(f"  {home.module:<34} {home.total:>3}  {families}")
        top = home.families[0]
        lines.append(f"  {'':<34}      e.g. {', '.join(top.examples)}")

    # Kinds before names. A base family answers "what is it a kind of", which is
    # the question a suffix census silently fails when the two disagree -- and
    # they disagree in most projects, so this section leads.
    lines += [
        "",
        f"## kinds - classes grouped by what they derive from {_shown(len(report.bases), top_n)}",
    ]
    if not report.bases:
        lines.append("  (none: no base defined in this project has enough subclasses)")
    for b in report.bases[:top_n]:
        agree = f"{b.suffix_agreement:.0%}"
        note = (
            ""
            if b.suffix_agreement >= 0.8
            else f"  <- name is NOT the rule ({agree} share a suffix)"
        )
        lines.append(f"  {b.base:<28} {b.count:>3} @ {b.home_module}{note}")
        lines.append(f"  {'':<28}     e.g. {', '.join(b.members[:5])}")
        for c in b.shared_constants[:3]:
            dist = ", ".join(f"{v}x{n}" for v, n in c.distribution)
            lines.append(f"  {'':<28}     {c.name} = {dist}  ({c.setters} of {b.count} set it)")

    lines += [
        "",
        "## registries - values declared by repeated construction "
        + _shown(len(report.registries), top_n),
    ]
    if not report.registries:
        lines.append("  (none)")
    for r in report.registries[:top_n]:
        lines.append(f"  {r.constructor:<28} {r.count:>3} @ {r.home_module} ({r.home_count})")
        if r.literal_names:
            shown = ", ".join(r.literal_names[:6])
            more = f" (+{len(r.literal_names) - 6})" if len(r.literal_names) > 6 else ""
            lines.append(f"  {'':<28}     names: {shown}{more}")
        for c in r.keyword_defaults[:3]:
            dist = ", ".join(f"{v}x{n}" for v, n in c.distribution)
            # "13 of 80 pass it" is the fact; a bare distribution reads as if the
            # whole family were declared that way, which inverts the meaning of
            # an argument that is usually left at its default.
            lines.append(f"  {'':<28}     {c.name} = {dist}  ({c.setters} of {r.count} pass it)")

    if report.export_gaps:
        lines += [
            "",
            f"## export gaps ({len(report.export_gaps)}) - defined but not re-exported "
            "beside their siblings",
        ]
        for g in report.export_gaps:
            lines.append(
                f"  {g.export_module:<28} {g.family}: {g.exported}/{g.defined}"
                f"  missing {', '.join(g.missing)}"
            )

    if report.doc_gaps:
        lines += [
            "",
            f"## doc gaps ({len(report.doc_gaps)}) - public members their own docs do not name",
        ]
        for g in report.doc_gaps:
            lines.append(
                f"  {g.doc_root + '/':<28} {g.family}: {g.documented}/{g.defined}"
                f"  missing {', '.join(g.missing)}"
            )

    lines += [
        "",
        f"## surfaces {_shown(len(report.surfaces), top_n)} - wire all of these together",
    ]
    if not report.surfaces:
        lines.append("  (none)")
    for s in report.surfaces[:top_n]:
        where = s.module or "across modules"
        lines.append(
            f"  {s.kind:<8} {s.stem:<28} {s.surface_count} @ {where}: {', '.join(s.surfaces)}"
        )

    # The question this section exists for is "should MY NEW FINDING gate?", so
    # a finding-failure exit and a bad-input exit must not share a count.
    optional = [g for g in report.gates if g.optional]
    codes = ", ".join(str(c) for c in report.gate_codes) or "none literal"
    lines += [
        "",
        f"## gates ({len(report.gates)} finding-failure exit(s), "
        f"{len(optional)} caller-controlled; exit code(s): {codes})",
    ]
    if not report.gates:
        lines.append("  (none: no finding in this project fails the build)")
    lines += [_gate_row(g) for g in report.gates]

    kinds = ", ".join(
        f"{kind}={sum(1 for g in report.errors if g.kind == kind)}"
        for kind in sorted({g.kind for g in report.errors})
    )
    where = ", ".join(sorted({g.module for g in report.errors}))
    lines += [
        "",
        f"## errors ({len(report.errors)} user-error exit(s) - bad input, not a finding)",
    ]
    lines.append(f"  {kinds} @ {where}" if report.errors else "  (none)")

    bases = ", ".join(f"{b}={n}" for b, n in m.base_counts[:5])
    lines += [
        "",
        "## models",
        f"  {m.value_classes} value class(es) of {m.total_classes} class(es); "
        f"{m.frozen_classes} frozen ({m.frozen_ratio:.0%})",
        f"  dominant base: {m.dominant_base or '(none)'}" + (f"  bases: {bases}" if bases else ""),
        f"  config: {', '.join(f'{k}={n}' for k, n in m.config_flags) or '(none)'}",
        f"  collection fields: {m.tuple_fields} tuple / {m.list_fields} list "
        f"({m.tuple_ratio:.0%} tuple)",
    ]
    return "\n".join(lines)
