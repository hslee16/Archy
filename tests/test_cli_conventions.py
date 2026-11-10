"""The `conventions` command."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from archy.cli import (
    main,
)
from tests.cli_helpers import _make_conventions_project, _make_empty_package_project


def test_python_m_entry_point_reaches_the_header_helpers(tmp_path: Path):
    """`python -m archy.cli` must run main() after the header helpers are defined.

    The `__main__` guard used to sit above `_require_headers_flags`, so this
    command died with a NameError instead of printing the usage error the
    console script prints (#782).
    """
    project = _make_empty_package_project(tmp_path)
    (project / "pkg" / "a.py").write_text("X = 1\n")
    r = subprocess.run(
        [sys.executable, "-m", "archy.cli", "conventions", str(project), "--write"],
        capture_output=True,
        text=True,
    )
    combined = r.stdout + r.stderr
    assert "NameError" not in combined
    assert "require --emit-headers" in combined


def test_conventions_text_reports_all_four_sections(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project)])
    assert result.exit_code == 0
    for heading in ("## naming", "## surfaces", "## gates", "## models"):
        assert heading in result.output
    assert "*Violation(3)" in result.output
    assert "pkg.layers" in result.output
    assert "param:strict" in result.output
    # The gate/error split is the headline: a finding-failure exit and a
    # bad-input exit must never share a count.
    assert "## errors" in result.output
    assert "1 finding-failure exit(s)" in result.output


def test_conventions_always_exits_zero_even_with_gates_present(tmp_path: Path):
    # Advisory, not a gate: only `check`, `contracts` and the `--strict`
    # variants fail a build. Reporting that a project HAS gates must not
    # itself become one.
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project)])
    assert result.exit_code == 0


def test_conventions_json_matches_the_text_surface(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["totals"]["naming"] >= 1
    home = next(h for h in payload["naming"] if h["module"] == "pkg.layers")
    family = next(f for f in home["families"] if f["suffix"] == "Violation")
    assert family["home_module"] == "pkg.layers"
    # Derived values must survive `model_dump()` -- a plain property would be
    # silently absent here and on the MCP wire.
    assert "concentration" in family
    assert {"total", "family_count"} <= set(home)
    assert "gate_modules" in payload
    # `run` guards its exit on `strict`, a plain parameter, not a Click flag,
    # so it is a finding-failure gate; nothing here rejects bad user input.
    assert [g["function"] for g in payload["gates"]] == ["run"]
    assert payload["gates"][0]["code"] == 1
    assert payload["errors"] == []
    assert payload["models"]["dominant_base"] is None


def test_conventions_top_truncates_but_reports_the_total(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(
        main, ["conventions", str(project), "--top", "1", "--format", "json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert len(payload["naming"]) == 1
    assert payload["totals"]["naming"] >= len(payload["naming"])


def test_conventions_rejects_a_nonsense_min_family(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project), "--min-family", "1"])
    assert result.exit_code != 0
    assert "min-family" in result.output


def test_conventions_rejects_a_nonsense_top(tmp_path: Path):
    project = _make_conventions_project(tmp_path)
    result = CliRunner().invoke(main, ["conventions", str(project), "--top", "0"])
    assert result.exit_code != 0


def test_conventions_default_output_surfaces_a_small_but_located_family(tmp_path: Path):
    # Acceptance criterion for the by-home-module shape: a 2-member family in
    # its own module must be visible with NO flags, even alongside a family
    # six times its size. Ranked flat by count it would fall below the fold.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mcp.py").write_text("".join(f"class P{i}Payload: pass\n" for i in range(13)))
    (pkg / "layers.py").write_text("class Violation: pass\nclass ReachViolation: pass\n")
    result = CliRunner().invoke(main, ["conventions", str(tmp_path)])
    assert result.exit_code == 0
    assert "pkg.layers" in result.output
    assert "*Violation(2)" in result.output


def test_conventions_separates_gates_from_user_error_exits(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "cli.py").write_text(
        "import click\n"
        "import sys\n"
        "def gate(bad):\n"
        "    if bad:\n"
        "        sys.exit(1)\n"
        "def oops(path):\n"
        "    if not path:\n"
        '        raise click.ClickException("nope")\n'
    )
    result = CliRunner().invoke(main, ["conventions", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 finding-failure exit(s)" in result.output
    assert "1 user-error exit(s)" in result.output
    assert "exit code(s): 1" in result.output
    assert "ClickException=1" in result.output
