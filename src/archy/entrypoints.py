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

    `errors` is non-empty when a `pyproject.toml` cannot be read as TOML, or
    when one inside the scan root names a script target that matches no
    scanned module. Both are reported rather than swallowed, because either
    shrinks `@entrypoints` and would let a rule pass over exactly the modules
    it was meant to bind. A target named by a `pyproject.toml` ABOVE the scan
    root may legitimately live outside the scan, so it is not an error.
    """
    found: dict[str, str] = {}
    for qualname in sorted(guarded):
        if qualname in modules:
            found.setdefault(qualname, MAIN_GUARD)
    for qualname in sorted(modules):
        if qualname == "__main__" or qualname.endswith(".__main__"):
            found.setdefault(qualname, MAIN_MODULE)

    scan_root = root.resolve()
    errors: list[str] = []
    for pyproject in _pyprojects(modules.values()):
        targets, error = _script_targets(pyproject)
        if error:
            errors.append(error)
            continue
        inside_scan = pyproject.parent.is_relative_to(scan_root)
        for name, target in targets:
            module = _resolve_target(target, modules)
            if module is not None:
                found.setdefault(module, f"console script `{name}` in {pyproject.name}")
            elif inside_scan:
                errors.append(
                    f"{pyproject}: script `{name}` targets {target!r}, which matches no "
                    "scanned module (if the package sits under a directory without "
                    "`__init__.py`, declare it with `roots:` in archy.yaml)"
                )
    return dict(sorted(found.items())), tuple(errors)


def _pyprojects(paths: Iterable[Path]) -> list[Path]:
    """Every `pyproject.toml` from each module's directory up to the repo root.

    The walk stops at the first directory holding `.git`, so a stray
    `pyproject.toml` in a home directory never joins a project's scan.
    """
    seen: set[Path] = set()
    for path in paths:
        current = path.resolve().parent
        while current not in seen:
            seen.add(current)
            if (current / ".git").exists() or current.parent == current:
                break
            current = current.parent
    return sorted(d / "pyproject.toml" for d in seen if (d / "pyproject.toml").is_file())


def _resolve_target(target: str, modules: Mapping[str, Path]) -> str | None:
    """The internal qualname a dotted script target refers to, if any.

    An exact match first. Failing that, drop leading segments while at least
    two remain: under a namespace package (no `__init__.py` in `acme/`) the
    installer imports `acme.tool.cli` where archy names the same file
    `tool.cli`. Two segments is the floor so a bare `cli` never captures
    `other.cli`.
    """
    if target in modules:
        return target
    parts = target.split(".")
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
