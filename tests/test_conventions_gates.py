"""Tests for conventions: gates."""

from __future__ import annotations

from pathlib import Path

from archy.conventions import (
    compute_conventions,
)
from tests.conventions_helpers import _project


def test_gate_control_names_the_click_flag(tmp_path: Path):
    project = _project(
        tmp_path,
        {
            "cli.py": (
                "import click\n"
                "import sys\n"
                "@click.command()\n"
                '@click.option("--strict", is_flag=True)\n'
                "def run(strict):\n"
                "    if strict:\n"
                "        sys.exit(1)\n"
            )
        },
    )
    report = compute_conventions(project)
    gate = next(g for g in report.gates if g.function == "run")
    assert gate.control == "flag:--strict"
    assert gate.kind == "sys.exit"
    assert gate.category == "gate"
    assert gate.code == 1
    assert gate.is_command is True
    assert gate.optional is True
    assert report.gate_modules == ("pkg.cli",)


def test_gate_control_falls_back_to_config_attribute_then_hardcoded(tmp_path: Path):
    project = _project(
        tmp_path,
        {
            "a.py": (
                "import sys\n"
                "def gated(config):\n"
                "    if config.fail_on_error:\n"
                "        sys.exit(2)\n"
                "def always():\n"
                "    raise SystemExit(1)\n"
            )
        },
    )
    report = compute_conventions(project)
    by_function = {g.function: g for g in report.gates}
    # `config` is a parameter, but the ATTRIBUTE is the lever a caller sets, so
    # the more specific reading wins over `param:config`.
    assert by_function["gated"].control == "config:config.fail_on_error"
    assert by_function["always"].control == "hardcoded"
    assert by_function["always"].optional is False


def test_finding_exits_and_user_error_exits_are_counted_separately(tmp_path: Path):
    # A `sys.exit(n)` says a FINDING failed (the code IS the result); a raised
    # ClickException says the USER supplied bad input. Pooling them makes the
    # headline count answer a different question than "should my finding gate".
    project = _project(
        tmp_path,
        {
            "cli.py": (
                "import click\n"
                "import sys\n"
                "def gate(violations):\n"
                "    if violations:\n"
                "        sys.exit(1)\n"
                "def bad_config(path):\n"
                "    if not path:\n"
                '        raise click.ClickException("no config")\n'
                "def bad_flag(top):\n"
                "    if top < 1:\n"
                '        raise click.UsageError("--top must be >= 1")\n'
            )
        },
    )
    report = compute_conventions(project)
    assert [g.function for g in report.gates] == ["gate"]
    assert sorted(g.function for g in report.errors) == ["bad_config", "bad_flag"]
    assert {g.category for g in report.gates} == {"gate"}
    assert {g.category for g in report.errors} == {"error"}
    # `gate_modules` is about finding-failure only; a module that merely
    # rejects bad input is not a module that gates.
    assert report.gate_modules == ("pkg.cli",)


def test_literal_exit_codes_are_reported_and_computed_ones_are_not(tmp_path: Path):
    # "everything fails with 1" is itself the convention; a computed code has
    # two possible values, so reporting either one would be a lie.
    project = _project(
        tmp_path,
        {
            "cli.py": (
                "import sys\n"
                "def fixed():\n"
                "    sys.exit(1)\n"
                "def computed(ok):\n"
                "    sys.exit(0 if ok else 1)\n"
                "def framework():\n"
                "    raise SystemExit(3)\n"
            )
        },
    )
    report = compute_conventions(project)
    codes = {g.function: g.code for g in report.gates}
    assert codes == {"fixed": 1, "computed": None, "framework": 3}
    assert report.gate_codes == (1, 3)
