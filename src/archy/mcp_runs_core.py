"""The warm-index plumbing every MCP tool shares: managers, graph loading, argument checks.

archy:owns        resolve_against, validate_impact_mode
archy:mirrored-by _load_graph -> archy.mcp_runs, archy.mcp_runs_check,
                  archy.mcp_runs_git, archy.mcp_runs_graph,
                  _rank_desc -> archy.mcp_runs_check, archy.mcp_runs_graph,
                  _validate_response_format -> archy.mcp_runs_git,
                  archy.mcp_runs_graph, resolve_against -> archy.mcp, archy.mcp_runs,
                  archy.mcp_runs_graph, validate_impact_mode -> archy.mcp,
                  archy.mcp_runs
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

from archy.graph import (
    DEFAULT_IGNORED_DIRS,
    ScanTooLargeError,
    build_graph,
    discover_modules,
    internal_subgraph,
    parse_project,
)
from archy.scan_config import (
    graph_kwargs,
    resolve_max_modules,
)
from archy.watcher import IndexManager


def _rank_desc(pairs: Iterable[tuple[str, float]]) -> list[tuple[str, float]]:
    """`(name, value)` pairs ranked by value descending, ties by name ascending.

    The tie-break is part of the wire contract: summary lists must be stable
    across runs, and every ranked list in this module has to order ties the same
    way. One definition, so a change cannot land at one site and miss the others
    (#500).
    """
    return sorted(pairs, key=lambda t: (-t[1], t[0]))


def resolve_against(path: Path, files: list[Path]) -> list[Path]:
    """Anchor relative file args against the project root; leave absolutes alone."""
    return [path / f if not f.is_absolute() else f for f in files]


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
