"""The work behind the score, check, contracts, brief, diff, simulate and conventions tools.

archy:owns        contracts_verdict, run_brief, run_check, run_contracts,
                  run_conventions, run_cycles, run_diff, run_module_view, run_score,
                  run_simulate, run_snapshot, run_trend, score_components
archy:mirrored-by contracts_verdict -> archy.mcp_runs, archy.mcp_tools_check,
                  run_brief -> archy.mcp_runs, archy.mcp_tools_check,
                  run_conventions -> archy.mcp_runs, archy.mcp_tools_conventions,
                  run_cycles -> archy.mcp_runs, archy.mcp_tools_check,
                  run_diff -> archy.mcp_runs, archy.mcp_tools_check,
                  run_module_view -> archy.mcp_runs, archy.mcp_tools_conventions,
                  run_score -> archy.mcp_runs, archy.mcp_tools_check,
                  run_simulate -> archy.mcp_runs, archy.mcp_tools_check,
                  run_snapshot -> archy.mcp_runs, archy.mcp_tools_check,
                  run_trend -> archy.mcp_runs, archy.mcp_tools_check
"""

from __future__ import annotations

from pathlib import Path

from archy.conventions import (
    ConventionsReport,
    ModuleView,
    compute_conventions,
    compute_module_view,
)
from archy.cycles import Cycle, CycleScan, scan_cycles
from archy.diff import (
    DiffReport,
    compute_diff,
    create_baseline_command,
    default_baseline_path,
    missing_baseline_hint,
    read_snapshot,
    strict_baseline_problem,
    strict_verdict,
    take_snapshot,
    write_snapshot,
)
from archy.diff_summary import summarize_diff
from archy.graph import (
    internal_module_count,
    zero_modules_reason,
)
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.layers import (
    LayerConfig,
    LayerConfigError,
    LayerCoverage,
    SdpViolation,
    compute_coverage,
    contracts_unverified,
    discover_config,
    find_reach_violations,
    find_sdp_violations,
    find_violations,
    load_config,
)
from archy.mcp_payloads import (
    BriefConventions,
    BriefLayer,
    BriefPayload,
    CheckErrorPayload,
    CheckPayload,
    ContractsPayload,
    DiffErrorPayload,
    ForbiddenEdge,
    InvariantBrief,
    LoadBearingModule,
    RequiredReach,
    ScoreComponents,
    ScoreGate,
    ScorePayload,
    SnapshotPayload,
    TrendRow,
    TrendRowInputs,
    TrendRowScore,
)
from archy.mcp_runs_core import _build_graph, _load_graph, _load_graph_pair, _rank_desc
from archy.risk import compute_edit_risk
from archy.scan_config import (
    graph_kwargs,
    resolve_exclude_type_checking,
    scan_kwargs,
)
from archy.score import Score, compute_score
from archy.simulate import EdgeSpec, SimulateReport, find_simulate


def score_components(score: Score) -> ScoreComponents:
    """Project a Score's five axes into the MCP ScoreComponents shape."""
    return ScoreComponents(
        modularity=score.modularity,
        acyclicity=score.acyclicity,
        depth=score.depth,
        equality=score.equality,
        complexity=score.complexity,
    )


def run_conventions(path: Path, *, min_family: int, include_tests: bool) -> ConventionsReport:
    if min_family < 2:
        raise ValueError(f"min_family must be >= 2; got {min_family}")
    return compute_conventions(
        path, **graph_kwargs(path), min_family=min_family, include_tests=include_tests
    )


def run_module_view(path: Path, module: str, *, include_tests: bool) -> ModuleView:
    """A lookup, not a ranking: every list complete, so absence is an answer.

    Kept a separate tool rather than a `module` parameter on `archy_conventions`
    because a union return type makes FastMCP wrap EVERY payload under a
    `result` key, silently changing the wire format of a released tool for
    consumers that never asked for this feature.
    """
    return compute_module_view(path, module, **graph_kwargs(path), include_tests=include_tests)


