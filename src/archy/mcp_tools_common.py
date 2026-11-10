"""What every MCP tool registration shares: the tool annotations and the project-path check.

archy:mirrored-by _project_dir -> archy.mcp_tools_check, archy.mcp_tools_conventions,
                  archy.mcp_tools_git, archy.mcp_tools_graph
"""

from __future__ import annotations

from pathlib import Path

from mcp.types import ToolAnnotations

_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
"""archy's tools are structural analysis: they compute over a project, are
closed-domain (no network/external world), and are idempotent for a fixed source
tree. Declaring this explicitly lets trusted clients auto-approve archy's calls
instead of prompting on every read (MCP tool-annotations, 2025-03-26 spec).
`archy_score` writes `.archy/history.jsonl` only when `record=True`, and keeps
this annotation so the common read-only call does not prompt; see
`_SNAPSHOT_ANNOTATIONS` for the tool that always writes."""


_SNAPSHOT_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
"""`archy_snapshot` always overwrites `.archy/baseline.json`. It used to claim
read-only on the grounds that the file is only a cache, which stopped being
true once a baseline could gate (`diff --strict`, #521): an auto-approved
re-snapshot is exactly how a new cycle gets grandfathered. The strict gate
refuses that file for the same reason; this annotation stops a client from
running the overwrite without asking."""


def _project_dir(path: str) -> Path:
    """Return `path` when it is an existing directory.

    Same rule as the CLI's `click.Path(exists=True, file_okay=False)`. MCP
    tools take a bare string. `open_index` then does
    `db_path.parent.mkdir(parents=True)`, which creates a typo path and
    `.archy/index.db` and the empty tree scores 1.0 (#552).
    """
    root = Path(path)
    if root.is_dir():
        return root
    if root.is_file():
        raise ValueError(f"Directory '{path}' is a file.")
    raise ValueError(f"Directory '{path}' does not exist.")
