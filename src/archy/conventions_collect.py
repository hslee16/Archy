"""Reading a project's modules into the facts every census works from.

archy:owns        censused_modules
archy:mirrored-by _ModuleFacts -> archy.conventions, archy.conventions_docs,
                  archy.conventions_families, archy.conventions_gates,
                  _dotted -> archy.conventions_families, archy.conventions_gates,
                  censused_modules -> archy.conventions, archy.headers
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from archy.conventions_models import UnparsedModule
from archy.graph import (
    Module,
    _follow_reexport_chains,
    resolve_from_import,
    resolve_relative_import,
)


def _dotted(node: ast.expr) -> str:
    """Render `a.b.c` / `a` from an expression, or "" for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class _ModuleFacts:
    """Everything one parsed module contributes, collected in a single pass."""

    def __init__(self, qualname: str) -> None:
        self.qualname = qualname
        self.classes: list[ast.ClassDef] = []
        self.functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        # Module-level assignments only, not `ast.walk`: a registry is a
        # top-level declaration, and a same-shaped call inside a function
        # body is a local variable that means nothing about house style.
        self.body_assignments: list[ast.Assign | ast.AnnAssign] = []
        # None means the module declares no `__all__` at all, which is a
        # different fact from declaring an empty one.
        self.exports: frozenset[str] | None = None
        # Names this module pulls in by `from ... import X`. Kept unfiltered;
        # `_surface_families` decides which of them this project defines.
        # (source module, name). The module is "" for a relative import, which
        # is not resolved here: a name that is unambiguous project-wide does not
        # need it, and one that is not must not be guessed at. See
        # `_surface_families`.
        self.internal_imports: set[tuple[str, str]] = set()
        # The MODULES this one imports, dotted and absolute, with relative form
        # resolved. Deliberately separate from `internal_imports` above, which
        # holds SYMBOLS and leaves `from . import x` unresolved on purpose:
        # "does `risk` import `hotspots`" is a question about modules, and
        # answering it from symbols alone would miss both `import pkg.mod` and
        # every relative import in the project.
        self.imported_modules: set[str] = set()
        # One entry per `from X import ...`: the resolved base and the (name,
        # local-name) pairs. Kept unresolved because whether a name is a
        # submodule, a re-export or a plain symbol depends on the project's
        # module set; see `_resolved_imports`.
        self.import_froms: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        # Whether this module is a package's `__init__.py`. Only those can
        # re-export, so only those contribute to the re-export map.
        self.is_package = False


def _collect(tree: ast.Module, qualname: str, *, is_package: bool = False) -> _ModuleFacts:
    facts = _ModuleFacts(qualname)
    facts.is_package = is_package
    _collect_definitions(tree, facts)
    _collect_imports(tree, facts, qualname, is_package)
    _collect_assignments(tree, facts)
    if facts.exports is None:
        reexports = _redundant_alias_reexports(tree)
        if reexports:
            facts.exports = frozenset(reexports)
    return facts


def _collect_definitions(tree: ast.Module, facts: _ModuleFacts) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            facts.classes.append(node)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            facts.functions.append(node)


def _collect_imports(
    tree: ast.Module, facts: _ModuleFacts, qualname: str, is_package: bool
) -> None:
    # The package a relative import is relative to. For a package's own
    # `__init__.py` that is the qualname itself, because Python sets
    # `__package__ == __name__` there; stripping a level as if it were a plain
    # submodule resolves `from . import x` one level too shallow, and the bogus
    # name is then filtered out as unknown -- a silent false negative in the
    # aggregating `__init__.py` that is the commonest place to see one.
    pkg = qualname if is_package else (qualname.rsplit(".", 1)[0] if "." in qualname else "")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            _collect_import_from(node, facts, pkg)