def run_brief(path: Path, *, top_n: int, with_contracts: bool, include_tests: bool) -> BriefPayload:
    """Compose conventions, coverage and (optionally) contracts, as `archy brief` does."""
    if top_n < 1:
        raise ValueError(f"top_n must be >= 1; got {top_n}")
    report = compute_conventions(path, **graph_kwargs(path), include_tests=include_tests)

    config, coverage, coverage_error = _brief_coverage(path)

    contracts = run_contracts(path, config_filename=None) if with_contracts else None
    checked, unverifiable_reason = (
        contracts_verdict(contracts) if contracts is not None else (False, None)
    )

    reason: str | None = None
    if config is not None and coverage is not None and not checked:
        reason = unverifiable_reason or _unverified_forbid_reason(config, coverage, contracts)

    return BriefPayload(
        conventions=_bound_conventions(report, top_n),
        coverage=coverage,
        coverage_error=coverage_error,
        contracts=contracts,
        transitive_checked=checked,
        transitive_unverified_reason=reason,
    )


def _brief_coverage(
    path: Path,
) -> tuple[LayerConfig | None, LayerCoverage | None, str | None]:
    config_path = discover_config(path)
    if config_path is None:
        return None, None, None
    try:
        config = load_config(config_path)
        return config, compute_coverage(_build_graph(path, **scan_kwargs(config)), config), None
    except LayerConfigError as exc:
        # Preserve the rest of the briefing, with the reason coverage is absent.
        return None, None, str(exc)


def _unverified_forbid_reason(
    config: LayerConfig, coverage: LayerCoverage, contracts: ContractsPayload | None
) -> str | None:
    if not contracts_unverified(config, coverage, []):
        return None
    n = len(config.forbid)
    stem = f"{n} forbid {'rule' if n == 1 else 'rules'} declared"
    if contracts is not None:
        return (
            f"{stem} and still unverified: archy_brief(contracts=True) "
            f"produced no transitive verdict ({contracts.error})."
        )
    return (
        f"{stem}, but this brief runs no direct-edge check and the layers "
        "govern too little of the graph to verify them; "
        "archy_brief(contracts=True) evaluates them transitively via import-linter."
    )


def _bound_conventions(report: ConventionsReport, top_n: int) -> BriefConventions:
    """Truncate the ranked sections to `top_n`, recording the untruncated totals."""
    fields = {name: getattr(report, name) for name in ConventionsReport.model_fields}
    fields.update(
        naming=report.naming[:top_n],
        surfaces=report.surfaces[:top_n],
        bases=report.bases[:top_n],
        registries=tuple(
            r.model_copy(update={"literal_names": r.literal_names[:6]})
            for r in report.registries[:top_n]
        ),
    )
    return BriefConventions(
        **fields,
        top_n=top_n,
        totals={
            "naming": len(report.naming),
            "surfaces": len(report.surfaces),
            "gates": len(report.gates),
            "errors": len(report.errors),
            "bases": len(report.bases),
            "registries": len(report.registries),
            "export_gaps": len(report.export_gaps),
            "doc_gaps": len(report.doc_gaps),
        },
    )


def run_score(
    path: Path,
    *,
    internal_only: bool,
    record: bool,
    strict: bool,
    strict_tolerance: float,
) -> ScorePayload:
    if not 0.0 <= strict_tolerance <= 1.0:
        raise ValueError(f"strict_tolerance must be in [0, 1]; got {strict_tolerance}")

    graph = _load_graph(path, internal_only=internal_only)
    score = compute_score(graph)
    if score.inputs.module_count == 0 and (record or strict):
        raise ValueError(
            f"{zero_modules_reason(path, 'scored')}; refusing to "
            f"{'record' if record else 'gate against history'}"
        )
    history_path = path / ".archy" / "history.jsonl"

    gate = _score_gate(history_path, score.overall, strict_tolerance) if strict else None

    if record:
        commit, branch = git_metadata(path)
        append_history(history_path, row_from_score(score, commit=commit, branch=branch))

    return ScorePayload(
        overall=score.overall,
        components=score_components(score),
        inputs=score.inputs,
        gate=gate,
        namespace_gaps=tuple(graph.graph.get("namespace_gaps", ())),
    )


