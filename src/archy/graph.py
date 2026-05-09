"""Build a module-level import graph from a Python project tree.

A "module" is identified by its dotted path relative to a discovered package
root. External imports (stdlib, third-party) appear as nodes with
`external=True` so callers can filter them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import networkx as nx

from archy.parser import ImportRef, parse_file

_DEFAULT_IGNORED_DIRS = frozenset(
    {
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "site-packages",
        "build",
        "dist",
    }
)


@dataclass(frozen=True)
class Module:
    """An internal Python module discovered in the project."""

    qualname: str  # e.g. "archy.parser"
    path: Path  # absolute path to the .py file
    is_package: bool  # True for __init__.py


def build_graph(
    root: Path,
    *,
    ignored_dirs: Iterable[str] = _DEFAULT_IGNORED_DIRS,
) -> nx.DiGraph:
    """Discover modules under `root` and build their import graph.

    Nodes are dotted module names (str). Internal modules carry attributes
    `path`, `is_package`, `external=False`. External modules carry only
    `external=True`. Edges are import relationships (source -> target).

    Edge attribute `is_relative` indicates whether the import was written
    using leading-dot syntax. Edge attribute `lines` is a tuple of source
    line numbers where the import appears (a single source/target pair may
    occur on multiple lines).
    """
    ignored = frozenset(ignored_dirs)
    modules = _discover_modules(root, ignored)
    qualname_set = {m.qualname for m in modules}

    graph: nx.DiGraph = nx.DiGraph()
    for m in modules:
        graph.add_node(
            m.qualname,
            path=str(m.path),
            is_package=m.is_package,
            external=False,
        )

    parse_errors: list[str] = []
    for m in modules:
        result = parse_file(m.path)
        if result.has_errors:
            parse_errors.append(m.qualname)
        for ref in result.imports:
            for target in _resolve_targets(ref, m, qualname_set):
                if target not in graph:
                    graph.add_node(target, external=True)
                _add_or_extend_edge(graph, m.qualname, target, ref)

    graph.graph["root"] = str(root)
    graph.graph["parse_errors"] = tuple(sorted(parse_errors))
    return graph


def _discover_modules(root: Path, ignored: frozenset[str]) -> list[Module]:
    """Find Python source files and assign dotted module qualnames.

    Package roots are directories containing __init__.py whose parent
    directory does NOT contain one. The conventional `src/<pkg>/__init__.py`
    layout is supported because `src` itself is not a package.
    """
    package_roots = _find_package_roots(root, ignored)
    modules: list[Module] = []

    for py_file in _iter_python_files(root, ignored):
        qualname = _qualname_for(py_file, package_roots)
        if qualname is None:
            # Top-level scripts not inside any package are skipped for v1.
            continue
        modules.append(
            Module(
                qualname=qualname,
                path=py_file.resolve(),
                is_package=py_file.name == "__init__.py",
            )
        )

    modules.sort(key=lambda m: m.qualname)
    return modules


def _find_package_roots(root: Path, ignored: frozenset[str]) -> list[Path]:
    """Return absolute paths to top-level package directories under root."""
    package_dirs: set[Path] = set()
    for path in root.rglob("__init__.py"):
        if any(part in ignored for part in path.parts):
            continue
        package_dirs.add(path.parent.resolve())

    roots: list[Path] = []
    for pkg in package_dirs:
        if pkg.parent.resolve() not in package_dirs:
            roots.append(pkg)
    roots.sort()
    return roots


def _iter_python_files(root: Path, ignored: frozenset[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if any(part in ignored for part in path.parts):
            continue
        yield path


def _qualname_for(py_file: Path, package_roots: list[Path]) -> str | None:
    abs_path = py_file.resolve()
    for pkg_root in package_roots:
        try:
            rel = abs_path.relative_to(pkg_root.parent)
        except ValueError:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) if parts else pkg_root.name
    return None


def _resolve_targets(
    ref: ImportRef,
    source_module: Module,
    internal_qualnames: set[str],
) -> list[str]:
    """Map an ImportRef to one or more target qualnames.

    Why "one or more": `from pkg import a, b` may pull in two separate
    submodules (pkg.a, pkg.b) or two names from pkg's namespace. We can't
    tell statically without semantic analysis, so when the parent module
    is internal we emit edges to every imported name that resolves to
    a known internal module, and otherwise emit one edge to the parent.
    """
    if ref.is_relative:
        base = _resolve_relative_base(ref.module, source_module)
        if base is None:
            return []
        return _expand_with_imported_names(base, ref.imported_names, internal_qualnames)

    return _expand_with_imported_names(ref.module, ref.imported_names, internal_qualnames)


def _expand_with_imported_names(
    base: str,
    imported_names: tuple[str, ...],
    internal_qualnames: set[str],
) -> list[str]:
    """Decide whether to attribute edges to `base` or to its submodules."""
    base_internal = base in internal_qualnames

    if base_internal and imported_names:
        # `from internal_pkg import a, b` — prefer submodule edges where they exist,
        # fall back to the parent edge for names that are just symbols.
        targets: list[str] = []
        had_submodule_match = False
        for name in imported_names:
            candidate = f"{base}.{name}"
            if candidate in internal_qualnames:
                targets.append(candidate)
                had_submodule_match = True
        if not had_submodule_match:
            targets.append(base)
        return targets

    if base_internal:
        return [base]

    # External path. Try the longest internal prefix first (e.g. an external
    # path that happens to share a prefix with an internal package). If no
    # internal prefix matches, attribute the edge to the top-level package.
    parts = base.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in internal_qualnames:
            return [candidate]
    return [parts[0]]


def _resolve_relative_base(
    raw: str,
    source_module: Module,
) -> str | None:
    """Resolve a leading-dot import to the absolute qualname of the from-module."""
    leading_dots = 0
    for ch in raw:
        if ch == ".":
            leading_dots += 1
        else:
            break
    suffix = raw[leading_dots:]

    src_parts = source_module.qualname.split(".")
    if not source_module.is_package:
        src_parts = src_parts[:-1]

    walk_up = leading_dots - 1  # `from .` stays in the current package
    if walk_up > len(src_parts):
        return None  # escapes the project root
    base = src_parts[: len(src_parts) - walk_up] if walk_up else src_parts

    target_parts = [*base, *(suffix.split(".") if suffix else [])]
    target = ".".join(p for p in target_parts if p)
    return target or None


def _resolve_relative(
    raw: str,
    source_module: Module,
    internal_qualnames: set[str],
) -> str | None:
    """Resolve a leading-dot relative import to an absolute qualname.

    `raw` is the literal text of the relative_import node, e.g. '.', '..pkg',
    '...sub.mod'. The dot count tells us how many package levels to walk up
    from the source module's package.
    """
    leading_dots = 0
    for ch in raw:
        if ch == ".":
            leading_dots += 1
        else:
            break
    suffix = raw[leading_dots:]

    src_parts = source_module.qualname.split(".")
    if not source_module.is_package:
        # For a non-package module, its package is everything but the last segment.
        src_parts = src_parts[:-1]

    walk_up = leading_dots - 1  # `from .` stays in the current package
    if walk_up > len(src_parts):
        return None  # escapes the project root
    base = src_parts[: len(src_parts) - walk_up] if walk_up else src_parts

    target_parts = [*base, *(suffix.split(".") if suffix else [])]
    target = ".".join(p for p in target_parts if p)
    if not target:
        return None

    if target in internal_qualnames:
        return target
    # Try shorter prefixes — `from ..util import helpers` may target
    # `pkg.util` even if `pkg.util.helpers` isn't a module.
    parts = target.split(".")
    for end in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in internal_qualnames:
            return candidate
    return None


def _add_or_extend_edge(
    graph: nx.DiGraph,
    src: str,
    dst: str,
    ref: ImportRef,
) -> None:
    if graph.has_edge(src, dst):
        data = graph[src][dst]
        data["lines"] = (*data.get("lines", ()), ref.line)
    else:
        graph.add_edge(src, dst, is_relative=ref.is_relative, lines=(ref.line,))
