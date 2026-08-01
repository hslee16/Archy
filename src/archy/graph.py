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

from archy.complexity import FunctionComplexity
from archy.instability import compute_instability
from archy.parser import CallRef, ImportRef, ParseResult, parse_file
from archy.reach import compute_propagation_cost
from archy.risk import compute_edit_risk

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

# Soft ceiling on how many modules a single scan may discover before archy
# refuses to proceed. The named `DEFAULT_IGNORED_DIRS` above catch the standard
# vendored dirs, but a custom cache/generated dir (e.g. a bench `repo_cache`)
# can still pull tens of thousands of files into one scan and wedge the
# superlinear graph metrics for many minutes (see #213/#216). This backstop is
# name-agnostic: it trips on size alone. The default sits well above the largest
# real project archy benches (pytorch ~2,250 modules); callers pass `0`/`None`
# to disable, or override via `max_modules:` in archy.yaml.
DEFAULT_MAX_MODULES = 10_000


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

    Edge attribute `is_relative` indicates whether the import was written
    using leading-dot syntax. Edge attribute `lines` is a tuple of source
    line numbers where the import appears (a single source/target pair may
    occur on multiple lines).
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
    if max_modules and max_modules > 0 and len(modules) > max_modules:
        raise ScanTooLargeError(len(modules), root, max_modules)
    parse_results: dict[str, ParseResult] = {}
    for m in modules:
        try:
            parse_results[m.qualname] = parse_file(m.path)
        except OSError:
            # The file vanished or became unreadable between discovery and parse
            # (a branch switch, a concurrent edit, or the `archy mcp` watcher
            # rebuilding mid-flight). Skip it; assemble_graph drops modules with
            # no parse result, and the next build picks it up once disk settles.
            continue
    return modules, parse_results


def discover_modules(
    root: Path,
    *,
    ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS,
    extra_roots: Iterable[str] = (),
) -> list[Module]:
    """Discover internal modules under `root` (the FS-walk half of `build_graph`).

    Exposed so the persistent index (`archy.index`) can enumerate the module set
    and decide which files to (re)parse without committing to a parse up front.
    """
    return _discover_modules(root, frozenset(ignored_dirs), tuple(extra_roots))


def assemble_graph(
    root: Path,
    modules: list[Module],
    parse_results: dict[str, ParseResult],
) -> nx.DiGraph:
    """Build the import + call graph from already-parsed modules.

    This is the resolution-and-assembly half of `build_graph`, split out so the
    cold path and the cache-backed path (`archy.index.build_graph_cached`) share
    one resolution implementation. Resolution is global (relative imports,
    re-export chains, and alias tables all need the full `parse_results` set), so
    keeping a single code path is what guarantees the cached graph is identical
    to a cold build. `parse_results` is keyed by module qualname; a module with
    no entry (its file vanished between discovery and parse) is dropped here, so
    every caller -- build_graph, build_graph_cached, the mcp watcher -- is safe
    without repeating the guard.
    """
    modules = [m for m in modules if m.qualname in parse_results]
    qualname_set = {m.qualname for m in modules}

    graph: nx.DiGraph = nx.DiGraph()
    for m in modules:
        cc_aggregates = _cc_aggregates(parse_results[m.qualname].functions)
        graph.add_node(
            m.qualname,
            path=str(m.path),
            is_package=m.is_package,
            external=False,
            **cc_aggregates,
        )
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

    # Second pass: call edges. Calls only become an edge if their leftmost
    # identifier resolves through the source module's import alias table, so
    # we depend on the import pass having populated qualname_set + reexport
    # routing first. Call edges land on the longest internal qualname prefix
    # of (alias_target + chain), which can be deeper than the import edge
    # (e.g. `import pkg; pkg.sub.foo()` adds a call edge to pkg.sub even
    # when imports only edged to pkg) - that depth differential is the
    # whole point of LocAgent's invoke-edges signal.
    for m in modules:
        result = parse_results[m.qualname]
        if not result.calls:
            continue
        alias_table = _build_alias_table(result.imports, m, qualname_set, reexport_maps)
        for call in result.calls:
            target = _resolve_call_target(call, alias_table, qualname_set)
            if target is None or target == m.qualname:
                continue
            if target not in graph:
                graph.add_node(target, external=True)
            _add_or_extend_call_edge(graph, m.qualname, target, call)

    graph.graph["root"] = str(root)
    graph.graph["parse_errors"] = tuple(sorted(parse_errors))
    return graph


