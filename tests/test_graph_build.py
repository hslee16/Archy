"""Tests for graph: build."""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.graph import (
    DEFAULT_IGNORED_DIRS,
    ScanTooLargeError,
    _find_package_roots,
    build_graph,
)
from tests.graph_helpers import _internal_nodes, _src_layout_with_cycle, _write_flat_package


def test_external_imports_recorded_as_external_nodes(project: Path):
    g = build_graph(project)
    assert g.nodes["os"]["external"] is True
    assert g.nodes["requests"]["external"] is True
    assert g.nodes["json"]["external"] is True


def test_a_real_package_named_build_is_scanned(tmp_path: Path):
    """#518: a first-party package called `build` must not vanish.

    The layout is pip's `src/pip/_internal/operations/build/`: the directory is
    a package, imports first-party code, and used to be dropped silently.
    """
    core = tmp_path / "myapp" / "core"
    core.mkdir(parents=True)
    (tmp_path / "myapp" / "__init__.py").write_text("")
    (core / "__init__.py").write_text("")

    build_pkg = tmp_path / "myapp" / "operations" / "build"
    build_pkg.mkdir(parents=True)
    (tmp_path / "myapp" / "operations" / "__init__.py").write_text("")
    (build_pkg / "__init__.py").write_text("")
    (build_pkg / "wheel.py").write_text("from myapp import core\n")

    # Assert the fixture reaches the branch before asserting the outcome.
    assert (build_pkg / "__init__.py").is_file(), "fixture's build/ must be a package"

    g = build_graph(tmp_path)
    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}

    assert "myapp.operations.build" in internal
    assert "myapp.operations.build.wheel" in internal
    assert g.has_edge("myapp.operations.build.wheel", "myapp.core")


def test_wildcard_import_creates_single_edge_to_module(tmp_path: Path, pkg: Path):
    (pkg / "consumer.py").write_text("from pkg.utils import *\n")
    (pkg / "utils.py").write_text("")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.utils")


def test_function_local_imports_appear_in_graph(tmp_path: Path, pkg: Path):
    (pkg / "lazy.py").write_text("def go():\n    from pkg import other\n    return other\n")
    (pkg / "other.py").write_text("")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.lazy", "pkg.other")


