import ast
from pathlib import Path

from click.testing import CliRunner

from archy.cli import main
from tests.cli_helpers import _make_empty_package_project


def test_write_keeps_the_script_preamble_before_the_module_docstring(tmp_path: Path):
    project = _make_empty_package_project(tmp_path)
    script = project / "pkg" / "script.py"
    preamble = "#!/usr/bin/env python\n# -*- coding: utf-8 -*-\n\n# Script entry point.\n"
    script.write_text(preamble + "def run():\n    pass\n", encoding="utf-8")
    assert ast.get_docstring(ast.parse(script.read_bytes())) is None

    result = CliRunner().invoke(main, ["conventions", str(project), "--emit-headers", "--write"])

    assert result.exit_code == 0, result.output
    written = script.read_bytes()
    assert written.startswith(preamble.encode("utf-8"))
    assert ast.get_docstring(ast.parse(written)) == "archy:owns        run"
