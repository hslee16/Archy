"""Wrap import-linter's contract checks behind a stable archy interface.

archy.yaml ships direct-edge layer rules; import-linter ships transitive
contracts (Layers, Forbidden, Independence, Protected, AcyclicSiblings).
This module surfaces import-linter results as plain dataclasses, ready
for `archy contracts` (CLI) or `archy_check(contracts=True)` (MCP, which
nests the result under `CheckPayload.contracts`) to consume.

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

archy:owns        ContractCheck, ContractsConfigError, ContractsNotAvailable,
                  ContractsResult, run_contracts
archy:mirrored-by ContractsConfigError -> archy.cli, archy.mcp,
                  ContractsNotAvailable -> archy.cli, archy.mcp,
                  run_contracts -> archy.cli, archy.mcp
"""

from __future__ import annotations

import contextlib
import os
import sys
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, computed_field

from archy.layers import LayerConfig, LayerConfigError, load_config

if TYPE_CHECKING:
    from grimp import ImportGraph
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
    # Module expressions the contract declares that match no module in the
    # graph. Usually a typo or a pattern written in archy's dialect rather
    # than import-linter's; either way the author believes something is
    # governed that is not.
    unmatched_expressions: tuple[str, ...] = ()
    # True when a whole module-expression field resolved to nothing, so the
    # contract could not have failed whatever the code does. Reporting that
    # as `kept` is #435: "OK" and "not actually checked" are different facts.
    matched_nothing: bool = False


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unverifiable(self) -> int:
        """How many contracts could not have failed.

        A `@computed_field`, not a plain property: consumers read this off
        `model_dump()` and a plain property is dropped there silently.
        """
        return sum(1 for c in self.contracts if c.matched_nothing)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verified(self) -> bool:
        """Every contract was both evaluated and held.

        The verdict callers actually want. `all_kept` cannot answer it: a
        contract that matched no module is trivially "kept" (#435).
        """
        return self.broken == 0 and self.unverifiable == 0


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
        try:
            report = create_report(user_options, cache_dir=None)
        except ValueError as exc:
            # import-linter aborts the whole run when a contract names a module
            # that is not in the graph, and the message is just "Module 'x'
            # does not exist." Reaching a user as a traceback makes archy look
            # broken rather than the config; say which config and what to do.
            raise ContractsConfigError(
                f"a contract names a module that is not in the import graph: {exc}. "
                "Check the layer patterns in archy.yaml (or the module names in "
                ".importlinter) against the modules archy discovers, which "
                "`archy graph . --format json` lists."
            ) from exc
    finally:
        os.chdir(prior_cwd)

    contracts: list[ContractCheck] = []
    for contract, check in report.get_contracts_and_checks():
        unmatched, matched_nothing = _expression_coverage(contract, report.graph)
        contracts.append(
            ContractCheck(
                name=str(contract.name),
                contract_type=type(contract).__name__,
                kept=bool(check.kept),
                metadata=dict(check.metadata),
                warnings=tuple(check.warnings),
                unmatched_expressions=unmatched,
                matched_nothing=matched_nothing,
            )
        )
    return ContractsResult(
        kept=int(report.kept_count),
        broken=int(report.broken_count),
        module_count=int(report.module_count),
        import_count=int(report.import_count),
        contracts=tuple(contracts),
    )


def _expression_coverage(contract: object, graph: ImportGraph) -> tuple[tuple[str, ...], bool]:
    """`(expressions matching no module, whether the contract could not fail)`.

    A contract whose `source_modules` (or any other populated module-expression
    field) resolves to nothing holds no matter what the code does, and
    import-linter reports it as kept. That is #435: the verdict is
    indistinguishable from a contract that was evaluated and held.

    Fields are read off the contract class's declared `*Field` descriptors, so
    every contract type that names modules is covered without this function
    knowing any of them. `LayersContract.layers` is the one gap: its module
    names sit inside `Layer` objects rather than in a flat expression
    collection, so a layers contract naming only absent modules is not flagged
    here. It is still caught for `containers`.
    """
    from importlinter.domain.helpers import module_expression_to_modules
    from importlinter.domain.imports import ModuleExpression

    unmatched: list[str] = []
    matched_nothing = False
    for name in _module_expression_fields(type(contract)):
        value = getattr(contract, name, None)
        expressions = [e for e in _as_iterable(value) if isinstance(e, ModuleExpression)]
        if not expressions:
            # An optional field the config never populated governs nothing and
            # claims nothing. Only a field the author DID fill in can lie.
            continue
        field_total = 0
        for expression in expressions:
            try:
                count = len(module_expression_to_modules(graph, expression))
            except Exception:  # a resolver error is not a verdict either way
                continue
            field_total += count
            if count == 0:
                unmatched.append(str(expression))
        if field_total == 0:
            matched_nothing = True
    return tuple(dict.fromkeys(unmatched)), matched_nothing


def _module_expression_fields(contract_type: type) -> tuple[str, ...]:
    """Names of the contract's fields that hold module expressions.

    Read from the class's `*Field` descriptors rather than a hardcoded list, so
    a new contract type in import-linter is covered the day it lands.
    """
    from importlinter.domain.fields import ModuleExpressionField

    names: list[str] = []
    for klass in contract_type.__mro__:
        for name, field in vars(klass).items():
            subfield = getattr(field, "subfield", None)
            if isinstance(field, ModuleExpressionField) or isinstance(
                subfield, ModuleExpressionField
            ):
                names.append(name)
    return tuple(dict.fromkeys(names))


def _as_iterable(value: object) -> tuple[object, ...]:
    if isinstance(value, (set, frozenset, list, tuple)):
        return tuple(value)
    return (value,) if value is not None else ()


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
                "source_modules": _to_import_linter_expressions(
                    layer_modules.get(rule.from_layer, ())
                ),
                "forbidden_modules": _to_import_linter_expressions(
                    layer_modules.get(rule.to_layer, ())
                ),
            }
        )

    return UserOptions(session_options=session_options, contracts_options=contracts_options)


def _to_import_linter_expressions(patterns: Iterable[str]) -> list[str]:
    """Translate archy layer patterns into import-linter module expressions.

    The two dialects disagree on one thing, and it is the one archy tells
    people to write. archy's `pkg.**` means "pkg AND every descendant"
    (`layers._translate_pattern` collapses the dot so the bare parent
    matches); import-linter's `pkg.**` means the descendants ONLY. So
    `shipping.store.**` on a leaf module resolved to the empty set, the
    Forbidden contract had no source modules, and a project with a real
    forbidden transitive path reported a clean pass at exit 0 (#435).

    Dropping the trailing `.**` restores archy's meaning exactly, because
    import-linter's `as_packages` (on by default) already reads a named
    package as itself plus its descendants. Emitting the wildcard as well
    would add nothing and would report `pkg.**` as matching no module every
    time `pkg` is a leaf, which is noise archy itself generated.

    Nothing else is rewritten. A bare `pkg` keeps whatever `as_packages`
    gives it, which is how every existing config already behaves.
    """
    expressions: list[str] = []
    for pattern in patterns:
        expressions.append(pattern[: -len(".**")] if pattern.endswith(".**") else pattern)
    return list(dict.fromkeys(expressions))


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