def _score_gate(history_path: Path, current: float, tolerance: float) -> ScoreGate:
    """The strict gate against the last recorded score; a first run has nothing to fail."""
    rows = read_history(history_path)
    if not rows:
        return ScoreGate(
            previous=None, current=current, delta=None, tolerance=tolerance, passed=True
        )
    previous = rows[-1]
    delta = current - previous.overall
    return ScoreGate(
        previous=previous.overall,
        previous_commit=previous.commit,
        previous_timestamp=previous.timestamp,
        current=current,
        delta=delta,
        tolerance=tolerance,
        passed=delta >= -tolerance,
    )


def run_cycles(
    path: Path,
    *,
    min_size: int,
    internal_only: bool,
    exclude_type_checking: bool | None = None,
) -> list[Cycle] | CycleScan:
    """Cycles in `path`: a bare list, or a `CycleScan` when TYPE_CHECKING exclusion is on.

    The list shape is what every caller saw before #520, so it stays the default.
    Exclusion returns the object because an exclusion that empties the list
    would otherwise leave no trace of itself.
    """
    if min_size < 1:
        raise ValueError(f"min_size must be >= 1; got {min_size}")
    graph = _load_graph(path, internal_only=internal_only)
    if internal_module_count(graph) == 0:
        # A bare `[]` here would read as "no cycles"; an agent cannot tell it from
        # a clean project, so refuse with the reason (#523).
        raise ValueError(zero_modules_reason(path, "checked"))
    exclude = resolve_exclude_type_checking(path, exclude_type_checking)
    scan = scan_cycles(graph, min_size=min_size, exclude_type_checking=exclude)
    return scan if exclude or scan.namespace_gaps else list(scan.cycles)


def run_check(path: Path, *, config_path: Path | None) -> CheckPayload | CheckErrorPayload:
    if config_path is None:
        discovered = discover_config(path)
        if discovered is None:
            # Tier-3 recoverable precondition: no config to check against. In-band
            # (isError:false) so the agent can branch and create/point at a config,
            # not a raise. A *malformed* config below still raises (tier 2).
            return CheckErrorPayload(
                error=f"no archy.yaml found near {path}; pass config_path or create one."
            )
        config_path = discovered
    config = load_config(config_path)
    graph = _build_graph(path, **scan_kwargs(config))
    violations = find_violations(graph, config)
    reach_violations = find_reach_violations(graph, config)
    sdp_violations: list[SdpViolation] = []
    if config.sdp.enabled:
        sdp_violations = find_sdp_violations(graph, tolerance=config.sdp.tolerance)
    sdp_fails_gate = bool(sdp_violations) and config.sdp.mode == "error"
    coverage = compute_coverage(graph, config)
    # The presence floor gates `passed` here exactly as it gates the CLI's exit
    # code. Wiring it into one surface and not the other was the first cut, and
    # it left the AGENT-FACING surface reporting passed=true on the degenerate
    # single-module solution the check exists to catch: the case an agent in a
    # course-correction loop most needs to be told about.
    presence_fails = (
        config.min_layers_present is not None
        and coverage.layers_present < config.min_layers_present
    )
    unverified = contracts_unverified(config, coverage, violations)
    return CheckPayload(
        config_path=str(config_path),
        violations=tuple(violations),
        sdp_violations=tuple(sdp_violations),
        required_violations=tuple(reach_violations),
        passed=(
            not violations and not reach_violations and not sdp_fails_gate and not presence_fails
        ),
        coverage=coverage,
        presence_fails=presence_fails,
        min_layers_present=config.min_layers_present,
        namespace_gaps=tuple(graph.graph.get("namespace_gaps", ())),
        transitive_checked=False,
        transitive_unverified_reason=(
            (
                f"{len(config.forbid)} forbid "
                f"{'rule' if len(config.forbid) == 1 else 'rules'} declared, but this check "
                "governs too little of the graph to verify them; it sees DIRECT edges only. "
                "Call archy_check(contracts=True) to evaluate them transitively via "
                "import-linter, which sees paths a direct-edge check cannot."
            )
            if unverified
            else None
        ),
    )


