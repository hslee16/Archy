"""Derive a project's entrypoints: the modules written to be run directly.

A `required:` rule whose `source` is `@entrypoints` binds every module this
derives, so a script added outside every glob the author thought to write is
governed the moment it lands (#387 item 3). An explicit glob cannot do that:
it covers only the entrypoints someone already knew about.

Three signals, each a fact Python itself acts on rather than a naming guess:

- a top-level `if __name__ == "__main__":` guard (`python path/to/file.py`,
  `python -m pkg.mod`);
- a `__main__.py` module (`python -m pkg`);
- a console or GUI script target in `pyproject.toml` (`[project.scripts]`,
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

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10 CI
    import tomli as tomllib

MAIN_GUARD = 'top-level `if __name__ == "__main__":` guard'
MAIN_MODULE = "`__main__.py` module"


def derive_entrypoints(
    root: Path,
    guarded: Iterable[str],
    internal: Iterable[str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Return `(entrypoints, errors)`.

    `entrypoints` maps each internal module qualname to the reason it is an
    entrypoint; when several signals apply, the first in the module docstring's
    order wins. `guarded` names the modules whose parse found a main guard.

    `errors` is non-empty when `root/pyproject.toml` exists but cannot be read
    as TOML. That is reported rather than swallowed: dropping the script
    targets silently would shrink `@entrypoints` and let a rule pass over
    exactly the modules it was meant to bind.
    """
    internal_set = set(internal)
    found: dict[str, str] = {}
    for qualname in sorted(guarded):
        if qualname in internal_set:
            found.setdefault(qualname, MAIN_GUARD)
    for qualname in sorted(internal_set):
        if qualname == "__main__" or qualname.endswith(".__main__"):
            found.setdefault(qualname, MAIN_MODULE)
    scripts, errors = _script_targets(root / "pyproject.toml")
    for name, module in scripts:
        if module in internal_set:
            found.setdefault(module, f"console script `{name}` in pyproject.toml")
    return dict(sorted(found.items())), errors


def _script_targets(pyproject: Path) -> tuple[list[tuple[str, str]], tuple[str, ...]]:
    """`(script name, target module)` pairs declared in `pyproject`, plus errors."""
    if not pyproject.is_file():
        return [], ()
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [], (f"{pyproject} could not be read, so its script targets are missing: {exc}",)
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
            # `pkg.mod:func`, optionally `pkg.mod:obj.attr [extra]`. Poetry also
            # allows a table form (`{reference = ..., type = "file"}`) that
            # names a file, not a module; it has no module to bind, so skip it.
            if isinstance(spec, str):
                module = spec.split(":", 1)[0].strip()
                if module:
                    targets.append((str(name), module))
    return sorted(targets), ()
