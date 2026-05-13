"""Build a module-level import graph from a Python project tree.

A "module" is identified by its dotted path relative to a discovered package
root. External imports (stdlib, third-party) appear as nodes with
`external=True` so callers can filter them.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy.instability import compute_instability
from archy.parser import ImportRef, ParseResult, parse_file
from archy.reach import compute_propagation_cost

DEFAULT_IGNORED_DIRS = frozenset(
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


class Module(BaseModel):
    """An internal Python module discovered in the project."""

    model_config = ConfigDict(frozen=True)

    qualname: str  # e.g. "archy.parser"
    path: Path  # absolute path to the .py file
    is_package: bool  # True for __init__.py


def build_graph(
    root: Path,
    *,
    ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS,
    extra_roots: Iterable[str] = (),
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
    modules = _discover_modules(root, ignored, tuple(extra_roots))
    qualname_set = {m.qualname for m in modules}

    graph: nx.DiGraph = nx.DiGraph()
    for m in modules:
        graph.add_node(
            m.qualname,
            path=str(m.path),
            is_package=m.is_package,
            external=False,
        )

    parse_results: dict[str, ParseResult] = {m.qualname: parse_file(m.path) for m in modules}
    reexport_maps = _build_reexport_maps(modules, parse_results, qualname_set)

    parse_errors: list[str] = []
    for m in modules:
        result = parse_results[m.qualname]
        if result.has_errors:
            parse_errors.append(m.qualname)
        for ref in result.imports:
            for target in _resolve_targets(ref, m, qualname_set, reexport_maps):
                if target not in graph:
                    graph.add_node(target, external=True)
                _add_or_extend_edge(graph, m.qualname, target, ref)

    graph.graph["root"] = str(root)
    graph.graph["parse_errors"] = tuple(sorted(parse_errors))
    return graph


def graph_to_dict(graph: nx.DiGraph) -> dict:
    """Serialize a graph to the JSON shape emitted by `archy graph --format json`.

    Per-node `instability` (Martin's `I`) and `propagation_cost` (MacCormack
    reverse-reach fraction) are attached to internal nodes only; external
    modules have no meaningful values within the project. When called on
    a subgraph (e.g. by `archy_graph_focus`), values are computed relative
    to that subgraph's scope, not the full project. For the canonical
    project-wide propagation cost, read `archy score`'s `inputs.propagation_cost`.
    """
    instability = compute_instability(graph)
    _, propagation_cost = compute_propagation_cost(graph)
    return {
        "root": graph.graph.get("root"),
        "parse_errors": list(graph.graph.get("parse_errors", ())),
        "nodes": [
            {
                "id": n,
                **d,
                **({"instability": instability[n]} if n in instability else {}),
                **({"propagation_cost": propagation_cost[n]} if n in propagation_cost else {}),
            }
            for n, d in sorted(graph.nodes(data=True))
        ],
        "edges": [
            {"source": u, "target": v, **d}
            for u, v, d in sorted(graph.edges(data=True), key=lambda e: (e[0], e[1]))
        ],
    }


def resolve_modules(
    graph: nx.DiGraph,
    refs: Iterable[str],
    *,
    project_root: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve qualname-or-path strings to internal qualnames in `graph`.

    Each `refs` entry is treated as a qualname if it matches an internal node
    directly; otherwise as a filesystem path (relative paths are resolved
    against `project_root`, which defaults to `graph.graph["root"]`). Returns
    `(resolved, unresolved)` where `resolved` preserves first-seen order
    deduplicated and `unresolved` lists the original strings that matched
    nothing.
    """
    internal_nodes = {n for n, d in graph.nodes(data=True) if not d.get("external")}
    path_index: dict[Path, str] = {}
    for n in internal_nodes:
        raw = graph.nodes[n].get("path")
        if raw:
            path_index[Path(raw).resolve()] = n

    base = project_root or (Path(graph.graph["root"]) if graph.graph.get("root") else Path.cwd())

    resolved: list[str] = []
    seen: set[str] = set()
    unresolved: list[str] = []
    for ref in refs:
        if ref in internal_nodes:
            if ref not in seen:
                resolved.append(ref)
                seen.add(ref)
            continue
        candidate = Path(ref)
        if not candidate.is_absolute():
            candidate = base / candidate
        qualname = path_index.get(candidate.resolve())
        if qualname is None:
            unresolved.append(ref)
        elif qualname not in seen:
            resolved.append(qualname)
            seen.add(qualname)
    return resolved, unresolved


def _discover_modules(
    root: Path,
    ignored: frozenset[str],
    extra_roots: tuple[str, ...],
) -> list[Module]:
    """Find Python source files and assign dotted module qualnames.

    Package roots are directories containing __init__.py whose parent
    directory does NOT contain one. The conventional `src/<pkg>/__init__.py`
    layout is supported because `src` itself is not a package.
    """
    package_roots = _find_package_roots(root, ignored, extra_roots)
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


