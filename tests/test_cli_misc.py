"""The `index` command and the edge-spec parser."""

from __future__ import annotations

import shlex
import sqlite3
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from archy.cli import (
    _parse_edge_spec,
    main,
)
from tests.cli_helpers import _make_acyclic_project, _make_many_module_project


def test_index_sync_reports_stats_and_caches(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    first = CliRunner().invoke(main, ["index", "sync", str(project)])
    assert first.exit_code == 0
    assert "reparsed" in first.output
    assert (project / ".archy" / "index.db").exists()
    second = CliRunner().invoke(main, ["index", "sync", str(project)])
    assert second.exit_code == 0
    assert "0 reparsed" in second.output


def test_index_clear_removes_db(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    CliRunner().invoke(main, ["index", "sync", str(project)])
    result = CliRunner().invoke(main, ["index", "clear", str(project)])
    assert result.exit_code == 0
    assert not (project / ".archy" / "index.db").exists()
    again = CliRunner().invoke(main, ["index", "clear", str(project)])
    assert again.exit_code == 0
    assert "no cache" in again.output


def test_parse_edge_spec_valid():
    assert _parse_edge_spec("pkg.a:pkg.b") == ("pkg.a", "pkg.b")


@pytest.mark.parametrize("spec", ["pkg.a", "a:", ":b", "a:b:c", "C:\\x:C:\\y"])
def test_parse_edge_spec_rejects_bad_input(spec: str):
    # Includes the documented Windows-drive-path limitation: use MCP {from,to}.
    with pytest.raises(click.BadParameter):
        _parse_edge_spec(spec)


def test_index_sync_errors_on_oversized_scan(tmp_path: Path):
    # `index sync` reparses every changed file, so it gets the same backstop.
    project = _make_many_module_project(tmp_path, 5)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    result = CliRunner().invoke(main, ["index", "sync", str(project)])
    assert result.exit_code != 0
    assert "max_modules" in result.output


@pytest.mark.parametrize("command", ["status", "sync"])
def test_index_commands_report_a_corrupt_cache_without_a_traceback(tmp_path: Path, command: str):
    # #776: the commands a user runs to diagnose a bad cache crashed on it.
    project = _make_acyclic_project(tmp_path)
    assert CliRunner().invoke(main, ["index", "sync", str(project)]).exit_code == 0
    (project / ".archy" / "index.db").write_text("garbage\n")
    result = CliRunner().invoke(main, ["index", command, str(project)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)  # a ClickException, not a crash
    assert "corrupt cache at" in result.output
    assert "archy index clear" in result.output
    # The one-liner's remedy works.
    assert CliRunner().invoke(main, ["index", "clear", str(project)]).exit_code == 0
    assert CliRunner().invoke(main, ["index", command, str(project)]).exit_code == 0


def test_index_status_reports_drift_from_the_tree(tmp_path: Path):
    # #776: status promised freshness but printed only the row count.
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("")
    (project / "pkg" / "a.py").write_text("import pkg.b\n")
    (project / "pkg" / "b.py").write_text("X = 1\n")
    (project / "pkg" / "d.py").write_text("Z = 1\n")
    assert CliRunner().invoke(main, ["index", "sync", str(project)]).exit_code == 0

    fresh = CliRunner().invoke(main, ["index", "status", str(project)])
    assert fresh.exit_code == 0, fresh.output
    assert "cached_files: 4\nstale: 0\nmissing: 0\nunindexed: 0" in fresh.output

    (project / "pkg" / "c.py").write_text("Y = 1\n")  # added
    (project / "pkg" / "b.py").unlink()  # deleted
    (project / "pkg" / "d.py").write_text("Z = 22\n")  # changed: size differs
    drift = CliRunner().invoke(main, ["index", "status", str(project)])
    assert drift.exit_code == 0, drift.output
    assert "cached_files: 4\nstale: 1\nmissing: 1\nunindexed: 1" in drift.output

    # The counts are what the next sync acts on, and the sync clears them.
    synced = CliRunner().invoke(main, ["index", "sync", str(project)])
    assert "2 reparsed, 2 unchanged, 1 pruned" in synced.output
    after = CliRunner().invoke(main, ["index", "status", str(project)])
    assert "cached_files: 4\nstale: 0\nmissing: 0\nunindexed: 0" in after.output


@pytest.mark.parametrize("command", ["status", "sync"])
def test_index_commands_do_not_prescribe_clear_for_a_directory(tmp_path: Path, command: str):
    project = _make_acyclic_project(tmp_path)
    db = project / ".archy" / "index.db"
    db.mkdir(parents=True)
    result = CliRunner().invoke(main, ["index", command, str(project)])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(db) in result.output
    assert "Error: corrupt cache at" not in result.output
    assert "archy index clear" not in result.output
    assert db.is_dir()


@pytest.mark.parametrize("command", ["status", "sync"])
def test_index_commands_do_not_call_a_locked_cache_corrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
):
    project = _make_acyclic_project(tmp_path)
    runner = CliRunner()
    assert runner.invoke(main, ["index", "sync", str(project)]).exit_code == 0
    db = project / ".archy" / "index.db"
    connect = sqlite3.connect

    def connect_quickly(db_path: Path, *, check_same_thread: bool = True):
        return connect(db_path, timeout=0.01, check_same_thread=check_same_thread)

    # Use real SQLite locking; only shorten the busy wait for this test.
    monkeypatch.setattr(sqlite3, "connect", connect_quickly)
    lock = connect(db)
    try:
        lock.execute("BEGIN EXCLUSIVE")
        result = runner.invoke(main, ["index", command, str(project)])
    finally:
        lock.rollback()
        lock.close()
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "database is locked" in result.output
    assert "Error: corrupt cache at" not in result.output
    assert "archy index clear" not in result.output
    # Releasing the lock, without clearing the cache, restores normal operation.
    recovered = runner.invoke(main, ["index", "sync", str(project)])
    assert recovered.exit_code == 0
    assert "0 reparsed, 3 unchanged" in recovered.output


@pytest.mark.parametrize("command", ["status", "sync"])
def test_index_corrupt_cache_remedy_handles_spaces(tmp_path: Path, command: str):
    project = tmp_path / "my project"
    project.mkdir()
    _make_acyclic_project(project)
    db = project / ".archy" / "index.db"
    db.parent.mkdir()
    db.write_text("garbage\n")
    runner = CliRunner()
    result = runner.invoke(main, ["index", command, str(project)])
    assert result.exit_code == 1
    remedy = shlex.split(result.output.split("`")[1])
    assert remedy == ["archy", "index", "clear", str(project)]
    assert runner.invoke(main, remedy[1:]).exit_code == 0
    assert not db.exists()
