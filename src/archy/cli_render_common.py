"""Text and JSON helpers several command renderers share: locations, gates, deltas, summaries.

archy:mirrored-by _format_lines -> archy.cli_render_check, archy.cli_render_graph,
                  archy.cli_render_history, _gate_row -> archy.cli_render_check,
                  archy.cli_render_conventions, _gate_to_dict -> archy.cli_history,
                  archy.cli_render, _gate_to_text -> archy.cli_history,
                  archy.cli_render, _location -> archy.cli_render,
                  archy.cli_render_check, archy.cli_render_history
"""

from __future__ import annotations

from archy.conventions import (
    Gate,
)
from archy.diff import (
    DiffSummary,
    ScoreDelta,
)


def _format_lines(lines: tuple[int, ...]) -> str:
    label = "lines" if len(lines) > 1 else "line"
    text = ", ".join(str(n) for n in lines) or "?"
    return f"({label}: {text})"


def _location(path: str, lines: tuple[int, ...]) -> str:
    """`path:line: ` for a finding's first import line (#522), or "" when unknown.

    The `path:line:` shape is what editors, vim quickfix, reviewdog and GitHub
    problem matchers already read, so a finding can be clicked through to the
    offending import. Only a prefix: the module names and `(line: N)` stay, so
    anything grepping today's output keeps working. An old snapshot has no path,
    and an edge with no recorded line has nothing to point at; both print as before.
    """
    return f"{path}:{lines[0]}: " if path and lines else ""


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


def _indent_block(block: str) -> list[str]:
    # Per LINE, not per block: these renderers emit multi-line text and a
    # single strip() left every continuation flush against the margin,
    # which reads as a new section rather than the rest of a sentence.
    return ["  " + ln.lstrip("# ").rstrip() for ln in block.split("\n") if ln.strip()]


def _score_delta_lines(delta: ScoreDelta, header: str) -> list[str]:
    """One signed line per score axis, under ``header``, for `diff` and `simulate`.

    The axes come from ``ScoreDelta``'s own fields, in declaration order, so a
    new axis shows up in both renderers without editing either (#335).
    """
    return [header] + [
        f"  {name:11s} {getattr(delta, name):+.3f}" for name in ScoreDelta.model_fields
    ]


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


def _added_block(header: str, rows: list[str]) -> list[str]:
    """A blank line, the header and the rows, or nothing when there are no rows."""
    return ["", header, *rows] if rows else []


def _gate_row(gate: Gate) -> str:
    code = "?" if gate.code is None else str(gate.code)
    where = f"{gate.module}:{gate.function}"
    suffix = "  [command]" if gate.is_command else ""
    call = f"{gate.kind}({code})"
    return f"  {where:<34} {call:<18} {gate.control}{suffix}"


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
