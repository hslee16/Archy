"""Build a module-level import graph from a Python project tree.

A "module" is identified by its dotted path relative to a discovered package
root. External imports (stdlib, third-party) appear as nodes with
`external=True` so callers can filter them.

archy:owns        build_graph, parse_project
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import networkx as nx

from archy.graph_assemble import (
    _add_kind,
    assemble_graph,
    graph_to_dict,
    internal_module_count,
    internal_subgraph,
    zero_modules_reason,
)
from archy.graph_discovery import (
    _discover_modules,
    _find_package_roots,
    _is_conventional_test_file,
    _is_extension_or_stub_leaf,
    _iter_python_files,
    _qualname_for,
    _relative_paths,
    discover_modules,
    is_ignored_path,
)
from archy.graph_hints import (
    _SUGGEST_MIN_SIMILARITY,
    hint_unresolved,
    resolve_modules,
    suggest_files,
    suggest_modules,
)
from archy.graph_models import (
    DEFAULT_IGNORED_DIRS,
    DEFAULT_MAX_MODULES,
    Module,
    QualnameCollisionError,
    ScanTooLargeError,
    UnresolvedHint,
    effective_max_modules,
)
from archy.graph_resolve import (
    _follow_reexport_chains,
    resolve_from_import,
    resolve_relative_import,
)
from archy.parser import ParseResult, parse_file

_EMPTY_PARSE = ParseResult(imports=(), has_errors=False)


def build_graph(
    root: Path,
    *,
    ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS,
    extra_roots: Iterable[str] = (),
    max_modules: int | None = None,
) -> nx.DiGraph:
    """Discover modules under `root` and build their import graph.

    Nodes are dotted module names (str). Internal modules carry attributes
    `path`, `is_package`, `external=False`. External modules carry only
    `external=True`. Edges are import relationships (source -> target).

    Edge attribute `is_relative` indicates whether ANY import contributing
    to the edge was written using leading-dot syntax; a pair with both a
    relative and an absolute import reports True regardless of the order
    they appear in. Edge attribute `lines` is a tuple of source line numbers
    where the import appears (a single source/target pair may occur on
    multiple lines).
    """
    modules, parse_results = parse_project(
        root,
        ignored_dirs=ignored_dirs,
        extra_roots=extra_roots,
        max_modules=max_modules,
    )
    return assemble_graph(root, modules, parse_results)


def parse_project(
    root: Path,
    *,
    ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS,
    extra_roots: Iterable[str] = (),
    max_modules: int | None = None,
) -> tuple[list[Module], dict[str, ParseResult]]:
    """Discover and parse modules under `root`, returning `(modules, parse_results)`.

    The discover + size-guard + per-file parse half of `build_graph`, split out
    so function-grained diagnostics (e.g. `archy.duplicates`) can read the
    individual `ParseResult.functions` rows that `assemble_graph` rolls up into
    per-module aggregates and discards. `parse_results` is keyed by module
    qualname; modules whose file vanished between discovery and parse are absent.
    """
    ignored = frozenset(ignored_dirs)
    modules = _discover_modules(root, ignored, tuple(extra_roots))
    # Trip the size backstop here, at the cheap discovery boundary, BEFORE the
    # expensive per-file parse loop below: parsing tens of thousands of files is
    # what actually wedges, and the module count is already known. Any
    # non-positive `max_modules` (0/None, or a negative from a misused library
    # call) disables the guard, preserving direct/library callers.
    limit = _module_limit(max_modules)
    if limit is not None and len(modules) > limit:
        raise ScanTooLargeError(len(modules), root, limit)
    return modules, _parse_modules(modules)


def _module_limit(max_modules: int | None) -> int | None:
    """The enforced limit, or None when the guard is off (0, None or negative)."""
    return max_modules if max_modules and max_modules > 0 else None


def _parse_modules(modules: list[Module]) -> dict[str, ParseResult]:
    parse_results: dict[str, ParseResult] = {}
    for m in modules:
        if _is_extension_or_stub_leaf(m.path):
            # Compiled extensions are not Python source; stub-only leaves are
            # recorded so imports resolve, with no import graph of their own (#665).
            parse_results[m.qualname] = _EMPTY_PARSE
            continue
        try:
            parse_results[m.qualname] = parse_file(m.path)
        except OSError:
            # The file vanished or became unreadable between discovery and parse
            # (a branch switch, a concurrent edit, or the `archy mcp` watcher
            # rebuilding mid-flight). Skip it; assemble_graph drops modules with
            # no parse result, and the next build picks it up once disk settles.
            continue
    return parse_results


__all__ = [
    "DEFAULT_IGNORED_DIRS",
    "DEFAULT_MAX_MODULES",
    "_SUGGEST_MIN_SIMILARITY",
    "Module",
    "QualnameCollisionError",
    "ScanTooLargeError",
    "UnresolvedHint",
    "_add_kind",
    "_find_package_roots",
    "_follow_reexport_chains",
    "_is_conventional_test_file",
    "_iter_python_files",
    "_qualname_for",
    "_relative_paths",
    "assemble_graph",
    "build_graph",
    "discover_modules",
    "effective_max_modules",
    "graph_to_dict",
    "hint_unresolved",
    "internal_module_count",
    "internal_subgraph",
    "is_ignored_path",
    "parse_project",
    "resolve_from_import",
    "resolve_modules",
    "resolve_relative_import",
    "suggest_files",
    "suggest_modules",
    "zero_modules_reason",
]