def run_contracts(path: Path, *, config_filename: Path | None) -> ContractsPayload:
    from archy.contracts import (
        ContractsConfigError,
        ContractsNotAvailable,
        run_contracts,
    )

    try:
        result = run_contracts(path, config_filename=config_filename)
    except ContractsNotAvailable as exc:
        return ContractsPayload(available=False, error=str(exc))
    except ContractsConfigError as exc:
        return ContractsPayload(available=True, error=str(exc))

    return ContractsPayload(
        available=True,
        all_kept=result.all_kept,
        verified=result.verified,
        unverifiable=result.unverifiable,
        kept=result.kept,
        broken=result.broken,
        module_count=result.module_count,
        import_count=result.import_count,
        contracts=result.contracts,
        unscanned_dirs=result.unscanned_dirs,
    )


def contracts_verdict(contracts: ContractsPayload) -> tuple[bool, str | None]:
    """`(checked, unverifiable_reason)` for a contracts run, shared by every caller.

    Only a verdict counts as "checked": a missing import-linter leaves the rules
    as unverified as not asking (#343), and so does a contract that could not
    have failed (#457). The reason is set exactly when some contract could not
    have failed, whatever the direct edges govern. Shared by `archy_check` and
    `archy_brief` so the two cannot disagree about either (same rule as the
    CLI's `_transitive_checked`).
    """
    unverifiable = contracts.unverifiable or 0
    checked = contracts.available and contracts.error is None and unverifiable == 0
    reason = (
        f"{unverifiable} contract(s) could not have failed: their module patterns "
        "matched nothing, so the rule holds whatever the code does. Fix the "
        "patterns or drop the rule; the `contracts` field names the expressions "
        "that matched no module."
        if unverifiable
        else None
    )
    return checked, reason


_BRIEF_LOAD_BEARING_N = 5


def _build_invariant_brief(
    graph, config_path: Path | None, score: Score, cycles: tuple[Cycle, ...]
) -> InvariantBrief:
    """Recombine the snapshot's own data into the session-start brief.

    Layers/forbidden edges come from the same archy.yaml `take_snapshot`
    already loaded (so a malformed config has failed before reaching here);
    load-bearing modules are the top `edit_risk` nodes, the same ranking
    `archy_what_to_refactor_next(lens='structural')` reports.
    """
    layers: tuple[BriefLayer, ...] = ()
    forbidden: tuple[ForbiddenEdge, ...] = ()
    required: tuple[RequiredReach, ...] = ()
    if config_path is not None:
        config = load_config(config_path)
        layers = tuple(
            BriefLayer(name=layer.name, patterns=layer.patterns) for layer in config.layers
        )
        forbidden = tuple(
            ForbiddenEdge(from_layer=rule.from_layer, to_layer=rule.to_layer)
            for rule in config.forbid
        )
        required = tuple(
            RequiredReach(
                source=rule.source,
                must_reach=rule.must_reach,
                reason=rule.reason,
                within=rule.within,
            )
            for rule in config.required
        )

    ranked = _rank_desc(compute_edit_risk(graph).items())
    load_bearing = tuple(
        LoadBearingModule(module=name, edit_risk=risk)
        for name, risk in ranked[:_BRIEF_LOAD_BEARING_N]
    )

    return InvariantBrief(
        layers=layers,
        forbidden_edges=forbidden,
        required_reach=required,
        acyclic=not cycles,
        overall=score.overall,
        components=score_components(score),
        load_bearing=load_bearing,
    )


