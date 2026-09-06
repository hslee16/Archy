"""Orchestration for `archy install`: detect, select targets, apply plans.

CLI-agnostic on purpose. The Click command in ``archy.cli`` parses flags and
formats output; everything here returns structured results so the same logic is
driven directly by unit tests without going through ``CliRunner``.

archy:owns        AdapterResult, Detection, InstallResult, detect_all, plan_for,
                  print_config, resolve_targets, run_install, run_uninstall
archy:mirrored-by detect_all -> archy.cli, archy.install, print_config -> archy.cli,
                  archy.install, resolve_targets -> archy.cli, archy.install,
                  run_install -> archy.cli, archy.install, run_uninstall -> archy.cli,
                  archy.install
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archy.install.base import AgentAdapter, FileAction, Scope, apply_plan, apply_uninstall
from archy.install.registry import all_adapters, get_adapter
from archy.install.writer import (
    DryRunWriteSystem,
    InstallError,
    RealWriteSystem,
    WriteSystem,
)

# Named so callers and tests never depend on the raw "auto"/"all" string literals.
TARGET_AUTO = "auto"
TARGET_ALL = "all"


class Detection(BaseModel):
    """Whether one adapter's client was found on this machine."""

    # arbitrary_types_allowed: `adapter` is an AgentAdapter instance (an ABC),
    # not a pydantic-native type.
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    adapter: AgentAdapter
    detected: bool


class AdapterResult(BaseModel):
    """The files one adapter wrote during an install."""

    model_config = ConfigDict(frozen=True)

    adapter_id: str
    written: tuple[Path, ...] = ()


class InstallResult(BaseModel):
    """All files written across the adapters in one install run."""

    model_config = ConfigDict(frozen=True)

    results: tuple[AdapterResult, ...] = ()

    def all_paths(self) -> list[Path]:
        return [p for r in self.results for p in r.written]


def detect_all() -> list[Detection]:
    """Probe every registered adapter. Order matches the registry."""
    return [Detection(adapter=a, detected=a.detect()) for a in all_adapters()]


def resolve_targets(target: str) -> list[AgentAdapter]:
    """Map a ``--target`` value to the adapters to act on.

    - ``auto``  -> only adapters that detect their client on this machine.
    - ``all``   -> every registered adapter, detected or not.
    - ``a,b,c`` -> exactly those adapter ids (detection ignored; explicit intent).

    Raises :class:`InstallError` for an unknown id or an empty ``auto`` result.
    """
    spec = target.strip().lower()
    if spec == TARGET_ALL:
        return list(all_adapters())
    if spec == TARGET_AUTO:
        detected = [d.adapter for d in detect_all() if d.detected]
        if not detected:
            raise InstallError(
                "No supported agent clients detected. Pass --target=<id> "
                "explicitly or --target=all. Known agents: "
                + ", ".join(a.id for a in all_adapters())
                + "."
            )
        return detected
    requested = [piece.strip() for piece in spec.split(",") if piece.strip()]
    if not requested:
        raise InstallError("Empty --target. Use auto, all, or a comma list of ids.")
    adapters: list[AgentAdapter] = []
    for adapter_id in requested:
        try:
            adapters.append(get_adapter(adapter_id))
        except KeyError as exc:
            raise InstallError(str(exc)) from exc
    return adapters


def plan_for(
    adapter: AgentAdapter,
    scope: Scope,
    *,
    project_root: Path | None,
    seed_permissions: bool,
    for_uninstall: bool = False,
) -> list[FileAction]:
    return adapter.plan(
        scope,
        project_root=project_root,
        seed_permissions=seed_permissions,
        for_uninstall=for_uninstall,
    )


def _run_for_adapters(
    adapters: list[AgentAdapter],
    scope: Scope,
    *,
    project_root: Path | None,
    seed_permissions: bool,
    write_system: WriteSystem | None,
    apply_fn: Callable[[list[FileAction], WriteSystem], list[Path]],
    for_uninstall: bool = False,
) -> InstallResult:
    """Plan each adapter and run ``apply_fn`` over it. Shared by install/uninstall."""
    ws = write_system if write_system is not None else RealWriteSystem()
    results: list[AdapterResult] = []
    for adapter in adapters:
        plan = plan_for(
            adapter,
            scope,
            project_root=project_root,
            seed_permissions=seed_permissions,
            for_uninstall=for_uninstall,
        )
        touched = apply_fn(plan, ws)
        results.append(AdapterResult(adapter_id=adapter.id, written=tuple(touched)))
    return InstallResult(results=tuple(results))


def run_install(
    adapters: list[AgentAdapter],
    scope: Scope,
    *,
    project_root: Path | None = None,
    seed_permissions: bool = True,
    write_system: WriteSystem | None = None,
) -> InstallResult:
    """Apply each adapter's plan through ``write_system`` (real by default)."""
    return _run_for_adapters(
        adapters,
        scope,
        project_root=project_root,
        seed_permissions=seed_permissions,
        write_system=write_system,
        apply_fn=apply_plan,
    )


def run_uninstall(
    adapters: list[AgentAdapter],
    scope: Scope,
    *,
    project_root: Path | None = None,
    seed_permissions: bool = True,
    write_system: WriteSystem | None = None,
) -> InstallResult:
    """Reverse each adapter's plan: strip archy from configs, delete owned files.

    ``seed_permissions`` mirrors install so the Claude permission entries that
    install seeded are the ones uninstall removes (pass it through unchanged).
    Reports the paths touched (stripped or deleted) in the same result shape.
    """
    return _run_for_adapters(
        adapters,
        scope,
        project_root=project_root,
        seed_permissions=seed_permissions,
        write_system=write_system,
        apply_fn=apply_uninstall,
        for_uninstall=True,
    )


def print_config(
    adapter_id: str,
    scope: Scope,
    *,
    project_root: Path | None = None,
    seed_permissions: bool = True,
) -> list[tuple[Path, str]]:
    """Dry-run a single adapter: return the (path, rendered-content) it would write.

    Renders against the *real* current files (the dry-run system reads disk),
    so the preview reflects the actual merge result, not a fresh snippet.
    """
    try:
      adapter = get_adapter(adapter_id)
    except KeyError as err:
     raise ValueError(str(err)) from err
    ws = DryRunWriteSystem()
    plan = plan_for(adapter, scope, project_root=project_root, seed_permissions=seed_permissions)
    apply_plan(plan, ws)
    return [(record.path, record.content) for record in ws.records]
