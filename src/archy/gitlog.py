"""Shared plumbing for the git-log-backed diagnostics (``hotspots`` churn,
``coupling`` co-change).

Both mine ``git log`` for per-``.py``-file history, and both need the same three
things the raw log does not give for free: the repository root (so a target
inside a subdirectory still sees the whole history), rename folding (a ``git mv``
splits a file's commits across the old and new path, so the pre-rename history
must be re-attached to the current path or the file is undercounted and its old
path becomes a phantom that matches no live graph node), and path normalization
that matches what ``parse_file`` / the graph node ``path`` attribute produce.
Keeping these in one place means the churn pass and the co-change pass agree on
what "the same file" is, so their outputs line up module-for-module.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_repo_root(root: Path) -> Path | None:
    """The git top-level for ``root``, or ``None`` if it is not in a repo.

    ``None`` (rather than a raise) is the shared "no git here" contract every
    caller degrades on. ``surrogateescape`` round-trips non-UTF8 path bytes
    instead of crashing the decode: git's default ``core.quotePath`` already
    ASCII-escapes them, but a repo with ``core.quotePath=false`` would otherwise
    break the contract.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return Path(proc.stdout.strip())


def fold_rename(renames: dict[str, str], path: str) -> str:
    """Follow ``path`` through the rename chain to its current name.

    ``renames`` maps each pre-rename path to its immediate successor; a file
    moved twice is followed transitively. The ``seen`` guard never loops on a
    pathological cycle (a path renamed back to itself across history).
    """
    seen = {path}
    while path in renames:
        path = renames[path]
        if path in seen:
            break
        seen.add(path)
    return path


def normalize_py_path(repo_root: Path, path: str) -> str | None:
    """Repo-relative git path -> absolute resolved ``str``, or ``None`` if not ``.py``.

    ``resolve()`` normalizes against symlinks and ``..`` segments so the key
    matches the graph node ``path`` (also produced by ``parse_file``), letting a
    churn/co-change entry be joined to its module without path-shape mismatches.
    """
    if not path.endswith(".py"):
        return None
    return str((repo_root / path).resolve())