def run_snapshot(path: Path) -> SnapshotPayload:
    graph, full = _load_graph_pair(path)
    config_path = discover_config(path)
    snap = take_snapshot(graph, config_path=config_path, reach_graph=full)
    target = default_baseline_path(path)
    write_snapshot(snap, target)
    return SnapshotPayload(
        score=snap.score,
        cycles=snap.cycles,
        violations=snap.violations,
        sdp_violations=snap.sdp_violations,
        required_violations=snap.required_violations,
        baseline_path=str(target),
        invariant_brief=_build_invariant_brief(graph, config_path, snap.score, snap.cycles),
    )


def run_diff(
    path: Path, *, baseline: str | None = None, strict: bool = False
) -> DiffReport | DiffErrorPayload:
    if strict and baseline is None:
        # Same rule as the CLI: `.archy/baseline.json` is what archy_snapshot
        # overwrites, so a strict gate reading it could be grandfathered by the
        # very call the agent is told to make at the start of a task.
        create = create_baseline_command(path, path / "archy-baseline.json")
        return DiffErrorPayload(
            error=(
                "strict=True needs `baseline`: a committed file such as "
                f"archy-baseline.json, created with `{create}`. It never reads "
                ".archy/baseline.json, which archy_snapshot overwrites."
            )
        )
    target = default_baseline_path(path) if baseline is None else path / baseline
    if strict and (
        problem := strict_baseline_problem(path, target, suggested=path / "archy-baseline.json")
    ):
        return DiffErrorPayload(error=problem)
    try:
        snapshot = read_snapshot(target)
    except ValueError as exc:
        return DiffErrorPayload(error=str(exc))
    if snapshot is None:
        if baseline is not None:
            # A relative `baseline` resolves against `path` here, unlike the
            # CLI's cwd; say so, or the hint creates a second, drifting file.
            hint = missing_baseline_hint(path, target, committed=True)
            return DiffErrorPayload(error=f"{hint} (`baseline` is resolved relative to `path`.)")
        return DiffErrorPayload(
            error=f"no baseline at {target}; call archy_snapshot first to capture one."
        )
    graph, full = _load_graph_pair(path)
    config_path = discover_config(path)
    current = take_snapshot(graph, config_path=config_path, reach_graph=full)
    report = compute_diff(snapshot, current)
    update: dict[str, object] = {"summary": summarize_diff(report, graph)}
    if strict:
        update["strict"] = strict_verdict(
            snapshot,
            current,
            graph=graph,
            root=path,
            target=target,
            label=baseline or str(target),
            config_path=config_path,
            location_root=path,
        )
    return report.model_copy(update=update)


def run_simulate(path: Path, *, add: list[EdgeSpec], remove: list[EdgeSpec]) -> SimulateReport:
    graph, full = _load_graph_pair(path)
    return find_simulate(
        graph,
        add=[e.as_pair() for e in add],
        remove=[e.as_pair() for e in remove],
        config_path=discover_config(path),
        project_root=path,
        reach_graph=full,
    )


def run_trend(path: Path, *, last_n: int) -> list[TrendRow]:
    if last_n < 1:
        raise ValueError(f"last_n must be >= 1; got {last_n}")
    rows = read_history(path / ".archy" / "history.jsonl")
    window = rows[-last_n:]
    return [
        TrendRow(
            timestamp=r.timestamp,
            commit=r.commit,
            branch=r.branch,
            score=TrendRowScore(
                overall=r.overall,
                modularity=r.modularity,
                acyclicity=r.acyclicity,
                depth=r.depth,
                equality=r.equality,
                complexity=r.complexity,
            ),
            inputs=TrendRowInputs(
                module_count=r.module_count,
                edge_count=r.edge_count,
                cycle_count=r.cycle_count,
                tangle_ratio=r.tangle_ratio,
                max_depth=r.max_depth,
                community_count=r.community_count,
            ),
        )
        for r in window
    ]
