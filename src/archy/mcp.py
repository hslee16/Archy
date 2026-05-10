"""MCP server exposing archy's analysis as tools an AI agent can call.

Built on the official Python `mcp` SDK using its FastMCP API. The five
tools mirror the CLI surface (`archy_score`, `archy_cycles`,
`archy_check`, `archy_trend`, `archy_record_baseline`) so an agent can
treat archy as a structural sensor in its own feedback loop, the way
the README pitches.

The server runs over stdio (the MCP convention for local tools); start
it from the CLI via `archy mcp`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from archy.cycles import find_cycles
from archy.graph import DEFAULT_IGNORED_DIRS, build_graph
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.impact import find_impact
from archy.layers import (
    LayerConfigError,
    discover_config,
    find_violations,
    load_config,
)
from archy.score import compute_score


def create_server() -> FastMCP:
    server: FastMCP = FastMCP("archy")
    _register_tools(server)
    return server


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
    ) -> dict[str, Any]:
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
    ) -> list[dict[str, Any]]:
        return _run_cycles(Path(path), min_size=min_size, internal_only=internal_only)

    @server.tool(
        name="archy_check",
        description=(
            "Check a Python project against the layer rules in archy.yaml. "
            "Returns a list of forbidden inter-layer edges. An empty list means "
            "the architecture is intact."
        ),
    )
    def archy_check(
        path: str,
        config_path: str | None = None,
    ) -> dict[str, Any]:
        return _run_check(Path(path), config_path=Path(config_path) if config_path else None)

    @server.tool(
        name="archy_trend",
        description=(
            "Read the recent score history (.archy/history.jsonl) for a Python "
            "project. Returns up to last_n rows ordered oldest-first so an agent "
            "can compare deltas."
        ),
    )
    def archy_trend(path: str, last_n: int = 10) -> list[dict[str, Any]]:
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
    ) -> dict[str, Any]:
        return _run_impact(Path(path), files=[Path(f) for f in files])

    @server.tool(
        name="archy_record_baseline",
        description=(
            "Compute the score for a Python project AND append it to "
            ".archy/history.jsonl. Convenience wrapper for archy_score(record=True). "
            "Use at the start of an agent session so a later archy_score(strict=True) "
            "can detect degradation."
        ),
    )
    def archy_record_baseline(path: str, internal_only: bool = True) -> dict[str, Any]:
        return _run_score(
            Path(path),
            internal_only=internal_only,
            record=True,
            strict=False,
            strict_tolerance=0.02,
        )


# --- thin internals - the same shapes the CLI emits as JSON. ------------------


def _run_score(
    path: Path,
    *,
    internal_only: bool,
    record: bool,
    strict: bool,
    strict_tolerance: float,
) -> dict[str, Any]:
    graph = _load_graph(path, internal_only=internal_only)
    score = compute_score(graph)
    history_path = path / ".archy" / "history.jsonl"

    gate: dict[str, Any] | None = None
    if strict:
        rows = read_history(history_path)
        if rows:
            previous = rows[-1]
            delta = score.overall - previous.overall
            gate = {
                "previous": previous.overall,
                "previous_commit": previous.commit,
                "previous_timestamp": previous.timestamp,
                "current": score.overall,
                "delta": delta,
                "tolerance": strict_tolerance,
                "passed": delta >= -strict_tolerance,
            }
        else:
            gate = {
                "previous": None,
                "current": score.overall,
                "delta": None,
                "tolerance": strict_tolerance,
                "passed": True,
            }

    if record:
        commit, branch = git_metadata(path)
        append_history(history_path, row_from_score(score, commit=commit, branch=branch))

    payload: dict[str, Any] = {
        "overall": score.overall,
        "components": {
            "modularity": score.modularity,
            "acyclicity": score.acyclicity,
            "depth": score.depth,
            "equality": score.equality,
        },
        "inputs": {
            "module_count": score.inputs.module_count,
            "edge_count": score.inputs.edge_count,
            "cycle_count": score.inputs.cycle_count,
            "max_depth": score.inputs.max_depth,
            "community_count": score.inputs.community_count,
            "raw_modularity": score.inputs.raw_modularity,
            "raw_gini": score.inputs.raw_gini,
        },
    }
    if gate is not None:
        payload["gate"] = gate
    return payload


def _run_cycles(path: Path, *, min_size: int, internal_only: bool) -> list[dict[str, Any]]:
    graph = _load_graph(path, internal_only=internal_only)
    return [
        {
            "modules": list(c.modules),
            "edges": [
                {"source": e.source, "target": e.target, "lines": list(e.lines)} for e in c.edges
            ],
        }
        for c in find_cycles(graph, min_size=min_size)
    ]


def _run_check(path: Path, *, config_path: Path | None) -> dict[str, Any]:
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
    return {
        "config_path": str(config_path),
        "violations": [
            {
                "rule": {"from": v.rule.from_layer, "to": v.rule.to_layer},
                "source": v.source,
                "target": v.target,
                "lines": list(v.lines),
            }
            for v in violations
        ],
        "passed": not violations,
    }


def _run_impact(path: Path, *, files: list[Path]) -> dict[str, Any]:
    graph = _load_graph(path, internal_only=True)
    resolved = [path / f if not f.is_absolute() else f for f in files]
    result = find_impact(graph, resolved)
    return {
        "changed": list(result.changed),
        "unresolved": list(result.unresolved),
        "impacted": list(result.impacted),
    }


def _run_trend(path: Path, *, last_n: int) -> list[dict[str, Any]]:
    rows = read_history(path / ".archy" / "history.jsonl")
    window = rows[-last_n:] if last_n > 0 else rows
    return [
        {
            "timestamp": r.timestamp,
            "commit": r.commit,
            "branch": r.branch,
            "score": {
                "overall": r.overall,
                "modularity": r.modularity,
                "acyclicity": r.acyclicity,
                "depth": r.depth,
                "equality": r.equality,
            },
            "inputs": {
                "module_count": r.module_count,
                "edge_count": r.edge_count,
                "cycle_count": r.cycle_count,
                "max_depth": r.max_depth,
                "community_count": r.community_count,
            },
        }
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
