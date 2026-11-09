"""Commands that show the import graph: `graph`, `impact`, `affected`, `dsm`, `render`.

archy:owns        affected, dsm, graph, impact, render
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import click
import networkx as nx

from archy.affected import DEFAULT_DEPTH, Affected, find_affected
from archy.cli_app import _load_graph, _require, main
from archy.cli_render import (
    _affected_to_dict,
    _affected_to_text,
    _deleted_lines,
    _graph_to_text,
    _impact_to_dict,
    _impact_to_text,
)
from archy.gitlog import (
    resolve_changed_paths,
)
from archy.graph import (
    graph_to_dict,
    suggest_modules,
)
from archy.history import read as read_history
from archy.impact import DEFAULT_MAX_CHAINS, find_impact
from archy.render import DEFAULT_MAX_NODES as RENDER_MAX_NODES
from archy.render import render_dsm_html, render_trend_html


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
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
    g = _load_graph(path, internal_only=internal_only)

    if fmt == "json":
        click.echo(json.dumps(graph_to_dict(g), indent=2, sort_keys=True))
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
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--file",
    "files",
    type=click.Path(path_type=Path),
    multiple=True,
    required=True,
    help="Changed file path. Repeat for multiple files.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--max-chains",
    type=int,
    default=DEFAULT_MAX_CHAINS,
    show_default=True,
    help="Max causal chains (shortest import path to a changed module) to "
    "report, ranked closest-first. Use a negative value for all.",
)
def impact(path: Path, files: tuple[Path, ...], fmt: str, max_chains: int) -> None:
    """List internal modules that depend on the given file(s).

    Resolves each --file to a qualname via the import graph and prints
    every module that transitively imports any of them, with the shortest
    import path back to a changed module (the "because") for the closest
    dependents.
    """
    if max_chains == 0:
        raise click.ClickException(
            "--max-chains must be negative (for all) or positive (for a limit); got 0"
        )

    g = _load_graph(path, internal_only=True)
    result = find_impact(
        g,
        resolve_changed_paths(path, list(files)),
        max_chains=max_chains,
    )

    if fmt == "json":
        click.echo(json.dumps(_impact_to_dict(result), indent=2, sort_keys=True))
    else:
        click.echo(_impact_to_text(result))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.argument("files", nargs=-1, type=click.Path(path_type=Path))
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    default=False,
    help=(
        "Read changed file paths from stdin, one per line. These are MERGED "
        "with any FILES passed as positional arguments (both sources combine; "
        "they are not alternatives). Pairs with "
        "`git diff --name-only | archy affected --stdin`. Relative paths are "
        "tried under PATH first, then at its git work-tree root if missing "
        "(kept only if that file exists inside PATH). "
        "Use `git diff --relative --name-only` from PATH for project-relative paths."
    ),
)
@click.option(
    "-d",
    "--depth",
    type=int,
    default=DEFAULT_DEPTH,
    show_default=True,
    help="Maximum reverse-dependency hops to traverse.",
)
@click.option(
    "-f",
    "--filter",
    "test_filter",
    type=str,
    default=None,
    help="Recursive glob (matched against project-relative paths) identifying test files. "
    "Defaults to pytest conventions: test_*.py, *_test.py, anything under a tests/ directory. "
    "A glob that matches no file is an error, with a did-you-mean when a directory is close.",
)
@click.option(
    "-j",
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit JSON instead of human-readable text.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    default=False,
    help=(
        "Emit one affected test FILE PATH per line. "
        "Designed for `archy affected -q | xargs pytest`."
    ),
)
def affected(
    path: Path,
    files: tuple[Path, ...],
    from_stdin: bool,
    depth: int,
    test_filter: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Map changed source files to impacted modules and test files.

    Internal-only at launch: third-party and vendored code is not
    traced through. See docs/SPEC_INDEX_AND_INSTALL.md Q3.
    """
    if as_json and quiet:
        raise click.UsageError("--json and --quiet are mutually exclusive.")
    _require(depth >= 1, "depth", ">= 1", depth)

    # Positional FILES and --stdin are intentionally MERGED, not exclusive:
    # a caller can name a few files and pipe the rest (the help text says so).
    file_list = list(files)
    if from_stdin:
        file_list.extend(Path(line.strip()) for line in sys.stdin if line.strip())
    if not file_list:
        raise click.UsageError(
            "No changed files provided. Pass files as arguments or use --stdin "
            "(for example: `git diff --name-only | archy affected --stdin`)."
        )

    g = _load_graph(path, internal_only=True)
    resolved = resolve_changed_paths(path, file_list)
    try:
        result = find_affected(g, resolved, project_root=path, depth=depth, test_filter=test_filter)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(_affected_to_dict(result), indent=2, sort_keys=True))
    elif quiet:
        for test_path in _affected_test_paths(g, result):
            click.echo(test_path)
        for line in _deleted_lines(result.deleted):
            click.echo(line, err=True)
    else:
        click.echo(_affected_to_text(result))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--group",
    "group_by",
    type=click.Choice(["community", "layer", "topological"]),
    default="community",
    help="How to order rows/columns. Defaults to community-detected blocks.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["ascii", "json"]),
    default="ascii",
    help="Output format. ASCII rejects graphs over --max-nodes.",
)
@click.option(
    "--weight",
    type=click.Choice(["imports", "calls"]),
    default="imports",
    help="Cell value: binary edge presence (imports) or call_count (calls).",
)
@click.option(
    "--focus",
    type=str,
    default=None,
    help="Qualname to focus on; keeps focus + its N-hop neighborhood.",
)
@click.option(
    "--focus-depth",
    type=int,
    default=1,
    help="Hop count for --focus (default 1).",
)
@click.option(
    "--package",
    type=str,
    default=None,
    help="Keep only modules whose qualname starts with this prefix.",
)
@click.option(
    "--max-nodes",
    type=int,
    default=80,
    help="ASCII rendering refuses graphs larger than this.",
)
@click.option(
    "--diff",
    "diff_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to a previously written DSM JSON. Renders the diff against current.",
)
def dsm(
    path: Path,
    group_by: str,
    fmt: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
    max_nodes: int,
    diff_path: Path | None,
) -> None:
    """Design Structure Matrix view of the import graph.

    A DSM places modules on both axes in a chosen ordering; cell
    (row=source, col=target) is non-empty when source imports target.
    Use `--group=community` to see block-diagonal cohesion,
    `--group=layer` for layer-violation forensics, or
    `--group=topological` to localize back-edges (cycles appear as
    above-diagonal entries within an SCC block).

    For large projects, narrow with `--focus=<module>` or
    `--package=<prefix>`, or use `--format=json` and let the agent
    consume the structured view.
    """
    _require(focus_depth >= 0, "focus-depth", ">= 0", focus_depth)
    _require(max_nodes >= 1, "max-nodes", ">= 1", max_nodes)

    from archy.dsm import (
        diff_dsm,
        read_dsm,
        render_ascii,
        render_diff_text,
        render_json,
    )

    current = _dsm_from_path(
        path,
        group_by=group_by,
        weight=weight,
        focus=focus,
        focus_depth=focus_depth,
        package=package,
    )

    if diff_path is not None:
        try:
            before = read_dsm(diff_path)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if before is None:
            raise click.ClickException(f"no DSM snapshot at {diff_path}.")
        diff = diff_dsm(before, current)
        if fmt == "json":
            click.echo(json.dumps(diff.model_dump(), indent=2, sort_keys=True))
        else:
            click.echo(render_diff_text(diff, current, before))
        return

    if fmt == "json":
        click.echo(json.dumps(render_json(current), indent=2, sort_keys=True))
    else:
        click.echo(render_ascii(current, max_nodes=max_nodes))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--view",
    type=click.Choice(["dsm", "trend"]),
    default="dsm",
    show_default=True,
    help="Which view to render.",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the HTML here. Defaults to stdout.",
)
@click.option(
    "--group",
    "group_by",
    type=click.Choice(["community", "layer", "topological"]),
    default="community",
    help="dsm view: how to order rows/columns.",
)
@click.option(
    "--weight",
    type=click.Choice(["imports", "calls"]),
    default="imports",
    help="dsm view: cell value, edge presence (imports) or call_count (calls).",
)
@click.option(
    "--focus",
    type=str,
    default=None,
    help="dsm view: qualname to focus on; keeps focus + its N-hop neighborhood.",
)
@click.option(
    "--focus-depth",
    type=int,
    default=1,
    help="dsm view: hop count for --focus.",
)
@click.option(
    "--package",
    type=str,
    default=None,
    help="dsm view: keep only modules whose qualname starts with this prefix.",
)
@click.option(
    "--max-nodes",
    type=int,
    default=RENDER_MAX_NODES,
    show_default=True,
    help="dsm view: refuse to render matrices larger than this.",
)
@click.option(
    "--last",
    "last_n",
    type=int,
    default=10,
    show_default=True,
    help="trend view: number of most-recent records to plot.",
)
def render(
    path: Path,
    view: str,
    out_path: Path | None,
    group_by: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
    max_nodes: int,
    last_n: int,
) -> None:
    """Render a view as a self-contained HTML file (offline, no JS, no CDN).

    `--view=dsm` draws the Design Structure Matrix, back-edges in red;
    `--view=trend` plots the five axes over .archy/history.jsonl. Output is
    byte-stable for a fixed input, so two exports diff cleanly.

    Options are per-view (see the `dsm view:` / `trend view:` help text) and
    ignored by the other view. There is no `graph` view: a node-link diagram
    needs a vendored layout engine and carries the least signal of the three,
    so it stays deferred (docs/SPEC_VISUALIZATION.md).
    """
    if view == "trend":
        _require(last_n >= 1, "last", ">= 1", last_n)
        rows = read_history(path / ".archy" / "history.jsonl")
        html = render_trend_html(rows, last_n=last_n)
    else:
        _require(focus_depth >= 0, "focus-depth", ">= 0", focus_depth)
        _require(max_nodes >= 1, "max-nodes", ">= 1", max_nodes)

        matrix = _dsm_from_path(
            path,
            group_by=group_by,
            weight=weight,
            focus=focus,
            focus_depth=focus_depth,
            package=package,
        )
        try:
            html = render_dsm_html(matrix, max_nodes=max_nodes)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

    if out_path is None:
        click.echo(html, nl=False)
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    click.echo(f"Wrote {out_path}", err=True)


