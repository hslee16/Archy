"""Persistent parse cache backing `build_graph`.

Tree-sitter parsing is ~83% of build cost on a pytorch-scale repo (2,910-module
Django: 3.5s parse vs 0.1s resolve); resolution is cheap and *global* (relative
imports, re-export chains, and alias tables all need the full module set). So
the cache stores the expensive per-file `ParseResult` keyed by `(path, sha256)`
and re-runs the existing global resolution every call. That keeps one resolution
code path, which is what guarantees the cached graph is byte-identical to a cold
`build_graph` (the property the test suite asserts) and sidesteps the
incremental-resolution invalidation hazard entirely.

The DB at `.archy/index.db` is a pure cache: deleting it only costs a cold
reparse, never data. A schema-version bump drops and rebuilds it.

archy:owns        SyncStats, build_graph_cached, default_db_path, open_index, sync
archy:mirrored-by default_db_path -> archy.cli, archy.watcher, open_index -> archy.cli,
                  archy.watcher, sync -> archy.cli, archy.watcher
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict, ValidationError

from archy.graph import (
    DEFAULT_IGNORED_DIRS,
    Module,
    assemble_graph,
    discover_modules,
)
from archy.parser import ParseResult, parse_file

# Bump whenever the persisted `ParseResult` shape changes so stale rows are
# dropped rather than silently deserialized with defaults. v2 added
# `FunctionComplexity.shape_hash` / `size` (duplicate-function detection); old
# v1 rows would validate with empty hashes and yield zero duplicates on a warm
# cache until incidental reparse.
SCHEMA_VERSION = 2


class SyncStats(BaseModel):
    """Per-sync accounting: how many files were re-parsed, reused, or pruned."""

    model_config = ConfigDict(frozen=True)

    reparsed: int = 0
    unchanged: int = 0
    pruned: int = 0

    @property
    def total(self) -> int:
        return self.reparsed + self.unchanged


def default_db_path(root: Path) -> Path:
    """Cache location for a project: `<root>/.archy/index.db` (sibling to baselines)."""
    return root / ".archy" / "index.db"


def open_index(db_path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) the cache DB, rebuilding it on a schema bump.

    The cache is disposable, so a version mismatch just drops the tables rather
    than running a migration. Callers own the returned connection.

    ``check_same_thread=False`` lets the watcher's background thread reuse one
    connection (the IndexManager serializes all access with a lock, so this is
    safe); the CLI keeps the stdlib default.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    version = _read_version(conn)
    if version is not None and version != SCHEMA_VERSION:
        conn.executescript("DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS schema_version;")
        version = None
    conn.execute(
        "CREATE TABLE IF NOT EXISTS files ("
        "path TEXT PRIMARY KEY, mtime REAL, size INTEGER, "
        "sha256 TEXT, parse_json TEXT, last_parsed_at TEXT)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
    if version is None:
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()
    return conn


def _read_version(conn: sqlite3.Connection) -> int | None:
    try:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row["version"]) if row else None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_cached_parse(parse_json: str) -> ParseResult | None:
    """Deserialize a cached `parse_json`, or `None` if it is unusable.

    A row can become corrupt (interrupted write, disk fault) or schema-stale (a
    `ParseResult` change shipped without a `SCHEMA_VERSION` bump). The DB is a
    pure cache, so an unusable row is a miss, not a fatal error: returning `None`
    lets `sync()` fall through to a reparse that overwrites it. Mirrors how
    `history.read()` skips a corrupt line rather than crashing.
    """
    try:
        return ParseResult.model_validate_json(parse_json)
    except ValidationError:
        return None


def sync(
    conn: sqlite3.Connection, modules: list[Module]
) -> tuple[dict[str, ParseResult], SyncStats]:
    """Bring the cache in line with `modules` and return parse results by qualname.

    For each module: cheap stat first (mtime + size); only hash when those
    differ; only re-parse when the hash differs too. Rows for files no longer in
    `modules` are pruned. The returned dict is keyed by qualname, ready to hand
    straight to `assemble_graph`.
    """
    existing = {
        row["path"]: row
        for row in conn.execute(
            "SELECT path, mtime, size, sha256, parse_json FROM files"
        ).fetchall()
    }
    current_paths: set[str] = set()
    results: dict[str, ParseResult] = {}
    reparsed = unchanged = 0

    for module in modules:
        path = str(module.path)
        try:
            stat = module.path.stat()
        except OSError:
            # The file vanished between discovery and sync (a branch switch, a
            # concurrent edit, or the `archy mcp` watcher rebuilding mid-flight).
            # Skip it; any stale cache row is pruned below since `path` never
            # enters current_paths.
            continue
        current_paths.add(path)
        mtime, size = stat.st_mtime, stat.st_size
        row = existing.get(path)

        if row is not None and row["mtime"] == mtime and row["size"] == size:
            cached = _load_cached_parse(row["parse_json"])
            if cached is not None:
                results[module.qualname] = cached
                unchanged += 1
                continue
            # Corrupt/schema-incompatible row: fall through to a reparse, which
            # overwrites it. Keeps the docstring's "pure cache" promise intact.

        try:
            sha = _sha256(module.path)
        except OSError:
            continue  # vanished after stat; skip this build, re-parsed next time
        if row is not None and row["sha256"] == sha:
            cached = _load_cached_parse(row["parse_json"])
            if cached is not None:
                # Stat changed (e.g. a git checkout touched mtime) but content
                # did not: refresh the stat columns, reuse the cached parse.
                conn.execute(
                    "UPDATE files SET mtime = ?, size = ? WHERE path = ?", (mtime, size, path)
                )
                results[module.qualname] = cached
                unchanged += 1
                continue

        try:
            result = parse_file(module.path)
        except OSError:
            continue  # vanished after hashing; skip this build
        conn.execute(
            "INSERT INTO files (path, mtime, size, sha256, parse_json, last_parsed_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET "
            "mtime = excluded.mtime, size = excluded.size, sha256 = excluded.sha256, "
            "parse_json = excluded.parse_json, last_parsed_at = excluded.last_parsed_at",
            (path, mtime, size, sha, result.model_dump_json(), _now()),
        )
        results[module.qualname] = result
        reparsed += 1

    stale = [p for p in existing if p not in current_paths]
    if stale:
        conn.executemany("DELETE FROM files WHERE path = ?", [(p,) for p in stale])
    conn.commit()

    return results, SyncStats(reparsed=reparsed, unchanged=unchanged, pruned=len(stale))


def build_graph_cached(
    root: Path,
    *,
    ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS,
    extra_roots: Iterable[str] = (),
    db_path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> nx.DiGraph:
    """`build_graph` with a persistent parse cache; identical graph, warm-path fast.

    Pass an open `conn` to reuse a connection (the MCP server does); otherwise a
    connection is opened at `db_path` (default `<root>/.archy/index.db`) and
    closed before returning.
    """
    modules = discover_modules(root, ignored_dirs=ignored_dirs, extra_roots=extra_roots)
    own_conn = conn is None
    if conn is None:
        conn = open_index(db_path or default_db_path(root))
    try:
        parse_results, _stats = sync(conn, modules)
    finally:
        if own_conn:
            conn.close()
    # A module whose file vanished mid-sync is absent from parse_results;
    # assemble_graph drops such modules, so passing the full list is safe.
    return assemble_graph(root, modules, parse_results)