def _collect_import_from(node: ast.ImportFrom, facts: _ModuleFacts, pkg: str) -> None:
    source = "" if node.level else (node.module or "")
    for alias in node.names:
        if alias.name != "*":
            facts.internal_imports.add((source, alias.name))
    base = _resolve_relative(node.module, node.level, pkg)
    if base:
        # `from X import a, b` is statically ambiguous: the names may be
        # submodules or symbols in X's namespace. Which one cannot be
        # decided here, because it depends on what the project defines,
        # so the statement is recorded whole and resolved by
        # `_resolved_imports` once the module set is known. `node.module`
        # is carried because `from . import sibling` imports the sibling
        # and must never fall back to the containing package.
        facts.import_froms.append(
            (base, tuple((a.name, a.asname or a.name) for a in node.names if a.name != "*"))
        )


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """The plain names a module-level assignment binds."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in targets if isinstance(t, ast.Name)]


def _collect_assignments(tree: ast.Module, facts: _ModuleFacts) -> None:
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        facts.body_assignments.append(node)
        if "__all__" in _assigned_names(node):
            facts.exports = _string_literals(node.value)


def _redundant_alias_reexports(tree: ast.Module) -> set[str]:
    """Names a module re-exports with the redundant-alias form, absent `__all__`.

    `click` publishes its API with `from .x import Y as Y` and no `__all__` at
    all -- the PEP 484 explicit re-export form, which type checkers treat as
    public. Reading only `__all__` would report that such a package exports
    nothing, so a missing member would never surface. Only the redundant-alias
    form counts: a plain `from .x import Y` is an implementation import, not a
    promise.
    """
    return {
        alias.asname
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname and alias.asname == alias.name
    }


def _resolve_relative(module: str | None, level: int, pkg: str) -> str:
    """`graph.resolve_relative_import` in this module's vocabulary.

    The decision is not duplicated here; only the empty-string convention this
    file's callers expect, where the graph uses None for an import that walks
    past the project root.
    """
    return resolve_relative_import(module, level, pkg) or ""


def _string_literals(node: ast.expr | None) -> frozenset[str]:
    """The string constants of a list/tuple literal. Anything dynamic yields
    what could be read statically, never a guess about the rest."""
    if not isinstance(node, ast.List | ast.Tuple):
        return frozenset()
    return frozenset(
        e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
    )


# --------------------------------------------------------------------------
# Which modules the house style actually lives in.
#
# A census over every file a project ships answers a different question
# from the one an agent is asking. Test modules carry their own strong and
# deliberately throwaway conventions -- ``test_*`` functions, ``Foo`` and
# ``MyModel`` fixtures -- and there are usually more of them than of the
# thing being described, so they win every count they are entered in.
# Measured across four projects, the top mirrored "surface" was a test
# fixture in four cases out of four.
#
# Vendored copies are worse than noisy, because they are plausible. They
# duplicate the parent package's module names by construction, so the
# duplicate outranks the original on volume and the census names the dead
# copy as home.
_TEST_PARTS = frozenset({"tests", "test", "testing", "_test", "_tests"})


def _is_nonsource_module(qualname: str) -> bool:
    """Modules under a dot-directory: tooling that ships in the repo but is not
    the library. `pydantic` keeps a GitHub Action in `.github/actions/`, whose
    classes were outranking `pydantic`'s own on a `BaseModel` census."""
    return qualname.startswith(".") or any(part.startswith(".") for part in qualname.split("."))


def _is_test_module(qualname: str) -> bool:
    parts = qualname.split(".")
    if _TEST_PARTS & set(parts):
        return True
    # A directory name is not always a Python identifier: `mypyc/test-data/`
    # holds typeshed fixtures that redefine `RuntimeError` and `LookupError`,
    # and they were being reported as this project's own kind families.
    if any(part.startswith(("test-", "test_", "tests-", "_test")) for part in parts[:-1]):
        return True
    leaf = parts[-1]
    return leaf.startswith("test_") or leaf.endswith("_test") or leaf == "conftest"