def _dsm_from_path(
    path: Path,
    *,
    group_by: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
):
    """Load the graph and build the DSM, for `dsm` and `render` (#422).

    The six knobs were written out at both call sites, so adding a seventh
    meant remembering to edit two places. The `archy.dsm` import stays deferred
    here, as it was at both sites: it pulls the matrix machinery in only for the
    two commands that need it.
    """
    from archy.dsm import GroupBy, Weight, build_dsm

    try:
        return build_dsm(
            _load_graph(path, internal_only=False),
            group_by=cast(GroupBy, group_by),
            weight=cast(Weight, weight),
            focus=focus,
            focus_depth=focus_depth,
            package=package,
            suggest=suggest_modules,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def _affected_test_paths(graph: nx.DiGraph, result: Affected) -> list[str]:
    """File paths for the impacted tests, suitable for `xargs pytest`.

    Falls back to the qualname if a node has no resolvable path (which
    shouldn't happen for internal nodes, but degrades safely).
    """
    out: list[str] = []
    for qualname in result.impacted_tests:
        path = graph.nodes[qualname].get("path")
        out.append(str(path) if path else qualname)
    return out


def _graph_to_dot(g: nx.DiGraph) -> str:
    lines = ["digraph imports {", '  rankdir="LR";']
    for n, d in sorted(g.nodes(data=True)):
        style = ' style="dashed" color="gray"' if d.get("external") else ""
        lines.append(f'  "{n}"[{style.strip()}];')
    for u, v in sorted(g.edges()):
        lines.append(f'  "{u}" -> "{v}";')
    lines.append("}")
    return "\n".join(lines)
