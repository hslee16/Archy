"""MCP server exposing archy's analysis as tools an AI agent can call.

Built on the official Python `mcp` SDK using its FastMCP API. The
nine tools mirror the CLI surface (`archy_score`, `archy_cycles`,
`archy_check`, `archy_contracts`, `archy_trend`, `archy_impact`,
`archy_snapshot`, `archy_diff`, `archy_record_baseline`) so an agent
can treat archy as a structural sensor in its own feedback loop, the
way the README pitches.

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
from archy.graph import DEFAULT_IGNORED_DIRS, build_graph
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.impact import Impact, find_impact
from archy.layers import (
    LayerConfigError,
    SdpViolation,
    Violation,
    discover_config,
    find_sdp_violations,
    find_violations,
    load_config,
)
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
#
# These shape the MCP wire format. Each tool returns one of these; FastMCP
# turns the model into JSON for the client.


class ScoreComponents(BaseModel):
    model_config = ConfigDict(frozen=True)

    modularity: float
    acyclicity: float
    depth: float
    equality: float


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
            "equality - geometric mean) for a Python project. Optionally append "
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
            "in `unresolved`."
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
