"""Finding a project's Python files and naming each one: package roots, qualnames, ignore rules.

archy:owns        discover_modules, is_ignored_path
archy:mirrored-by _relative_paths -> archy.graph, archy.graph_assemble,
                  is_ignored_path -> archy.graph, archy.watcher
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from archy.graph_models import (
    _EXTENSION_SUFFIXES_BY_LENGTH,
    DEFAULT_IGNORED_DIRS,
    HARD_IGNORED_DIRS,
    SOFT_IGNORED_DIRS,
    Module,
    QualnameCollisionError,
)


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


def _relative_paths(root: Path, modules: list[Module]) -> dict[str, str]:
    """`{qualname: repo-relative POSIX path}` for every module under `root` (#522).

    An import edge's `lines` are line numbers in the IMPORTING module's file, so a
    finding looks its source module up here to say which file those lines are in.
    Relative to the scan root, so a src layout yields `src/pkg/b.py` and a
    package's own module is its `__init__.py`. The root is resolved because module
    paths are, and a relative root (`archy check .`) or a symlinked checkout would
    otherwise never match. A module outside the root is left out, and a finding
    for it prints as before.

    Graph-level, not a per-node attribute, for the reason the `entrypoints` block
    below gives: a node attribute would reach every node of `archy graph --format
    json`. It also keeps `cycles` and `layers` free of an import of this module,
    which would deepen every chain through them.
    """
    resolved_root = root.resolve()
    paths: dict[str, str] = {}
    for m in modules:
        try:
            paths[m.qualname] = m.path.relative_to(resolved_root).as_posix()
        except ValueError:
            continue
    return paths


def _discover_modules(
    root: Path,
    ignored: frozenset[str],
    extra_roots: tuple[str, ...],
) -> list[Module]:
    """Find Python source files and assign dotted module qualnames.

    Package roots are directories containing __init__.py whose parent
    directory does NOT contain one. The conventional `src/<pkg>/__init__.py`
    layout is supported because `src` itself is not a package.

    Compiled extension modules (``*.so`` / ``*.pyd`` via
    ``importlib.machinery.EXTENSION_SUFFIXES``) and ``*.pyi`` stubs with no
    ``.py`` twin are recorded as internal leaf modules so imports of them
    resolve to the leaf rather than collapsing onto the package (#665).
    """
    package_roots = _find_package_roots(root, ignored, extra_roots)
    modules, outside = _package_modules(root, ignored, package_roots)

    # Test files no package root names (an init-less `tests/` beside a `src/`
    # layout) get their path from the scan root, as a flat layout already gives
    # them. Only test files: `docs/conf.py`, `noxfile.py` and `examples/` are
    # scripts nothing imports, and naming them would move module counts and
    # scores for a reason that has nothing to do with test selection. Last, so a
    # fallback name that equals a real module is reported by
    # `_raise_on_qualname_collision` below rather than replacing it (#529, #531).
    modules.extend(_fallback_test_modules(outside, root))

    _raise_on_qualname_collision(modules, root)

    seen = {m.qualname for m in modules}
    modules.extend(_leaf_modules(root, ignored, package_roots, seen))

    modules.sort(key=lambda m: m.qualname)
    return modules


def _package_modules(
    root: Path, ignored: frozenset[str], package_roots: list[Path]
) -> tuple[list[Module], list[Path]]:
    """Modules a package root names, and the `.py` files none of them does."""
    modules: list[Module] = []
    outside: list[Path] = []
    for py_file in _iter_python_files(root, ignored):
        qualname = _qualname_for(py_file, package_roots)
        if qualname is None:
            outside.append(py_file)
            continue
        modules.append(
            Module(
                qualname=qualname,
                path=py_file.resolve(),
                is_package=py_file.name == "__init__.py",
            )
        )
    return modules, outside


def _fallback_test_modules(outside: list[Path], root: Path) -> list[Module]:
    named = ((py_file, _scan_root_qualname(py_file, root)) for py_file in outside)
    return [
        Module(qualname=qualname, path=py_file.resolve(), is_package=False)
        for py_file, qualname in named
        if qualname is not None
    ]


def _leaf_modules(
    root: Path, ignored: frozenset[str], package_roots: list[Path], seen: set[str]
) -> list[Module]:
    """Extension and stub leaves whose qualname no `.py` module already holds."""
    leaves: list[Module] = []
    for leaf_path, stem in _iter_extension_and_stub_leaves(root, ignored):
        qualname = _qualname_for_stem(leaf_path, stem, package_roots)
        if qualname is None or qualname in seen:
            continue
        leaves.append(Module(qualname=qualname, path=leaf_path.resolve(), is_package=False))
        seen.add(qualname)
    return leaves


def _raise_on_qualname_collision(modules: list[Module], root: Path) -> None:
    """Refuse a module set in which two distinct files share a qualname (#531).

    Runs on the `.py` modules and the init-less test fallback, before the
    extension/stub leaves are added: a compiled `foo.so` beside `foo.py` (a
    mypyc build) is one module with two artefacts, not two modules, and the leaf
    loop already skips it by design.
    """
    by_name: dict[str, list[Path]] = {}
    for m in modules:
        by_name.setdefault(m.qualname, []).append(m.path)
    resolved_root = root.resolve()
    collisions = {
        qualname: [_display_path(p, resolved_root) for p in sorted(set(paths))]
        for qualname, paths in by_name.items()
        if len(set(paths)) >= 2
    }
    if collisions:
        raise QualnameCollisionError(collisions)


def _display_path(path: Path, resolved_root: Path) -> str:
    try:
        return path.relative_to(resolved_root).as_posix()
    except ValueError:
        return path.as_posix()


def _extension_module_stem(filename: str) -> str | None:
    """Return the importable module stem for a compiled-extension filename."""
    for suffix in _EXTENSION_SUFFIXES_BY_LENGTH:
        if filename.endswith(suffix):
            return filename[: -len(suffix)]
    # EXTENSION_SUFFIXES is host-local; bare .pyd covers a Windows-built tree
    # scanned on POSIX (and vice versa for non-host tagged .so via the glob).
    if filename.endswith(".pyd"):
        return filename[:-4]
    return None


def _stub_leaves(root: Path, ignored: frozenset[str]) -> dict[tuple[Path, str], Path]:
    chosen: dict[tuple[Path, str], Path] = {}
    for path in sorted(root.rglob("*.pyi")):
        if is_ignored_path(path, ignored, root):
            continue
        if path.with_suffix(".py").is_file():
            continue
        stem = path.name[:-4]
        # Skip __init__.pyi: mapping it as a leaf would invent pkg.sub.__init__
        # (unlike _qualname_for, which drops a trailing __init__). Package roots
        # still need an __init__.py, so this edge case is rare but misleading.
        if stem == "__init__":
            continue
        chosen.setdefault((path.parent.resolve(), stem), path)
    return chosen


def _extension_leaves(root: Path, ignored: frozenset[str]) -> dict[tuple[Path, str], Path]:
    chosen: dict[tuple[Path, str], Path] = {}
    # WHY: EXTENSION_SUFFIXES lists only this host's tags, so a Windows .pyd
    # (or non-host .so) would be missed on POSIX without these broad globs.
    for pattern in ("*.so", "*.pyd"):
        for path in sorted(root.rglob(pattern)):
            if is_ignored_path(path, ignored, root):
                continue
            stem = _extension_module_stem(path.name)
            if stem is None:
                continue
            chosen[(path.parent.resolve(), stem)] = path
    return chosen


def _iter_extension_and_stub_leaves(
    root: Path,
    ignored: frozenset[str],
) -> list[tuple[Path, str]]:
    """Paths that define an internal leaf with no ``.py`` twin.

    Extension binaries win over a sibling ``.pyi`` for the same stem so the
    module is recorded once. A ``.pyi`` whose ``.py`` twin exists is skipped
    here; discovery of the ``.py`` already owns that qualname.
    """
    chosen = _stub_leaves(root, ignored)
    chosen.update(_extension_leaves(root, ignored))
    return sorted(
        ((path, stem) for (_parent, stem), path in chosen.items()),
        key=lambda item: (item[0].as_posix(), item[1]),
    )


def _qualname_under_roots(
    path: Path,
    leaf_parts: list[str],
    package_roots: list[Path],
) -> str | None:
    """Owners-first dotted name for ``path`` with precomputed leaf parts.

    A root that contains the file names it. Without this, a repo-root
    package (benchmarks/, examples/) that sorts before src/mypkg captures
    src/mypkg/a.py as `src.mypkg.a`, and its `mypkg.*` imports go external
    (#516). Files under no root (init-less tests/, top-level scripts) still
    fall back to the first root whose parent holds them.
    """
    abs_path = path.resolve()
    owners = [r for r in package_roots if abs_path.is_relative_to(r)]
    for pkg_root in owners or package_roots:
        try:
            rel_parent = abs_path.parent.relative_to(pkg_root.parent)
        except ValueError:
            continue
        # leaf_parts already carries the module stem (or is empty for
        # __init__.py → package), so a bare `".".join` is enough; the old
        # `else pkg_root.name` branch was dead when a stem was present.
        parts = [*rel_parent.parts, *leaf_parts]
        return ".".join(parts) if parts else pkg_root.name
    return None


def _scan_root_qualname(py_file: Path, root: Path) -> str | None:
    """Dotted name of a test file relative to the scan root, or None if it is not one.

    Also None when any path part is not a valid identifier (`my-tests/test_a.py`),
    because such a name could never be imported and would only add noise.
    """
    try:
        rel = py_file.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    parts = rel.with_suffix("").parts
    if not _is_conventional_test_file(rel) or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def _is_conventional_test_file(rel: Path) -> bool:
    """`test_*.py`, `*_test.py`, or anything under a `tests/` directory.

    The same default `archy affected` uses to decide what is a test
    (`affected._AUTO_TEST_GLOBS` / `_AUTO_TEST_DIR`); `affected` imports this
    module, so the rule is restated here and a test pins that the two agree.
    A `--filter` glob can call other files tests, but the graph has to decide
    what to scan before any filter exists.
    """
    return rel.name.startswith("test_") or rel.name.endswith("_test.py") or "tests" in rel.parts


def _qualname_for_stem(
    path: Path,
    stem: str,
    package_roots: list[Path],
) -> str | None:
    """Dotted qualname for an extension/stub leaf using an explicit module stem."""
    return _qualname_under_roots(path, [stem], package_roots)


def _is_extension_or_stub_leaf(path: Path) -> bool:
    """True when ``path`` is a discovered binary/stub leaf (empty parse)."""
    name = path.name
    if name.endswith(".pyi"):
        return True
    return _extension_module_stem(name) is not None


def _package_dirs(root: Path, ignored: frozenset[str]) -> set[Path]:
    return {
        path.parent.resolve()
        for path in root.rglob("__init__.py")
        if not is_ignored_path(path, ignored, root)
    }


def _declared_roots(root: Path, extra_roots: tuple[str, ...]) -> set[Path]:
    candidates = ((root / r).resolve() for r in extra_roots)
    return {candidate for candidate in candidates if candidate.is_dir()}


def _find_package_roots(
    root: Path,
    ignored: frozenset[str],
    extra_roots: tuple[str, ...],
) -> list[Path]:
    """Return absolute paths to top-level package directories under root."""
    package_dirs = _package_dirs(root, ignored)

    # User-declared namespace package roots: treat the directory as a package
    # even without __init__.py, so descendants get qualnames rooted there.
    # Adding it to package_dirs also demotes any already-discovered child
    # packages (their parent is now a package_dir, so they stop being roots),
    # which is exactly the nesting we want.
    package_dirs.update(_declared_roots(root, extra_roots))

    roots = [pkg for pkg in package_dirs if pkg.parent.resolve() not in package_dirs]
    roots.sort()
    return roots


def _iter_python_files(root: Path, ignored: frozenset[str]) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        if is_ignored_path(path, ignored, root):
            continue
        yield path


def is_ignored_path(path: Path, ignored: frozenset[str], root: Path) -> bool:
    """Return True when the scan must not read ``path``.

    A hard-skipped name is refused at any depth. A soft-skipped name is refused
    only when the directory holding it is not a package, so a first-party
    package that happens to be called ``build`` / ``dist`` / ``venv`` /
    ``site-packages`` is scanned (#518) while build output, which carries no
    ``__init__.py`` at its top level, stays out. A name the caller excludes
    itself is refused unconditionally.

    Ancestors of ``root`` itself must not count: a project that merely lives
    under a directory named ``build`` / ``dist`` / ``venv`` would otherwise
    scan as empty when given an absolute path (#517).
    """
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        # Path is outside the scan root (watcher unit tests pass synthetic
        # absolutes). Fall back to the full path so in-path ignored names like
        # ``.venv`` still filter; no directory can be probed from it.
        rel = path
        current: Path | None = None
    else:
        current = root.resolve()
    user_ignored = ignored - DEFAULT_IGNORED_DIRS
    for part in rel.parts[:-1]:
        current = None if current is None else current / part
        if part in HARD_IGNORED_DIRS or part in user_ignored:
            return True
        if part in SOFT_IGNORED_DIRS and _holds_no_package(current):
            return True
    return False


def _holds_no_package(directory: Path | None) -> bool:
    """True when `directory` is unknown (no probe possible) or has no `__init__.py`."""
    return directory is None or not (directory / "__init__.py").is_file()


def _qualname_for(py_file: Path, package_roots: list[Path]) -> str | None:
    """Dotted qualname for a ``.py`` file; delegates to ``_qualname_under_roots``."""
    # __init__.py names the package (empty leaf); other files contribute stem.
    leaf: list[str] = [] if py_file.name == "__init__.py" else [py_file.stem]
    return _qualname_under_roots(py_file, leaf, package_roots)
