"""What a scan shares: Module, the scan-size and collision errors, and the ignored directories.

archy:owns        Module, QualnameCollisionError, ScanTooLargeError, UnresolvedHint,
                  effective_max_modules
archy:mirrored-by QualnameCollisionError -> archy.cli_app, archy.graph,
                  archy.graph_discovery, effective_max_modules -> archy.graph,
                  archy.scan_config
"""

from __future__ import annotations

from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict

# Longest first so `.cpython-312-….so` wins over bare `.so`.
_EXTENSION_SUFFIXES_BY_LENGTH = cast(
    tuple[str, ...],
    tuple(sorted((str(suffix) for suffix in EXTENSION_SUFFIXES), key=len, reverse=True)),
)


# Names the scan never enters, whatever they hold: version-control and tool
# bookkeeping that never carry first-party source.
HARD_IGNORED_DIRS = frozenset(
    {
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        ".git",
        ".venv",
        "node_modules",
    }
)


# Names skipped only when the directory is not a Python package. Build output
# has no `__init__.py` at its top level (setuptools writes `build/lib/pkg/`), so
# this keeps artifacts out; a real package that happens to be called `build`,
# `dist`, `venv` or `site-packages` (#518) is scanned instead of vanishing.
SOFT_IGNORED_DIRS = frozenset({"build", "dist", "venv", "site-packages"})


DEFAULT_IGNORED_DIRS = HARD_IGNORED_DIRS | SOFT_IGNORED_DIRS


# Soft ceiling on how many modules a single scan may discover before archy
# refuses to proceed. The named `DEFAULT_IGNORED_DIRS` above catch the standard
# vendored dirs, but a custom cache/generated dir (e.g. a bench `repo_cache`)
# can still pull tens of thousands of files into one scan and wedge the
# superlinear graph metrics for many minutes (see #213/#216). This backstop is
# name-agnostic: it trips on size alone. The default sits well above the largest
# real project archy benches (pytorch ~2,250 modules); callers pass `0`/`None`
# to disable, or override via `max_modules:` in archy.yaml.
DEFAULT_MAX_MODULES = 10_000


# How many colliding qualnames the error message lists before summarising the rest.
_MAX_COLLISIONS_SHOWN = 5


class ScanTooLargeError(Exception):
    """Raised when a scan discovers more modules than the configured ceiling.

    Carries the measured `count`, the `root` scanned, and the `limit` so callers
    (the CLI, the MCP tools) can render an actionable message without re-deriving
    them.
    """

    def __init__(self, count: int, root: Path, limit: int) -> None:
        self.count = count
        self.root = root
        self.limit = limit
        super().__init__(
            f"Found {count:,} modules under {root} (limit {limit:,}). This is far "
            f"larger than a typical project and usually means a vendored, cache, or "
            f"generated directory is being scanned. Add it to `exclude:` in "
            f"archy.yaml, point archy at a narrower path, or raise/disable the limit "
            f"with `max_modules:` in archy.yaml (0 disables)."
        )


class QualnameCollisionError(ValueError):
    """Raised when two distinct files map to the same module qualname (#531).

    The graph keeps one node per qualname, so the second file would silently
    replace the first and every answer about it (`affected` most of all, which
    would report "0 tests" for tests that were dropped) would be quietly
    incomplete. A `ValueError` so the MCP tool boundary already re-raises it
    with its message intact. `collisions` maps each shared qualname to every
    path that claims it, relative to the scan root where possible.
    """

    def __init__(self, collisions: dict[str, list[str]]) -> None:
        self.collisions = collisions
        shown = sorted(collisions)[:_MAX_COLLISIONS_SHOWN]
        lines = [f"  {q}: {', '.join(collisions[q])}" for q in shown]
        more = len(collisions) - len(shown)
        if more > 0:
            lines.append(f"  ... and {more} more")
        head = (
            "1 module name maps" if len(collisions) == 1 else f"{len(collisions)} module names map"
        )
        super().__init__(
            f"{head} to more than one file, and archy "
            f"keeps one node per name, so one file would silently replace the other:\n"
            + "\n".join(lines)
            + "\nRun archy on each member separately, add one of the trees to `exclude:` "
            "in archy.yaml, or delete the file a same-named package shadows."
        )


class Module(BaseModel):
    """An internal Python module discovered in the project."""

    model_config = ConfigDict(frozen=True)

    qualname: str
    path: Path
    is_package: bool


def effective_max_modules(configured: int | None) -> int | None:
    """Resolve a configured scan ceiling to the value the guard should use.

    `None` (unset in archy.yaml, or no config) -> `DEFAULT_MAX_MODULES`; `0` ->
    `None` (explicitly disabled); a positive value -> itself. Shared by the CLI
    and MCP boundaries so they apply identical semantics.
    """
    if configured is None:
        return DEFAULT_MAX_MODULES
    return configured or None


class UnresolvedHint(BaseModel):
    """What an unresolved reference was probably meant to be.

    Carried beside a tool's `unresolved` list rather than replacing its strings,
    so a consumer that reads `unresolved` as a list of names keeps working. `ref`
    is the reference exactly as it was given, a module name or a file path.
    """

    model_config = ConfigDict(frozen=True)

    ref: str
    did_you_mean: tuple[str, ...]
