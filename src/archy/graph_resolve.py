"""Resolving imports and call targets to modules, including re-exports and aliases.

archy:owns        resolve_from_import, resolve_relative_import
archy:mirrored-by _follow_reexport_chains -> archy.conventions_collect, archy.graph,
                  resolve_from_import -> archy.conventions_collect, archy.graph,
                  resolve_relative_import -> archy.conventions_collect, archy.graph
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from archy.graph_models import Module
from archy.parser import CallRef, ImportRef, ParseResult


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
        _src_layout_base(ref.module, internal_qualnames),
        ref.imported_names,
        internal_qualnames,
        reexport_maps,
    )


def resolve_from_import(
    base: str,
    imported_names: Iterable[str],
    known: set[str] | frozenset[str],
    reexports: dict[str, dict[str, str]],
) -> list[str]:
    """Which internal modules `from base import ...` actually reaches.

    🔴 SHARED BECAUSE THE TWO CALLERS MUST AGREE. `archy.conventions_collect` answers
    the same question for `conventions --module`, and when it had its own copy
    the two disagreed five separate ways (#414), every one a case where the
    lookup contradicted `archy impact` on archy's own source. A lookup whose
    purpose is being trusted in the negative cannot disagree with the graph it
    sits beside, so the decision lives here once.

    `from pkg import submodule` is an edge to the SUBMODULE, not to `pkg`.
    A name the package re-exports routes to the module that defines it. The
    bare package is the fallback only when neither applies, which is when the
    package really is the thing imported, including `from . import NAME` where
    NAME is defined directly in the `__init__.py`.
    """
    targets: list[str] = []
    pkg_map = reexports.get(base, {})
    for name in imported_names:
        target = _resolve_imported_name(base, name, known, pkg_map)
        if target is not None:
            targets.append(target)
    if not targets and base in known:
        targets.append(base)
    return targets


def _resolve_imported_name(
    base: str,
    name: str,
    known: set[str] | frozenset[str],
    pkg_map: dict[str, str],
) -> str | None:
    """Where `name` in `from base import name` lands, or None when neither rule applies.

    One copy of the routing decision, because `resolve_from_import` (edges) and
    `_build_alias_table` (call resolution) each need it and must not drift: an
    import edge and a call edge for the same name that disagreed would put two
    different targets in front of every downstream metric. The None case is
    deliberately left to the caller, since the edge path drops the name and
    only falls back to `base` when NO name resolved, while the alias path binds
    the local name to `base` immediately.
    """
    submodule = f"{base}.{name}"
    if submodule in known:
        return submodule
    return pkg_map.get(name)


def resolve_relative_import(module: str | None, level: int, package: str) -> str | None:
    """Turn `from . import x` / `from ..y import z` into an absolute dotted name.

    Shared for the same reason as `resolve_from_import`: a relative import is
    invisible to a plain name match, so a census that resolved it differently
    from the graph would answer "no, it does not import that" about a module
    that plainly does.

    `package` is the CONTAINING package, so a caller holding a module rather
    than a package strips the last segment before calling. None means the
    import walks past the project root; Python rejects it at runtime too, and
    returning the bare suffix would invent an edge to whatever top-level module
    happened to share the name.
    """
    if not level:
        return module or None
    parts = package.split(".") if package else []
    up = level - 1  # `from .` stays in the current package
    if up >= len(parts):
        return None
    base = ".".join(parts[: len(parts) - up])
    joined = f"{base}.{module}" if module else base
    return joined or None


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
        return resolve_from_import(base, imported_names, internal_qualnames, reexport_maps)

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

    # This side carries the dots inside the raw module string and holds a
    # Module rather than a package name, so it adapts both before handing the
    # decision to the shared resolver.
    src_parts = source_module.qualname.split(".")
    if not source_module.is_package:
        src_parts = src_parts[:-1]
    return resolve_relative_import(suffix or None, leading_dots, ".".join(src_parts))


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
        pkg_map = _package_reexports(m, result, internal_qualnames)
        if pkg_map:
            maps[m.qualname] = pkg_map
    _follow_reexport_chains(maps)
    return maps


def _package_reexports(
    package: Module, result: ParseResult, internal_qualnames: set[str]
) -> dict[str, str]:
    pkg_map: dict[str, str] = {}
    for ref in result.imports:
        source = _reexport_source(ref, package, internal_qualnames)
        if source is None:
            continue
        for _, local in _local_bindings(ref):
            pkg_map[local] = source
    return pkg_map


def _follow_reexport_chains(maps: dict[str, dict[str, str]], *, max_depth: int = 8) -> None:
    # If pkg/__init__.py re-exports name X from pkg.sub, and pkg.sub/__init__.py
    # re-exports the same name X from pkg.sub.impl, the consumer's `from pkg
    # import X` should land on pkg.sub.impl. Walk each (pkg, name) -> target
    # in place, capped at max_depth so a malicious cycle (A re-exports from B,
    # B re-exports from A) cannot loop forever.
    for pkg, name_map in maps.items():
        for name, target in name_map.items():
            name_map[name] = _final_reexport_target(maps, (pkg, name), target, max_depth)


def _final_reexport_target(
    maps: dict[str, dict[str, str]], origin: tuple[str, str], target: str, max_depth: int
) -> str:
    pkg, name = origin
    visited: set[str] = {pkg}
    for _ in range(max_depth):
        deeper = maps.get(target, {}).get(name)
        if deeper is None or deeper in visited:
            break
        visited.add(target)
        target = deeper
    return target


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


def _build_alias_table(
    imports: tuple[ImportRef, ...],
    source_module: Module,
    internal_qualnames: set[str],
    reexport_maps: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Map each name a source module has in scope after its imports to its target qualname.

    Shares `_resolve_imported_name` with the import-edge path so call
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
            base = _src_layout_base(ref.module, internal_qualnames)
        if ref.imported_names:
            table.update(_from_import_bindings(ref, base, internal_qualnames, reexport_maps))
            continue
        binding = _plain_import_binding(ref, base, internal_qualnames)
        if binding is not None:
            table[binding[0]] = binding[1]
    return table


def _from_import_bindings(
    ref: ImportRef,
    base: str,
    internal_qualnames: set[str],
    reexport_maps: dict[str, dict[str, str]],
) -> dict[str, str]:
    """One entry per name `from base import ...` binds, in source order."""
    base_internal = base in internal_qualnames
    pkg_map = reexport_maps.get(base, {}) if base_internal else {}
    bindings: dict[str, str] = {}
    named = [(name, local) for name, local in _local_bindings(ref) if local]
    for name, local in named:
        if base_internal:
            resolved = _resolve_imported_name(base, name, internal_qualnames, pkg_map)
            bindings[local] = base if resolved is None else resolved
        else:
            bindings[local] = _external_target(base, internal_qualnames)
    return bindings


def _plain_import_binding(
    ref: ImportRef, base: str, internal_qualnames: set[str]
) -> tuple[str, str] | None:
    """The (name, target) a plain `import X.Y [as Z]` binds, or None if it binds nothing."""
    alias = ref.imported_aliases[0] if ref.imported_aliases else None
    if alias:
        # `import X.Y as Z` binds Z to the deepest module (X.Y), unlike
        # bare `import X.Y` which only binds the top-level name X --
        # the alias short-circuits Python's attribute-walk semantics.
        return alias, _external_target(base, internal_qualnames)
    # `import X.Y` binds only the top-level name `X` per Python's
    # actual import semantics (X.Y is accessed via attribute on X).
    top = ref.module.split(".")[0]
    if not top:
        return None
    return top, _external_target(_src_layout_base(top, internal_qualnames), internal_qualnames)


def _local_bindings(ref: ImportRef) -> Iterator[tuple[str, str]]:
    """Yield `(imported_name, local_name)` for each name a `from X import ...` binds.

    `imported_aliases` may be shorter than `imported_names`; a name with no
    alias binds under itself. Kept in one place so the re-export map and the
    call-resolution alias table cannot disagree on that padding rule.
    """
    for i, name in enumerate(ref.imported_names):
        alias = ref.imported_aliases[i] if i < len(ref.imported_aliases) else None
        yield name, alias or name


def _src_layout_base(module: str, internal_qualnames: set[str]) -> str:
    """Resolve `infra.x` to `src.infra.x` when `src/` is a package used as the import root.

    A `src/__init__.py` makes archy name every module `src.*`, while a src-layout
    project imports them as `infra.*`, so each such import used to resolve external
    and the graph lost its edges (#853: definit-dsa 0 internal edges, ruff 2563).
    `ruff analyze graph` puts `src` on its import path by default, so this matches it.

    Only an import whose top-level name is NOT itself internal is rewritten, so a real
    top-level package of the same name still wins, and `from src.x import y` is untouched.
    """
    top = module.split(".", 1)[0]
    if top in internal_qualnames or f"src.{top}" not in internal_qualnames:
        return module
    return f"src.{module}"


def _external_target(base: str, internal_qualnames: set[str]) -> str:
    """Collapse a dotted path to its longest internal prefix, else its top-level pkg.

    Returns `base` itself when `base` is internal, so callers need no
    `in internal_qualnames` branch of their own."""
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