def _shadow_roots(
    qualnames: Iterable[str], *, min_modules: int = 5, ratio: float = 0.5
) -> frozenset[str]:
    """Subpackages that re-implement their own parent, found by overlap.

    Derived rather than pattern-matched on purpose. Hardcoding ``v1`` would
    catch ``pydantic`` and nothing else, and would be wrong for a project
    whose ``v1`` is the live one. A subtree that restates half its parent's
    module names under a second prefix is a copy whatever it is called.
    """
    names = list(qualnames)
    by_prefix = _inner_names_by_prefix(names)
    shadows = set()
    for prefix, inner in by_prefix.items():
        if len(inner) < min_modules:
            continue
        overlap = len(inner & _names_outside_prefix(prefix, names))
        if overlap / len(inner) >= ratio:
            shadows.add(prefix)
    return frozenset(shadows)


def _inner_names_by_prefix(names: list[str]) -> dict[str, set[str]]:
    """`{pkg.sub: {names below it, with that prefix stripped}}` for names 3+ deep."""
    by_prefix: dict[str, set[str]] = defaultdict(set)
    for q in names:
        parts = q.split(".")
        if len(parts) >= 3:
            by_prefix[".".join(parts[:2])].add(".".join(parts[2:]))
    return by_prefix


def _names_outside_prefix(prefix: str, names: list[str]) -> set[str]:
    """Names under the same top-level package as `prefix` but not under `prefix`."""
    root = prefix.split(".")[0]
    return {
        q[len(root) + 1 :]
        for q in names
        if not q.startswith(prefix + ".") and q.startswith(root + ".")
    }


def _reexport_maps(
    facts: dict[str, _ModuleFacts], known: frozenset[str]
) -> dict[str, dict[str, str]]:
    """`{package: {exported name: the module it actually lives in}}`.

    Re-exports live in package `__init__.py` files. Without this map, `from
    archy.install import run_install` resolves to the package `archy.install`,
    and the module that really defines `run_install` (`archy.install.runner`)
    never appears -- a FALSE NEGATIVE on archy's own source, which `archy
    impact` gets right. Mirrors `graph._build_reexport_maps`; the two must
    agree, because a lookup that contradicts the dependency graph is worse
    than no lookup.
    """
    maps: dict[str, dict[str, str]] = {}
    for qualname, f in facts.items():
        if not f.is_package:
            continue
        pkg_map = _package_reexports(qualname, f, known)
        if pkg_map:
            maps[qualname] = pkg_map
    # The graph's own walker, not a copy of it. The two resolvers have to agree
    # about where a re-exported name lives, and a second hand-synchronized
    # implementation is exactly the drift this module already had to fix.
    _follow_reexport_chains(maps)
    return maps


def _package_reexports(qualname: str, f: _ModuleFacts, known: frozenset[str]) -> dict[str, str]:
    """The names one package re-exports, each mapped to the submodule it came from."""
    pkg_map: dict[str, str] = {}
    for base, names in f.import_froms:
        # Only a SELF-referential import re-exports. `graph._reexport_source`
        # skips anything else, on the grounds that re-exporting a module from
        # outside the package says nothing about where the package's own names
        # live; treating it as a re-export routed `from pkgA import Thing` to
        # `pkgB.impl` and reported nothing importing `pkgA`, where the graph
        # reports the `pkgA` edge. A relative import always lands inside the
        # package by construction, so this one check covers both forms.
        if base != qualname and not base.startswith(qualname + "."):
            continue
        # `from .x import y` re-exports `y` from the submodule `x`, always.
        # Whether `x.y` is a module or a symbol `x` defines is not in the
        # syntax: Python prefers the attribute, so a symbol `y` shadows a
        # sibling `x/y.py`, and mapping to `x.y` would invent an import
        # the program never makes (#436). `x` is an edge in both cases.
        if base in known and base != qualname:
            pkg_map.update({local: base for _name, local in names})
    return pkg_map


def _resolved_imports(
    facts: _ModuleFacts, known: frozenset[str], reexports: dict[str, dict[str, str]]
) -> set[str]:
    """The project modules `facts` imports, resolved against what exists.

    The resolution itself is `graph.resolve_from_import`, not a copy of it, so
    this lookup and archy's own dependency graph cannot give different answers.
    They did, five ways, before the algorithm was shared (#414, #419).
    """
    resolved = {m for m in facts.imported_modules if m in known}
    for base, names in facts.import_froms:
        resolved |= set(resolve_from_import(base, (n for n, _ in names), known, reexports))
    return resolved - {facts.qualname}