def graph_to_dict(graph: nx.DiGraph) -> dict:
    """Serialize a graph to the JSON shape emitted by `archy graph --format json`.

    Per-node `instability` (Martin's `I`), `propagation_cost` (MacCormack
    reverse-reach fraction), and `edit_risk` (geometric-mean composite of
    propagation cost, normalized fan-in, and instability) are attached to
    internal nodes only; external modules have no meaningful values within
    the project. When called on a subgraph (e.g. by `archy_graph_focus`),
    values are computed relative to that subgraph's scope, not the full
    project. For the canonical project-wide propagation cost, read
    `archy score`'s `inputs.propagation_cost`.
    """
    instability = compute_instability(graph)
    _, propagation_cost = compute_propagation_cost(graph)
    edit_risk = compute_edit_risk(graph)
    return {
        "root": graph.graph.get("root"),
        "parse_errors": list(graph.graph.get("parse_errors", ())),
        "nodes": [
            {
                "id": n,
                **d,
                **({"instability": instability[n]} if n in instability else {}),
                **({"propagation_cost": propagation_cost[n]} if n in propagation_cost else {}),
                **({"edit_risk": edit_risk[n]} if n in edit_risk else {}),
            }
            for n, d in sorted(graph.nodes(data=True))
        ],
        "edges": [
            {"source": u, "target": v, **d}
            for u, v, d in sorted(graph.edges(data=True), key=lambda e: (e[0], e[1]))
        ],
    }


def internal_subgraph(graph: nx.DiGraph) -> nx.DiGraph:
    """A copy of `graph` with external nodes removed.

    Score, cycles and the DSM all want internal-only input, and the CLI and MCP
    boundaries each need to derive one alongside the full graph (required-reach
    rules may name an external package, so the externals have to survive
    somewhere). Shared so the definition of "external" lives in one place: both
    boundaries strip on the same rule, and a future change to how nodes are
    classified cannot land in one and miss the other.
    """
    internal = graph.copy()
    internal.remove_nodes_from([n for n, d in graph.nodes(data=True) if d.get("external")])
    return internal


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
    return [_external_target(base, internal_qualnames)]


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
    if walk_up >= len(src_parts):
        # `==` already consumes the whole package path and lands at the project
        # root, so any remaining suffix attaches to a bare name outside the
        # project (e.g. `from ...x` in a 2-deep package). Python rejects this
        # same import at runtime; dropping it avoids injecting a phantom node.
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
        kinds = data.get("kinds") or ("import",)
        if "import" not in kinds:
            data["kinds"] = (*kinds, "import")
    else:
        graph.add_edge(
            src,
            dst,
            is_relative=ref.is_relative,
            lines=(ref.line,),
            kinds=("import",),
        )


def _add_or_extend_call_edge(
    graph: nx.DiGraph,
    src: str,
    dst: str,
    call: CallRef,
) -> None:
    """Attach call-site data to an edge, creating it if no import edge exists.

    Call-only edges (kinds=('call',)) appear when calls resolve to a deeper
    submodule than the import edge - e.g., `import pkg; pkg.sub.foo()`
    creates a call edge to pkg.sub on top of the import edge to pkg.
    """
    if graph.has_edge(src, dst):
        data = graph[src][dst]
        data["call_lines"] = (*data.get("call_lines", ()), call.line)
        data["call_count"] = data.get("call_count", 0) + 1
        kinds = data.get("kinds") or ("import",)
        if "call" not in kinds:
            data["kinds"] = (*kinds, "call")
    else:
        graph.add_edge(
            src,
            dst,
            is_relative=False,
            lines=(),
            kinds=("call",),
            call_lines=(call.line,),
            call_count=1,
        )


