"""Score history must distinguish internal-only scores from scores including externals."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.history import read
from archy.mcp import create_server
from archy.mcp_runs import run_score, run_trend
from tests.mcp_helpers import call_tool


@pytest.fixture
def scoped_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("import os, sys, json\nfrom pkg import b\n", encoding="utf-8")
    (pkg / "b.py").write_text("import re, math, typing\n", encoding="utf-8")
    internal = _run(tmp_path, True)
    external = _run(tmp_path, False)
    assert internal["inputs"]["module_count"] == 3
    assert external["inputs"]["module_count"] == 9
    assert internal["overall"] != external["overall"]
    return tmp_path


def _run(path: Path, scope: bool) -> dict:
    return run_score(
        path, internal_only=scope, record=False, strict=True, strict_tolerance=0.0
    ).model_dump()


def _seed(path: Path, rows: list[tuple[bool | None, float]]) -> None:
    history = path / ".archy" / "history.jsonl"
    CliRunner().invoke(main, ["score", str(path), "--record"])
    template = json.loads(history.read_text(encoding="utf-8").splitlines()[0])
    lines = []
    for scope, overall in rows:
        template["internal_only"] = scope
        template["score"]["overall"] = overall
        lines.append(json.dumps(template))
    history.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize("scope", [True, False])
def test_other_scope_refuses_on_all_surfaces(scoped_project: Path, scope: bool):
    path = scoped_project
    flags = ["--internal-only"] if scope else ["--all"]
    opposite = ["--all"] if scope else ["--internal-only"]
    runner = CliRunner()
    assert runner.invoke(main, ["score", str(path), *opposite, "--record"]).exit_code == 0
    for fmt in ("text", "json"):
        result = runner.invoke(main, ["score", str(path), *flags, "--strict", "--format", fmt])
        assert result.exit_code == 1, result.output
        assert "no recorded score for" in result.output
        assert "--record" in result.output
        assert "improved" not in result.output and "dropped" not in result.output
    gate = _run(path, scope)["gate"]
    assert gate["passed"] is False and gate["previous"] is None and gate["delta"] is None
    _, wire = call_tool(
        create_server(), "archy_score", {"path": str(path), "internal_only": scope, "strict": True}
    )
    assert wire is not None
    assert wire["result"]["gate"]["passed"] is False
    assert "--record" in wire["result"]["gate"]["reason"]


@pytest.mark.parametrize("legacy", ["absent", "older", "newer"])
def test_latest_matching_row_is_pinned_on_wire(scoped_project: Path, legacy: str):
    current = _run(scoped_project, True)["overall"]
    assert current != 0.1
    rows: list[tuple[bool | None, float]] = [(True, 0.1), (True, current), (False, 0.2)]
    if legacy != "absent":
        rows.insert(0 if legacy == "older" else len(rows), (None, 0.9))
    _seed(scoped_project, rows)
    gate = _run(scoped_project, True)["gate"]
    assert gate["previous"] == current
    assert gate["delta"] == 0.0 and gate["passed"] is True
    result = CliRunner().invoke(
        main, ["score", str(scoped_project), "--strict", "--format", "json"]
    )
    assert json.loads(result.output)["gate"]["delta"] == 0.0


def test_legacy_fallback_is_explicit_and_latest(scoped_project: Path):
    _seed(scoped_project, [(None, 0.1), (False, 0.2), (None, 0.9)])
    gate = _run(scoped_project, True)["gate"]
    assert gate["previous"] == 0.9
    assert "scope unknown" in gate["reason"]
    result = CliRunner().invoke(main, ["score", str(scoped_project), "--strict"])
    assert "scope unknown" in result.output


@pytest.mark.parametrize("mcp", [False, True])
def test_strict_record_compares_before_append_and_writes_scope(scoped_project: Path, mcp: bool):
    _seed(scoped_project, [(True, 0.1), (True, 0.99), (False, 0.2)])
    if mcp:
        gate = run_score(
            scoped_project, internal_only=True, record=True, strict=True, strict_tolerance=0.0
        ).model_dump()["gate"]
    else:
        result = CliRunner().invoke(
            main, ["score", str(scoped_project), "--strict", "--record", "--format", "json"]
        )
        assert result.exit_code == 1
        gate = json.loads(result.output)["gate"]
    assert gate["previous"] == 0.99 and gate["passed"] is False
    rows = read(scoped_project / ".archy" / "history.jsonl")
    assert len(rows) == 4 and rows[-1].internal_only is True


MIXED_ROWS: list[tuple[bool | None, float]] = [(True, 0.1), (False, 0.9), (None, 0.5)]
SCOPE_LABELS = ("internal-only", "all", "unknown")


def test_recording_both_scopes_labels_each_row(scoped_project: Path):
    for scope in (True, False):
        run_score(
            scoped_project, internal_only=scope, record=True, strict=False, strict_tolerance=0.0
        )
    assert _run(scoped_project, True)["gate"]["delta"] == 0.0
    assert [r.internal_only for r in read(scoped_project / ".archy/history.jsonl")] == [True, False]


def test_trend_text_labels_mixed_scopes_and_draws_no_line(scoped_project: Path):
    _seed(scoped_project, MIXED_ROWS)
    text = CliRunner().invoke(main, ["trend", str(scoped_project)]).output
    for label in SCOPE_LABELS:
        assert label in text
    assert "mixed scopes" in text
    assert "0.100 -> 0.500" not in text


def test_trend_json_and_mcp_keep_each_scope(scoped_project: Path):
    _seed(scoped_project, MIXED_ROWS)
    result = CliRunner().invoke(main, ["trend", str(scoped_project), "--format", "json"])
    assert [row["internal_only"] for row in json.loads(result.output)] == [True, False, None]
    rows = run_trend(scoped_project, last_n=10)
    assert [row.model_dump()["internal_only"] for row in rows] == [True, False, None]


def test_trend_html_labels_mixed_scopes_and_draws_no_line(scoped_project: Path):
    _seed(scoped_project, MIXED_ROWS)
    html = CliRunner().invoke(main, ["render", str(scoped_project), "--view", "trend"]).output
    for label in SCOPE_LABELS:
        assert label in html
    assert "mixed scopes" in html
    assert "<polyline" not in html


@pytest.mark.parametrize("bad", ["false", 0, 1, [], {}])
def test_corrupt_scope_is_not_coerced_to_a_known_scope(scoped_project: Path, bad: object):
    _seed(scoped_project, [(True, 0.4)])
    history = scoped_project / ".archy" / "history.jsonl"
    payload = json.loads(history.read_text(encoding="utf-8"))
    payload["internal_only"] = bad
    history.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    assert read(history) == []


def test_legacy_rows_do_not_hide_the_trend(scoped_project: Path):
    """An upgrade leaves unlabelled rows before labelled ones; the line must survive it."""
    _seed(scoped_project, [(None, 0.1), (True, 0.5)])
    runner = CliRunner()
    text = runner.invoke(main, ["trend", str(scoped_project)]).output
    assert "0.100 -> 0.500" in text and "mixed scopes" not in text
    html = runner.invoke(main, ["render", str(scoped_project), "--view", "trend"]).output
    assert "<polyline" in html and "mixed scopes" not in html


@pytest.mark.parametrize("mcp", [False, True])
def test_strict_record_seeds_a_new_scope_instead_of_failing_once(scoped_project: Path, mcp: bool):
    """The refusal's advice is to record; a run that records already follows it."""
    _seed(scoped_project, [(False, 0.2)])
    if mcp:
        gate = run_score(
            scoped_project, internal_only=True, record=True, strict=True, strict_tolerance=0.0
        ).model_dump()["gate"]
        assert gate["passed"] is True
        assert gate["previous"] is None and "This run records one." in gate["reason"]
    else:
        result = CliRunner().invoke(main, ["score", str(scoped_project), "--strict", "--record"])
        assert result.exit_code == 0 and "This run records one." in result.output
    assert _run(scoped_project, True)["gate"]["delta"] == 0.0


def test_legacy_fallback_names_the_command_that_ends_it(scoped_project: Path):
    _seed(scoped_project, [(None, 0.1)])
    assert "archy score --internal-only --record" in _run(scoped_project, True)["gate"]["reason"]
