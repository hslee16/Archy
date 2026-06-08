"""Wrap import-linter's contract checks behind a stable archy interface.

archy.yaml ships direct-edge layer rules; import-linter ships transitive
contracts (Layers, Forbidden, Independence, Protected, AcyclicSiblings).
This module surfaces import-linter results as plain dataclasses, ready
for `archy contracts` (CLI) or `archy_contracts` (MCP) to consume.

Config resolution order:
  1. Explicit `config_filename` argument (.importlinter or pyproject.toml).
  2. `.importlinter` in the project root: the canonical contracts config.
     Supports all five contract types (Layers, Forbidden, Independence,
     Protected, AcyclicSiblings) and `ignore_imports` whitelists for
     legitimate transitive edges. Recommended for any project that needs
     more than a clean direct-edge rule.
  3. `archy.yaml` in the project root, translated to a list of Forbidden
     contracts (one per `forbid` rule). Best-effort fallback so a project
     with only an archy.yaml can run `archy contracts` without extra
     config; emits a UserWarning because this path cannot express
     `ignore_imports` (transitive false-positives are unavoidable).

import-linter is an optional dependency. Without it installed, the public
functions raise `ContractsNotAvailable` with an actionable message. The
wrap depends on a non-public entry point (`_register_contract_types`)
and on the `UserOptions` shape; both are pinned to a single import-linter
minor in `pyproject.toml`, and `tests/test_contracts.py` exercises the
surface end-to-end so a pin override breaks loudly.
"""

from __future__ import annotations

import contextlib
import os
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from archy.layers import LayerConfig, LayerConfigError, load_config

if TYPE_CHECKING:
    from importlinter.application.user_options import UserOptions


class ContractCheck(BaseModel):
    """One contract's result. `metadata` is the import-linter contract-type-
    specific shape (e.g., `invalid_chains` for ForbiddenContract); kept opaque
    here so the wrap doesn't need to know every contract type's schema."""

    model_config = ConfigDict(frozen=True)

    name: str
    contract_type: str
    kept: bool
    metadata: dict[str, object]
    warnings: tuple[str, ...]


class ContractsResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    kept: int
    broken: int
    module_count: int
    import_count: int
    contracts: tuple[ContractCheck, ...] = ()

    @property
    def all_kept(self) -> bool:
        return self.broken == 0


class ContractsNotAvailable(RuntimeError):
    """Raised when `import-linter` is not installed."""


class ContractsConfigError(RuntimeError):
    """Raised when the .importlinter file is missing or invalid."""


def run_contracts(
    project_dir: Path,
    config_filename: str | Path | None = None,
) -> ContractsResult:
    """Run import-linter against `project_dir` and return a structured result.

    `project_dir` must contain (or be the parent of) an importable copy of
    the package(s) named in the config; import-linter's graph builder uses
    runtime `import` resolution.

    Config resolution: `config_filename` (if given) wins; otherwise prefers
    `.importlinter` in `project_dir`; otherwise falls back to translating
    `archy.yaml` into Forbidden contracts. Raises `ContractsConfigError`
    if none are present.
    """
    try:
        from importlinter import configuration as _configuration  # noqa: F401
    except ImportError as exc:
        raise ContractsNotAvailable(
            "import-linter is not installed. "
            "Install with `pip install archy[contracts]` to use this feature."
        ) from exc

    project_dir = project_dir.resolve()

    if config_filename is not None:
        config_path = Path(config_filename).resolve()
        if not config_path.exists():
            raise ContractsConfigError(f"contracts config not found: {config_path}")
        if not config_path.is_file():
            raise ContractsConfigError(
                f"contracts config must be a file, not a directory: {config_path}"
            )
        with _ProjectOnSysPath(project_dir):
            return _drive_import_linter(config_path=config_path)

    importlinter_path = project_dir / ".importlinter"
    if importlinter_path.exists():
        with _ProjectOnSysPath(project_dir):
            return _drive_import_linter(config_path=importlinter_path)

    archy_yaml_path = project_dir / "archy.yaml"
    if archy_yaml_path.exists():
        try:
            user_options = _archy_yaml_to_user_options(archy_yaml_path)
        except LayerConfigError as exc:
            raise ContractsConfigError(
                f"could not derive contracts from {archy_yaml_path}: {exc}"
            ) from exc
        warnings.warn(
            "deriving transitive contracts from archy.yaml `forbid:` is a best-effort "
            "fallback and cannot express ignore_imports / whitelisted edges. For any "
            "project that needs to allow legitimate transitive paths, add a "
            ".importlinter file (the canonical contracts config). See "
            "https://import-linter.readthedocs.io/en/stable/contract_types.html",
            UserWarning,
            stacklevel=2,
        )
        with _ProjectOnSysPath(project_dir):
            return _drive_import_linter(user_options=user_options)

    raise ContractsConfigError(
        f"no contracts config found in {project_dir}: expected `.importlinter` "
        "(canonical, supports all five contract types and ignore_imports whitelists) "
        "or `archy.yaml` (best-effort fallback that translates `forbid:` rules to "
        "transitive Forbidden contracts). See "
        "https://import-linter.readthedocs.io/en/stable/contract_types.html"
    )


