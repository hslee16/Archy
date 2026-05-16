"""MCP server exposing archy's analysis as tools an AI agent can call.

Built on the official Python `mcp` SDK using its FastMCP API. The
twelve tools mirror the CLI surface (`archy_score`, `archy_cycles`,
`archy_check`, `archy_contracts`, `archy_trend`, `archy_impact`,
`archy_snapshot`, `archy_diff`, `archy_record_baseline`) plus three
graph-navigation tools (`archy_graph_focus`, `archy_graph_summary`,
`archy_graph`) so an agent can treat archy as a structural sensor in
its own feedback loop, the way the README pitches.

The server runs over stdio (the MCP convention for local tools); start
it from the CLI via `archy mcp`.

Tool returns are pydantic models; FastMCP serializes them to JSON for
the MCP client. The model shapes are the public wire contract for any
agent calling these tools.
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from archy.contracts import ContractCheck
from archy.cycles import Cycle, find_cycles
from archy.diff import (
    DiffReport,
    compute_diff,
    read_snapshot,
    take_snapshot,
    write_snapshot,
)
from archy.graph import DEFAULT_IGNORED_DIRS, build_graph, graph_to_dict, resolve_modules
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.hotspots import compute_hotspots, git_churn
from archy.impact import Impact, find_impact
from archy.instability import compute_instability
from archy.layers import (
    LayerConfigError,
    SdpViolation,
    Violation,
    discover_config,
    find_sdp_violations,
    find_violations,
    load_config,
)
from archy.reach import compute_propagation_cost
from archy.risk import compute_edit_risk
from archy.score import Score, ScoreInputs, compute_score

_AGENT_LOOP_PROMPT = """\
# archy agent loop

archy turns the project's structural health into a number you can act on
between edits. The loop is:

1. **Snapshot** at session start so you have a baseline:
   `archy_snapshot(path)`
2. **Look up impact** before editing a module so you know who breaks if
   the change is wrong:
   `archy_impact(path, files=[<file you plan to edit>])`

   For a bounded, bidirectional neighborhood with edge line numbers,
   use `archy_graph_focus(path, modules=[<file or qualname>])` instead.
   `archy_graph_summary(path)` gives a top-N overview when you don't yet
   know which module to look at. Before a non-trivial edit, call
   `archy_high_risk_modules(path)` to see whether your target sits in
   the project's central-and-fragile zone (high blast radius combined
   with high instability); if it does, scope down or pause for review.
3. **Edit** the code as you normally would.
4. **Diff** after the edit to see what got better, what got worse, and
   exactly which cycles or layer rules changed:
   `archy_diff(path)`
5. If `score_delta.overall` dropped or `cycles.added` / `violations.added`
   are non-empty, the change introduced regressions. Inspect the named
   modules, fix or revert, then loop back to step 4. Recurse until the
   diff is clean.

