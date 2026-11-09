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

``--since`` is also shared: help text promises a date or a refspec, but
``git log --since=`` only accepts dates and silently treats a tag or typo as
"now" (matching 0 commits). :func:`resolve_since_log_args` turns a real ref into
a ``<ref>..HEAD`` range, passes real dates through, and refuses garbage.

archy:owns        InvalidSinceError, fold_rename, normalize_py_path,
                  resolve_changed_paths, resolve_repo_root, resolve_since_log_args,
                  since_window_empty
archy:mirrored-by fold_rename -> archy.coupling, archy.hotspots,
                  normalize_py_path -> archy.coupling, archy.hotspots,
                  resolve_changed_paths -> archy.cli_graph, archy.mcp_runs,
                  resolve_repo_root -> archy.cli_git, archy.coupling, archy.hotspots,
                  archy.mcp_runs, resolve_since_log_args -> archy.coupling,
                  archy.hotspots, archy.mcp_runs, since_window_empty -> archy.cli_git,
                  archy.mcp_runs
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

# Relative dates that git resolves to approximately "now". A garbage string also
# resolves to now, so these words are the allowlist that keeps a deliberate
# `--since now` from being refused as a typo.
_NOW_LIKE = frozenset({"now", "today"})
# git's "about now" for an unparseable --since is the current epoch second;
# only absorb the resolve-call skew itself. A wider window (e.g. 120s) also
# refused legitimate relative dates like "1 minute ago" / "30 seconds ago".
_NOW_SKEW_SECONDS = 2


class InvalidSinceError(ValueError):
    """``since`` is neither a git ref nor a date git understands.

    Carries the original value so CLI/MCP surfaces can name it in the refusal
    instead of collapsing into an empty result set.
    """

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(
            f"invalid --since value {value!r}: not a git ref and not a date git understands"
        )


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


def resolve_changed_paths(path: Path, files: list[Path]) -> list[Path]:
    """Anchor changed-file args against PATH, retrying missing relatives at the git root.

    Existing project-relative paths take precedence. A git-root candidate is
    used only if it exists and lies inside PATH, so a deleted project file is
    never matched to a namesake elsewhere in the repository. Absolute paths and
    paths missing from both roots keep their original resolution, including
    when git is unavailable or PATH is outside a work tree.
    """
    resolved = [path / f for f in files]
    missing = [i for i, f in enumerate(files) if not f.is_absolute() and not resolved[i].exists()]
    if missing and (repo_root := resolve_repo_root(path)) is not None:
        project = path.resolve()
        for i in missing:
            candidate = repo_root / files[i]
            if candidate.exists() and candidate.resolve().is_relative_to(project):
                resolved[i] = candidate
    return resolved


def resolve_since_log_args(repo_root: Path, since: str) -> tuple[str, ...]:
    """Translate a ``--since`` value into ``git log`` argv fragments.

    - A value that resolves as a commit (tag, branch, SHA, ``HEAD~N``, ...)
      becomes ``("<value>..HEAD",)`` so the window is the commits after that
      ref, matching the help text's "refspec" promise.
    - A value git parses as a date becomes ``("--since=<value>",)``.
    - A value that is neither (typo / garbage) raises :class:`InvalidSinceError`
      instead of silently matching 0 commits the way bare ``--since=garbage``
      does (git treats unparseable dates as "now").
    """
    verify = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "rev-parse",
            "--verify",
            "--quiet",
            f"{since}^{{commit}}",
        ],
        capture_output=True,
        text=True,
        errors="surrogateescape",
    )
    if verify.returncode == 0:
        return (f"{since}..HEAD",)

    try:
        parsed = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", f"--since={since}"],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise InvalidSinceError(since) from exc

    # `git rev-parse --since=X` prints `--max-age=<epoch>`.
    token = parsed.stdout.strip()
    try:
        max_age = int(token.split("=", 1)[1])
    except (IndexError, ValueError) as exc:
        raise InvalidSinceError(since) from exc

    if (
        abs(max_age - int(time.time())) <= _NOW_SKEW_SECONDS
        and since.strip().lower() not in _NOW_LIKE
    ):
        raise InvalidSinceError(since)
    return (f"--since={since}",)


def since_window_empty(repo_root: Path, since: str) -> bool:
    """True when the resolved ``--since`` window contains no commits.

    Used to put that fact in the JSON ``note`` instead of implying the project
    has no hotspots/coupling when the window itself is empty (e.g. ``--since``
    at HEAD).
    """
    args = resolve_since_log_args(repo_root, since)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--format=%H", "-1", *args],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return True
    return not proc.stdout.strip()


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
