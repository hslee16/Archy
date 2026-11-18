"""Shared wording for fused refactor-priority notes.

archy:owns        FusedNoteInputs, empty_window_note, fused_refactor_note
archy:mirrored-by FusedNoteInputs -> archy.cli_render_git, archy.mcp_runs_git,
                  empty_window_note -> archy.cli_render_git, archy.mcp_runs_git,
                  fused_refactor_note -> archy.cli_render_git, archy.mcp_runs_git
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


def empty_window_note(since: str) -> str:
    """Shared wording for an empty ``--since`` commit window."""
    return (
        f"No commits in the --since window ({since}). "
        "Widen the window or omit --since to use full history."
    )


class FusedNoteInputs(BaseModel):
    """What the fused note is worded from: what ran and what was found."""

    model_config = ConfigDict(frozen=True)

    has_rows: bool
    min_risk: float
    git_available: bool
    no_commits: bool = False
    # Set only when git ran and the --since window held no commits at all.
    window_since: str | None = None


def fused_refactor_note(note: FusedNoteInputs) -> str | None:
    """Explain an empty or degraded fused ranking on CLI text, CLI JSON and MCP surfaces."""
    if not note.has_rows:
        return _empty_ranking_note(
            min_risk=note.min_risk,
            git_available=note.git_available,
            no_commits=note.no_commits,
            window_since=note.window_since,
        )
    if note.git_available:
        return None
    return _structural_only_note(no_commits=note.no_commits)


def _empty_ranking_note(
    *, min_risk: float, git_available: bool, no_commits: bool, window_since: str | None
) -> str:
    """Why the fused ranking has no rows."""
    if git_available:
        if window_since:
            return empty_window_note(window_since)
        return (
            "No refactoring priorities surfaced: no file is both complex "
            "and frequently changed, and no module is central and fragile "
            f"above min_risk={min_risk}; nothing to prioritize. Lower min_risk to widen the "
            "structural lens."
        )
    if no_commits:
        return (
            "No commit history yet, so the CC x churn lens was skipped and "
            f"only the structural lens ran; no module is central and fragile "
            f"above min_risk={min_risk}. Lower min_risk to widen the "
            "structural lens. Run inside git for the fused view."
        )
    return (
        "Not a git repository (or git is unavailable), so the CC x churn lens was skipped "
        f"and only the structural lens ran; no module is central and fragile above "
        f"min_risk={min_risk}. Lower min_risk to widen the structural lens. "
        "Run inside git for the fused view."
    )


def _structural_only_note(*, no_commits: bool) -> str:
    """Why a non-empty fused ranking is structural-only."""
    if no_commits:
        return (
            "No commit history yet; the CC x churn lens was skipped and "
            "this ranking is structural-only (edit-risk)."
        )
    return (
        "Not a git repository (or git is unavailable); the CC x churn lens was skipped "
        "and this ranking is structural-only (edit-risk)."
    )