def _find_package_roots(
    root: Path,
    ignored: frozenset[str],
    extra_roots: tuple[str, ...],
) -> list[Path]:
    """Return absolute paths to top-level package directories under root."""
    package_dirs: set[Path] = set()
    for path in root.rglob("__init__.py"):
        if _is_ignored(path, ignored):
            continue
        package_dirs.add(path.parent.resolve())

    # User-declared namespace package roots: treat the directory as a package
    # even without __init__.py, so descendants get qualnames rooted there.
    # Adding it to package_dirs also demotes any already-discovered child
    # packages (their parent is now a package_dir, so they stop being roots),
    # which is exactly the nesting we want.
    for r in extra_roots:
        candidate = (root / r).resolve()
        if candidate.is_dir():
            package_dirs.add(candidate)

    roots: list[Path] = []
    for pkg in package_dirs:
        if pkg.parent.resolve() not in package_dirs:
            roots.append(pkg)
    roots.sort()
    return roots


def _iter_python_files(root: Path, ignored: frozenset[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if _is_ignored(path, ignored):
            continue
        yield path


def _is_ignored(path: Path, ignored: frozenset[str]) -> bool:
    return any(part in ignored for part in path.parts)


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
    reexport_maps: dict[str, dict[str, str]],
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
        return _expand_with_imported_names(
            base, ref.imported_names, internal_qualnames, reexport_maps
        )

    return _expand_with_imported_names(
        ref.module, ref.imported_names, internal_qualnames, reexport_maps
    )


def _expand_with_imported_names(
    base: str,
    imported_names: tuple[str, ...],
    internal_qualnames: set[str],
    reexport_maps: dict[str, dict[str, str]],
) -> list[str]:
    # `from X import a, b` is statically ambiguous (a, b may be submodules or
    # symbols in X's namespace). When X is internal we prefer submodule edges
    # where they exist; otherwise we consult X's __init__.py re-export map
    # so that `from pkg import Foo` resolves to the file where Foo actually
    # lives rather than the package node (which would manufacture a cycle).
    # For external X we collapse to a single edge to the top-level package.
    base_internal = base in internal_qualnames

    if base_internal and imported_names:
        targets: list[str] = []
        matched_any = False
        reexports = reexport_maps.get(base, {})
        for name in imported_names:
            submodule = f"{base}.{name}"
            if submodule in internal_qualnames:
                targets.append(submodule)
                matched_any = True
            elif name in reexports:
                targets.append(reexports[name])
                matched_any = True
        if not matched_any:
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


def _build_reexport_maps(
    modules: list[Module],
    parse_results: dict[str, ParseResult],
    internal_qualnames: set[str],
) -> dict[str, dict[str, str]]:
    # Re-exports live in package __init__.py files. For each `from .x import Foo`
    # (or absolute self-reference), record `{local_name: source_qualname}` so the
    # consumer-side resolver can route `from pkg import Foo` to `pkg.x` instead
    # of falling back to `pkg`.
    maps: dict[str, dict[str, str]] = {}
    for m in modules:
        if not m.is_package:
            continue
        result = parse_results.get(m.qualname)
        if result is None:
            continue
        pkg_map: dict[str, str] = {}
        for ref in result.imports:
            source = _reexport_source(ref, m, internal_qualnames)
            if source is None:
                continue
            for i, name in enumerate(ref.imported_names):
                alias = ref.imported_aliases[i] if i < len(ref.imported_aliases) else None
                local = alias or name
                pkg_map[local] = source
        if pkg_map:
            maps[m.qualname] = pkg_map
    _follow_reexport_chains(maps)
    return maps


def _follow_reexport_chains(maps: dict[str, dict[str, str]], *, max_depth: int = 8) -> None:
    # If pkg/__init__.py re-exports name X from pkg.sub, and pkg.sub/__init__.py
    # re-exports the same name X from pkg.sub.impl, the consumer's `from pkg
    # import X` should land on pkg.sub.impl. Walk each (pkg, name) -> target
    # in place, capped at max_depth so a malicious cycle (A re-exports from B,
    # B re-exports from A) cannot loop forever.
    for pkg, name_map in maps.items():
        for name, target in name_map.items():
            visited: set[str] = {pkg}
            for _ in range(max_depth):
                deeper = maps.get(target, {}).get(name)
                if deeper is None or deeper in visited:
                    break
                visited.add(target)
                target = deeper
            name_map[name] = target


def _reexport_source(
    ref: ImportRef,
    package: Module,
    internal_qualnames: set[str],
) -> str | None:
    if not ref.imported_names:
        return None
    if ref.is_relative:
        base = _resolve_relative_base(ref.module, package)
    elif ref.module == package.qualname or ref.module.startswith(package.qualname + "."):
        base = ref.module
    else:
        # Re-exports of *external* modules don't help us resolve cycles inside
        # the project, so we skip them.
        return None
    if base is None or base not in internal_qualnames:
        return None
    return base


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
