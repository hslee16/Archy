"""The work behind the duplicates and what-to-refactor-next tools.

archy:owns        run_duplicates, run_what_to_refactor_next
archy:mirrored-by run_what_to_refactor_next -> archy.mcp_runs, archy.mcp_tools_git
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archy.coupling import (
    git_cochange,
)
from archy.duplicates import (
    DuplicateGroup,
    classify_variants,
    compute_duplicates,
    compute_near_duplicates,
    demote_independent,
)
from archy.gitlog import (
    resolve_repo_root,
    resolve_since_log_args,
    since_window_empty,
)
from archy.hotspots import NoCommitsError, git_churn
from archy.mcp_payloads import (
    DuplicateGroupSummary,
    DuplicatesPayload,
    RefactorPriorityEntry,
    WhatToRefactorPayload,
)
from archy.mcp_runs_core import (
    _load_graph,
    _parse_project,
    _validate_refactor_lens,
    _validate_response_format,
)
from archy.refactor import compute_refactor_priorities
from archy.scan_config import (
    graph_kwargs,
    resolve_max_modules,
)


def _summarize_duplicate_group(group: DuplicateGroup) -> DuplicateGroupSummary:
    first = group.members[0]
    return DuplicateGroupSummary(
        shape_hash=group.shape_hash,
        size=group.size,
        member_count=group.member_count,
        redundancy=group.redundancy,
        category=group.category,
        variant_reason=group.variant_reason,
        exact=group.exact,
        similarity=group.similarity,
        sample=f"{first.module}:{first.line} {first.qualified_name}",
    )


def run_duplicates(
    path: Path,
    *,
    response_format: str,
    min_nodes: int,
    top_n: int,
    members: int,
    co_change: bool = True,
    near_miss: bool = False,
) -> DuplicatesPayload:
    """Two-tier duplicate clusters for archy_duplicates.

    Reads per-function rows via the function-grained warm path (`parse_map`),
    classifies clusters into the primary/variant tiers, and shapes each tier by
    `response_format` (a per-cluster summary vs the full member lists). When
    `co_change` and git history is available, demotes primary clusters whose
    copies never co-change (issue #242); best-effort (no git -> unchanged). When
    `near_miss`, adds a lower-confidence Type-3 tier via token overlap (#246).
    """
    _validate_duplicates_args(response_format, min_nodes, top_n, members)
    modules, results = _parse_project(
        path, max_modules=resolve_max_modules(path), **graph_kwargs(path)
    )
    rows = classify_variants(
        compute_duplicates(modules, results, min_size=min_nodes, min_members=members)
    )
    if co_change:
        cochange = git_cochange(path, keep_paths=frozenset(str(m.path) for m in modules))
        if cochange is not None:
            rows = demote_independent(
                rows, counts=cochange.counts, pair_support=cochange.pair_support
            )
    near = compute_near_duplicates(modules, results, min_size=min_nodes) if near_miss else []
    dups = [g for g in rows if g.category == "duplicate"]
    variants = [g for g in rows if g.category == "variant"]

    def _shape(groups: list[DuplicateGroup]):
        top = groups[:top_n]
        if response_format == "full":
            return tuple(top)
        return tuple(_summarize_duplicate_group(g) for g in top)

    return DuplicatesPayload(
        min_nodes=min_nodes,
        total=len(dups),
        exact_total=sum(1 for g in dups if g.exact),
        variant_total=len(variants),
        near_total=len(near),
        shown=min(top_n, len(dups)),
        duplicated_functions=sum(g.member_count for g in dups),
        duplicates=_shape(dups),
        variants=_shape(variants),
        near_miss=_shape(near),
        note=_duplicates_note(len(dups), len(variants), min_nodes),
    )


def _validate_duplicates_args(
    response_format: str, min_nodes: int, top_n: int, members: int
) -> None:
    _validate_response_format(response_format)
    if min_nodes < 1:
        raise ValueError(f"min_nodes must be >= 1; got {min_nodes}")
    if top_n < 1:
        raise ValueError(f"top_n must be >= 1; got {top_n}")
    if members < 2:
        raise ValueError(f"members must be >= 2; got {members}")


def _duplicates_note(dup_count: int, variant_count: int, min_nodes: int) -> str | None:
    if dup_count:
        return None
    note = (
        f"No likely-duplicate function bodies of >= {min_nodes} normalized "
        "nodes found; lower min_nodes to widen the search."
    )
    if variant_count:
        note += (
            f" ({variant_count} likely-intentional variant(s) were found but demoted: "
            "same-class / boilerplate / test / vendored / independent.)"
        )
    return note


class _RefactorNoteInputs(BaseModel):
    """What `_refactor_note` words its note from: the lens, what ran, what was found."""

    model_config = ConfigDict(frozen=True)

    lens: str
    has_rows: bool
    git_available: bool | None
    min_risk: float
    missing_git: str
    # Set only when git ran and the --since window held no commits at all.
    empty_window_since: str | None = None


def _missing_git_phrase(path: Path, no_commits: bool) -> str:
    return (
        f"{path} has no commit history yet"
        if no_commits
        else f"{path} is not inside a git repository (or git is unavailable)"
    )


def _refactor_note(note: _RefactorNoteInputs) -> str | None:
    """Lens-aware empty/degraded note for archy_what_to_refactor_next.

    behavioral needs git; structural never does; fused runs both and degrades
    to structural-only off git. A non-empty single-lens result needs no note.
    An empty --since window is named so it is not mistaken for a calm codebase.
    """
    if note.has_rows:
        if note.lens == "fused" and not note.git_available:
            return (
                f"{note.missing_git}; "
                "the CC x churn behavioral lens was skipped and this ranking is "
                "structural-only (edit-risk). Run inside git for the fused view."
            )
        return None
    if note.empty_window_since:
        return (
            f"No commits in the --since window ({note.empty_window_since}). "
            "Widen the window or omit --since to use full history."
        )
    return _empty_lens_note(note.lens, note.git_available, note.missing_git, note.min_risk)


def _empty_lens_note(
    lens: str, git_available: bool | None, missing_git: str, min_risk: float
) -> str:
    """The note for a lens that surfaced nothing, worded per lens and per git state."""
    if lens == "behavioral":
        if not git_available:
            return (
                f"{missing_git}; "
                "the behavioral lens needs git history to compute per-file churn. "
                "Use lens='structural' for a git-free 'is this edit dangerous?' "
                "signal instead."
            )
        return (
            "No behavioral hotspots surfaced: no file is both complex and "
            "frequently changed (CC x churn). The project is likely small, "
            "young, or structurally clean."
        )
    if lens == "structural":
        return (
            f"No module is central and fragile above min_risk={min_risk}. Lower "
            "min_risk (pass 0 to surface every module) to widen the lens."
        )
    # fused: the only lens left once behavioral and structural have returned
    if git_available:
        return (
            "No refactoring priorities surfaced: no file is both complex and "
            "frequently changed (CC x churn hotspots: 0), and no module is "
            f"central and fragile above min_risk={min_risk} (0). The project is "
            "likely small, young, or structurally clean - there is genuinely "
            "nothing to prioritize right now. Lower min_risk to widen the "
            "structural lens."
        )
    return (
        f"{missing_git}, so the "
        "CC x churn behavioral lens was skipped and only the structural "
        f"edit-risk lens ran. No module is central and fragile above "
        f"min_risk={min_risk}, so nothing was surfaced. Lower min_risk to widen "
        "the structural lens."
    )


def _validate_refactor_bounds(lens: str, top_n: int, min_risk: float) -> None:
    _validate_refactor_lens(lens)
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1; got {top_n}")
    if not 0.0 <= min_risk <= 1.0:
        raise ValueError(f"min_risk must be in [0, 1]; got {min_risk}")


def _validate_since(path: Path, since: str | None) -> None:
    # Refuse a garbage `since` even when lens='structural' skips the git call,
    # so a typo cannot silently look like "nothing to prioritize".
    if since is not None:
        repo = resolve_repo_root(path)
        if repo is not None:
            resolve_since_log_args(repo, since)


def _refactor_churn(path: Path, lens: str, since: str | None):
    """Per-file churn and whether the repo has no commits at all.

    The behavioral lens needs git; the structural lens never does, so skip the
    git call entirely for structural (since is irrelevant there). A repo with
    zero commits degrades the same way but gets its own note wording.
    """
    if lens == "structural":
        return None, False
    try:
        return git_churn(path, since=since), False
    except NoCommitsError:
        return None, True


def _rows_for_lens(rows: list, lens: str) -> list:
    if lens != "behavioral":
        # structural: churn=None already restricts rows to the edit_risk lens, sorted
        # by priority (== normalized edit_risk), matching the old high-risk ranking.
        return rows
    # Keep only rows the behavioral lens fired on and restore the pure
    # CC x churn ranking (the fused `priority` would let a both-lens module
    # outrank a bigger pure hotspot).
    return sorted(
        (r for r in rows if "hotspot" in r.lenses),
        key=lambda r: (-r.hotspot_score, r.module),
    )


def run_what_to_refactor_next(
    path: Path, *, lens: str = "fused", top_n: int, since: str | None, min_risk: float
) -> WhatToRefactorPayload:
    _validate_refactor_bounds(lens, top_n, min_risk)
    _validate_since(path, since)

    graph = _load_graph(path, internal_only=True)
    churn, no_commits = _refactor_churn(path, lens, since)
    git_available = None if lens == "structural" else churn is not None

    rows = _rows_for_lens(compute_refactor_priorities(graph, churn=churn, min_risk=min_risk), lens)
    shown = rows[:top_n]
    entries = tuple(
        RefactorPriorityEntry(
            module=r.module,
            path=r.path,
            lenses=r.lenses,
            priority=r.priority,
            cc_sum=None if lens == "structural" else r.cc_sum,
            churn=None if lens == "structural" else r.churn,
            hotspot_score=None if lens == "structural" else r.hotspot_score,
            edit_risk=r.edit_risk,
            propagation_cost=r.propagation_cost,
            instability=r.instability,
            fan_in=r.fan_in,
            rationale=r.rationale,
        )
        for r in shown
    )

    empty_window = False
    if since and git_available and not rows:
        repo = resolve_repo_root(path)
        if repo is not None:
            empty_window = since_window_empty(repo, since)
    note = _refactor_note(
        _RefactorNoteInputs(
            lens=lens,
            has_rows=bool(rows),
            git_available=git_available,
            min_risk=min_risk,
            missing_git=_missing_git_phrase(path, no_commits),
            empty_window_since=since if empty_window else None,
        )
    )

    return WhatToRefactorPayload(
        since=since,
        min_risk=min_risk,
        git_available=git_available,
        total=len(rows),
        shown=len(entries),
        priorities=entries,
        note=note,
    )
