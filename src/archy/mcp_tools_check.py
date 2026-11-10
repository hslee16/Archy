"""MCP tool registrations for score, cycles, check, snapshot, diff, simulate and brief.

archy:owns        register_archy_brief, register_archy_check, register_archy_cycles,
                  register_archy_diff, register_archy_score, register_archy_simulate,
                  register_archy_snapshot
"""

from __future__ import annotations

from pathlib import Path

from archy.cycles import Cycle, CycleScan
from archy.diff import (
    DiffReport,
)
from archy.mcp_compat import Server
from archy.mcp_payloads import (
    BriefPayload,
    CheckErrorPayload,
    CheckPayload,
    DiffErrorPayload,
    ScorePayload,
    SnapshotPayload,
    TrendRow,
)
from archy.mcp_runs import (
    contracts_verdict,
    resolve_against,
    run_brief,
    run_check,
    run_contracts,
    run_cycles,
    run_diff,
    run_score,
    run_simulate,
    run_snapshot,
    run_trend,
)
from archy.mcp_tool_descriptions import (
    ARCHY_BRIEF_DESCRIPTION,
    ARCHY_CHECK_DESCRIPTION,
    ARCHY_CYCLES_DESCRIPTION,
    ARCHY_DIFF_DESCRIPTION,
    ARCHY_SCORE_DESCRIPTION,
    ARCHY_SIMULATE_DESCRIPTION,
    ARCHY_SNAPSHOT_DESCRIPTION,
)
from archy.mcp_tools_common import _READ_ONLY_ANNOTATIONS, _SNAPSHOT_ANNOTATIONS, _project_dir
from archy.simulate import EdgeSpec, SimulateReport


def register_archy_score(server: Server) -> None:
    @server.tool(
        name="archy_score",
        title="Score project structure",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_SCORE_DESCRIPTION,
    )
    def archy_score(
        path: str,
        view: str = "current",
        internal_only: bool = True,
        record: bool = False,
        strict: bool = False,
        strict_tolerance: float = 0.02,
        last_n: int = 10,
    ) -> ScorePayload | list[TrendRow]:
        root = _project_dir(path)
        if view == "history":
            return run_trend(root, last_n=last_n)
        if view != "current":
            raise ValueError(f"view must be 'current' or 'history'; got {view!r}")
        return run_score(
            root,
            internal_only=internal_only,
            record=record,
            strict=strict,
            strict_tolerance=strict_tolerance,
        )


def register_archy_cycles(server: Server) -> None:
    @server.tool(
        name="archy_cycles",
        title="Find import cycles",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_CYCLES_DESCRIPTION,
    )
    def archy_cycles(
        path: str,
        min_size: int = 2,
        internal_only: bool = True,
        exclude_type_checking: bool | None = None,
    ) -> list[Cycle] | CycleScan:
        return run_cycles(
            _project_dir(path),
            min_size=min_size,
            internal_only=internal_only,
            exclude_type_checking=exclude_type_checking,
        )


def register_archy_check(server: Server) -> None:
    @server.tool(
        name="archy_check",
        title="Check layer boundaries",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_CHECK_DESCRIPTION,
    )
    def archy_check(
        path: str,
        config_path: str | None = None,
        contracts: bool = False,
        contracts_config_path: str | None = None,
    ) -> CheckPayload | CheckErrorPayload:
        root = _project_dir(path)
        result = run_check(
            root,
            config_path=resolve_against(root, [Path(config_path)])[0] if config_path else None,
        )
        if contracts and isinstance(result, CheckPayload):
            payload = run_contracts(
                root,
                config_filename=(
                    resolve_against(root, [Path(contracts_config_path)])[0]
                    if contracts_config_path
                    else None
                ),
            )
            # Only a verdict counts as "checked". Requesting contracts and having
            # import-linter turn out to be missing leaves the rules exactly as
            # unverified as not asking, so reporting transitive_checked=True
            # there would reintroduce the bug this field exists to close (#343).
            # A contract that could not have failed is not a verdict either, so
            # it leaves the rules as unverified as a missing import-linter does
            # (#457). Same rule as the CLI's `_transitive_checked`, so the two
            # surfaces cannot disagree about whether a run was checked.
            checked, unverifiable_reason = contracts_verdict(payload)
            # A stale reason is worse than none. `run_check` computed its reason
            # before contracts were attempted, so it names contracts=True -- and
            # replaying that at a caller who just passed contracts=True and
            # watched it fail sends them round a loop they have been through.
            # A non-None reason from `run_check` is also the signal that there
            # WAS something to verify, so it gates the no-verdict branch.
            if unverifiable_reason:
                # Unlike the no-verdict case this does not depend on there
                # having been a reason already: contracts ran and answered
                # nothing, which is worth saying however well-governed the
                # direct edges are.
                reason = unverifiable_reason
            elif checked or result.transitive_unverified_reason is None:
                reason = None
            else:
                reason = (
                    "the `forbid` rules are still unverified: archy_check(contracts=True) "
                    f"produced no transitive verdict ({payload.error})."
                )
            result = result.model_copy(
                update={
                    "contracts": payload,
                    "transitive_checked": checked,
                    "transitive_unverified_reason": reason,
                }
            )
        return result


def register_archy_snapshot(server: Server) -> None:
    @server.tool(
        name="archy_snapshot",
        title="Snapshot baseline",
        annotations=_SNAPSHOT_ANNOTATIONS,
        description=ARCHY_SNAPSHOT_DESCRIPTION,
    )
    def archy_snapshot(path: str) -> SnapshotPayload:
        return run_snapshot(_project_dir(path))


def register_archy_diff(server: Server) -> None:
    @server.tool(
        name="archy_diff",
        title="Diff against baseline",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_DIFF_DESCRIPTION,
    )
    def archy_diff(
        path: str, baseline: str | None = None, strict: bool = False
    ) -> DiffReport | DiffErrorPayload:
        return run_diff(_project_dir(path), baseline=baseline, strict=strict)


def register_archy_simulate(server: Server) -> None:
    @server.tool(
        name="archy_simulate",
        title="Simulate import-edge change",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_SIMULATE_DESCRIPTION,
    )
    def archy_simulate(
        path: str,
        add: list[EdgeSpec] | None = None,
        remove: list[EdgeSpec] | None = None,
    ) -> SimulateReport:
        return run_simulate(_project_dir(path), add=add or [], remove=remove or [])


def register_archy_brief(server: Server) -> None:
    @server.tool(
        name="archy_brief",
        title="One bounded briefing before touching an unfamiliar repository",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=ARCHY_BRIEF_DESCRIPTION,
    )
    def archy_brief(
        path: str,
        top_n: int = 8,
        contracts: bool = False,
        include_tests: bool = False,
    ) -> BriefPayload:
        return run_brief(
            _project_dir(path), top_n=top_n, with_contracts=contracts, include_tests=include_tests
        )
