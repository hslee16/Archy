"""Tests for the IndexManager + debounced filesystem watcher (`archy.watcher`).

The manager's build must match a cold build (it delegates to the same cache and
assembly). The watcher is exercised two ways: its filter/debounce logic in
isolation (fast, deterministic) and one real-observer integration test behind a
shortened debounce. The watcher is best-effort, so failure to start must never
break building.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from archy import watcher as watcher_mod
from archy.graph import build_graph, graph_to_dict
from archy.watcher import IndexManager


def _write(root: Path, rel: str, body: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    _write(pkg, "__init__.py", "")
    _write(pkg, "a.py", "from pkg import b\n")
    _write(pkg, "b.py", "VALUE = 1\n")
    return tmp_path


def _manager(project: Path, tmp_path: Path) -> IndexManager:
    return IndexManager(project, db_path=tmp_path / "index.db")


def test_build_matches_cold(project: Path, tmp_path: Path):
    m = _manager(project, tmp_path)
    try:
        assert graph_to_dict(m.build_graph()) == graph_to_dict(build_graph(project))
    finally:
        m.stop()


def test_build_sets_last_synced_at(project: Path, tmp_path: Path):
    m = _manager(project, tmp_path)
    try:
        assert m.last_synced_at is None
        m.build_graph()
        assert m.last_synced_at is not None
    finally:
        m.stop()


def test_sync_now_returns_stats_and_count(project: Path, tmp_path: Path):
    m = _manager(project, tmp_path)
    try:
        stats = m.sync_now()
        assert stats.reparsed == 3  # __init__, a, b
        assert m.cached_file_count() == 3
        assert m.sync_now().reparsed == 0  # warm
    finally:
        m.stop()


def test_is_watched_py_filters(project: Path, tmp_path: Path):
    m = _manager(project, tmp_path)
    try:
        assert m._is_watched_py(Path("/x/foo.py")) is True
        assert m._is_watched_py(Path("/x/index.db")) is False
        assert m._is_watched_py(Path("/x/notes.txt")) is False
        assert m._is_watched_py(Path("/x/.venv/foo.py")) is False  # ignored dir
        assert m._is_watched_py(Path("/x/__pycache__/foo.py")) is False
    finally:
        m.stop()


def test_debounced_fire_runs_sync(project: Path, tmp_path: Path):
    m = _manager(project, tmp_path)
    try:
        m._debounced_fire()
        assert m.last_synced_at is not None
        assert m.cached_file_count() == 3
    finally:
        m.stop()


def test_reset_timer_coalesces(project: Path, tmp_path: Path, monkeypatch):
    # Short debounce so the test is fast; rapid events should collapse into one
    # sync, not one per event.
    monkeypatch.setattr(watcher_mod, "DEBOUNCE_SECONDS", 0.15)
    m = _manager(project, tmp_path)
    try:
        for _ in range(5):
            m._reset_timer()
            time.sleep(0.02)  # all within the debounce window
        # Only one timer is ever pending.
        assert m._timer is not None
        time.sleep(0.3)  # let it fire
        assert m.last_synced_at is not None
    finally:
        m.stop()


def test_start_watching_idempotent(project: Path, tmp_path: Path):
    m = _manager(project, tmp_path)
    try:
        first = m.start_watching()
        if not first:
            pytest.skip("watchdog observer unavailable in this environment")
        observer = m._observer
        assert m.start_watching() is True
        assert m._observer is observer  # not restarted
    finally:
        m.stop()


def test_start_watching_falls_back_when_missing(project: Path, tmp_path: Path, monkeypatch):
    # Force the lazy `import watchdog...` to fail; the manager must stay usable.
    monkeypatch.setitem(sys.modules, "watchdog.observers", None)
    m = _manager(project, tmp_path)
    try:
        assert m.start_watching() is False
        assert m.watching is False
        m.build_graph()  # still works without a watcher
    finally:
        m.stop()


def test_real_watcher_syncs_on_change(project: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(watcher_mod, "DEBOUNCE_SECONDS", 0.2)
    m = _manager(project, tmp_path)
    try:
        if not m.start_watching():
            pytest.skip("watchdog observer unavailable in this environment")
        m.sync_now()
        before = m.cached_file_count()
        _write(project / "pkg", "c.py", "from pkg import b\n")
        # Poll for the background sync to pick up the new file.
        deadline = time.time() + 5
        while time.time() < deadline and m.cached_file_count() <= before:
            time.sleep(0.1)
        assert m.cached_file_count() == before + 1
        assert m.last_synced_at is not None
    finally:
        m.stop()
