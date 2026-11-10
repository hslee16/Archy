"""The `index` command and the edge-spec parser."""

from __future__ import annotations

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
