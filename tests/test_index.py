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
from archy.parser import ParseResult


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
    """A cold build through the cache. Deliberately NOT the cache's own claim.

    On an empty database `sync` returns the freshly parsed object and no cached
    row is ever read, so both sides of this are the same `parse_file` +
    `assemble_graph` computation and it cannot fail on anything the cache does.
    A write-only cache (storing a corrupt blob so every warm read misses) leaves
    it green (#439). It is kept because the cold path is still worth exercising,
    and renamed expectations live in `test_warm_cache_reads_what_it_wrote` below,
    which is where the cache is actually the thing under test.
    """
    assert _equal(project, tmp_path / "index.db")


def test_warm_cache_reads_what_it_wrote(project: Path, tmp_path: Path):
    """The claim the cold test cannot make: a row written on one build is read
    back on the next and produces the same graph.

    Pins it two ways, because equality alone is satisfied by a cache that
    silently re-parses everything: nothing may be reparsed on the second pass,
    and the graph must still match a cold build.
    """
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)

    with open_index(db) as conn:
        _, stats = sync(conn, discover_modules(project))

    assert stats.reparsed == 0, "a warm build must not re-parse an unchanged file"
    assert stats.unchanged > 0, "and it must actually read rows back"
    assert _equal(project, db)


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


@pytest.mark.parametrize(
    "corrupt_json",
    [
        '{"imports": [trunc',  # truncated / non-JSON (interrupted write, disk fault)
        '{"imports":[],"calls":[],"functions":[]}',  # old schema, missing required field
    ],
    ids=["truncated", "schema-stale"],
)
def test_corrupt_cache_row_self_heals(project: Path, tmp_path: Path, corrupt_json: str):
    """Regression for #167: a corrupt/schema-stale parse_json whose mtime+size
    still match disk must NOT crash the build. The docstring promises a pure
    cache; an unusable row is a miss that triggers a reparse, not a fatal
    ValidationError that takes down every graph-building tool until the user
    deletes .archy/index.db by hand."""
    db = tmp_path / "index.db"
    build_graph_cached(project, db_path=db)  # cold build populates the cache

    conn = open_index(db)
    # Corrupt one row's parse_json, leaving mtime/size intact so sync() takes
    # the cheap cache-hit branch and would deserialize the bad blob.
    path = conn.execute("SELECT path FROM files LIMIT 1").fetchone()["path"]
    conn.execute("UPDATE files SET parse_json = ? WHERE path = ?", (corrupt_json, path))
    conn.commit()
    conn.close()

    # Must recover (reparse), matching a cold build, rather than raising.
    assert _equal(project, db)

    # And the corrupt row must be healed in place, not left to crash next time.
    conn = open_index(db)
    healed = conn.execute("SELECT parse_json FROM files WHERE path = ?", (path,)).fetchone()
    conn.close()
    assert healed["parse_json"] != corrupt_json
    ParseResult.model_validate_json(healed["parse_json"])  # round-trips cleanly now


def test_sync_skips_file_that_vanishes_after_discovery(project: Path, tmp_path: Path):
    """A module listed by discovery can be gone by the time sync stats it
    (branch switch, concurrent edit, watcher mid-rebuild). sync must skip it,
    not crash on the stat()."""
    modules = discover_modules(project)
    next(m for m in modules if m.qualname == "pkg.b").path.unlink()
    conn = open_index(tmp_path / "index.db")
    results, _stats = sync(conn, modules)  # must not raise
    conn.close()
    assert "pkg.b" not in results  # vanished module skipped
    assert "pkg.a" in results  # the rest still parsed


def test_build_graph_cached_survives_vanished_file(project: Path, tmp_path: Path, monkeypatch):
    """If a file vanishes after hashing but before parse, sync drops it; the
    cached build must keep assemble_graph's module list aligned with the parse
    results so it does not KeyError on the dropped module."""
    import archy.index as index_mod

    real_parse_file = index_mod.parse_file

    def flaky_parse_file(path: Path):
        if path.name == "b.py":
            raise FileNotFoundError(path)
        return real_parse_file(path)

    monkeypatch.setattr(index_mod, "parse_file", flaky_parse_file)

    g = build_graph_cached(project, db_path=tmp_path / "index.db")  # must not raise

    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}
    assert "pkg.b" not in internal
    assert "pkg.a" in internal
