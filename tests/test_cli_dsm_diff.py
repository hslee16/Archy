"""`archy dsm --diff` names the edge that closes a cycle, whichever way the names sort (#747)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from tests.cli_helpers import _dsm_json

# (source, target): the edit adds source -> target, closing a two-module cycle.
# "b" -> "a" sorts the new edge backward; "a" -> "z" sorts it forward.
EDGE_ORDERS = [("b", "a"), ("a", "z")]


def _close_cycle(tmp_path: Path, source: str, target: str) -> tuple[Path, dict]:
    """Snapshot target -> source as the baseline, then add source -> target."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / f"{source}.py").write_text("X = 1\n")
    (pkg / f"{target}.py").write_text(f"from pkg import {source}\n")
    baseline = tmp_path / "before.json"
    baseline.write_text(json.dumps(_dsm_json(tmp_path, "--group", "topological")))
    (pkg / f"{source}.py").write_text(f"from pkg import {target}\n")
    return baseline, _dsm_json(tmp_path, "--group", "topological")


def _diff(tmp_path: Path, baseline: Path, output_format: str) -> str:
    result = CliRunner().invoke(
        main,
        [
            "dsm",
            str(tmp_path),
            "--group",
            "topological",
            "--diff",
            str(baseline),
            "--format",
            output_format,
        ],
    )
    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.parametrize(("source", "target"), EDGE_ORDERS)
def test_json_diff_lists_the_cycle_closing_edge(tmp_path: Path, source: str, target: str):
    baseline, after = _close_cycle(tmp_path, source, target)

    cells = json.loads(_diff(tmp_path, baseline, "json"))["new_back_edges"]

    names = [(after["ordering"][c["row"]], after["ordering"][c["col"]]) for c in cells]
    assert names == [(f"pkg.{source}", f"pkg.{target}")]


@pytest.mark.parametrize(("source", "target"), EDGE_ORDERS)
def test_ascii_diff_names_the_cycle_closing_edge(tmp_path: Path, source: str, target: str):
    baseline, _ = _close_cycle(tmp_path, source, target)

    output = _diff(tmp_path, baseline, "ascii")

    assert "Cycle-closing edges (1):" in output
    assert f"pkg.{source} -> pkg.{target}" in output
