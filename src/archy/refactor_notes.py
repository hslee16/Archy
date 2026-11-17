"""Shared wording for fused refactor-priority notes.

archy:owns        empty_window_note, fused_refactor_note
archy:mirrored-by empty_window_note -> archy.cli_render_git, archy.mcp_runs_git,
                  fused_refactor_note -> archy.cli_render_git, archy.mcp_runs_git
"""

from __future__ import annotations


def empty_window_note(since: str) -> str:
    """Shared wording for an empty ``--since`` commit window."""
    return (
        f"No commits in the --since window ({since}). "
        "Widen the window or omit --since to use full history."
    )


def fused_refactor_note(
    *,
    has_rows: bool,
    min_risk: float,
    git_available: bool,
    no_commits: bool = False,
    empty_window: bool = False,
    since: str | None = None,
) -> str | None:
    """Explain an empty or degraded fused ranking on CLI text, CLI JSON and MCP surfaces."""
    if not has_rows:
        if empty_window and since and git_available:
            return empty_window_note(since)
        if git_available:
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
    if not git_available:
        if no_commits:
            return (
                "No commit history yet; the CC x churn lens was skipped and "
                "this ranking is structural-only (edit-risk)."
            )
        return (
            "Not a git repository (or git is unavailable); the CC x churn lens was skipped "
            "and this ranking is structural-only (edit-risk)."
        )
    return None
