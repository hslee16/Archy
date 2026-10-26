"""Derive a project's entrypoints: the modules written to be run directly.

A `required:` rule whose `source` is `@entrypoints` binds every module this
derives, so a script added outside every glob the author thought to write is
governed the moment it lands (#387 item 3). An explicit glob cannot do that:
it covers only the entrypoints someone already knew about.

Three signals, each a fact Python itself acts on rather than a naming guess:

- a top-level `if __name__ == "__main__":` guard (`python path/to/file.py`,
  `python -m pkg.mod`);
- a `__main__.py` module (`python -m pkg`);
- a console or GUI script target in a `pyproject.toml` (`[project.scripts]`,
  `[project.gui-scripts]`, `[tool.poetry.scripts]`), which the installer turns
  into an executable that imports that module.

Each derived module carries the reason it was derived, so a violation can say
why the rule applied to a module the author never named.

archy:owns        derive_entrypoints
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10 CI
    import tomli as tomllib

MAIN_GUARD = 'top-level `if __name__ == "__main__":` guard'
MAIN_MODULE = "`__main__.py` module"


def derive_entrypoints(
    root: Path,
    guarded: Iterable[str],
    modules: Mapping[str, Path],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Return `(entrypoints, errors)`.

    `entrypoints` maps each internal module qualname to the reason it is an
    entrypoint; when several signals apply, the first in the module docstring's
    order wins. `guarded` names the modules whose parse found a main guard, and
    `modules` maps every internal qualname to its file.

    Script targets are read from every `pyproject.toml` between a module's
    directory and the repository root, not only the scan root's: `archy check
    src` must see the scripts the project root declares, and a monorepo's
    sub-projects each carry their own.

    A target that matches no scanned module is skipped, not reported. It is
    usually legitimate: an `exclude:`d directory, a script that re-exports a
    third-party tool (`alembic.config:main`), or a test fixture's own
    pyproject. Refusing the verdict there left the rule with no way to pass.

    `errors` is non-empty when a `pyproject.toml` that is read cannot be parsed
    as TOML. That is reported rather than swallowed, because dropping its
    script targets would let a rule pass over exactly the modules they name.
    """
    found: dict[str, str] = {}
    for qualname in sorted(guarded):
        if qualname in modules:
            found.setdefault(qualname, MAIN_GUARD)
    for qualname in sorted(modules):
        if qualname == "__main__" or qualname.endswith(".__main__"):
            found.setdefault(qualname, MAIN_MODULE)

    top_level = {qualname.split(".", 1)[0] for qualname in modules}
    errors: list[str] = []
    for pyproject in _pyprojects(root, modules.values()):
        targets, error = _script_targets(pyproject)
        if error:
            errors.append(error)
            continue
        for name, target in targets:
            module = _resolve_target(target, modules, top_level)
            if module is not None:
                found.setdefault(module, f"console script `{name}` in {pyproject.name}")
    return dict(sorted(found.items())), tuple(errors)


# Directories that mark a repository root. `.git` alone missed Mercurial,
# Jujutsu, Subversion and Sapling checkouts, where the walk then climbed to `/`.
_REPO_MARKERS = (".git", ".hg", ".jj", ".svn", ".sl")


def _pyprojects(root: Path, paths: Iterable[Path]) -> list[Path]:
    """Every `pyproject.toml` from each module's directory up to the repo root.

    The repo root is the nearest directory at or above the scan root holding a
    VCS marker. With none (an exported tree, a Docker build context without
    `.git`), the scan root itself is the ceiling: climbing to `/` would let a
    stray `pyproject.toml` in a parent directory bind or break this project.
    """
    scan_root = root.resolve()
    ceiling = next(
        (d for d in (scan_root, *scan_root.parents) if _is_repo_root(d)),
        scan_root,
    )
    seen: set[Path] = set()
    for path in paths:
        current = path.resolve().parent
        while current not in seen and current.is_relative_to(ceiling):
            seen.add(current)
            current = current.parent
    return sorted(d / "pyproject.toml" for d in seen if (d / "pyproject.toml").is_file())


def _is_repo_root(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in _REPO_MARKERS)


def _resolve_target(target: str, modules: Mapping[str, Path], top_level: set[str]) -> str | None:
    """The internal qualname a dotted script target refers to, if any.

    An exact match first. Failing that, and only when the target's first
    segment is NOT one of this project's own top-level names, drop leading
    segments while at least two remain: under a namespace package (no
    `__init__.py` in `acme/`) the installer imports `acme.tool.cli` where archy
    names the same file `tool.cli`. If `acme` IS a scanned package, the target
    is simply stale, and stripping it would blame an unrelated `tool.cli`. Two
    segments is the floor so a bare `cli` never captures `other.cli`.
    """
    if target in modules:
        return target
    parts = target.split(".")
    if parts[0] in top_level:
        return None
    for start in range(1, len(parts) - 1):
        candidate = ".".join(parts[start:])
        if candidate in modules:
            return candidate
    return None


def _script_targets(pyproject: Path) -> tuple[list[tuple[str, str]], str | None]:
    """`(script name, target module)` pairs declared in `pyproject`, or an error."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [], f"{pyproject} could not be read, so its script targets are missing: {exc}"
    tables: list[object] = []
    project = data.get("project")
    if isinstance(project, Mapping):
        tables += [project.get("scripts"), project.get("gui-scripts")]
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, Mapping) else None
    if isinstance(poetry, Mapping):
        tables.append(poetry.get("scripts"))
    targets: list[tuple[str, str]] = []
    for table in tables:
        if not isinstance(table, Mapping):
            continue
        for name, spec in table.items():
            reference = _script_reference(spec)
            # `pkg.mod:func`, optionally `pkg.mod:obj.attr [extra]`.
            module = reference.split(":", 1)[0].strip() if reference else ""
            if module:
                targets.append((str(name), module))
    return sorted(targets), None


def _script_reference(spec: object) -> str | None:
    """The `module:callable` reference a script entry names, if it names one.

    Poetry's table form (`{reference = "pkg.cli:main", type = "console",
    extras = [...]}`) is a console script like the string form; only
    `type = "file"` names a file rather than a module.
    """
    if isinstance(spec, str):
        return spec
    if not isinstance(spec, Mapping):
        return None
    # The isinstance narrows `object` to `Mapping[Never, Never]`, on which
    # `.get` cannot be called; TOML tables always have string keys.
    table = cast("Mapping[str, object]", spec)
    if table.get("type", "console") == "console":
        reference = table.get("reference")
        if isinstance(reference, str):
            return reference
    return None
