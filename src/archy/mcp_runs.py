"""The work behind each MCP tool, independent of the MCP framework.

Every ``run_*`` function here takes plain arguments and returns a payload from
``archy.mcp_payloads``; ``archy.mcp`` only registers them as tools and maps
expected refusals onto ``ToolError``. The warm-index plumbing (``_manager_for``
and friends) lives here too, because the ``run_*`` bodies are its only callers.

archy:owns        contracts_verdict, resolve_against, run_affected, run_brief,
                  run_check, run_contracts, run_conventions, run_cycles, run_diff,
                  run_dsm, run_duplicates, run_graph, run_graph_dump, run_graph_focus,
                  run_graph_summary, run_impact, run_module_view, run_score,
                  run_simulate, run_snapshot, run_trend, run_what_to_refactor_next,
                  score_components, validate_impact_mode
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar, cast

from archy.affected import Affected, find_affected
from archy.conventions import (
    ConventionsReport,
    ModuleView,
    compute_conventions,
    compute_module_view,
)
from archy.coupling import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_SUPPORT,
    co_changed_with,
    git_cochange,
    internal_module_paths,
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
from archy.dsm import (
    DSM,
    DSMDiff,
    DSMSummary,
    GroupBy,
    Weight,
    build_dsm,
    diff_dsm,
    read_dsm,
    summarize_dsm,
)
from archy.duplicates import (
    DuplicateGroup,
    classify_variants,
    compute_duplicates,
    compute_near_duplicates,
    demote_independent,
    is_test_path,
)
from archy.gitlog import (
    resolve_changed_paths,
    resolve_repo_root,
    resolve_since_log_args,
    since_window_empty,
)
from archy.graph import (
    DEFAULT_IGNORED_DIRS,
    ScanTooLargeError,
    UnresolvedHint,
    build_graph,
    discover_modules,
    graph_to_dict,
    hint_unresolved,
    internal_module_count,
    internal_subgraph,
    parse_project,
    resolve_modules,
    suggest_modules,
    zero_modules_reason,
)
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.hotspots import NoCommitsError, git_churn
from archy.impact import DEFAULT_MAX_CHAINS, Impact, find_impact
from archy.instability import compute_instability
from archy.layers import (
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
    DSMErrorPayload,
    DSMTooLargePayload,
    DuplicateGroupSummary,
    DuplicatesPayload,
    ForbiddenEdge,
    GraphEdge,
    GraphNode,
    GraphPayload,
    GraphSummaryEntry,
    GraphSummaryPayload,
    GraphTooLargePayload,
    InvariantBrief,
    LoadBearingModule,
    RefactorPriorityEntry,
    RequiredReach,
    ScoreComponents,
    ScoreGate,
    ScorePayload,
    SnapshotPayload,
    TrendRow,
    TrendRowInputs,
    TrendRowScore,
    WhatToRefactorPayload,
)
from archy.reach import compute_propagation_cost
from archy.refactor import compute_refactor_priorities
from archy.risk import compute_edit_risk
from archy.scan_config import (
    graph_kwargs,
    resolve_exclude_type_checking,
    resolve_max_modules,
    scan_kwargs,
)
from archy.score import Score, compute_score
from archy.simulate import EdgeSpec, SimulateReport, find_simulate
from archy.watcher import IndexManager


def score_components(score: Score) -> ScoreComponents:
    """Project a Score's five axes into the MCP ScoreComponents shape."""
    return ScoreComponents(
        modularity=score.modularity,
        acyclicity=score.acyclicity,
        depth=score.depth,
        equality=score.equality,
        complexity=score.complexity,
    )


