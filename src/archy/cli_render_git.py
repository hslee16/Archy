"""Renderers for the git-history commands: hotspots, coupling, duplicates, what-to-refactor-next.

archy:mirrored-by _coupling_to_dict -> archy.cli_git, archy.cli_render,
                  _coupling_to_text -> archy.cli, archy.cli_git, archy.cli_render,
                  _duplicates_to_dict -> archy.cli_git, archy.cli_render,
                  _duplicates_to_text -> archy.cli, archy.cli_git, archy.cli_render,
                  _hotspots_to_dict -> archy.cli_git, archy.cli_render,
                  _hotspots_to_text -> archy.cli, archy.cli_git, archy.cli_render,
                  _refactor_to_dict -> archy.cli_git, archy.cli_render,
                  _refactor_to_text -> archy.cli_git, archy.cli_render
"""

from __future__ import annotations

from archy.coupling import (
    CouplingPair,
)
from archy.duplicates import (
    DuplicateGroup,
)
from archy.hotspots import Hotspot
from archy.refactor import (
    RefactorPriority,
)


def _empty_window_note(since: str) -> str:
    """Shared wording for an empty ``--since`` commit window."""
    return (
        f"No commits in the --since window ({since}). "
        "Widen the window or omit --since to use full history."
    )


def _hotspots_note(rows: list[Hotspot], *, since: str | None, empty_window: bool) -> str | None:
    """Explain an empty hotspot list; name an empty --since window explicitly."""
    if rows:
        return None
    if empty_window and since:
        return _empty_window_note(since)
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
        return _empty_window_note(since)
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
            return _empty_window_note(since)
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
            return f"# {_empty_window_note(since)}"
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