def _drive_import_linter(
    *,
    config_path: Path | None = None,
    user_options: UserOptions | None = None,
) -> ContractsResult:
    """The narrow surface we depend on inside import-linter.

    Either pass `config_path` (we delegate reading to import-linter's
    INI/TOML readers) or pass pre-built `user_options` (used when we
    derive contracts from archy.yaml). Imports are deferred so the
    optional-dep guard in `run_contracts` triggers first.
    """
    from importlinter import configuration
    from importlinter.application.use_cases import (
        _register_contract_types,
        create_report,
        read_user_options,
    )

    configuration.configure()
    prior_cwd = Path.cwd()
    try:
        if user_options is None:
            assert config_path is not None
            # import-linter's INI reader resolves config_filename relative to
            # cwd; chdir into the config's directory for the duration of
            # read+report, then restore.
            os.chdir(config_path.parent)
            user_options = read_user_options(config_filename=config_path.name)
        _register_contract_types(user_options)
        report = create_report(user_options, cache_dir=None)
    finally:
        os.chdir(prior_cwd)

    contracts: list[ContractCheck] = []
    for contract, check in report.get_contracts_and_checks():
        contracts.append(
            ContractCheck(
                name=str(contract.name),
                contract_type=type(contract).__name__,
                kept=bool(check.kept),
                metadata=dict(check.metadata),
                warnings=tuple(check.warnings),
            )
        )
    return ContractsResult(
        kept=int(report.kept_count),
        broken=int(report.broken_count),
        module_count=int(report.module_count),
        import_count=int(report.import_count),
        contracts=tuple(contracts),
    )


def _archy_yaml_to_user_options(archy_yaml_path: Path) -> UserOptions:
    """Translate archy.yaml's `forbid` rules into import-linter UserOptions.

    Each `{from: A, to: B}` rule becomes one Forbidden contract whose
    source_modules are layer A's patterns and forbidden_modules are layer
    B's. Forbidden checks transitively, so the resulting contracts are a
    strictness upgrade over `archy check` (direct edges only).

    Root packages are inferred from the top-level dotted token of every
    layer pattern, deduplicated. Multi-root projects are handled via
    `root_packages = ...` rather than `root_package = ...`.
    """
    from importlinter.application.user_options import UserOptions

    config: LayerConfig = load_config(archy_yaml_path)
    layer_modules = {layer.name: list(layer.patterns) for layer in config.layers}

    roots = sorted(
        {pattern.split(".", 1)[0] for layer in config.layers for pattern in layer.patterns}
    )
    if not roots:
        raise LayerConfigError(
            f"could not infer root_package from {archy_yaml_path}: no layer patterns"
        )

    # Always emit `root_packages` (list). import-linter normalizes singular
    # `root_package` → list inside `read_user_options`, but we bypass that
    # path when building UserOptions directly, so `create_report` would
    # KeyError on the missing list. Plural-list works in both shapes.
    session_options: dict[str, object] = {"root_packages": roots}

    contracts_options: list[dict[str, object]] = []
    for rule in config.forbid:
        contract_id = f"{rule.from_layer}-must-not-reach-{rule.to_layer}"
        contracts_options.append(
            {
                "id": contract_id,
                "name": f"{rule.from_layer} layer must not reach {rule.to_layer} layer",
                "type": "forbidden",
                "source_modules": list(layer_modules.get(rule.from_layer, ())),
                "forbidden_modules": list(layer_modules.get(rule.to_layer, ())),
            }
        )

    return UserOptions(session_options=session_options, contracts_options=contracts_options)


class _ProjectOnSysPath:
    """Context manager that prepends `project_dir` and `project_dir/src` to
    sys.path so import-linter's importlib lookup resolves the project
    correctly when the user hasn't installed the package."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._added: list[str] = []

    def __enter__(self) -> None:
        for candidate in (self._project_dir, self._project_dir / "src"):
            entry = str(candidate)
            if candidate.is_dir() and entry not in sys.path:
                sys.path.insert(0, entry)
                self._added.append(entry)

    def __exit__(self, *exc: object) -> None:
        for entry in self._added:
            with contextlib.suppress(ValueError):
                sys.path.remove(entry)
        self._added.clear()
