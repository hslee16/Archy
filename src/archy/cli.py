"""Click-based command-line interface for archy."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import networkx as nx

from archy import __version__
from archy.cycles import Cycle, find_cycles
from archy.graph import build_graph
from archy.layers import (
    LayerConfigError,
    Violation,
    discover_config,
    find_violations,
    load_config,
)
from archy.score import Score, compute_score


@click.group()
@click.version_option(__version__)
def main() -> None:
    """archy - architectural sensor for Python codebases."""


@main.command()
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "dot", "text"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--internal-only",
    is_flag=True,
    help="Hide edges to external (third-party / stdlib) modules.",
)
def graph(path: Path, fmt: str, internal_only: bool) -> None:
    """Build the import graph for a Python project rooted at PATH."""
    g = build_graph(path)
    if internal_only:
        _drop_external_nodes(g)

    if fmt == "json":
        click.echo(json.dumps(_graph_to_dict(g), indent=2, sort_keys=True))
    elif fmt == "dot":
        click.echo(_graph_to_dot(g))
    else:
        click.echo(_graph_to_text(g))

    if g.graph.get("parse_errors"):
        click.echo(
            f"\n[archy] {len(g.graph['parse_errors'])} file(s) had parse errors "
            "(partial trees were used). Run with --format json to see which.",
            err=True,
        )


@main.command()
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--internal-only/--all",
    default=True,
    help="Restrict cycle detection to internal modules (the default).",
)
@click.option(
    "--min-size",
    type=int,
    default=2,
    show_default=True,
    help="Minimum SCC size to report.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero if any cycles are found.",
)
def cycles(path: Path, fmt: str, internal_only: bool, min_size: int, strict: bool) -> None:
    """Find import cycles in a Python project rooted at PATH."""
    g = build_graph(path)
    if internal_only:
        _drop_external_nodes(g)

    found = find_cycles(g, min_size=min_size)

    if fmt == "json":
        click.echo(json.dumps(_cycles_to_json(found), indent=2, sort_keys=True))
    else:
        click.echo(_cycles_to_text(found, min_size))

    if strict and found:
        sys.exit(1)


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to archy.yaml. Discovered from PATH upward if omitted.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def check(path: Path, config_path: Path | None, fmt: str) -> None:
    """Check the project at PATH against layer rules in archy.yaml.

    Exits 0 if there are no violations, 1 otherwise.
    """
    if config_path is None:
        discovered = discover_config(path)
        if discovered is None:
            raise click.ClickException(
                f"no archy.yaml found near {path}; pass --config or create one at the project root."
            )
        config_path = discovered

    try:
        config = load_config(config_path)
    except LayerConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    g = build_graph(path)
    try:
        violations = find_violations(g, config)
    except LayerConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    if fmt == "json":
        click.echo(json.dumps(_violations_to_json(violations), indent=2, sort_keys=True))
    else:
        click.echo(_violations_to_text(violations, config_path))

    if violations:
        sys.exit(1)


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--internal-only/--all",
    default=True,
    help="Restrict scoring to internal modules (default).",
)
def score(path: Path, fmt: str, internal_only: bool) -> None:
    """Compute the composite architecture quality score for PATH."""
    g = build_graph(path)
    if internal_only:
        _drop_external_nodes(g)
    s = compute_score(g)
    if fmt == "json":
        click.echo(json.dumps(_score_to_dict(s), indent=2, sort_keys=True))
    else:
        click.echo(_score_to_text(s))


@main.command()
def trend() -> None:
    """Show the score trend over recorded history. (not implemented)"""
    raise click.ClickException("not implemented yet")


def _drop_external_nodes(g: nx.DiGraph) -> None:
    external = {n for n, d in g.nodes(data=True) if d.get("external")}
    g.remove_nodes_from(external)


def _format_lines(lines: tuple[int, ...]) -> str:
    label = "lines" if len(lines) > 1 else "line"
    text = ", ".join(str(n) for n in lines) or "?"
    return f"({label}: {text})"


def _score_to_dict(s: Score) -> dict:
    return {
        "overall": s.overall,
        "components": {
            "modularity": s.modularity,
            "acyclicity": s.acyclicity,
            "depth": s.depth,
            "equality": s.equality,
        },
        "inputs": {
            "module_count": s.inputs.module_count,
            "edge_count": s.inputs.edge_count,
            "cycle_count": s.inputs.cycle_count,
            "max_depth": s.inputs.max_depth,
            "community_count": s.inputs.community_count,
            "raw_modularity": s.inputs.raw_modularity,
            "raw_gini": s.inputs.raw_gini,
        },
    }


def _score_to_text(s: Score) -> str:
    lines = [
        f"# archy score: {s.overall:.3f}",
        f"modularity:  {s.modularity:.3f}  "
        f"({s.inputs.community_count} communities, raw Q={s.inputs.raw_modularity:.3f})",
        f"acyclicity:  {s.acyclicity:.3f}  ({s.inputs.cycle_count} cycles)",
        f"depth:       {s.depth:.3f}  (max depth {s.inputs.max_depth})",
        f"equality:    {s.equality:.3f}  (Gini={s.inputs.raw_gini:.3f})",
        f"# graph: {s.inputs.module_count} modules, {s.inputs.edge_count} edges",
    ]
    return "\n".join(lines)


def _violations_to_json(violations: list[Violation]) -> list[dict]:
    return [
        {
            "rule": {"from": v.rule.from_layer, "to": v.rule.to_layer},
            "source": v.source,
            "target": v.target,
            "lines": list(v.lines),
        }
        for v in violations
    ]


def _violations_to_text(violations: list[Violation], config_path: Path) -> str:
    if not violations:
        return f"# No layer violations (config: {config_path})."
    lines = [f"# {len(violations)} layer violation(s) (config: {config_path})"]
    current_rule: tuple[str, str] | None = None
    for v in violations:
        rule_pair = (v.rule.from_layer, v.rule.to_layer)
        if rule_pair != current_rule:
            lines.append(f"\n{v.rule.from_layer} -> {v.rule.to_layer} (forbidden):")
            current_rule = rule_pair
        lines.append(f"  {v.source} -> {v.target}  {_format_lines(v.lines)}")
    return "\n".join(lines)


def _cycles_to_json(cycles: list[Cycle]) -> list[dict]:
    return [
        {
            "modules": list(c.modules),
            "edges": [
                {"source": e.source, "target": e.target, "lines": list(e.lines)} for e in c.edges
            ],
        }
        for c in cycles
    ]


def _cycles_to_text(cycles: list[Cycle], min_size: int) -> str:
    if not cycles:
        return f"# No cycles found (min_size={min_size})."
    lines = [f"# {len(cycles)} cycle(s) found"]
    for c in cycles:
        lines.append(f"\nCycle of {len(c.modules)} module(s):")
        for m in c.modules:
            lines.append(f"  - {m}")
        lines.append("Edges:")
        for e in c.edges:
            lines.append(f"  {e.source} -> {e.target}  {_format_lines(e.lines)}")
    return "\n".join(lines)


def _graph_to_dict(g: nx.DiGraph) -> dict:
    return {
        "root": g.graph.get("root"),
        "parse_errors": list(g.graph.get("parse_errors", ())),
        "nodes": [{"id": n, **d} for n, d in sorted(g.nodes(data=True))],
        "edges": [
            {"source": u, "target": v, **d}
            for u, v, d in sorted(g.edges(data=True), key=lambda e: (e[0], e[1]))
        ],
    }


def _graph_to_dot(g: nx.DiGraph) -> str:
    lines = ["digraph imports {", '  rankdir="LR";']
    for n, d in sorted(g.nodes(data=True)):
        style = ' style="dashed" color="gray"' if d.get("external") else ""
        lines.append(f'  "{n}"[{style.strip()}];')
    for u, v in sorted(g.edges()):
        lines.append(f'  "{u}" -> "{v}";')
    lines.append("}")
    return "\n".join(lines)


def _graph_to_text(g: nx.DiGraph) -> str:
    internal = sorted(n for n, d in g.nodes(data=True) if not d.get("external"))
    lines = [f"# {len(internal)} internal module(s), {g.number_of_edges()} import edge(s)"]
    for n in internal:
        lines.append(f"{n}")
        for t in sorted(g.successors(n)):
            marker = "ext" if g.nodes[t].get("external") else "int"
            lines.append(f"  -> [{marker}] {t}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
