"""Per-root index manager + debounced filesystem watcher for `archy mcp`.

The MCP server is long-lived and is queried for the same project repeatedly
between edits. :class:`IndexManager` keeps one persistent cache connection per
project root and, when watching is started, a `watchdog` observer that runs the
parse-cache sync in the background on a 2-second debounce so the index is warm
before the next tool call.

The watcher is a *latency optimization* on top of the part-1 cache, never a
correctness mechanism: every `build_graph` still runs a sync under the lock, so
a graph is never stale even if the watcher missed an event or was never started.
That is why a missed event, a watcher that fails to start (no inotify slots,
sandbox), or a crashed debounce thread can only cost a re-sync, never a wrong
answer.

Concurrency: one connection guarded by one lock. Both the tool-call thread
(`build_graph` / `sync_now`) and the debounce thread acquire the same lock, so
SQLite is only ever touched by one thread at a time (hence
`check_same_thread=False` is safe).
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import networkx as nx

from archy.graph import DEFAULT_IGNORED_DIRS, assemble_graph, discover_modules
from archy.index import SyncStats, default_db_path, open_index, sync

DEBOUNCE_SECONDS = 2.0


class _ObserverLike(Protocol):
    """The slice of watchdog's Observer the manager uses (avoids a hard import)."""

    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IndexManager:
    """Owns one project's cache connection, freshness, and optional watcher."""

    def __init__(
        self,
        root: Path,
        *,
        ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS,
        extra_roots: Iterable[str] = (),
        db_path: Path | None = None,
    ) -> None:
        self.root = root
        self._ignored_dirs = frozenset(ignored_dirs)
        self._extra_roots = tuple(extra_roots)
        self._conn = open_index(db_path or default_db_path(root), check_same_thread=False)
        self._lock = threading.Lock()
        self.last_synced_at: str | None = None
        self._timer: threading.Timer | None = None
        self._observer: _ObserverLike | None = None

    # --- graph building (tool-call thread) -------------------------------
    def _sync_locked(self) -> tuple[list, dict, SyncStats]:
        modules = discover_modules(
            self.root, ignored_dirs=self._ignored_dirs, extra_roots=self._extra_roots
        )
        results, stats = sync(self._conn, modules)
        self.last_synced_at = _now()
        return modules, results, stats

    def build_graph(self) -> nx.DiGraph:
        """Sync the cache and assemble the graph. Always fresh w.r.t. disk."""
        with self._lock:
            modules, results, _ = self._sync_locked()
            # assemble_graph drops any module whose file vanished mid-sync (absent
            # from results), so passing the full list is safe.
            return assemble_graph(self.root, modules, results)

    def parse_map(self) -> tuple[list, dict]:
        """Sync the cache and return `(modules, parse_results)` without assembling.

        The function-grained warm path (used by `archy_duplicates`): duplicate
        detection needs the individual `ParseResult.functions` rows that
        `assemble_graph` rolls up into per-module aggregates and discards.
        """
        with self._lock:
            modules, results, _ = self._sync_locked()
            return modules, results

    def sync_now(self) -> SyncStats:
        """Run a sync without assembling a graph (used by the debounce fire)."""
        with self._lock:
            _, _, stats = self._sync_locked()
            return stats

    def cached_file_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()
            return int(row[0])

    @property
    def watching(self) -> bool:
        return self._observer is not None

    # --- watcher (background threads) ------------------------------------
    def start_watching(self) -> bool:
        """Start the debounced watcher. Returns False if watchdog is unavailable.

        Best-effort: any failure (missing watchdog, no inotify capacity, a
        sandboxed filesystem) leaves the manager fully functional via on-demand
        sync; the server must still serve.
        """
        if self._observer is not None:
            return True
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except Exception:
            return False

        manager = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: object) -> None:
                manager._on_fs_event(event)

        try:
            observer = Observer()
            observer.schedule(_Handler(), str(self.root), recursive=True)
            observer.daemon = True
            observer.start()
        except Exception:
            return False
        self._observer = observer
        return True

    def _on_fs_event(self, event: object) -> None:
        if getattr(event, "is_directory", False):
            return
        candidates = [getattr(event, "src_path", "")]
        dest = getattr(event, "dest_path", "")
        if dest:
            candidates.append(dest)
        if any(self._is_watched_py(Path(p)) for p in candidates if p):
            self._reset_timer()

    def _is_watched_py(self, path: Path) -> bool:
        """A Python source file that isn't in an ignored directory.

        Filters out `.archy/index.db` writes (not `.py`) so the cache's own
        writes can't feed the watcher a loop.
        """
        if path.suffix != ".py":
            return False
        return not any(part in self._ignored_dirs for part in path.parts)

    def _reset_timer(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SECONDS, self._debounced_fire)
            self._timer.daemon = True
            self._timer.start()

    def _debounced_fire(self) -> None:
        # The watcher must never crash the server; a failed background sync is
        # corrected by the next on-demand build.
        with contextlib.suppress(Exception):
            self.sync_now()

    def stop(self) -> None:
        observer = self._observer
        if observer is not None:
            observer.stop()
            observer.join(timeout=2)
            self._observer = None
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._conn.close()
