"""An init-less `tests/` beside a `src/` layout is scanned (#529).

`_qualname_for` names a file only under the parent of some package root. In a
flat layout that parent is the repo root, so `tests/` got names by accident; in a
src layout it is `src/`, so a `tests/` with no `__init__.py` (pytest's own
recommendation) vanished and `archy affected` printed `0 test(s)` with no
warning, which a `| xargs pytest` pipeline reads as "nothing to run".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.affected import _test_classifier
from archy.cli import main
from archy.graph import _is_conventional_test_file, build_graph
from archy.mcp_runs import run_affected


def _src_layout(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "core.py").write_text("VALUE = 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_core.py").write_text("from pkg.core import VALUE\n")
    # The fixture reaches the branch: no `__init__.py` makes `tests` a package.
    assert not (root / "tests" / "__init__.py").exists()
    return root


def test_the_graph_holds_an_initless_tests_directory_beside_src(tmp_path: Path):
    graph = build_graph(_src_layout(tmp_path))

    assert "tests.test_core" in graph
    assert graph.has_edge("tests.test_core", "pkg.core")


def test_affected_selects_the_test_through_the_cli(tmp_path: Path):
    root = _src_layout(tmp_path)

    result = CliRunner().invoke(main, ["affected", str(root), "src/pkg/core.py", "--json"])

    payload = json.loads(result.output)
    assert payload["impacted_tests"] == ["tests.test_core"]


def test_affected_selects_the_test_on_the_mcp_surface(tmp_path: Path):
    root = _src_layout(tmp_path)

    dumped = run_affected(
        root, files=[root / "src" / "pkg" / "core.py"], depth=5, test_filter=None
    ).model_dump()

    assert list(dumped["impacted_tests"]) == ["tests.test_core"]


def test_only_test_files_outside_a_package_are_named(tmp_path: Path):
    """`docs/conf.py`, `noxfile.py` and `examples/` are scripts nothing imports.
    Naming them would move module counts and scores for no test-selection gain."""
    root = _src_layout(tmp_path)
    (root / "docs").mkdir()
    (root / "docs" / "conf.py").write_text("")
    (root / "examples").mkdir()
    (root / "examples" / "demo.py").write_text("")
    (root / "noxfile.py").write_text("")

    graph = build_graph(root)

    internal = {n for n, d in graph.nodes(data=True) if not d.get("external")}
    assert internal == {"pkg", "pkg.core", "tests.test_core"}


def test_a_loose_file_directly_under_src_is_named_from_src(tmp_path: Path):
    root = _src_layout(tmp_path)
    (root / "src" / "foo.py").write_text("")

    internal = {n for n, d in build_graph(root).nodes(data=True) if not d.get("external")}

    assert "foo" in internal
    assert "src.foo" not in internal


def test_a_fallback_name_that_equals_a_real_module_is_skipped_not_overwritten(tmp_path: Path):
    """`src/tests/` is a real package holding `tests.test_core`; the top-level
    `tests/test_core.py` would get the same name and must lose."""
    root = _src_layout(tmp_path)
    (root / "src" / "tests").mkdir()
    (root / "src" / "tests" / "__init__.py").write_text("")
    (root / "src" / "tests" / "test_core.py").write_text("REAL = 1\n")

    graph = build_graph(root)

    assert (
        Path(graph.nodes["tests.test_core"]["path"])
        == (root / "src" / "tests" / "test_core.py").resolve()
    )


def test_a_directory_that_cannot_be_imported_is_not_named(tmp_path: Path):
    root = _src_layout(tmp_path)
    (root / "my-tests").mkdir()
    (root / "my-tests" / "test_x.py").write_text("")

    internal = {n for n, d in build_graph(root).nodes(data=True) if not d.get("external")}

    assert not any("my-tests" in n for n in internal)


def test_a_declared_root_still_names_its_own_files(tmp_path: Path):
    root = _src_layout(tmp_path)
    (root / "archy.yaml").write_text("layers: {}\nforbid: []\nroots: [tests]\n")

    from archy.layers import load_config
    from archy.scan_config import scan_kwargs

    graph = build_graph(root, **scan_kwargs(load_config(root / "archy.yaml")))

    assert {n for n, d in graph.nodes(data=True) if not d.get("external")} == {
        "pkg",
        "pkg.core",
        "tests.test_core",
    }


@pytest.mark.parametrize(
    "rel",
    [
        "tests/test_a.py",
        "tests/helpers.py",
        "tests/sub/conftest.py",
        "pkg/test_b.py",
        "pkg/b_test.py",
        "docs/conf.py",
        "noxfile.py",
        "examples/demo.py",
        "src/attests/x.py",
    ],
)
def test_the_graph_and_affected_agree_on_what_a_test_file_is(tmp_path: Path, rel: str):
    """The rule is restated in `graph` because `affected` imports it, so pin them together."""
    assert _is_conventional_test_file(Path(rel)) == _test_classifier(tmp_path, None)(tmp_path / rel)
