"""Wrap import-linter's contract checks behind a stable archy interface.

archy.yaml ships direct-edge layer rules; import-linter ships transitive
contracts (Layers, Forbidden, Independence, Protected, AcyclicSiblings).
This module loads an `.importlinter` config and surfaces the result as
plain dataclasses, ready for `archy contracts` (CLI) or `archy_contracts`
(MCP) to consume.

import-linter is an optional dependency. Without it installed, the public
functions raise `ContractsNotAvailable` with an actionable message. The
wrap depends on a non-public entry point (`_register_contract_types`)
which has been stable across the 2.x series; an integration test in
`tests/test_contracts.py` exercises the wrap end-to-end so we catch
breakage on import-linter upgrades.
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ContractCheck:
    """One contract's result. `metadata` is the import-linter contract-type-
    specific shape (e.g., `invalid_chains` for ForbiddenContract); kept opaque
    here so the wrap doesn't need to know every contract type's schema."""

    name: str
    contract_type: str
    kept: bool
    metadata: dict[str, Any]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ContractsResult:
    kept: int
    broken: int
    module_count: int
    import_count: int
    contracts: tuple[ContractCheck, ...] = field(default_factory=tuple)

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
    the package(s) named in the `.importlinter` config; import-linter's
    graph builder uses runtime `import` resolution.

    `config_filename` defaults to `.importlinter` in `project_dir`. INI and
    TOML formats are both supported by import-linter.
    """
    try:
        from importlinter import configuration as _configuration  # noqa: F401
    except ImportError as exc:
        raise ContractsNotAvailable(
            "import-linter is not installed. "
            "Install with `pip install archy[contracts]` to use this feature."
        ) from exc

    project_dir = project_dir.resolve()
    config_path = (
        Path(config_filename).resolve() if config_filename else project_dir / ".importlinter"
    )
    if not config_path.exists():
        raise ContractsConfigError(f"contracts config not found: {config_path}")

    with _ProjectOnSysPath(project_dir):
        return _drive_import_linter(config_path)


def _drive_import_linter(config_path: Path) -> ContractsResult:
    """The narrow surface we depend on inside import-linter.

    We import inside the function so the optional-dep guard in run_contracts
    runs first and gives a clean error before this triggers.
    """
    from importlinter import configuration
    from importlinter.application.use_cases import (
        _register_contract_types,
        create_report,
        read_user_options,
    )

    configuration.configure()
    # import-linter's INI reader resolves config_filename relative to cwd, so
    # the simplest robust call is to chdir into the config's directory for
    # the duration of read+report. We restore cwd afterwards.
    prior_cwd = Path.cwd()
    try:
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
