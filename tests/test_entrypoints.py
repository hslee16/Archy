"""Entrypoint derivation: the facts behind `required:` rules' `@entrypoints` source.

The parser half pins which statements count as a main guard; the derivation
half pins the three signals and how each module's reason is chosen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.entrypoints import MAIN_GUARD, MAIN_MODULE, derive_entrypoints
from archy.graph import build_graph
from archy.parser import parse_source


@pytest.mark.parametrize(
    "source",
    [
        'if __name__ == "__main__":\n    main()\n',
        "if __name__ == '__main__':\n    main()\n",
        'if "__main__" == __name__:\n    main()\n',
        'import sys\n\ndef main():\n    pass\n\nif __name__ == "__main__":\n    sys.exit(main())\n',
    ],
)
def test_parser_detects_a_top_level_main_guard(source: str):
    assert parse_source(source.encode()).has_main_guard is True


@pytest.mark.parametrize(
    "source",
    [
        # Nested in a function: never runs when the file is executed.
        'def f():\n    if __name__ == "__main__":\n        pass\n',
        'class C:\n    if __name__ == "__main__":\n        pass\n',
        # The inverse test runs on import, not as a script entry.
        'if __name__ != "__main__":\n    pass\n',
        'if __name__ == "main":\n    pass\n',
        'if name == "__main__":\n    pass\n',
        "x = 1\n",
    ],
)
def test_parser_rejects_what_is_not_a_main_guard(source: str):
    assert parse_source(source.encode()).has_main_guard is False


def _write(root: Path, rel: str, body: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def test_derive_names_each_signal_with_its_reason(tmp_path: Path):
    """One module per signal, plus one that matches none, on real files."""
    _write(tmp_path, "app/__init__.py")
    _write(tmp_path, "app/worker.py", 'if __name__ == "__main__":\n    pass\n')
    _write(tmp_path, "app/__main__.py")
    _write(tmp_path, "app/cli.py", "def main():\n    pass\n")
    _write(tmp_path, "app/gui.py", "def main():\n    pass\n")
    _write(tmp_path, "app/library.py", "def helper():\n    pass\n")
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\nname = "app"\n'
        '[project.scripts]\napp-cli = "app.cli:main"\n'
        '[project.gui-scripts]\napp-gui = "app.gui:main [extra]"\n',
    )

    entrypoints = build_graph(tmp_path).graph["entrypoints"]

    assert entrypoints == {
        "app.__main__": MAIN_MODULE,
        "app.cli": "console script `app-cli` in pyproject.toml",
        "app.gui": "console script `app-gui` in pyproject.toml",
        "app.worker": MAIN_GUARD,
    }


def test_derive_reads_poetry_scripts_and_skips_the_file_form(tmp_path: Path):
    """Poetry's table form names a FILE, so it has no module to bind."""
    _write(
        tmp_path,
        "pyproject.toml",
        "[tool.poetry.scripts]\n"
        'run = "app.cli:main"\n'
        'tool = { reference = "bin/tool.sh", type = "file" }\n',
    )

    entrypoints, errors = derive_entrypoints(tmp_path, guarded=(), internal=("app", "app.cli"))

    assert entrypoints == {"app.cli": "console script `run` in pyproject.toml"}
    assert errors == ()


def test_derive_ignores_a_script_target_outside_the_scan(tmp_path: Path):
    _write(tmp_path, "pyproject.toml", '[project.scripts]\nother = "elsewhere.cli:main"\n')

    entrypoints, _ = derive_entrypoints(tmp_path, guarded=(), internal=("app.cli",))

    assert entrypoints == {}


def test_derive_prefers_the_guard_when_signals_overlap(tmp_path: Path):
    """A console-script target that also has a guard keeps one stable reason."""
    _write(tmp_path, "pyproject.toml", '[project.scripts]\napp = "app.cli:main"\n')

    entrypoints, _ = derive_entrypoints(tmp_path, guarded=("app.cli",), internal=("app.cli",))

    assert entrypoints == {"app.cli": MAIN_GUARD}


def test_derive_reports_an_unreadable_pyproject(tmp_path: Path):
    """Swallowing the error would shrink the set and let a rule pass over the
    very modules the scripts table names."""
    _write(tmp_path, "pyproject.toml", "[project.scripts\n")

    entrypoints, errors = derive_entrypoints(tmp_path, guarded=(), internal=("app.cli",))

    assert entrypoints == {}
    assert len(errors) == 1
    assert "pyproject.toml could not be read" in errors[0]


def test_derive_without_a_pyproject_is_not_an_error(tmp_path: Path):
    entrypoints, errors = derive_entrypoints(tmp_path, guarded=("a",), internal=("a",))

    assert entrypoints == {"a": MAIN_GUARD}
    assert errors == ()