def _unparsed_reason(exc: BaseException) -> str:
    """One-line reason naming the exception and, for SyntaxError, its locus."""
    if isinstance(exc, SyntaxError):
        where = f" (line {exc.lineno})" if exc.lineno is not None else ""
        msg = exc.msg or str(exc)
        return f"{type(exc).__name__}: {msg}{where}"
    return f"{type(exc).__name__}: {exc}"


def _try_collect_detailed(
    module: Module,
    *,
    root: Path | None = None,
) -> tuple[_ModuleFacts | None, UnparsedModule | None]:
    """Parse one module, or an UnparsedModule naming why it was skipped.

    Advisory command: an unreadable or half-written file is a fact to report,
    never a reason to fail. `_collect_all` is the one place that calls this, so
    the two surfaces that census a project cannot drift on WHICH failures are
    tolerated -- a narrower except tuple in one of them would turn a
    work-in-progress file into a crash on only one of the two surfaces.
    """
    try:
        # Bytes, not read_text("utf-8"): ast.parse honours the PEP 263 cookie,
        # so a Latin-1 module is parsed instead of being reported as unparsed
        # (#649).
        tree = ast.parse(module.path.read_bytes())
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError) as exc:
        rel: str
        if root is not None:
            try:
                rel = str(module.path.resolve().relative_to(root.resolve()))
            except ValueError:
                rel = str(module.path)
        else:
            rel = str(module.path)
        skipped = UnparsedModule(
            module=module.qualname,
            path=rel,
            reason=_unparsed_reason(exc),
            python=sys.version.split()[0],
        )
        return None, skipped
    return _collect(tree, module.qualname, is_package=module.is_package), None


def _classify_module(
    qualname: str, shadows: frozenset[str], *, include_tests: bool
) -> tuple[str, str]:
    """Which partition bucket `qualname` falls in, and why, in ONE check order.

    Both the ranked census and the single-module lookup have to answer this,
    and they used to answer it separately. The copies drifted in check order,
    so a test module inside a shadow root was counted as a test by one and
    reported as shadowed by the other: the same module, two different answers
    about why it was set aside. A lookup whose whole purpose is being trusted
    in the negative cannot afford to disagree with the census it sits beside,
    so the order lives here once and neither caller gets its own.
    """
    if _is_nonsource_module(qualname):
        return "nonsource", "set aside: under a dot-directory, not library source"
    if not include_tests and _is_test_module(qualname):
        return "test", "set aside: a test module -- pass --include-tests to census it"
    root_of = next(
        (sh for sh in sorted(shadows) if qualname == sh or qualname.startswith(sh + ".")), None
    )
    if root_of:
        return "shadowed", f"set aside: in {root_of}, which duplicates its parent"
    return "censused", "censused"


def censused_modules(qualnames: Iterable[str], *, include_tests: bool = False) -> frozenset[str]:
    """The subset of `qualnames` the census actually looked at.

    A third caller of `_classify_module`, for the same reason the other two
    share it: a surface that reports on modules the census set aside is
    reporting about a population the census never measured. `--emit-headers`
    got this wrong first time and wrote a derived block into all 143 modules,
    tests included, while the census had looked at 79 of them.
    """
    names = list(qualnames)
    shadows = _shadow_roots(names)
    return frozenset(
        name
        for name in names
        if _classify_module(name, shadows, include_tests=include_tests)[0] == "censused"
    )


def _collect_all(
    modules: Iterable[Module], root: Path
) -> tuple[list[_ModuleFacts], list[UnparsedModule]]:
    """Parse every module; the facts of those that parsed and a record of those that did not.

    One loop for `compute_conventions` and `compute_module_view`, so the two cannot
    drift on how a module that fails to parse is recorded (#874).
    """
    facts: list[_ModuleFacts] = []
    skipped: list[UnparsedModule] = []
    for module in modules:
        collected, miss = _try_collect_detailed(module, root=root)
        if collected is None:
            assert miss is not None
            skipped.append(miss)
        else:
            facts.append(collected)
    return facts, skipped
