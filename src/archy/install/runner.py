"""Orchestration for `archy install`: detect, select targets, apply plans.

CLI-agnostic on purpose. The Click command in ``archy.cli`` parses flags and
formats output; everything here returns structured results so the same logic is
driven directly by unit tests without going through ``CliRunner``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from archy.install.base import AgentAdapter, FileAction, Scope, apply_plan
from archy.install.registry import all_adapters, get_adapter
from archy.install.writer import (
    DryRunWriteSystem,
    InstallError,
    RealWriteSystem,
    WriteSystem,
)

# Sentinel target selectors accepted by --target in addition to explicit ids.
TARGET_AUTO = "auto"
TARGET_ALL = "all"


@dataclass(frozen=True)
class Detection:
    adapter: AgentAdapter
    detected: bool


@dataclass
class AdapterResult:
    adapter_id: str
    written: list[Path] = field(default_factory=list)


@dataclass
class InstallResult:
    results: list[AdapterResult] = field(default_factory=list)

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
) -> list[FileAction]:
    return adapter.plan(scope, project_root=project_root, seed_permissions=seed_permissions)


def run_install(
    adapters: list[AgentAdapter],
    scope: Scope,
    *,
    project_root: Path | None = None,
    seed_permissions: bool = True,
    write_system: WriteSystem | None = None,
) -> InstallResult:
    """Apply each adapter's plan through ``write_system`` (real by default)."""
    ws = write_system if write_system is not None else RealWriteSystem()
    result = InstallResult()
    for adapter in adapters:
        plan = plan_for(
            adapter,
            scope,
            project_root=project_root,
            seed_permissions=seed_permissions,
        )
        written = apply_plan(plan, ws)
        result.results.append(AdapterResult(adapter_id=adapter.id, written=written))
    return result


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
    adapter = get_adapter(adapter_id)
    ws = DryRunWriteSystem()
    plan = plan_for(adapter, scope, project_root=project_root, seed_permissions=seed_permissions)
    apply_plan(plan, ws)
    return [(record.path, record.content) for record in ws.records]
