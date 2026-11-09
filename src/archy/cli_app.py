"""The `archy` command group and the helpers every command shares.

archy:owns        main
archy:mirrored-by _graph_kwargs_or_exit -> archy.cli_conventions, archy.cli_git,
                  archy.cli_install, _layer_config_errors -> archy.cli_check,
                  archy.cli_history, _load_graph -> archy.cli_git, archy.cli_graph,
                  archy.cli_history, _require -> archy.cli_conventions, archy.cli_git,
                  archy.cli_graph, archy.cli_history
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click
import networkx as nx

from archy import __version__
from archy.graph import (
    QualnameCollisionError,
    ScanTooLargeError,
    build_graph,
    internal_subgraph,
)
from archy.history import read as read_history
from archy.layers import (
    LayerConfigError,
)
from archy.scan_config import (
    graph_kwargs,
    resolve_max_modules,
)
from archy.score import Score

if TYPE_CHECKING:
    # Type-only: archy.install is imported lazily inside `install` to keep startup cheap.
    pass


class _ArchyGroup(click.Group):
    """Render a module-name collision as the usual one-line `Error:` (#531).

    Every command that scans reaches `_discover_modules`, so catching the error
    once here beats a handler at each of the ~10 call sites that would drift.
    """

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except QualnameCollisionError as exc:
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_ArchyGroup)
@click.version_option(__version__)
def main() -> None:
    """archy - architectural sensor for Python codebases."""


def _require(ok: bool, flag: str, constraint: str, value: object) -> None:
    """Raise a uniform ClickException for a failed CLI option bound-check."""
    if not ok:
        raise click.ClickException(f"--{flag} must be {constraint}; got {value}")


def _layer_config_errors(command: Callable[..., None]) -> Callable[..., None]:
    """Print a match-time LayerConfigError as the one-line CLI error (#750).

    `match_layer` raises while the graph is being read, not while the config is
    parsed, so the parse-time guards from #524 never see it: `check` reaches it
    through `compute_coverage`, `snapshot`, `simulate` and `diff` through their
    snapshot helpers. Wrapping a command keeps a new match-time call site inside
    it from reintroducing the traceback; a command that can reach `match_layer`
    has to carry this decorator or catch `LayerConfigError` itself, as `brief` does.
    """

    @functools.wraps(command)
    def wrapper(*args: object, **kwargs: object) -> None:
        try:
            command(*args, **kwargs)
        except LayerConfigError as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


def _graph_kwargs_or_exit(path: Path) -> dict:
    """`graph_kwargs` for CLI callers: turn LayerConfigError into ClickException.

    `graph_kwargs` itself stays raising so MCP can map LayerConfigError to isError.
    Every CLI site that used to call `graph_kwargs(path)` (and `_load_graph`) goes
    through this helper or the matching catch in `_load_graph`, so a malformed
    archy.yaml prints a one-line Error instead of a traceback (#524, same class as #207).
    """
    try:
        return graph_kwargs(path)
    except LayerConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _load_graph(path: Path, *, internal_only: bool) -> nx.DiGraph:
    try:
        g = build_graph(
            path,
            **_graph_kwargs_or_exit(path),
            max_modules=resolve_max_modules(path),
        )
    except ScanTooLargeError as exc:
        raise click.ClickException(str(exc)) from exc
    except LayerConfigError as exc:
        # resolve_max_modules also loads the config; keep the same one-line Error.
        raise click.ClickException(str(exc)) from exc
    if internal_only:
        external = {n for n, d in g.nodes(data=True) if d.get("external")}
        g.remove_nodes_from(external)
    return g


def _load_graph_pair(path: Path) -> tuple[nx.DiGraph, nx.DiGraph]:
    """Return `(internal_only, full)` from ONE scan, for the snapshot paths.

    Score and cycles want the internal-only graph; required-reach rules need the
    full one, because `must_reach` may name an external package. Built once and
    copied rather than scanned twice.
    """
    full = _load_graph(path, internal_only=False)
    return internal_subgraph(full), full


def _gate_against_history(current: Score, history_path: Path) -> dict:
    rows = read_history(history_path)
    if not rows:
        return {"previous": None, "current": current.overall, "delta": None}
    previous = rows[-1]
    return {
        "previous": previous.overall,
        "previous_commit": previous.commit,
        "previous_timestamp": previous.timestamp,
        "current": current.overall,
        "delta": current.overall - previous.overall,
    }