# Cell-count ceiling for a full-mode DSM dump. Mirrors archy_graph's
# max_nodes guard: above this, the full matrix is refused with a pointer to
# the concise summary / focus / package escape hatches rather than dumping
# thousands of cells into the agent's context.
DEFAULT_MAX_DSM_CELLS = 2000


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

    coverage: LayerCoverage | None = None
    config = None
    config_path = discover_config(path)
    if config_path is not None:
        try:
            config = load_config(config_path)
            coverage = compute_coverage(_build_graph(path, **scan_kwargs(config)), config)
        except LayerConfigError:
            # A malformed archy.yaml is `archy_check`'s to report. Here it would
            # replace the whole briefing with one error.
            config = coverage = None

    contracts = run_contracts(path, config_filename=None) if with_contracts else None
    checked, unverifiable_reason = (
        contracts_verdict(contracts) if contracts is not None else (False, None)
    )

    reason: str | None = None
    if config is not None and coverage is not None and not checked:
        if unverifiable_reason:
            reason = unverifiable_reason
        elif contracts_unverified(config, coverage, []):
            n = len(config.forbid)
            stem = f"{n} forbid {'rule' if n == 1 else 'rules'} declared"
            if contracts is not None:
                reason = (
                    f"{stem} and still unverified: archy_brief(contracts=True) "
                    f"produced no transitive verdict ({contracts.error})."
                )
            else:
                reason = (
                    f"{stem}, but this brief runs no direct-edge check and the layers "
                    "govern too little of the graph to verify them; "
                    "archy_brief(contracts=True) evaluates them transitively via import-linter."
                )

    return BriefPayload(
        conventions=_bound_conventions(report, top_n),
        coverage=coverage,
        contracts=contracts,
        transitive_checked=checked,
        transitive_unverified_reason=reason,
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

    gate: ScoreGate | None = None
    if strict:
        rows = read_history(history_path)
        if rows:
            previous = rows[-1]
            delta = score.overall - previous.overall
            gate = ScoreGate(
                previous=previous.overall,
                previous_commit=previous.commit,
                previous_timestamp=previous.timestamp,
                current=score.overall,
                delta=delta,
                tolerance=strict_tolerance,
                passed=delta >= -strict_tolerance,
            )
        else:
            gate = ScoreGate(
                previous=None,
                current=score.overall,
                delta=None,
                tolerance=strict_tolerance,
                passed=True,
            )

    if record:
        commit, branch = git_metadata(path)
        append_history(history_path, row_from_score(score, commit=commit, branch=branch))

    return ScorePayload(
        overall=score.overall,
        components=score_components(score),
        inputs=score.inputs,
        gate=gate,
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
    return scan if exclude else list(scan.cycles)


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


def _rank_desc(pairs: Iterable[tuple[str, float]]) -> list[tuple[str, float]]:
    """`(name, value)` pairs ranked by value descending, ties by name ascending.

    The tie-break is part of the wire contract: summary lists must be stable
    across runs, and every ranked list in this module has to order ties the same
    way. One definition, so a change cannot land at one site and miss the others
    (#500).
    """
    return sorted(pairs, key=lambda t: (-t[1], t[0]))


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


def resolve_against(path: Path, files: list[Path]) -> list[Path]:
    """Anchor relative file args against the project root; leave absolutes alone."""
    return [path / f if not f.is_absolute() else f for f in files]


def run_impact(
    path: Path,
    *,
    files: list[Path],
    max_chains: int = DEFAULT_MAX_CHAINS,
    co_change: bool = False,
) -> Impact:
    if not files:
        raise ValueError("pass at least one file (got files=[])")
    if max_chains == 0:
        raise ValueError("--max-chains must be negative (for all) or positive (for a limit); got 0")
    graph = _load_graph(path, internal_only=True)
    result = find_impact(graph, resolve_changed_paths(path, files), max_chains=max_chains)
    if not co_change or not result.changed:
        return result
    # Opt-in behavioral overlay: surface modules that co-change with the edited
    # set but have no structural edge (so the blast radius above missed them).
    # Source-only (test co-change is mostly the module's own test) and best-effort
    # (no git -> empty). Exclude modules already in `impacted` - the point is the
    # HIDDEN dependents. Only this path reads git history; resolving `files`
    # may also ask git for the work-tree root.
    source_paths = frozenset(p for p in internal_module_paths(graph) if not is_test_path(p))
    cochange = git_cochange(path, keep_paths=source_paths)
    if cochange is None:
        return result
    hints = co_changed_with(
        graph,
        cochange,
        targets=frozenset(result.changed),
        exclude=frozenset(result.impacted),
        min_support=DEFAULT_MIN_SUPPORT,
        min_confidence=DEFAULT_MIN_CONFIDENCE,
    )
    return result.model_copy(update={"co_changed": tuple(hints)})


def run_affected(
    path: Path,
    *,
    files: list[Path],
    depth: int,
    test_filter: str | None,
) -> Affected:
    if not files:
        raise ValueError("pass at least one file (got files=[])")
    graph = _load_graph(path, internal_only=True)
    resolved = resolve_changed_paths(path, files)
    return find_affected(
        graph,
        resolved,
        project_root=path,
        depth=depth,
        test_filter=test_filter,
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


def run_graph_focus(
    path: Path,
    *,
    modules: list[str],
    depth: int,
    direction: str,
    internal_only: bool,
) -> GraphPayload:
    import networkx as nx

    if direction not in ("in", "out", "both"):
        raise ValueError(f"direction must be 'in', 'out', or 'both'; got {direction!r}")
    if depth < 0:
        raise ValueError(f"depth must be >= 0; got {depth}")

    graph = _load_graph(path, internal_only=internal_only)
    resolved, unresolved = resolve_modules(graph, modules, project_root=path)
    if not resolved:
        return _graph_payload_from(
            _empty_subgraph(graph),
            unresolved=tuple(unresolved),
            unresolved_hints=hint_unresolved(graph, unresolved, base=path),
        )

    reachable: set[str] = set(resolved)
    if direction in ("out", "both"):
        for seed in resolved:
            reachable |= set(nx.ego_graph(graph, seed, radius=depth).nodes())
    if direction in ("in", "both"):
        reverse = graph.reverse(copy=False)
        for seed in resolved:
            reachable |= set(nx.ego_graph(reverse, seed, radius=depth).nodes())

    sub = graph.subgraph(reachable).copy()
    sub.graph["root"] = graph.graph.get("root")
    sub.graph["parse_errors"] = graph.graph.get("parse_errors", ())
    return _graph_payload_from(
        sub,
        unresolved=tuple(unresolved),
        unresolved_hints=hint_unresolved(graph, unresolved, base=path),
    )


def _pagerank(graph, *, damping: float = 0.85, iterations: int = 50, tol: float = 1e-6) -> dict:
    # NetworkX 3.x's pagerank requires numpy/scipy. archy stays dependency-light,
    # so we hand-roll the power iteration. Identical formulation to the standard
    # damped random-walk PageRank with dangling-node redistribution.
    nodes = list(graph.nodes())
    n = len(nodes)
    if n == 0:
        return {}
    out_degree = {v: graph.out_degree(v) for v in nodes}
    pr = dict.fromkeys(nodes, 1.0 / n)
    teleport = (1.0 - damping) / n
    for _ in range(iterations):
        dangling_mass = damping * sum(pr[v] for v in nodes if out_degree[v] == 0) / n
        new_pr = {v: teleport + dangling_mass for v in nodes}
        for u in nodes:
            if out_degree[u]:
                share = damping * pr[u] / out_degree[u]
                for v in graph.successors(u):
                    new_pr[v] += share
        if sum(abs(new_pr[v] - pr[v]) for v in nodes) < tol:
            return new_pr
        pr = new_pr
    return pr


def run_graph_summary(path: Path, *, top_n: int) -> GraphSummaryPayload:
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1; got {top_n}")

    graph = _load_graph(path, internal_only=False)
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]
    internal_set = set(internal)

    internal_view = graph.subgraph(internal)
    instability = compute_instability(internal_view)
    _, propagation_cost = compute_propagation_cost(internal_view)
    edit_risk = compute_edit_risk(internal_view)

    internal_edge_count = internal_view.number_of_edges()
    external_edge_count = sum(
        1 for u, v in graph.edges() if u in internal_set and v not in internal_set
    )

    fan_in = _rank_desc((n, internal_view.in_degree(n)) for n in internal)
    fan_out = _rank_desc((n, internal_view.out_degree(n)) for n in internal)

    pagerank = _pagerank(internal_view)
    pr_sorted = _rank_desc(pagerank.items())

    risk_sorted = _rank_desc(edit_risk.items())

    external_counts: dict[str, int] = {}
    for _, v in graph.edges():
        if v not in internal_set and graph.nodes[v].get("external"):
            external_counts[v] = external_counts.get(v, 0) + 1
    ext_sorted = _rank_desc(external_counts.items())

    def _entries(
        pairs: list[tuple[str, float | int]],
        *,
        with_internal_metrics: bool,
    ) -> tuple[GraphSummaryEntry, ...]:
        return tuple(
            GraphSummaryEntry(
                module=name,
                value=float(value),
                instability=instability.get(name) if with_internal_metrics else None,
                propagation_cost=propagation_cost.get(name) if with_internal_metrics else None,
                edit_risk=edit_risk.get(name) if with_internal_metrics else None,
            )
            for name, value in pairs[:top_n]
        )

    return GraphSummaryPayload(
        module_count=len(internal),
        internal_edge_count=internal_edge_count,
        external_edge_count=external_edge_count,
        parse_errors=tuple(graph.graph.get("parse_errors", ())),
        top_fan_in=_entries(list(fan_in), with_internal_metrics=True),
        top_fan_out=_entries(list(fan_out), with_internal_metrics=True),
        top_pagerank=_entries(list(pr_sorted), with_internal_metrics=True),
        top_edit_risk=_entries(list(risk_sorted), with_internal_metrics=True),
        external_deps=_entries(list(ext_sorted), with_internal_metrics=False),
    )


def run_graph_dump(
    path: Path,
    *,
    internal_only: bool,
    max_nodes: int,
) -> GraphPayload | GraphTooLargePayload:
    if max_nodes < 1:
        raise ValueError(f"max_nodes must be >= 1; got {max_nodes}")
    graph = _load_graph(path, internal_only=internal_only)
    node_count = graph.number_of_nodes()
    if node_count > max_nodes:
        return GraphTooLargePayload(
            error=(
                f"graph has {node_count} nodes (> max_nodes={max_nodes}). "
                "Use archy_graph(focus=[...]) for a local slice or "
                "archy_graph(response_format='summary') for a top-N overview, or "
                "call archy_graph again with a higher max_nodes if you really "
                "want the full dump."
            ),
            node_count=node_count,
            max_nodes=max_nodes,
        )
    return _graph_payload_from(graph)


def _validate_response_format(response_format: str) -> None:
    if response_format not in ("summary", "full"):
        raise ValueError(f"response_format must be 'summary' or 'full'; got {response_format!r}")


def validate_impact_mode(mode: str) -> None:
    if mode not in ("blast", "affected"):
        raise ValueError(f"mode must be 'blast' or 'affected'; got {mode!r}")


def _validate_refactor_lens(lens: str) -> None:
    if lens not in ("fused", "behavioral", "structural"):
        raise ValueError(f"lens must be 'fused', 'behavioral', or 'structural'; got {lens!r}")


def _validate_group_by(group_by: str) -> None:
    if group_by not in ("community", "layer", "topological"):
        raise ValueError(
            f"group_by must be 'community', 'layer', or 'topological'; got {group_by!r}"
        )


def _validate_weight(weight: str) -> None:
    if weight not in ("imports", "calls"):
        raise ValueError(f"weight must be 'imports' or 'calls'; got {weight!r}")


def run_graph(
    path: Path,
    *,
    response_format: str,
    focus: list[str] | None = None,
    depth: int = 2,
    direction: str = "both",
    internal_only: bool,
    max_nodes: int,
    top_n: int,
) -> GraphSummaryPayload | GraphPayload | GraphTooLargePayload:
    """Route archy_graph between a focused subgraph, the summary, and the dump.

    `focus` (absorbing the old archy_graph_focus) takes precedence: a focused
    neighborhood is already bounded, so response_format/max_nodes/top_n do not
    apply to it. Otherwise summary is the default (top-N overview, identical to
    the old archy_graph_summary) and full is the opt-in dump, guarded by
    max_nodes. response_format is validated on every path so an invalid value
    raises even when focus is set.
    """
    _validate_response_format(response_format)
    if focus:
        return run_graph_focus(
            path,
            modules=focus,
            depth=depth,
            direction=direction,
            internal_only=internal_only,
        )
    if response_format == "summary":
        return run_graph_summary(path, top_n=top_n)
    return run_graph_dump(path, internal_only=internal_only, max_nodes=max_nodes)


def run_dsm(
    path: Path,
    *,
    response_format: str,
    group_by: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
    baseline_path: str | None,
) -> DSMSummary | DSM | DSMDiff | DSMTooLargePayload | DSMErrorPayload:
    """Route archy_dsm between the concise summary, the full matrix, and a diff.

    A diff (baseline_path set) is a deliberate, already-compact comparison
    (new_back_edges is the signal), not a dump, so response_format does not
    apply to it. The full matrix is capped at DEFAULT_MAX_DSM_CELLS cells.
    group_by and weight are validated up front too, before the scan, so a typo
    never pays for a full build only to fail on a raw pydantic error (#645).
    """
    _validate_response_format(response_format)
    if focus_depth < 0:
        raise ValueError(f"focus_depth must be >= 0; got {focus_depth}")
    _validate_group_by(group_by)
    _validate_weight(weight)
    graph = _load_graph(path, internal_only=False)
    current = build_dsm(
        graph,
        group_by=cast(GroupBy, group_by),
        weight=cast(Weight, weight),
        focus=focus,
        focus_depth=focus_depth,
        package=package,
        suggest=suggest_modules,
    )
    if baseline_path is not None:
        try:
            before = read_dsm(resolve_against(path, [Path(baseline_path)])[0])
        except ValueError as exc:
            return DSMErrorPayload(error=str(exc))
        if before is None:
            return DSMErrorPayload(error=f"no DSM snapshot at {baseline_path}")
        return diff_dsm(before, current)
    if response_format == "summary":
        return summarize_dsm(current)
    if len(current.cells) > DEFAULT_MAX_DSM_CELLS:
        return DSMTooLargePayload(
            error=(
                f"DSM has {len(current.cells)} cells (> max_cells="
                f"{DEFAULT_MAX_DSM_CELLS}). Read response_format='summary' for a "
                "compact overview, or narrow with focus=<qualname> or "
                "package=<prefix>."
            ),
            cell_count=len(current.cells),
            max_cells=DEFAULT_MAX_DSM_CELLS,
        )
    return current


def _summarize_duplicate_group(group: DuplicateGroup) -> DuplicateGroupSummary:
    first = group.members[0]
    return DuplicateGroupSummary(
        shape_hash=group.shape_hash,
        size=group.size,
        member_count=group.member_count,
        redundancy=group.redundancy,
        category=group.category,
        variant_reason=group.variant_reason,
        exact=group.exact,
        similarity=group.similarity,
        sample=f"{first.module}:{first.line} {first.qualified_name}",
    )


def run_duplicates(
    path: Path,
    *,
    response_format: str,
    min_nodes: int,
    top_n: int,
    members: int,
    co_change: bool = True,
    near_miss: bool = False,
) -> DuplicatesPayload:
    """Two-tier duplicate clusters for archy_duplicates.

    Reads per-function rows via the function-grained warm path (`parse_map`),
    classifies clusters into the primary/variant tiers, and shapes each tier by
    `response_format` (a per-cluster summary vs the full member lists). When
    `co_change` and git history is available, demotes primary clusters whose
    copies never co-change (issue #242); best-effort (no git -> unchanged). When
    `near_miss`, adds a lower-confidence Type-3 tier via token overlap (#246).
    """
    _validate_duplicates_args(response_format, min_nodes, top_n, members)
    modules, results = _parse_project(
        path, max_modules=resolve_max_modules(path), **graph_kwargs(path)
    )
    rows = classify_variants(
        compute_duplicates(modules, results, min_size=min_nodes, min_members=members)
    )
    if co_change:
        cochange = git_cochange(path, keep_paths=frozenset(str(m.path) for m in modules))
        if cochange is not None:
            rows = demote_independent(
                rows, counts=cochange.counts, pair_support=cochange.pair_support
            )
    near = compute_near_duplicates(modules, results, min_size=min_nodes) if near_miss else []
    dups = [g for g in rows if g.category == "duplicate"]
    variants = [g for g in rows if g.category == "variant"]

    def _shape(groups: list[DuplicateGroup]):
        top = groups[:top_n]
        if response_format == "full":
            return tuple(top)
        return tuple(_summarize_duplicate_group(g) for g in top)

    return DuplicatesPayload(
        min_nodes=min_nodes,
        total=len(dups),
        exact_total=sum(1 for g in dups if g.exact),
        variant_total=len(variants),
        near_total=len(near),
        shown=min(top_n, len(dups)),
        duplicated_functions=sum(g.member_count for g in dups),
        duplicates=_shape(dups),
        variants=_shape(variants),
        near_miss=_shape(near),
        note=_duplicates_note(len(dups), len(variants), min_nodes),
    )


def _validate_duplicates_args(
    response_format: str, min_nodes: int, top_n: int, members: int
) -> None:
    _validate_response_format(response_format)
    if min_nodes < 1:
        raise ValueError(f"min_nodes must be >= 1; got {min_nodes}")
    if top_n < 1:
        raise ValueError(f"top_n must be >= 1; got {top_n}")
    if members < 2:
        raise ValueError(f"members must be >= 2; got {members}")


def _duplicates_note(dup_count: int, variant_count: int, min_nodes: int) -> str | None:
    if dup_count:
        return None
    note = (
        f"No likely-duplicate function bodies of >= {min_nodes} normalized "
        "nodes found; lower min_nodes to widen the search."
    )
    if variant_count:
        note += (
            f" ({variant_count} likely-intentional variant(s) were found but demoted: "
            "same-class / boilerplate / test / vendored / independent.)"
        )
    return note


def _empty_subgraph(graph):
    import networkx as nx

    empty: nx.DiGraph = nx.DiGraph()
    empty.graph["root"] = graph.graph.get("root")
    empty.graph["parse_errors"] = graph.graph.get("parse_errors", ())
    return empty


def _graph_payload_from(
    graph,
    *,
    unresolved: tuple[str, ...] = (),
    unresolved_hints: tuple[UnresolvedHint, ...] = (),
) -> GraphPayload:
    data = graph_to_dict(graph)
    nodes = tuple(
        GraphNode(
            id=n["id"],
            external=bool(n.get("external", False)),
            path=n.get("path"),
            is_package=n.get("is_package"),
            instability=n.get("instability"),
            propagation_cost=n.get("propagation_cost"),
            edit_risk=n.get("edit_risk"),
        )
        for n in data["nodes"]
    )
    edges = tuple(
        GraphEdge(
            source=e["source"],
            target=e["target"],
            is_relative=bool(e.get("is_relative", False)),
            lines=tuple(e.get("lines", ())),
        )
        for e in data["edges"]
    )
    return GraphPayload(
        root=data["root"],
        parse_errors=tuple(data["parse_errors"]),
        nodes=nodes,
        edges=edges,
        unresolved=unresolved,
        unresolved_hints=unresolved_hints,
    )


def _refactor_note(
    *,
    lens: str,
    rows: list,
    git_available: bool,
    path: Path,
    min_risk: float,
    no_commits: bool = False,
    empty_window: bool = False,
    since: str | None = None,
) -> str | None:
    """Lens-aware empty/degraded note for archy_what_to_refactor_next.

    behavioral needs git; structural never does; fused runs both and degrades
    to structural-only off git. A non-empty single-lens result needs no note.
    An empty --since window is named so it is not mistaken for a calm codebase.
    """
    missing_git = (
        f"{path} has no commit history yet"
        if no_commits
        else f"{path} is not inside a git repository (or git is unavailable)"
    )
    if rows:
        if lens == "fused" and not git_available:
            return (
                f"{missing_git}; "
                "the CC x churn behavioral lens was skipped and this ranking is "
                "structural-only (edit-risk). Run inside git for the fused view."
            )
        return None
    if empty_window and since and git_available:
        return (
            f"No commits in the --since window ({since}). "
            "Widen the window or omit --since to use full history."
        )
    return _empty_lens_note(lens, git_available, missing_git, min_risk)


def _empty_lens_note(lens: str, git_available: bool, missing_git: str, min_risk: float) -> str:
    """The note for a lens that surfaced nothing, worded per lens and per git state."""
    if lens == "behavioral":
        if not git_available:
            return (
                f"{missing_git}; "
                "the behavioral lens needs git history to compute per-file churn. "
                "Use lens='structural' for a git-free 'is this edit dangerous?' "
                "signal instead."
            )
        return (
            "No behavioral hotspots surfaced: no file is both complex and "
            "frequently changed (CC x churn). The project is likely small, "
            "young, or structurally clean."
        )
    if lens == "structural":
        return (
            f"No module is central and fragile above min_risk={min_risk}. Lower "
            "min_risk (pass 0 to surface every module) to widen the lens."
        )
    # fused: the only lens left once behavioral and structural have returned
    if git_available:
        return (
            "No refactoring priorities surfaced: no file is both complex and "
            "frequently changed (CC x churn hotspots: 0), and no module is "
            f"central and fragile above min_risk={min_risk} (0). The project is "
            "likely small, young, or structurally clean - there is genuinely "
            "nothing to prioritize right now. Lower min_risk to widen the "
            "structural lens."
        )
    return (
        f"{missing_git}, so the "
        "CC x churn behavioral lens was skipped and only the structural "
        f"edit-risk lens ran. No module is central and fragile above "
        f"min_risk={min_risk}, so nothing was surfaced. Lower min_risk to widen "
        "the structural lens."
    )


def _validate_refactor_args(
    path: Path, lens: str, top_n: int, since: str | None, min_risk: float
) -> None:
    _validate_refactor_lens(lens)
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1; got {top_n}")
    if not 0.0 <= min_risk <= 1.0:
        raise ValueError(f"min_risk must be in [0, 1]; got {min_risk}")
    # Refuse a garbage `since` even when lens='structural' skips the git call,
    # so a typo cannot silently look like "nothing to prioritize".
    if since is not None:
        repo = resolve_repo_root(path)
        if repo is not None:
            resolve_since_log_args(repo, since)


def _refactor_churn(path: Path, lens: str, since: str | None):
    """Per-file churn and whether the repo has no commits at all.

    The behavioral lens needs git; the structural lens never does, so skip the
    git call entirely for structural (since is irrelevant there). A repo with
    zero commits degrades the same way but gets its own note wording.
    """
    if lens == "structural":
        return None, False
    try:
        return git_churn(path, since=since), False
    except NoCommitsError:
        return None, True


def _rows_for_lens(rows: list, lens: str) -> list:
    if lens != "behavioral":
        # structural: churn=None already restricts rows to the edit_risk lens, sorted
        # by priority (== normalized edit_risk), matching the old high-risk ranking.
        return rows
    # Keep only rows the behavioral lens fired on and restore the pure
    # CC x churn ranking (the fused `priority` would let a both-lens module
    # outrank a bigger pure hotspot).
    return sorted(
        (r for r in rows if "hotspot" in r.lenses),
        key=lambda r: (-r.hotspot_score, r.module),
    )


def run_what_to_refactor_next(
    path: Path, *, lens: str = "fused", top_n: int, since: str | None, min_risk: float
) -> WhatToRefactorPayload:
    _validate_refactor_args(path, lens, top_n, since, min_risk)

    graph = _load_graph(path, internal_only=True)
    churn, no_commits = _refactor_churn(path, lens, since)
    git_available = churn is not None

    rows = _rows_for_lens(compute_refactor_priorities(graph, churn=churn, min_risk=min_risk), lens)
    shown = rows[:top_n]
    entries = tuple(
        RefactorPriorityEntry(
            module=r.module,
            path=r.path,
            lenses=r.lenses,
            priority=r.priority,
            cc_sum=r.cc_sum,
            churn=r.churn,
            hotspot_score=r.hotspot_score,
            edit_risk=r.edit_risk,
            propagation_cost=r.propagation_cost,
            instability=r.instability,
            fan_in=r.fan_in,
            rationale=r.rationale,
        )
        for r in shown
    )

    empty_window = False
    if since and git_available and not rows:
        repo = resolve_repo_root(path)
        if repo is not None:
            empty_window = since_window_empty(repo, since)
    note = _refactor_note(
        lens=lens,
        rows=rows,
        git_available=git_available,
        path=path,
        min_risk=min_risk,
        no_commits=no_commits,
        empty_window=empty_window,
        since=since,
    )

    return WhatToRefactorPayload(
        since=since,
        min_risk=min_risk,
        git_available=git_available,
        total=len(rows),
        shown=len(entries),
        priorities=entries,
        note=note,
    )


_ManagerKey = tuple[str, frozenset[str], tuple[str, ...]]
_MANAGERS: dict[_ManagerKey, IndexManager] = {}
_MANAGERS_LOCK = threading.Lock()


def _manager_cache_key(root: Path, kwargs: dict) -> _ManagerKey:
    """Config-aware cache key for ``_manager_for``.

    The key folds in the graph-building kwargs (``ignored_dirs`` /
    ``extra_roots``) normalized exactly as ``IndexManager.__init__`` normalizes
    them, so two calls with the same effective config share a manager while a
    call with a *different* config gets its own. Keying on ``root`` alone would
    pin the first config seen and silently ignore later kwargs.
    """
    ignored = frozenset(kwargs.get("ignored_dirs", DEFAULT_IGNORED_DIRS))
    extra = tuple(kwargs.get("extra_roots", ()))
    return (str(root), ignored, extra)


def _manager_for(path: Path, *, max_modules: int | None = None, **kwargs) -> IndexManager:
    """Get-or-create the per-(root, config) IndexManager, starting its watcher once.

    Managers live for the server's lifetime: one persistent cache connection
    and one debounced watcher per project, so repeated tool calls reuse a warm,
    background-synced index. ``kwargs`` (ignored_dirs / extra_roots) are part of
    the cache key, so changing the discovered config produces a fresh manager
    rather than reusing one built with stale kwargs.

    ``max_modules`` is the scan-size backstop (see graph.DEFAULT_MAX_MODULES). It
    is enforced here, on first creation, via a cheap discovery walk BEFORE the
    recursive watcher is scheduled -- scheduling a watcher over a 40k-file
    vendored tree is itself costly, so a `ScanTooLargeError` must short-circuit
    before `start_watching`. It is intentionally NOT part of the cache key (it is
    a guard, not part of the manager's identity).
    """
    root = path.resolve()
    key = _manager_cache_key(root, kwargs)
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            if max_modules and max_modules > 0:
                count = len(
                    discover_modules(
                        root,
                        ignored_dirs=kwargs.get("ignored_dirs", DEFAULT_IGNORED_DIRS),
                        extra_roots=kwargs.get("extra_roots", ()),
                    )
                )
                if count > max_modules:
                    raise ScanTooLargeError(count, root, max_modules)
            # The config for this root changed (different key, same path): retire
            # any manager built with the superseded config so we don't leak its
            # watcher thread + cache connection on the same directory. A root has
            # exactly one live config at a time, so at most one manager per root.
            for stale_key in [k for k in _MANAGERS if k[0] == key[0]]:
                _MANAGERS.pop(stale_key).stop()
            manager = IndexManager(root, **kwargs)
            manager.start_watching()  # best-effort; on-demand sync works regardless
            _MANAGERS[key] = manager
        return manager


def _build_graph(path: Path, *, max_modules: int | None = None, **kwargs):
    """Cache-backed build for the long-lived MCP server (its hot path).

    Routes through a per-root IndexManager (persistent connection + background
    watcher). Falls back to a cold `build_graph` if the cache cannot be used
    (read-only filesystem, permission error): the index is an optimization,
    never a dependency, so a tool call must still succeed without it.

    A `ScanTooLargeError` from the guard is NOT a cache failure, so it propagates
    rather than being retried by the cold path (which would re-raise anyway).
    """
    return _warm_or_cold(path, max_modules, kwargs, IndexManager.build_graph, build_graph)


def _parse_project(path: Path, *, max_modules: int | None = None, **kwargs):
    """Function-grained warm path: `(modules, parse_results)` for archy_duplicates.

    The same warm-then-cold routing as `_build_graph`, through the manager's
    `parse_map` and falling back to `graph.parse_project`.
    """
    return _warm_or_cold(path, max_modules, kwargs, IndexManager.parse_map, parse_project)


_T = TypeVar("_T")


def _warm_or_cold(
    path: Path,
    max_modules: int | None,
    kwargs: dict,
    warm: Callable[[IndexManager], _T],
    cold: Callable[..., _T],
) -> _T:
    """`warm(manager)` via the per-root IndexManager, else `cold(path, ...)`.

    Only a cache failure (`sqlite3.Error`, `OSError`) takes the cold path. A
    `ScanTooLargeError` from the guard is not one, so it propagates rather than
    being retried by a cold scan that would raise it again.
    """
    try:
        return warm(_manager_for(path, max_modules=max_modules, **kwargs))
    except (sqlite3.Error, OSError):
        return cold(path, max_modules=max_modules, **kwargs)


def _load_graph(path: Path, *, internal_only: bool):
    graph = _build_graph(path, max_modules=resolve_max_modules(path), **graph_kwargs(path))
    if internal_only:
        external = {n for n, d in graph.nodes(data=True) if d.get("external")}
        graph.remove_nodes_from(external)
    return graph


def _load_graph_pair(path: Path):
    """Return `(internal_only, full)` from ONE scan. Mirrors cli._load_graph_pair.

    Score and cycles want the internal-only graph; required-reach rules need the
    full one, because `must_reach` may name an external package.
    """
    full = _load_graph(path, internal_only=False)
    return internal_subgraph(full), full