`archy_score(path, strict=True)` is a one-shot gate against the last
recorded run; it's lighter than the snapshot/diff loop and useful as
the final pre-commit check.
"""


# --- response models ----------------------------------------------------------


class ScoreComponents(BaseModel):
    model_config = ConfigDict(frozen=True)

    modularity: float
    acyclicity: float
    depth: float
    equality: float
    complexity: float


class ScoreGate(BaseModel):
    model_config = ConfigDict(frozen=True)

    previous: float | None
    previous_commit: str | None = None
    previous_timestamp: str | None = None
    current: float
    delta: float | None
    tolerance: float
    passed: bool


class ScorePayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: float
    components: ScoreComponents
    inputs: ScoreInputs
    gate: ScoreGate | None = None


class CheckPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_path: str
    violations: tuple[Violation, ...]
    sdp_violations: tuple[SdpViolation, ...] = ()
    passed: bool


class ContractsPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    error: str | None = None
    all_kept: bool | None = None
    kept: int | None = None
    broken: int | None = None
    module_count: int | None = None
    import_count: int | None = None
    contracts: tuple[ContractCheck, ...] = ()


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: Score
    cycles: tuple[Cycle, ...]
    violations: tuple[Violation, ...]
    sdp_violations: tuple[SdpViolation, ...] = ()
    baseline_path: str


class DiffErrorPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: str


class TrendRowScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: float
    modularity: float
    acyclicity: float
    depth: float
    equality: float
    # Optional: rows written by archy < 0.20 don't have a complexity axis.
    complexity: float | None = None


class TrendRowInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    module_count: int
    edge_count: int
    cycle_count: int
    tangle_ratio: float
    max_depth: int
    community_count: int


class TrendRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: str
    commit: str | None
    branch: str | None
    score: TrendRowScore
    inputs: TrendRowInputs


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    external: bool
    path: str | None = None
    is_package: bool | None = None
    instability: float | None = None
    propagation_cost: float | None = None
    edit_risk: float | None = None


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    is_relative: bool
    lines: tuple[int, ...]


class GraphPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str | None
    parse_errors: tuple[str, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    unresolved: tuple[str, ...] = ()


class GraphTooLargePayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: str
    node_count: int
    max_nodes: int


class GraphSummaryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    module: str
    value: float
    instability: float | None = None
    propagation_cost: float | None = None
    edit_risk: float | None = None


class GraphSummaryPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    module_count: int
    internal_edge_count: int
    external_edge_count: int
    parse_errors: tuple[str, ...]
    top_fan_in: tuple[GraphSummaryEntry, ...]
    top_fan_out: tuple[GraphSummaryEntry, ...]
    top_pagerank: tuple[GraphSummaryEntry, ...]
    top_edit_risk: tuple[GraphSummaryEntry, ...]
    external_deps: tuple[GraphSummaryEntry, ...]


class HighRiskEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    module: str
    edit_risk: float
    propagation_cost: float
    instability: float
    fan_in: int


class HighRiskPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    module_count: int
    modules: tuple[HighRiskEntry, ...]


class HotspotEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    module: str
    path: str
    cc_sum: int
    churn: int
    score: int


class HotspotsPayload(BaseModel):
    """Per-file CC x churn ranking. `note` is set when the metric
    cannot run (project is not under git), and `hotspots` is empty -
    the agent should pivot to `archy_high_risk_modules` for a
    git-free structural alternative."""

    model_config = ConfigDict(frozen=True)

    since: str | None
    total: int
    shown: int
    hotspots: tuple[HotspotEntry, ...]
    note: str | None = None


def create_server() -> FastMCP:
    server: FastMCP = FastMCP("archy")
    _register_tools(server)
    _register_prompts(server)
    return server


def _register_prompts(server: FastMCP) -> None:
    @server.prompt(
        name="loop",
        description=(
            "How to use archy as an architectural feedback loop while editing code. "
            "Read this at session start so subsequent tool calls follow the right "
            "snapshot -> edit -> diff cadence."
        ),
    )
    def loop() -> str:
        return _AGENT_LOOP_PROMPT


def _register_tools(server: FastMCP) -> None:
    @server.tool(
        name="archy_score",
        description=(
            "Compute the composite quality score (modularity, acyclicity, depth, "
            "equality, complexity - geometric mean of five axes) for a Python "
            "project. Optionally append "
            "the result to .archy/history.jsonl and/or compare against the most "
            "recent recorded run as a regression gate."
        ),
    )
    def archy_score(
        path: str,
        internal_only: bool = True,
        record: bool = False,
        strict: bool = False,
        strict_tolerance: float = 0.02,
    ) -> ScorePayload:
        return _run_score(
            Path(path),
            internal_only=internal_only,
            record=record,
            strict=strict,
            strict_tolerance=strict_tolerance,
        )

    @server.tool(
        name="archy_cycles",
        description=(
            "Find import cycles (Tarjan SCCs of size >= min_size, plus self-loops) "
            "in a Python project. Returns cycles sorted largest-first."
        ),
    )
    def archy_cycles(
        path: str,
        min_size: int = 2,
        internal_only: bool = True,
    ) -> list[Cycle]:
        return _run_cycles(Path(path), min_size=min_size, internal_only=internal_only)

    @server.tool(
        name="archy_check",
        description=(
            "**Call after any Python edit that adds, removes, or changes an "
            "import statement.** Returns forbidden direct edges between layers "
            "declared in archy.yaml under `violations`, plus Stable Dependencies "
            "Principle violations (when `sdp.enabled: true` in archy.yaml) under "
            "`sdp_violations`. Empty lists on both mean no direct boundary "
            "crossings; pair with archy_contracts for transitive (multi-hop) "
            "checks."
        ),
    )
    def archy_check(
        path: str,
        config_path: str | None = None,
    ) -> CheckPayload:
        return _run_check(Path(path), config_path=Path(config_path) if config_path else None)

    @server.tool(
        name="archy_contracts",
        description=(
            "**Call after any Python edit that adds, removes, or changes an "
            "import statement, especially across package boundaries.** A "
            "failed contract means the new import violates the architecture - "
            "revert or restructure before continuing. Runs import-linter "
            "contracts (transitive Layers, Forbidden, Independence, Protected, "
            "AcyclicSiblings); stricter than archy_check, which only catches "
            "direct edges between layers in archy.yaml. Reads .importlinter "
            "(or pyproject.toml). Requires `pip install archy[contracts]`."
        ),
    )
    def archy_contracts(
        path: str,
        config_path: str | None = None,
    ) -> ContractsPayload:
        return _run_contracts(
            Path(path),
            config_filename=Path(config_path) if config_path else None,
        )

    @server.tool(
        name="archy_trend",
        description=(
            "Read the recent score history (.archy/history.jsonl) for a Python "
            "project. Returns up to last_n rows ordered oldest-first so an agent "
            "can compare deltas."
        ),
    )
    def archy_trend(path: str, last_n: int = 10) -> list[TrendRow]:
        return _run_trend(Path(path), last_n=last_n)

    @server.tool(
        name="archy_impact",
        description=(
            "Given a list of changed file paths, return the internal modules "
            "that transitively import any of them (the blast radius). Use "
            "before refactoring or removing a module to see what would break. "
            "Files that don't resolve to any module in the graph are returned "
            "in `unresolved`. `propagation_cost` is the MacCormack-style "
            "blast-radius scalar: fraction of the project's internal module "
            "count that this edit set can reach (changed plus impacted, over "
            "total internal modules). Higher values mean the edit is more "
            "structurally consequential."
        ),
    )
    def archy_impact(
        path: str,
        files: list[str],
    ) -> Impact:
        return _run_impact(Path(path), files=[Path(f) for f in files])

    @server.tool(
        name="archy_snapshot",
        description=(
            "Capture score, cycles, and layer violations to .archy/baseline.json "
            "as a baseline that archy_diff will compare against. Call at the "
            "start of an editing session. See the `loop` prompt for full usage."
        ),
    )
    def archy_snapshot(path: str) -> SnapshotPayload:
        return _run_snapshot(Path(path))

    @server.tool(
        name="archy_diff",
        description=(
            "Compare the current project state to the last snapshot. Returns "
            "per-component score deltas plus the cycles and layer violations "
            "that have been added or resolved since the baseline. Use after "
            "edits to localize regressions; see the `loop` prompt."
        ),
    )
    def archy_diff(path: str) -> DiffReport | DiffErrorPayload:
        return _run_diff(Path(path))

    @server.tool(
        name="archy_record_baseline",
        description=(
            "Compute the score for a Python project AND append it to "
            ".archy/history.jsonl. Convenience wrapper for archy_score(record=True). "
            "Use at the start of an agent session so a later archy_score(strict=True) "
            "can detect degradation."
        ),
    )
    def archy_record_baseline(path: str, internal_only: bool = True) -> ScorePayload:
        return _run_score(
            Path(path),
            internal_only=internal_only,
            record=True,
            strict=False,
            strict_tolerance=0.02,
        )

    @server.tool(
        name="archy_graph_focus",
        description=(
            "Return a subgraph centered on one or more modules. Pass qualnames "
            "(e.g. 'archy.parser') or file paths. `depth` caps hop distance; "
            "`direction` is 'in' (who depends on me), 'out' (my dependencies), "
            "or 'both'. Each node carries instability (Martin's I); each edge "
            "carries the source line numbers of the import statements. Prefer "
            "this over archy_impact when you want forward dependencies, "
            "edge-level detail, or a bounded blast radius."
        ),
    )
    def archy_graph_focus(
        path: str,
        modules: list[str],
        depth: int = 1,
        direction: str = "both",
        internal_only: bool = True,
    ) -> GraphPayload:
        return _run_graph_focus(
            Path(path),
            modules=modules,
            depth=depth,
            direction=direction,
            internal_only=internal_only,
        )

    @server.tool(
        name="archy_graph_summary",
        description=(
            "Whole-project structural overview sized for LLM context. Returns "
            "top-N modules by fan-in, fan-out, and PageRank (importance "
            "weighted by importance of dependents), plus the top external "
            "dependencies. Cheaper than dumping the full graph; use for "
            "'where is the gravity in this codebase' questions. Call "
            "archy_cycles separately for cycle detail."
        ),
    )
    def archy_graph_summary(path: str, top_n: int = 20) -> GraphSummaryPayload:
        return _run_graph_summary(Path(path), top_n=top_n)

    @server.tool(
        name="archy_graph",
        description=(
            "Full dependency-graph dump matching `archy graph --format json`. "
            "Refuses to serialize graphs larger than `max_nodes` (default 500) "
            "to avoid blowing the agent's context; bump the limit explicitly "
            "if you really want everything. For most reasoning, prefer "
            "archy_graph_focus (local neighborhood) or archy_graph_summary "
            "(top-N overview)."
        ),
    )
    def archy_graph(
        path: str,
        internal_only: bool = True,
        max_nodes: int = 500,
    ) -> GraphPayload | GraphTooLargePayload:
        return _run_graph_dump(
            Path(path),
            internal_only=internal_only,
            max_nodes=max_nodes,
        )

    @server.tool(
        name="archy_high_risk_modules",
        description=(
            "Return the top-N internal modules ranked by edit-risk: the "
            "geometric mean of MacCormack propagation cost, normalized "
            "fan-in, and Martin's instability. High score means editing is "
            "both expensive (wide blast radius, many direct importers) and "
            "likely to need iteration (the module itself depends on many "
            "things). Call before a non-trivial edit to decide whether to "
            "scope down, snapshot more aggressively, or pause for human "
            "review. Each entry breaks the composite back out into its "
            "components so you can see *why* a module ranks high."
        ),
    )
    def archy_high_risk_modules(path: str, top_n: int = 10) -> HighRiskPayload:
        return _run_high_risk_modules(Path(path), top_n=top_n)

    @server.tool(
        name="archy_hotspots",
        description=(
            "Rank internal modules by cyclomatic complexity x git "
            "churn (Tornhill / CodeScene's 'Code Red'). Each entry is "
            "`{module, path, cc_sum, churn, score}` where "
            "`score = cc_sum * churn`. Files with zero CC or zero "
            "churn are filtered so the top-K only contains files that "
            "score on both axes. The structural cousin "
            "`archy_high_risk_modules` answers 'is this edit "
            "dangerous?' without needing git history; `archy_hotspots` "
            "answers 'where is the refactoring leverage?' and needs "
            "git. `since` is passed straight to `git log --since` "
            "(e.g. '12.months', '2025-01-01'); the default is full "
            "history. If the project isn't under git, the tool "
            "returns an empty list plus a `note` explaining why so "
            "the agent can pivot to `archy_high_risk_modules` "
            "instead."
        ),
    )
    def archy_hotspots(
        path: str,
        top: int = 20,
        since: str | None = None,
    ) -> HotspotsPayload:
        return _run_hotspots(Path(path), top=top, since=since)


# --- thin internals ----------------------------------------------------------


def _run_score(
    path: Path,
    *,
    internal_only: bool,
    record: bool,
    strict: bool,
    strict_tolerance: float,
) -> ScorePayload:
    graph = _load_graph(path, internal_only=internal_only)
    score = compute_score(graph)
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
        components=ScoreComponents(
            modularity=score.modularity,
            acyclicity=score.acyclicity,
            depth=score.depth,
            equality=score.equality,
            complexity=score.complexity,
        ),
        inputs=score.inputs,
        gate=gate,
    )


def _run_cycles(path: Path, *, min_size: int, internal_only: bool) -> list[Cycle]:
    graph = _load_graph(path, internal_only=internal_only)
    return list(find_cycles(graph, min_size=min_size))


def _run_check(path: Path, *, config_path: Path | None) -> CheckPayload:
    if config_path is None:
        discovered = discover_config(path)
        if discovered is None:
            raise LayerConfigError(
                f"no archy.yaml found near {path}; pass config_path or create one."
            )
        config_path = discovered
    config = load_config(config_path)
    graph = build_graph(
        path,
        ignored_dirs=DEFAULT_IGNORED_DIRS | frozenset(config.exclude),
        extra_roots=config.roots,
    )
    violations = find_violations(graph, config)
    sdp_violations: list[SdpViolation] = []
    if config.sdp.enabled:
        sdp_violations = find_sdp_violations(graph, tolerance=config.sdp.tolerance)
    sdp_fails_gate = bool(sdp_violations) and config.sdp.mode == "error"
    return CheckPayload(
        config_path=str(config_path),
        violations=tuple(violations),
        sdp_violations=tuple(sdp_violations),
        passed=not violations and not sdp_fails_gate,
    )


def _run_contracts(path: Path, *, config_filename: Path | None) -> ContractsPayload:
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
        kept=result.kept,
        broken=result.broken,
        module_count=result.module_count,
        import_count=result.import_count,
        contracts=result.contracts,
    )


def _run_snapshot(path: Path) -> SnapshotPayload:
    graph = _load_graph(path, internal_only=True)
    config_path = discover_config(path)
    snap = take_snapshot(graph, config_path=config_path)
    target = path / ".archy" / "baseline.json"
    write_snapshot(snap, target)
    return SnapshotPayload(
        score=snap.score,
        cycles=snap.cycles,
        violations=snap.violations,
        sdp_violations=snap.sdp_violations,
        baseline_path=str(target),
    )


def _run_diff(path: Path) -> DiffReport | DiffErrorPayload:
    target = path / ".archy" / "baseline.json"
    baseline = read_snapshot(target)
    if baseline is None:
        return DiffErrorPayload(
            error=f"no baseline at {target}; call archy_snapshot first to capture one."
        )
    graph = _load_graph(path, internal_only=True)
    current = take_snapshot(graph, config_path=discover_config(path))
    return compute_diff(baseline, current)


def _run_impact(path: Path, *, files: list[Path]) -> Impact:
    graph = _load_graph(path, internal_only=True)
    resolved = [path / f if not f.is_absolute() else f for f in files]
    return find_impact(graph, resolved)


def _run_trend(path: Path, *, last_n: int) -> list[TrendRow]:
    rows = read_history(path / ".archy" / "history.jsonl")
    window = rows[-last_n:] if last_n > 0 else rows
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


def _run_graph_focus(
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
        return _graph_payload_from(_empty_subgraph(graph), unresolved=tuple(unresolved))

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
    return _graph_payload_from(sub, unresolved=tuple(unresolved))


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


def _run_graph_summary(path: Path, *, top_n: int) -> GraphSummaryPayload:
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1; got {top_n}")

    graph = _load_graph(path, internal_only=False)
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]
    internal_set = set(internal)

    internal_subgraph = graph.subgraph(internal)
    instability = compute_instability(internal_subgraph)
    _, propagation_cost = compute_propagation_cost(internal_subgraph)
    edit_risk = compute_edit_risk(internal_subgraph)

    internal_edge_count = internal_subgraph.number_of_edges()
    external_edge_count = sum(
        1 for u, v in graph.edges() if u in internal_set and v not in internal_set
    )

    fan_in = sorted(
        ((n, internal_subgraph.in_degree(n)) for n in internal),
        key=lambda t: (-t[1], t[0]),
    )
    fan_out = sorted(
        ((n, internal_subgraph.out_degree(n)) for n in internal),
        key=lambda t: (-t[1], t[0]),
    )

    pagerank = _pagerank(internal_subgraph)
    pr_sorted = sorted(pagerank.items(), key=lambda t: (-t[1], t[0]))

    risk_sorted = sorted(edit_risk.items(), key=lambda t: (-t[1], t[0]))

    external_counts: dict[str, int] = {}
    for _, v in graph.edges():
        if v not in internal_set and graph.nodes[v].get("external"):
            external_counts[v] = external_counts.get(v, 0) + 1
    ext_sorted = sorted(external_counts.items(), key=lambda t: (-t[1], t[0]))

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


def _run_graph_dump(
    path: Path,
    *,
    internal_only: bool,
    max_nodes: int,
) -> GraphPayload | GraphTooLargePayload:
    graph = _load_graph(path, internal_only=internal_only)
    node_count = graph.number_of_nodes()
    if node_count > max_nodes:
        return GraphTooLargePayload(
            error=(
                f"graph has {node_count} nodes (> max_nodes={max_nodes}). "
                "Use archy_graph_focus for a local slice or archy_graph_summary "
                "for a top-N overview, or call archy_graph again with a higher "
                "max_nodes if you really want the full dump."
            ),
            node_count=node_count,
            max_nodes=max_nodes,
        )
    return _graph_payload_from(graph)


def _empty_subgraph(graph):
    import networkx as nx

    empty: nx.DiGraph = nx.DiGraph()
    empty.graph["root"] = graph.graph.get("root")
    empty.graph["parse_errors"] = graph.graph.get("parse_errors", ())
    return empty


def _graph_payload_from(graph, *, unresolved: tuple[str, ...] = ()) -> GraphPayload:
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
    )


def _run_hotspots(path: Path, *, top: int, since: str | None) -> HotspotsPayload:
    if top <= 0:
        raise ValueError(f"top must be >= 1; got {top}")
    graph = _load_graph(path, internal_only=True)
    churn = git_churn(path, since=since)
    if churn is None:
        return HotspotsPayload(
            since=since,
            total=0,
            shown=0,
            hotspots=(),
            note=(
                f"{path} is not inside a git repository (or git is unavailable); "
                "hotspots needs git history to compute per-file churn. For a "
                "git-free 'is this edit dangerous?' signal, call "
                "archy_high_risk_modules instead."
            ),
        )
    rows = compute_hotspots(graph, churn=churn)
    shown = rows[:top]
    entries = tuple(
        HotspotEntry(
            module=r.module,
            path=r.path,
            cc_sum=r.cc_sum,
            churn=r.churn,
            score=r.score,
        )
        for r in shown
    )
    return HotspotsPayload(
        since=since,
        total=len(rows),
        shown=len(entries),
        hotspots=entries,
    )


def _run_high_risk_modules(path: Path, *, top_n: int) -> HighRiskPayload:
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1; got {top_n}")

    graph = _load_graph(path, internal_only=True)
    instability = compute_instability(graph)
    _, propagation_cost = compute_propagation_cost(graph)
    edit_risk = compute_edit_risk(graph)

    ranked = sorted(edit_risk.items(), key=lambda t: (-t[1], t[0]))
    entries = tuple(
        HighRiskEntry(
            module=name,
            edit_risk=risk,
            propagation_cost=propagation_cost.get(name, 0.0),
            instability=instability.get(name, 0.0),
            fan_in=graph.in_degree(name),
        )
        for name, risk in ranked[:top_n]
    )
    return HighRiskPayload(module_count=len(edit_risk), modules=entries)


def _load_graph(path: Path, *, internal_only: bool):
    graph = build_graph(path, **_graph_kwargs(path))
    if internal_only:
        external = {n for n, d in graph.nodes(data=True) if d.get("external")}
        graph.remove_nodes_from(external)
    return graph


def _graph_kwargs(path: Path) -> dict:
    # Best-effort archy.yaml discovery so MCP tools honor `exclude:` and
    # `roots:` the same way the CLI does. See cli._graph_kwargs.
    config_path = discover_config(path)
    if config_path is None:
        return {}
    config = load_config(config_path)
    return {
        "ignored_dirs": DEFAULT_IGNORED_DIRS | frozenset(config.exclude),
        "extra_roots": config.roots,
    }
