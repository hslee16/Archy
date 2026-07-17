"""Adapter base class, install scope, and shared MCP/instruction content.

The installer is a *registry of adapters* (`docs/SPEC_INDEX_AND_INSTALL.md`
Part 4): each adapter knows how to detect its client, where to write that
client's MCP config, and what instruction file the client expects. Adding a
sixth client is a new adapter, not a new binary.

The split that keeps the test pyramid cheap: an adapter's :meth:`plan` returns
:class:`FileAction`s whose ``render`` is a *pure* function from existing file
content to new content. Rendering is what snapshot tests (layer 2) freeze and
contract tests (layer 4) parse back; the filesystem only enters when
:func:`apply_plan` feeds those renders through a :class:`WriteSystem`.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archy.install import paths
from archy.install.writer import WriteSystem

# The MCP server every adapter wires in. `uvx archy mcp` runs the server over
# stdio without a global install, matching the manual snippet in the README and
# the spec's "manual MCP config" block.
MCP_COMMAND = "uvx"
MCP_ARGS: tuple[str, ...] = ("archy", "mcp")

# The tools the server exposes, in the canonical order used everywhere else
# (README permission snippet, plugin manifest docs). Kept as a flat tuple so the
# Claude permission seed and any future allowlist stay in lockstep with the
# server. tests/install pin this against src/archy/mcp.py so it cannot drift.
TOOL_NAMES: tuple[str, ...] = (
    "archy_score",
    "archy_cycles",
    "archy_check",
    "archy_impact",
    "archy_snapshot",
    "archy_diff",
    "archy_graph",
    "archy_what_to_refactor_next",
    "archy_dsm",
    "archy_simulate",
    "archy_duplicates",
)

# Server key used both as the MCP stanza key and in the Claude permission
# pattern `mcp__<server_key>__<tool>`.
SERVER_KEY = "archy"

# Agent-facing instructions dropped as each client's rules file (CLAUDE.md
# snippet, .cursor/rules/archy.mdc, ~/.codex/AGENTS.md). Fenced by markers so a
# re-run replaces the block in place rather than appending a duplicate.
INSTRUCTIONS_BEGIN = "<!-- archy:begin -->"
INSTRUCTIONS_END = "<!-- archy:end -->"

INSTRUCTIONS_BODY = """\
## archy (architectural sensor)

archy is wired in as an MCP server. It is a *judge*, not a librarian: it scores
architectural health, finds cycles and layer violations, and maps the blast
radius of a change. Use it on the edit loop, not just on demand.

- Before editing, call `archy_impact` (or `archy_impact(mode='affected')` on a
  set of changed files) to see what a module change reaches.
- Take a baseline with `archy_snapshot` at the start of a task; after edits,
  call `archy_diff` and read `summary.headline` first.
- When asked "is this codebase healthy / where is the risk", reach for
  `archy_score`, `archy_what_to_refactor_next` (lens='structural' or
  'behavioral'), and `archy_cycles` rather than guessing from file names.

Outputs are structured JSON meant for you to act on, not dashboards for humans.
"""


def permission_patterns() -> list[str]:
    """Claude `permissions.allow` entries for every archy tool."""
    return [f"mcp__{SERVER_KEY}__{tool}" for tool in TOOL_NAMES]


def instructions_block() -> str:
    """The full marker-fenced instruction block written into a rules file."""
    return f"{INSTRUCTIONS_BEGIN}\n{INSTRUCTIONS_BODY}{INSTRUCTIONS_END}\n"


def _drop_leading_newline(text: str) -> str:
    """Drop one leading newline so removing a fenced block doesn't leave a gap."""
    return text[1:] if text.startswith("\n") else text


def upsert_instructions(existing: str | None) -> str:
    """Insert or replace the archy instruction block, leaving the rest intact.

    Idempotent: a second call with the first call's output is a no-op. Other
    content in the file (the user's own CLAUDE.md, an existing AGENTS.md) is
    preserved; only the text between the archy markers is rewritten.
    """
    block = instructions_block()
    if not existing:
        return block
    start = existing.find(INSTRUCTIONS_BEGIN)
    if start == -1:
        sep = "" if existing.endswith("\n") else "\n"
        return f"{existing}{sep}\n{block}"
    end = existing.find(INSTRUCTIONS_END, start)
    if end == -1:
        # No closing marker: recover by overwriting from the marker rather than
        # raising, so a hand-corrupted file still re-installs cleanly.
        return f"{existing[:start]}{block}"
    end += len(INSTRUCTIONS_END)
    return f"{existing[:start]}{block}{_drop_leading_newline(existing[end:])}"


