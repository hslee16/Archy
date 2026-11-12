"""Imports that target a PEP 420 namespace directory the scan did not name as a root (#586).

A directory with no `__init__.py` is not a package root, so `acme/core/util.py`
under `packages/core/src/` is named `core.util` and `from acme.web import views`
resolves to an external node called `acme`. The graph then has no edge between
the two halves of a real cycle, and `cycles` and `score` report a clean result
over a graph that was never joined. `find_namespace_gaps` names that case so the
surfaces can say so, instead of leaving a false green that looks exactly like a
clean codebase.

It detects and never infers: the suggested `roots:` are the namespace directories
themselves, which is what `roots:` expects (a root names the package directory,
not the directory that contains it).

archy:owns        NamespaceGap, find_namespace_gaps, namespace_gap_reason
archy:mirrored-by NamespaceGap -> archy.cli_render_history, archy.cycles,
                  archy.mcp_payloads
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import networkx as nx
from pydantic import BaseModel, ConfigDict


class NamespaceGap(BaseModel):
    """An external import target that is really a namespace directory inside the scan."""

    model_config = ConfigDict(frozen=True)

    name: str
    """The imported top-level name, for example `acme`."""
    directories: tuple[str, ...]
    """Every directory named `name` with no `__init__.py` holding scanned modules, root-relative."""
    packages: tuple[str, ...]
    """The names directly under those directories that hold scanned modules."""
    importers: int
    """How many internal modules import `name`."""


def namespace_gap_reason(gap: NamespaceGap) -> str:
    """One line that says what is wrong and what to set, for every surface to print."""
    n = gap.importers
    imports = "1 import targets" if n == 1 else f"{n} modules import"
    roots = ", ".join(gap.directories)
    return (
        f"{imports} `{gap.name}`, a directory with no __init__.py that contains scanned "
        f"packages ({', '.join(gap.packages)}); it is a PEP 420 namespace, so those imports "
        f"resolve to nothing and cycles through it are invisible. Set `roots:` in archy.yaml "
        f"to the namespace directories themselves, not their parents: `roots: [{roots}]`"
    )


def find_namespace_gaps(graph: nx.DiGraph) -> tuple[NamespaceGap, ...]:
    """Namespace directories the scan's own imports point at, sorted by name.

    A gap needs three facts at once: an internal module imports an external name,
    a directory of that name sits above scanned modules, and it has no
    `__init__.py`. A third-party import that no scanned directory shares a name
    with never qualifies, however it is spelled.
    """
    root = Path(graph.graph.get("root") or ".")
    # An external node exists only because something imports it, and it has no
    # outgoing edges, so its in-degree is its number of importers.
    importers = {n: graph.in_degree(n) for n, d in graph.nodes(data=True) if d.get("external")}
    if not importers:
        return ()

    # directory (root-relative posix) -> names directly under it that hold scanned modules
    found: dict[str, set[str]] = {}
    for _, data in graph.nodes(data=True):
        if data.get("external") or not data.get("path"):
            continue
        rel = _relative(Path(data["path"]), root)
        parts = rel.parts
        for i, part in enumerate(parts[:-1]):
            if part in importers:
                found.setdefault(PurePosixPath(*parts[: i + 1]).as_posix(), set()).add(
                    PurePosixPath(parts[i + 1]).stem
                )

    by_name: dict[str, dict[str, set[str]]] = {}
    for directory, below in found.items():
        if (root / directory / "__init__.py").exists():
            continue
        by_name.setdefault(PurePosixPath(directory).name, {})[directory] = below

    return tuple(
        NamespaceGap(
            name=name,
            directories=tuple(sorted(dirs)),
            packages=tuple(sorted(set().union(*dirs.values()))),
            importers=importers[name],
        )
        for name, dirs in sorted(by_name.items())
    )


def _relative(path: Path, root: Path) -> PurePosixPath:
    try:
        return PurePosixPath(path.resolve().relative_to(root.resolve()).as_posix())
    except ValueError:
        return PurePosixPath(path.as_posix())
