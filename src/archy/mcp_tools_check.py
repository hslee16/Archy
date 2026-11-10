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
from archy.mcp_tools_common import _READ_ONLY_ANNOTATIONS, _SNAPSHOT_ANNOTATIONS, _project_dir
from archy.simulate import EdgeSpec, SimulateReport


def register_archy_score(server: Server) -> None:
    @server.tool(
        name="archy_score",
        title="Score project structure",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Compute the composite quality score (modularity, acyclicity, depth, "
            "equality, complexity - geometric mean of five axes) for a Python "
            "project. `view='current'` (the DEFAULT) returns the five-axis score "
            "payload; optionally append the result to .archy/history.jsonl "
            "(record=True) and/or compare against the most recent recorded run as "
            "a regression gate (strict=True). `strict_tolerance` must be in "
            "[0, 1] (default 0.02). Pass record=True to record a "
            "baseline at session start (replaces the removed archy_record_baseline). "
            "`view='history'` instead reads the recent score history "
            "(.archy/history.jsonl), returning up to `last_n` rows oldest-first so "
            "you can compare deltas over time (replaces the removed archy_trend); "
            "last_n must be >= 1, and record/strict/strict_tolerance do not apply."
        ),
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
        description=(
            "Find import cycles (Tarjan SCCs of size >= min_size, plus self-loops) "
            "in a Python project. Returns cycles sorted largest-first. Each cycle's "
            "`type_checking_edges` counts edges that exist only under "
            "`if TYPE_CHECKING:` (they never run); when it is non-zero, pass "
            "`exclude_type_checking=true` to leave them out. With it on the result "
            "is an object, `{cycles, excluded_type_checking_edges}`, so a clean "
            "result that is clean only because of the exclusion says so. Unset, it "
            "follows archy.yaml's `exclude_type_checking_imports`, else off."
        ),
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
        description=(
            "**Call after any Python edit that adds, removes, or changes an "
            "import statement.** Returns forbidden direct edges between layers "
            "declared in archy.yaml under `violations`, plus Stable Dependencies "
            "Principle violations (when `sdp.enabled: true` in archy.yaml) under "
            "`sdp_violations`. Empty lists on both mean no direct boundary "
            "crossings. `required_violations` reports the INVERSE constraint: "
            "`required:` rules in archy.yaml declare that every module matching "
            "`source` must TRANSITIVELY reach `must_reach` (e.g. every standalone "
            "entrypoint must reach a model registry that has to be imported "
            "before the framework configures itself). Reach counts indirect "
            "paths and the implicit package-`__init__` import, so satisfying "
            "such a rule once in a package `__init__.py` covers its submodules; "
            "each violation carries a `detail` saying which module fails and "
            "why, and a rule whose patterns match nothing is reported as a "
            "violation rather than passing silently. Fix by adding the missing "
            "import, not by deleting the rule. Pass `contracts=True` to ALSO run the transitive "
            "(multi-hop) import-linter contracts (Layers, Forbidden, "
            "Independence, Protected, AcyclicSiblings) - stricter than the direct "
            "archy.yaml edges - nested under the `contracts` field; a failed "
            "contract means an indirect import path violates the architecture, so "
            "restructure rather than weaken the rule. That path reads .importlinter "
            "(or pyproject.toml, override with `contracts_config_path`, relative to `path`) "
            "and needs "
            "`pip install archy[contracts]`; if the extra is absent, `contracts` "
            "comes back with `available=false` and an advisory rather than "
            "raising (replaces the removed archy_contracts tool). If no archy.yaml "
            "is found, returns an in-band CheckErrorPayload (an `error` field, not "
            "a raised error) so you can create one or pass `config_path` (relative to `path`), and "
            "contracts are not run in that case; a malformed archy.yaml instead "
            "raises (it cannot be checked against)."
        ),
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
        description=(
            "Capture score, cycles, and layer violations to .archy/baseline.json "
            "as a baseline that archy_diff will compare against. Call at the "
            "start of an editing session. Also returns `invariant_brief`: the "
            "constraints to know before your first edit (declared layers, "
            "forbidden inter-layer edges, whether the graph is acyclic, the "
            "baseline score per axis, and the top load-bearing / highest-risk "
            "modules). Read it up front to avoid proposing a cross-layer or "
            "cycle-introducing edit, rather than being told after the diff. "
            "See the `loop` prompt for full usage."
        ),
    )
    def archy_snapshot(path: str) -> SnapshotPayload:
        return run_snapshot(_project_dir(path))


def register_archy_diff(server: Server) -> None:
    @server.tool(
        name="archy_diff",
        title="Diff against baseline",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Compare the current project state to the last snapshot. Returns "
            "a risk-weighted `summary` (headline + top regressions / "
            "improvements), per-component score deltas, and the cycles and "
            "layer violations that have been added or resolved since the "
            "baseline. Use after edits to localize regressions; see the `loop` "
            "prompt. `strict=True` with `baseline` (a committed file, relative "
            "to `path`) adds `strict`. `passed` is false when a cycle or layer "
            "violation is new, or when archy.yaml removes or narrows a rule the "
            "baseline fingerprint records. `reason` names it. A baseline with no "
            "fingerprint still checks cycles and violations."
        ),
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
        description=(
            "Counterfactual pre-edit check: given a proposed import-edge delta "
            "(`add` / `remove` lists of {from, to} module-or-path pairs), return "
            "the structural consequence BEFORE any file is written -- new/resolved "
            "cycles, new back-edges, new layer/SDP violations, per-axis score "
            "delta, and the propagation_cost change -- with a risk-ranked "
            "`summary` phrased conditionally ('would form a cycle'). Use it to "
            "test a refactoring hypothesis and reshape the plan before editing. "
            "Endpoints matching no internal module are returned in "
            "`applied.unresolved` with a `did you mean` in "
            "`applied.unresolved_hints`; a spec that could not be applied is "
            "listed in `applied.skipped`, and `applied.coverage` is `none` when "
            "no requested edge was simulated (the zero deltas then describe an "
            "unchanged graph, not a free edit) or `partial` when some were. "
            "Caveat: it models the graph delta you "
            "describe, not arbitrary code edits (it cannot move the complexity "
            "axis), and one submodule import (`a.b.c`) also implies edges to its "
            "ancestor packages -- include them in `add` to model it exactly (a "
            "lone submodule edge is a lower bound; see "
            "docs/research/SIMULATE_ORACLE_EMPIRICS.md)."
        ),
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
        description=(
            "**Call once, before starting on an unfamiliar repository.** "
            "Composes `archy_conventions` (naming, mirrored surfaces, gates, "
            "export and doc gaps), the layer `coverage` of `archy.yaml` and, "
            "with `contracts=True`, the transitive import-linter verdict, into "
            "one answer to 'what do I need to know before I touch this'. "
            "`top_n` (default 8) bounds the ranked sections and `conventions."
            "totals` says how many were dropped; `gates`, `errors`, "
            "`export_gaps` and `doc_gaps` are never truncated. `coverage` is "
            "null when there is no usable archy.yaml. This tool does NOT run "
            "the direct-edge check, so `transitive_checked=false` with a "
            "`transitive_unverified_reason` means the `forbid` rules are "
            "unverified, not that they hold; pass `contracts=True` to settle "
            "it. It is ranked and truncated like `archy_conventions`, so for a "
            "negative call `archy_module_view`. Advisory: mutates nothing."
        ),
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