def test_multiple_imports_same_target_aggregate_lines(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("import os\nimport os.path\nfrom os import sep\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.a", "os")
    assert len(g["pkg.a"]["os"]["lines"]) == 3


def test_unknown_name_falls_back_to_package_edge(tmp_path: Path, pkg: Path):
    (pkg / "consumer.py").write_text("from pkg import SomethingDefinedInline\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg")


def test_call_to_unimported_name_is_dropped(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("rogue.do()\n")
    g = build_graph(tmp_path)
    # Unresolved heads must not invent edges; doing so would let
    # locals/runtime-injected names corrupt the coupling signal.
    assert not any(d.get("call_count") for _, _, d in g.edges(data=True))


def test_cc_aggregates_on_internal_nodes(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text(
        "def f(x):\n    if x:\n        return 1\n    return 0\ndef g():\n    return 42\n"
    )
    (pkg / "empty.py").write_text("")
    g = build_graph(tmp_path)
    a = g.nodes["pkg.a"]
    assert a["function_count"] == 2
    assert a["cc_sum"] == 3
    assert a["cc_max"] == 2
    assert a["cc_mean"] == 1.5
    empty = g.nodes["pkg.empty"]
    assert empty["function_count"] == 0
    assert empty["cc_sum"] == 0


def test_build_graph_skips_file_that_vanishes_mid_build(project: Path, monkeypatch):
    """A file discovered by the FS walk can disappear before it is parsed (a
    branch switch, a concurrent edit, or the `archy mcp` watcher rebuilding
    mid-flight). build_graph must drop that module, not crash the whole build."""
    import archy.graph as graph_mod

    real_parse_file = graph_mod.parse_file

    def flaky_parse_file(path: Path):
        if path.name == "utils.py":
            raise FileNotFoundError(path)
        return real_parse_file(path)

    monkeypatch.setattr(graph_mod, "parse_file", flaky_parse_file)

    g = build_graph(project)

    internal = _internal_nodes(g)
    assert "myapp.utils" not in internal
    assert "myapp.core" in internal


def test_build_graph_unguarded_by_default(tmp_path: Path):
    # Direct/library callers (max_modules unset) are never guarded.
    _write_flat_package(tmp_path, "pkg", 5)
    g = build_graph(tmp_path)
    assert g.number_of_nodes() >= 5


def test_build_graph_trips_scan_size_guard(tmp_path: Path):
    _write_flat_package(tmp_path, "pkg", 5)  # 5 leaves + 1 package = 6 modules
    with pytest.raises(ScanTooLargeError) as exc:
        build_graph(tmp_path, max_modules=3)
    assert exc.value.limit == 3
    assert exc.value.count > 3
    assert "max_modules" in str(exc.value)


def test_build_graph_under_limit_ok(tmp_path: Path):
    _write_flat_package(tmp_path, "pkg", 2)
    g = build_graph(tmp_path, max_modules=1000)
    assert g.number_of_nodes() >= 2


def test_build_graph_zero_disables_guard(tmp_path: Path):
    _write_flat_package(tmp_path, "pkg", 5)
    g = build_graph(tmp_path, max_modules=0)  # 0 = disabled
    assert g.number_of_nodes() >= 5


def test_a_repo_root_package_sorting_before_src_does_not_rename_the_library(
    tmp_path: Path,
):
    """#516: the decoy used to capture every file, renaming the library to
    `src.mypkg.*`, turning its self-imports external and hiding the cycle."""
    root = _src_layout_with_cycle(tmp_path, decoy=True)
    roots = _find_package_roots(root.resolve(), DEFAULT_IGNORED_DIRS, ())
    # The fixture reaches the branch: the decoy is a root and sorts first.
    assert [r.name for r in roots] == ["benchmarks", "mypkg"]

    g = build_graph(root)

    assert "mypkg.core.db" in g and "src.mypkg.core.db" not in g
    assert g.has_edge("mypkg.core.db", "mypkg.api.views")
    assert g.has_edge("mypkg.api.views", "mypkg.core.db")
    assert not g.nodes["mypkg.core.db"].get("external")


def test_the_decoy_does_not_change_the_graph_of_the_library(tmp_path: Path):
    """Adding an empty sibling package adds one node and nothing else."""
    without = build_graph(_src_layout_with_cycle(tmp_path / "a", decoy=False))
    with_decoy = build_graph(_src_layout_with_cycle(tmp_path / "b", decoy=True))

    assert set(with_decoy.nodes) - set(without.nodes) == {"benchmarks"}
    assert set(with_decoy.edges) == set(without.edges)


def test_a_file_under_no_package_root_still_falls_back_to_a_root_parent(
    tmp_path: Path,
):
    """Flat layout with an init-less tests/: tests are named via the fallback,
    which the #516 fix must keep (dropping it zeroes `affected` here)."""
    (tmp_path / "mypkg").mkdir()
    (tmp_path / "mypkg" / "__init__.py").write_text("")
    (tmp_path / "mypkg" / "core.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text("from mypkg import core\n")

    g = build_graph(tmp_path)

    assert g.has_edge("tests.test_core", "mypkg.core")


def test_when_two_roots_contain_a_file_the_outermost_names_it(tmp_path: Path):
    """A namespace gap (`app/plugins/` has no `__init__.py`) makes `foo` a root
    nested inside `app`, so both contain `x.py`. The outer root must win, or
    `x` becomes `foo.x` and its `from app import core` edge stops linking up."""
    app = tmp_path / "app"
    foo = app / "plugins" / "foo"
    foo.mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "core.py").write_text("from app.plugins.foo import x\n")
    (foo / "__init__.py").write_text("")
    (foo / "x.py").write_text("from app import core\n")
    roots = _find_package_roots(tmp_path.resolve(), DEFAULT_IGNORED_DIRS, ())
    # The fixture reaches the branch: both roots contain foo/x.py.
    assert [r.name for r in roots] == ["app", "foo"]

    g = build_graph(tmp_path)

    assert g.has_edge("app.plugins.foo.x", "app.core")
    assert g.has_edge("app.core", "app.plugins.foo.x")


def test_a_declared_root_that_is_not_a_directory_is_not_a_package_root(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")

    roots = _find_package_roots(tmp_path, DEFAULT_IGNORED_DIRS, ("no_such_dir",))

    assert roots == [pkg.resolve()]