def remove_instructions(existing: str | None) -> str | None:
    """Inverse of :func:`upsert_instructions`: drop only the archy block.

    Returns the file content with the marker-fenced block removed, preserving
    everything the user wrote around it. Returns ``None`` (signalling "delete the
    file") when nothing but the archy block remains, since in that case archy is
    the only reason the file exists. Idempotent and a no-op if no block present.
    """
    if not existing:
        return None
    start = existing.find(INSTRUCTIONS_BEGIN)
    if start == -1:
        return existing
    end = existing.find(INSTRUCTIONS_END, start)
    if end == -1:
        # No closing marker: recover by discarding from the marker on rather
        # than raising, so uninstall stays idempotent on a corrupted file.
        remainder = existing[:start]
    else:
        end += len(INSTRUCTIONS_END)
        remainder = existing[:start] + _drop_leading_newline(existing[end:])
    return None if not remainder.strip() else remainder


def delete_file(_existing: str | None) -> None:
    """Unrender for files archy owns outright: always remove them on uninstall."""
    return None


def local_root(project_root: Path | None) -> Path:
    """Anchor for local-scope writes: the given project root, or the cwd."""
    return project_root or Path.cwd()


class Scope(str, Enum):
    """Where config is written: every project, or just this one."""

    GLOBAL = "global"
    LOCAL = "local"


class FileAction(BaseModel):
    """One file archy manages, as paired pure functions over its current text.

    ``render`` (install) takes the file's existing content (``None`` if absent)
    and returns the full new content. ``unrender`` (uninstall) takes the existing
    content and returns either the content with archy's part removed, or ``None``
    to delete the file (used for files archy owns outright). Pairing both
    directions on one object keeps install and uninstall symmetric per file, so a
    new adapter declares both at once. ``kind`` is ``mcp`` / ``instructions`` /
    ``permissions`` for reporting and selective skipping.
    """

    model_config = ConfigDict(frozen=True)

    path: Path
    kind: str
    render: Callable[[str | None], str]
    unrender: Callable[[str | None], str | None]


class AgentAdapter(ABC):
    """Detect one agent client and emit its archy config + instructions."""

    id: str
    name: str
    cli_name: str

    # --- detection -------------------------------------------------------
    @abstractmethod
    def config_paths(self, scope: Scope, project_root: Path | None = None) -> list[Path]:
        """MCP-config file(s) this adapter writes at ``scope``.

        ``project_root`` anchors local-scope paths (defaults to the cwd); it is
        ignored for global scope, which is always homedir/AppData anchored.
        """

    def detection_paths(self) -> list[Path]:
        """Files/dirs whose existence signals the client has been set up.

        Defaults to the global-scope config paths' parents; adapters override
        when the meaningful probe is a directory or a different file.
        """
        return [p.parent for p in self.config_paths(Scope.GLOBAL)]

    def mac_app_bundles(self) -> list[Path]:
        """macOS .app fallback probes (used only if CLI + config probes miss)."""
        return []

    def windows_install_dirs(self) -> list[Path]:
        """Windows per-user install-dir fallback probes."""
        return []

    def detect(self) -> bool:
        """Layered probe: CLI on PATH, then config dirs, then OS fallbacks.

        Any hit returns True. This catches "installed but never launched" (CLI
        present, no config yet) and "launched but not on PATH" (Electron apps
        with no CLI), on top of the baseline "has been run" (config exists).
        """
        if self.cli_name and shutil.which(self.cli_name):
            return True
        if any(p.exists() for p in self.detection_paths()):
            return True
        if paths.is_macos() and any(p.exists() for p in self.mac_app_bundles()):
            return True
        return bool(paths.is_windows() and any(p.exists() for p in self.windows_install_dirs()))

    # --- planning --------------------------------------------------------
    @abstractmethod
    def plan(
        self,
        scope: Scope,
        *,
        project_root: Path | None = None,
        seed_permissions: bool = True,
        for_uninstall: bool = False,
    ) -> list[FileAction]:
        """The ordered list of file actions for archy at ``scope``.

        ``project_root`` anchors local-scope writes (defaults to cwd).
        ``seed_permissions`` is honored only by adapters whose client supports a
        permission allowlist (Claude today); others ignore it.
        ``for_uninstall`` lets an adapter widen the plan for the reverse pass so
        uninstall stays the exact inverse of install regardless of state that
        changed in between (Claude uses it; others ignore it).
        """


def apply_plan(plan: list[FileAction], write_system: WriteSystem) -> list[Path]:
    """Feed each action's pure render through the write system. Returns paths."""
    written: list[Path] = []
    for action in plan:
        existing = write_system.read_text(action.path)
        write_system.write_text(action.path, action.render(existing))
        written.append(action.path)
    return written


def apply_uninstall(plan: list[FileAction], write_system: WriteSystem) -> list[Path]:
    """Run each action's ``unrender`` in reverse: strip archy or delete the file.

    A file that does not exist is skipped (uninstall is idempotent). When
    ``unrender`` returns ``None`` the file is removed; otherwise the stripped
    content is written back. Returns the paths actually touched.
    """
    touched: list[Path] = []
    for action in plan:
        existing = write_system.read_text(action.path)
        if existing is None:
            continue
        result = action.unrender(existing)
        if result is None:
            write_system.remove(action.path)
        else:
            write_system.write_text(action.path, result)
        touched.append(action.path)
    return touched
