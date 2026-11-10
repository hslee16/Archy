"""Renderers for `check`, `contracts` and `brief`: violations, coverage, contracts, the brief.

archy:owns        ContractsOutcome
archy:mirrored-by ContractsOutcome -> archy.cli_check, archy.cli_render,
                  _brief_to_text -> archy.cli_conventions, archy.cli_render,
                  _contracts_handoff_to_text -> archy.cli_check, archy.cli_render,
                  _contracts_outcome_to_dict -> archy.cli_check, archy.cli_conventions,
                  archy.cli_render, _contracts_outcome_to_text -> archy.cli_check,
                  archy.cli_render, _contracts_to_dict -> archy.cli_check,
                  archy.cli_render, _contracts_to_text -> archy.cli_check,
                  archy.cli_render, _coverage_to_json -> archy.cli_check,
                  archy.cli_conventions, archy.cli_render,
                  _coverage_to_text -> archy.cli_check, archy.cli_render,
                  _pattern_hints_to_text -> archy.cli_check, archy.cli_render,
                  _presence_to_text -> archy.cli_check, archy.cli_render,
                  _reach_violations_to_json -> archy.cli_check, archy.cli_render,
                  _reach_violations_to_text -> archy.cli_check, archy.cli_render,
                  _sdp_violations_to_json -> archy.cli_check, archy.cli_render,
                  _sdp_violations_to_text -> archy.cli_check, archy.cli_render,
                  _unverified_forbid_rules -> archy.cli_check, archy.cli_render,
                  _violations_to_json -> archy.cli_check, archy.cli_render,
                  _violations_to_text -> archy.cli_check, archy.cli_render
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict

from archy.cli_render_common import _format_lines, _gate_row, _indent_block, _location
from archy.contracts import (
    ContractsResult,
)
from archy.conventions import (
    ConventionsReport,
)
from archy.layers import (
    LayerConfig,
    LayerCoverage,
    ReachViolation,
    RequiredRule,
    SdpViolation,
    Violation,
    contracts_unverified,
)


def _violations_to_json(violations: list[Violation]) -> list[dict]:
    return [
        {
            "rule": {"from": v.rule.from_layer, "to": v.rule.to_layer},
            "source": v.source,
            "target": v.target,
            "lines": list(v.lines),
            "path": v.path,
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
        lines.append(
            f"  {_location(v.path, v.lines)}{v.source} -> {v.target}  {_format_lines(v.lines)}"
        )
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
        "external_edges_governed": coverage.external_edges_governed,
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
    if coverage.external_edges_governed:
        line += (
            f"; {coverage.external_edges_governed} internal-to-external edge(s) governed "
            "(counted apart from the internal ratio)"
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


def _brief_to_text(
    report, coverage, config, contracts, *, top_n: int, coverage_error: str | None = None
) -> str:
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
    lines = [
        *_brief_headline(report),
        "",
        *_brief_kind_lines(report, top_n),
        "",
        *_brief_cochange_lines(report, top_n),
        "",
        *_brief_gate_lines(report, top_n),
        "",
        *_brief_blind_spot_lines(coverage, config, coverage_error=coverage_error),
    ]
    if contracts is not None:
        lines.append("")
        lines.append(_contracts_outcome_to_text(contracts))
    return "\n".join(lines)


def _brief_headline(report: ConventionsReport) -> list[str]:
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
    return lines


def _brief_kind_lines(report: ConventionsReport, top_n: int) -> list[str]:
    lines = ["## what kind of thing am I adding, and where does it go"]
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
    return lines


def _brief_cochange_lines(report: ConventionsReport, top_n: int) -> list[str]:
    lines = ["## what must change WITH it"]
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
    return lines


def _brief_gate_lines(report: ConventionsReport, top_n: int) -> list[str]:
    codes = ", ".join(str(c) for c in report.gate_codes) or "none literal"
    header = (
        f"## does a new finding gate ({len(report.gates)} "
        f"finding-failure exit(s); code(s): {codes})"
    )
    rows = [_gate_row(g) for g in report.gates[:top_n]]
    return [header, *(rows or ["  (nothing here fails a build on a finding)"])]


def _brief_blind_spot_lines(coverage, config, *, coverage_error: str | None = None) -> list[str]:
    lines = ["## what this configuration cannot see"]
    if coverage_error is not None:
        lines.append(f"  archy.yaml found but invalid: {coverage_error}")
        return lines
    if coverage is None:
        lines.append("  (no archy.yaml discovered, so no layer rule governs anything)")
        return lines
    lines += _indent_block(_coverage_to_text(coverage))
    hints = _pattern_hints_to_text(coverage)
    if hints:
        lines += _indent_block(hints)
    if config is not None:
        handoff = _contracts_handoff_to_text(config, coverage, [])
        if handoff:
            lines += _indent_block(handoff)
    return lines


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
            "path": v.path,
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
            f"  {_location(v.path, v.lines)}{v.source} (I={v.source_instability:.2f}) -> "
            f"{v.target} (I={v.target_instability:.2f}, gap={gap:+.2f})  "
            f"{_format_lines(v.lines)}"
        )
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
        # Directories the engine does not read; a pass says nothing about them.
        "unscanned_dirs": list(result.unscanned_dirs),
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
    if result.unscanned_dirs:
        n = len(result.unscanned_dirs)
        noun, pronoun = ("directory", "it") if n == 1 else ("directories", "them")
        lines.append(
            f"# {n} {noun} not visible to the contracts engine (no __init__.py), so a pass "
            f"says nothing about the modules in {pronoun}: " + ", ".join(result.unscanned_dirs)
        )
    for c in result.contracts:
        lines.extend(_contract_lines(c))
    return "\n".join(lines)


def _contract_lines(c) -> list[str]:
    # A contract that could not have failed gets its own marker. Rendering
    # it as `OK` is the whole of #435: it reads as protection that is not
    # there, and the exit code agrees with the marker.
    marker = "??" if c.matched_nothing else ("OK" if c.kept else "X ")
    lines = [f"{marker} {c.name}  [{c.contract_type}]"]
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
        lines.extend(_invalid_chain_lines(c))
    return lines


def _invalid_chain_lines(c) -> list[str]:
    # `metadata` is `dict[str, object]` (import-linter's per-contract-
    # type shape); cast each level explicitly at the read sites rather
    # than typing the import-linter wire format.
    lines: list[str] = []
    chains = cast(list[dict[str, object]], c.metadata.get("invalid_chains") or [])
    for chain in chains:
        upstream = chain.get("upstream_module", "?")
        downstream = chain.get("downstream_module", "?")
        lines.append(f"    {downstream} -> {upstream}")
        paths = cast(list[list[dict[str, object]]], chain.get("chains") or [])
        # Multi-step paths are worth showing; single-step paths are already in
        # the line above.
        for path in (p for p in paths if len(p) > 1):
            nodes = [str(path[0].get("importer", "?"))]
            nodes.extend(str(step.get("imported", "?")) for step in path)
            lines.append(f"      via {' -> '.join(nodes)}")
    return lines
