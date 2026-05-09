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


@click.group()
@click.version_option(__version__)
def main() -> None:
    """archy — architectural sensor for Python codebases."""


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
        external = {n for n, d in g.nodes(data=True) if d.get("external")}
        g.remove_nodes_from(external)

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
        external = {n for n, d in g.nodes(data=True) if d.get("external")}
        g.remove_nodes_from(external)

    found = find_cycles(g, min_size=min_size)

    if fmt == "json":
        click.echo(json.dumps(_cycles_to_json(found), indent=2, sort_keys=True))
    else:
        click.echo(_cycles_to_text(found, min_size))

    if strict and found:
        sys.exit(1)


@main.command()
def check() -> None:
    """Run rule checks against the current codebase. (not implemented)"""
    raise click.ClickException("not implemented yet")


@main.command()
def score() -> None:
    """Compute the architecture score for the current commit. (not implemented)"""
    raise click.ClickException("not implemented yet")


@main.command()
def trend() -> None:
    """Show the score trend over recorded history. (not implemented)"""
    raise click.ClickException("not implemented yet")


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
            line_label = "lines" if len(e.lines) > 1 else "line"
            line_text = ", ".join(str(n) for n in e.lines) or "?"
            lines.append(f"  {e.source} -> {e.target}  ({line_label}: {line_text})")
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
