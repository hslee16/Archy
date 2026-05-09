"""Click-based command-line interface for archy."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import networkx as nx

from archy import __version__
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