def _build_alias_table(
    imports: tuple[ImportRef, ...],
    source_module: Module,
    internal_qualnames: set[str],
    reexport_maps: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Map each name a source module has in scope after its imports to its target qualname.

    Mirrors `_expand_with_imported_names`'s routing decisions so call
    resolution and import resolution agree on where each name comes from.
    `from X import a, b` populates one entry per imported name; `import
    X.Y` binds only the top-level name `X` per Python's actual import
    semantics. Function-local imports are merged into the module-level
    table - this slightly over-resolves (an inner-scope binding can leak
    to outer call sites in our static view), matching the import graph's
    existing behavior on function-local imports.
    """
    table: dict[str, str] = {}
    for ref in imports:
        if ref.is_relative:
            base = _resolve_relative_base(ref.module, source_module)
            if base is None:
                continue
        else:
            base = ref.module
        if ref.imported_names:
            base_internal = base in internal_qualnames
            reexports = reexport_maps.get(base, {}) if base_internal else {}
            for i, name in enumerate(ref.imported_names):
                alias = ref.imported_aliases[i] if i < len(ref.imported_aliases) else None
                local = alias or name
                if not local:
                    continue
                if base_internal:
                    submodule = f"{base}.{name}"
                    if submodule in internal_qualnames:
                        table[local] = submodule
                    elif name in reexports:
                        table[local] = reexports[name]
                    else:
                        table[local] = base
                else:
                    table[local] = _external_target(base, internal_qualnames)
        else:
            alias = ref.imported_aliases[0] if ref.imported_aliases else None
            if alias:
                # `import X.Y as Z` binds Z to the deepest module (X.Y), unlike
                # bare `import X.Y` which only binds the top-level name X --
                # the alias short-circuits Python's attribute-walk semantics.
                if base in internal_qualnames:
                    table[alias] = base
                else:
                    table[alias] = _external_target(base, internal_qualnames)
            else:
                # `import X.Y` binds only the top-level name `X` per Python's
                # actual import semantics (X.Y is accessed via attribute on X).
                top = base.split(".")[0]
                if not top:
                    continue
                if top in internal_qualnames:
                    table[top] = top
                else:
                    table[top] = _external_target(top, internal_qualnames)
    return table


def _external_target(base: str, internal_qualnames: set[str]) -> str:
    """Collapse an external dotted path to the longest internal prefix or top-level pkg."""
    parts = base.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in internal_qualnames:
            return candidate
    return parts[0]


def _resolve_call_target(
    call: CallRef,
    alias_table: dict[str, str],
    internal_qualnames: set[str],
) -> str | None:
    """Resolve a CallRef to the target module qualname, or None if unresolvable.

    The leftmost identifier (`call.head`) must appear in the alias table -
    we don't try to follow class-attribute or assignment chains. The
    chain segments before the trailing function name are then walked
    against `internal_qualnames` to find the longest internal prefix
    (`import pkg; pkg.sub.foo()` resolves to `pkg.sub` rather than the
    import-edge target `pkg`). When no deeper internal match exists we
    fall back to the alias-table target, which is already a node in the
    graph (it was the import target).
    """
    base = alias_table.get(call.head)
    if base is None:
        return None
    base_parts = base.split(".")
    # The trailing chain segment is the function name being called; a module
    # itself isn't callable, so don't extend the candidate qualname through it.
    chain_for_module = list(call.chain[:-1]) if call.chain else []
    extended = base_parts + chain_for_module
    for end in range(len(extended), len(base_parts), -1):
        candidate = ".".join(extended[:end])
        if candidate in internal_qualnames:
            return candidate
    return base


def _cc_aggregates(functions: tuple[FunctionComplexity, ...]) -> dict[str, int | float]:
    """Per-module CC roll-up: function count, sum, max, and mean.

    Empty / no-function modules (e.g. plain `__init__.py`, type-only stub
    modules) get function_count=0 and the rest 0; downstream consumers
    treat 0 as "no signal" rather than "perfectly simple."
    """
    if not functions:
        return {"function_count": 0, "cc_sum": 0, "cc_max": 0, "cc_mean": 0.0}
    counts = [f.cyclomatic for f in functions]
    total = sum(counts)
    return {
        "function_count": len(counts),
        "cc_sum": total,
        "cc_max": max(counts),
        "cc_mean": total / len(counts),
    }
