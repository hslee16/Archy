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
        'if (__name__ == "__main__"):\n    main()\n',
        'if __name__ == "__main__" and not DEBUG:\n    main()\n',
        'if ready and (__name__ == "__main__"):\n    main()\n',
        'if __name__ == r"__main__":\n    main()\n',
        'if __name__ == """__main__""":\n    main()\n',
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
        # `or` runs the body on import too; bytes and f-strings never equal a
        # module name; the last compares against the value `'__main__'`.
        'if __name__ == "__main__" or x:\n    pass\n',
        'if __name__ == b"__main__":\n    pass\n',
        'if __name__ == f"__main__":\n    pass\n',
        "if __name__ == \"'__main__'\":\n    pass\n",
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


def _modules(root: Path, *qualnames: str) -> dict[str, Path]:
    """Real files for `qualnames`, laid out as packages under `root`."""
    out: dict[str, Path] = {}
    for qualname in qualnames:
        path = root.joinpath(*qualname.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        out[qualname] = path
    return out


def test_derive_reads_both_poetry_script_forms_and_skips_files(tmp_path: Path):
    """Poetry's table form is a console script unless `type = "file"`."""
    _write(
        tmp_path,
        "pyproject.toml",
        "[tool.poetry.scripts]\n"
        'run = "app.cli:main"\n'
        'extra = { reference = "app.extra:main", type = "console", extras = ["x"] }\n'
        'tool = { reference = "bin/tool.sh", type = "file" }\n',
    )
    modules = _modules(tmp_path, "app.cli", "app.extra")

    entrypoints, errors = derive_entrypoints(tmp_path, guarded=(), modules=modules)

    assert entrypoints == {
        "app.cli": "console script `run` in pyproject.toml",
        "app.extra": "console script `extra` in pyproject.toml",
    }
    assert errors == ()


def test_derive_reads_the_project_pyproject_when_scanning_a_subdirectory(tmp_path: Path):
    """`archy check src` must see what the repository root declares."""
    (tmp_path / ".git").mkdir()
    _write(tmp_path, "pyproject.toml", '[project.scripts]\npkg-cli = "pkg.cli:main"\n')
    modules = _modules(tmp_path / "src", "pkg.cli")

    entrypoints, errors = derive_entrypoints(tmp_path / "src", guarded=(), modules=modules)

    assert entrypoints == {"pkg.cli": "console script `pkg-cli` in pyproject.toml"}
    assert errors == ()


def test_derive_reads_each_monorepo_subproject(tmp_path: Path):
    _write(tmp_path, "packages/a/pyproject.toml", '[project.scripts]\nalpha = "alpha.cli:main"\n')
    _write(tmp_path, "packages/b/pyproject.toml", '[project.scripts]\nbeta = "beta.cli:main"\n')
    modules = {
        **_modules(tmp_path / "packages" / "a", "alpha.cli"),
        **_modules(tmp_path / "packages" / "b", "beta.cli"),
    }

    entrypoints, _ = derive_entrypoints(tmp_path, guarded=(), modules=modules)

    assert set(entrypoints) == {"alpha.cli", "beta.cli"}


def test_derive_stops_at_the_repository_root(tmp_path: Path):
    """A pyproject.toml above `.git` belongs to some other project."""
    _write(tmp_path, "pyproject.toml", '[project.scripts]\nstray = "pkg.cli:main"\n')
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    modules = _modules(repo, "pkg.cli")

    entrypoints, errors = derive_entrypoints(repo, guarded=(), modules=modules)

    assert entrypoints == {}
    assert errors == ()


def test_derive_resolves_a_namespace_package_target(tmp_path: Path):
    """No `acme/__init__.py`: the installer imports `acme.tool.cli`, archy
    names the file `tool.cli`."""
    _write(tmp_path, "pyproject.toml", '[project.scripts]\nacme-tool = "acme.tool.cli:main"\n')
    modules = _modules(tmp_path, "tool.cli")

    entrypoints, errors = derive_entrypoints(tmp_path, guarded=(), modules=modules)

    assert entrypoints == {"tool.cli": "console script `acme-tool` in pyproject.toml"}
    assert errors == ()


def test_derive_never_resolves_a_target_to_a_single_segment(tmp_path: Path):
    _write(tmp_path, "pyproject.toml", '[project.scripts]\nx = "other.cli:main"\n')
    modules = _modules(tmp_path, "cli")

    entrypoints, errors = derive_entrypoints(tmp_path, guarded=(), modules=modules)

    assert entrypoints == {}
    assert errors == ()


@pytest.mark.parametrize(
    "target",
    [
        pytest.param("tools.gen:main", id="excluded-directory"),
        pytest.param("alembic.config:main", id="re-exported-third-party-tool"),
    ],
)
def test_derive_skips_a_target_outside_the_scan_without_refusing(tmp_path: Path, target: str):
    """Refusing here left the rule permanently unable to pass: nothing in
    archy.yaml can make a third-party module or an excluded directory scanned."""
    _write(tmp_path, "pyproject.toml", f'[project.scripts]\nx = "{target}"\n')
    modules = _modules(tmp_path, "pkg.cli")

    entrypoints, errors = derive_entrypoints(tmp_path, guarded=(), modules=modules)

    assert entrypoints == {}
    assert errors == ()


def test_derive_does_not_rebind_a_stale_target_of_a_scanned_package(tmp_path: Path):
    """`myproj` is a real scanned package, so `myproj.tools.cli` is stale, not a
    namespace spelling; stripping `myproj.` would blame an unrelated `tools.cli`."""
    _write(tmp_path, "pyproject.toml", '[project.scripts]\nmyproj = "myproj.tools.cli:main"\n')
    modules = _modules(tmp_path, "myproj.boot", "tools.cli")

    entrypoints, _ = derive_entrypoints(tmp_path, guarded=(), modules=modules)

    assert entrypoints == {}


def test_derive_without_a_repository_marker_reads_nothing_above_the_scan(tmp_path: Path):
    """An exported tree with no VCS directory: a parent's pyproject.toml is not
    this project's, so it neither binds modules nor breaks the rule."""
    _write(tmp_path, "pyproject.toml", '[project.scripts]\nother = "unrelated.app.cli:main"\n')
    project = tmp_path / "proj"
    modules = _modules(project, "app.cli")

    assert derive_entrypoints(project, guarded=(), modules=modules) == ({}, ())

    _write(tmp_path, "pyproject.toml", "[project.scripts\n")
    assert derive_entrypoints(project, guarded=(), modules=modules) == ({}, ())


@pytest.mark.parametrize("marker", [".git", ".hg", ".jj", ".svn", ".sl"])
def test_derive_climbs_to_any_repository_marker(tmp_path: Path, marker: str):
    (tmp_path / marker).mkdir()
    _write(tmp_path, "pyproject.toml", '[project.scripts]\npkg-cli = "pkg.cli:main"\n')
    modules = _modules(tmp_path / "src", "pkg.cli")

    entrypoints, _ = derive_entrypoints(tmp_path / "src", guarded=(), modules=modules)

    assert entrypoints == {"pkg.cli": "console script `pkg-cli` in pyproject.toml"}


def test_derive_ignores_an_unresolved_target_above_the_scan(tmp_path: Path):
    """Scanning one package of a project: the root's other scripts are not
    this scan's business."""
    (tmp_path / ".git").mkdir()
    _write(tmp_path, "pyproject.toml", '[project.scripts]\nother = "elsewhere.cli:main"\n')
    modules = _modules(tmp_path / "src", "pkg.cli")

    entrypoints, errors = derive_entrypoints(tmp_path / "src", guarded=(), modules=modules)

    assert entrypoints == {}
    assert errors == ()


def test_derive_prefers_the_guard_when_signals_overlap(tmp_path: Path):
    """A console-script target that also has a guard keeps one stable reason."""
    _write(tmp_path, "pyproject.toml", '[project.scripts]\napp = "app.cli:main"\n')
    modules = _modules(tmp_path, "app.cli")

    entrypoints, _ = derive_entrypoints(tmp_path, guarded=("app.cli",), modules=modules)

    assert entrypoints == {"app.cli": MAIN_GUARD}


def test_derive_reports_an_unreadable_pyproject(tmp_path: Path):
    """Swallowing the error would shrink the set and let a rule pass over the
    very modules the scripts table names."""
    _write(tmp_path, "pyproject.toml", "[project.scripts\n")
    modules = _modules(tmp_path, "app.cli")

    entrypoints, errors = derive_entrypoints(tmp_path, guarded=(), modules=modules)

    assert entrypoints == {}
    assert len(errors) == 1
    assert "pyproject.toml could not be read" in errors[0]


def test_derive_without_a_pyproject_is_not_an_error(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    modules = _modules(tmp_path, "a")

    entrypoints, errors = derive_entrypoints(tmp_path, guarded=("a",), modules=modules)

    assert entrypoints == {"a": MAIN_GUARD}
    assert errors == ()
