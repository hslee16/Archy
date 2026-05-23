"""Tests for the persistent parse cache (`archy.index`).

The load-bearing contract is one invariant: a cache-backed build is byte-identical
to a cold `build_graph` no matter what mutation sequence preceded it. Asserting
`graph_to_dict(cached) == graph_to_dict(cold)` after each mutation covers the
spec's invalidation cases (rename, delete-then-readd, import-target-rename,
circular recompute) without any incremental-resolution logic to get wrong, since
the cached path re-resolves globally every call.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from archy.graph import build_graph, discover_modules, graph_to_dict
from archy.index import (
    SCHEMA_VERSION,
    build_graph_cached,
    default_db_path,
    open_index,
    sync,
)


def _write(root: Path, rel: str, body: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A small multi-module package with import + relative-import edges."""
    pkg = tmp_path / "pkg"
    _write(pkg, "__init__.py", "")
    _write(pkg, "a.py", "from pkg import b\nfrom pkg.sub import c\n")
    _write(pkg, "b.py", "VALUE = 1\n")
    _write(pkg, "sub/__init__.py", "")
    _write(pkg, "sub/c.py", "from .. import b\n")
    return tmp_path


def _equal(root: Path, db_path: Path) -> bool:
    """Cached build matches a cold build, by canonical serialization."""
    return graph_to_dict(build_graph_cached(root, db_path=db_path)) == graph_to_dict(
        build_graph(root)
    )


def test_cold_cache_matches_cold_build(project: Path, tmp_path: Path):
    assert _equal(project, tmp_path / "index.db")


def test_noop_resync_matches(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)  # populate so this run exercises the all-cached path
    assert _equal(project, db)


def test_edit_file_picked_up(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)
    # A new import edge must survive the cache: the changed file is re-parsed and
    # the global re-resolve picks the edge up.
    _write(project / "pkg", "b.py", "from pkg import a\nVALUE = 2\n")
    assert _equal(project, db)


def test_add_file_picked_up(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)
    _write(project / "pkg", "d.py", "from pkg import b\n")
    assert _equal(project, db)


def test_delete_file_pruned(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)
    (project / "pkg" / "b.py").unlink()
    assert _equal(project, db)


def test_rename_module(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)
    # Rename b.py -> renamed.py and repoint the importer.
    (project / "pkg" / "b.py").rename(project / "pkg" / "renamed.py")
    _write(project / "pkg", "a.py", "from pkg import renamed\nfrom pkg.sub import c\n")
    assert _equal(project, db)


def test_delete_then_readd(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)
    body = (project / "pkg" / "b.py").read_text()
    (project / "pkg" / "b.py").unlink()
    # The intermediate sync must record the absence, so re-adding identical
    # content is not silently served from the pre-deletion cache row.
    build_graph_cached(project, db_path=db)
    _write(project / "pkg", "b.py", body)
    assert _equal(project, db)


def test_import_target_rename(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)
    # Move sub.c's target: edit a.py to import a different module.
    _write(project / "pkg", "e.py", "X = 1\n")
    _write(project / "pkg", "a.py", "from pkg import e\nfrom pkg.sub import c\n")
    assert _equal(project, db)


def test_circular_imports(tmp_path: Path):
    pkg = tmp_path / "cyc"
    _write(pkg, "__init__.py", "")
    _write(pkg, "x.py", "from cyc import y\n")
    _write(pkg, "y.py", "from cyc import x\n")
    db = tmp_path / "index.db"
    build_graph_cached(tmp_path, db_path=db)
    # Break and reform the cycle; cached must still match cold each time.
    _write(pkg, "y.py", "VALUE = 1\n")
    assert _equal(tmp_path, db)
    _write(pkg, "y.py", "from cyc import x\n")
    assert _equal(tmp_path, db)


def test_deleting_db_is_safe(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)
    # An external deletion of the cache must not break a build: it cold-rebuilds.
    db.unlink()
    assert _equal(project, db)


# --- sync accounting -------------------------------------------------------


def test_sync_stats_reparse_then_reuse(project: Path, tmp_path: Path):
    conn = open_index(tmp_path / "index.db")
    modules = discover_modules(project)
    _, first = sync(conn, modules)
    assert first.reparsed == len(modules)
    assert first.unchanged == 0
    _, second = sync(conn, discover_modules(project))
    assert second.reparsed == 0
    assert second.unchanged == len(modules)
    conn.close()


def test_sync_reparses_only_changed(project: Path, tmp_path: Path):
    conn = open_index(tmp_path / "index.db")
    sync(conn, discover_modules(project))
    _write(project / "pkg", "b.py", "VALUE = 99\n")
    _, stats = sync(conn, discover_modules(project))
    assert stats.reparsed == 1
    assert stats.unchanged == len(discover_modules(project)) - 1


def test_sync_prunes_deleted(project: Path, tmp_path: Path):
    conn = open_index(tmp_path / "index.db")
    sync(conn, discover_modules(project))
    (project / "pkg" / "b.py").unlink()
    _, stats = sync(conn, discover_modules(project))
    assert stats.pruned == 1
    conn.close()


def test_touch_without_content_change_is_unchanged(project: Path, tmp_path: Path):
    conn = open_index(tmp_path / "index.db")
    sync(conn, discover_modules(project))
    # Rewrite identical bytes so size matches but mtime advances.
    b = project / "pkg" / "b.py"
    content = b.read_text()
    future = time.time() + 10
    os.utime(b, (future, future))
    b.write_text(content, encoding="utf-8")  # same content
    os.utime(b, (future, future))
    _, stats = sync(conn, discover_modules(project))
    # mtime moved so it is re-hashed, but the sha matches -> reused, not reparsed.
    assert stats.reparsed == 0
    assert stats.unchanged == len(discover_modules(project))
    conn.close()


# --- schema versioning -----------------------------------------------------


def test_schema_bump_rebuilds(project: Path, tmp_path: Path):
    db = tmp_path / "index.db"
    conn = open_index(db)
    sync(conn, discover_modules(project))
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    # Reopening sees a stale version, drops + recreates; build still matches cold.
    assert _equal(project, db)


def test_default_db_path():
    assert default_db_path(Path("/proj")) == Path("/proj/.archy/index.db")


def test_parse_json_is_valid_json(project: Path, tmp_path: Path):
    conn = open_index(tmp_path / "index.db")
    sync(conn, discover_modules(project))
    row = conn.execute("SELECT parse_json FROM files LIMIT 1").fetchone()
    # A truncated or non-JSON blob would silently break every later cache read,
    # so assert what we stored is parseable.
    json.loads(row["parse_json"])
    conn.close()
