"""Tests for graph: discovery."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from archy.graph import (
    DEFAULT_IGNORED_DIRS,
    DEFAULT_MAX_MODULES,
    build_graph,
    effective_max_modules,
)
from archy.reach import package_init_edges, with_package_init_edges
from tests.graph_helpers import _internal_nodes, _tiny_pkg_project


def test_package_init_edges_are_not_in_the_built_graph(project: Path):
    """The built graph records written imports only. Pinned because the reach
    feature depends on this being false, and a later change that started adding
    these edges to `build_graph` would move every metric silently."""
    g = build_graph(project)

    assert not g.has_edge("myapp.sub.leaf", "myapp.sub")
    assert not g.has_edge("myapp.core", "myapp")


def test_package_init_edges_point_submodules_at_their_packages(project: Path):
    g = build_graph(project)

    edges = set(package_init_edges(g))

    assert ("myapp.sub.leaf", "myapp.sub") in edges
    assert ("myapp.sub.leaf", "myapp") in edges  # every ancestor, not just the parent
    assert ("myapp.core", "myapp") in edges


def test_package_init_edges_skip_external_nodes():
    """An external node gets no implicit `__init__.py` edge in either
    direction: archy never parsed that package, so it cannot claim what its
    `__init__.py` imports, nor that anything reaches through it.

    Hand-built rather than taken from a project because `_external_target`
    collapses every external import to a SINGLE segment, and a single-segment
    node has no ancestors to iterate over: the previous fixture asserted about
    `requests`, for which the loop body never runs with or without the guards
    (#440). A dotted external node is the only shape that reaches them.
    """
    external_child: nx.DiGraph = nx.DiGraph()
    external_child.add_node("myapp", external=False)
    external_child.add_node("myapp.core", external=False)
    external_child.add_node("myapp.vendored", external=True)
    assert package_init_edges(external_child) == [("myapp.core", "myapp")]

    external_parent: nx.DiGraph = nx.DiGraph()
    external_parent.add_node("vendor", external=True)
    external_parent.add_node("vendor.sub", external=False)
    assert package_init_edges(external_parent) == []


def test_with_package_init_edges_leaves_the_original_untouched(project: Path):
    g = build_graph(project)
    before = (g.number_of_nodes(), g.number_of_edges())

    augmented = with_package_init_edges(g)

    assert (g.number_of_nodes(), g.number_of_edges()) == before
    assert augmented.number_of_edges() > before[1]
    assert augmented["myapp.sub.leaf"]["myapp.sub"]["implicit"] == "package_init"


def test_with_package_init_edges_preserves_a_real_edge(tmp_path: Path, pkg: Path):
    """A written `from . import x` edge must keep its line numbers, not be
    overwritten by an implicit one that happens to connect the same pair."""
    (pkg / "child.py").write_text("import pkg\n")

    augmented = with_package_init_edges(build_graph(tmp_path))

    assert augmented["pkg.child"]["pkg"]["lines"] == (1,)
    assert "implicit" not in augmented["pkg.child"]["pkg"]


def test_discovers_internal_modules(project: Path):
    g = build_graph(project)
    internal = _internal_nodes(g)
    assert internal == {
        "myapp",
        "myapp.core",
        "myapp.utils",
        "myapp.cli",
        "myapp.sub",
        "myapp.sub.leaf",
    }


def test_syntax_errors_dont_kill_the_run(tmp_path: Path, pkg: Path):
    (pkg / "good.py").write_text("import os\n")
    (pkg / "bad.py").write_text("import os\ndef broken(:\n")
    g = build_graph(tmp_path)
    assert "pkg.good" in g.nodes
    assert "pkg.bad" in g.nodes
    assert "pkg.bad" in g.graph["parse_errors"]
    assert g.has_edge("pkg.good", "os")
    assert g.has_edge("pkg.bad", "os")


def test_ignored_dirs_are_skipped(tmp_path: Path, pkg: Path):
    (pkg / "real.py").write_text("import os\n")
    venv_pkg = tmp_path / ".venv" / "lib" / "fake"
    venv_pkg.mkdir(parents=True)
    (venv_pkg / "__init__.py").write_text("")
    (venv_pkg / "noise.py").write_text("import sys\n")
    g = build_graph(tmp_path)
    internal = _internal_nodes(g)
    assert "pkg" in internal and "pkg.real" in internal
    assert not any(n.startswith("fake") for n in internal)


@pytest.mark.parametrize("ancestor", ["build", "dist", "venv"])
def test_ignored_ancestor_dir_does_not_empty_an_absolute_scan(tmp_path: Path, ancestor: str):
    """#517: living under build/dist/venv must not zero an abs-path scan."""
    under_ancestor = _tiny_pkg_project(tmp_path / ancestor / "proj")
    baseline = _tiny_pkg_project(tmp_path / "plain" / "proj")

    g_abs = build_graph(under_ancestor.resolve())
    g_base = build_graph(baseline.resolve())

    assert set(g_abs.nodes) == set(g_base.nodes) == {"pkg", "pkg.a", "pkg.b"}
    assert set(g_abs.edges) == set(g_base.edges)
    assert g_abs.has_edge("pkg.a", "pkg.b")


def test_build_dir_inside_the_scan_root_is_still_skipped(tmp_path: Path):
    """#517: relative-to-root ignore must keep in-root build/ semantics."""
    _tiny_pkg_project(tmp_path)
    noise = tmp_path / "build" / "noise"
    noise.mkdir(parents=True)
    (noise / "__init__.py").write_text("")
    (noise / "x.py").write_text("import os\n")

    g = build_graph(tmp_path.resolve())
    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}
    assert {"pkg", "pkg.a", "pkg.b"} <= internal
    assert not any(n.startswith("noise") or n.startswith("build") for n in internal)


def test_build_output_without_a_top_level_init_is_still_skipped(tmp_path: Path):
    """#518: the soft skip must keep real build output out of the graph.

    setuptools writes `build/lib/pkg/`, so the skipped directory holds an
    `__init__.py` further down while its own top level does not.
    """
    _tiny_pkg_project(tmp_path)
    artifact = tmp_path / "build" / "lib" / "pkg"
    artifact.mkdir(parents=True)
    (artifact / "__init__.py").write_text("")
    (artifact / "generated.py").write_text("import os\n")

    assert not (tmp_path / "build" / "__init__.py").exists(), (
        "fixture's build/ must not be a package"
    )

    g = build_graph(tmp_path)
    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}

    assert {"pkg", "pkg.a", "pkg.b"} <= internal
    assert "pkg.generated" not in internal


def test_monorepo_with_two_top_level_packages(tmp_path: Path):
    """Two unrelated packages under one root, each with internal imports."""
    a = tmp_path / "alpha"
    b = tmp_path / "beta"
    a.mkdir()
    b.mkdir()
    (a / "__init__.py").write_text("")
    (a / "core.py").write_text("from alpha import util\n")
    (a / "util.py").write_text("")
    (b / "__init__.py").write_text("")
    (b / "main.py").write_text("from beta import core\nfrom alpha import core as a_core\n")
    (b / "core.py").write_text("")
    g = build_graph(tmp_path)
    assert g.has_edge("alpha.core", "alpha.util")
    assert g.has_edge("beta.main", "beta.core")
    assert g.has_edge("beta.main", "alpha.core")


def test_extra_roots_treats_directory_without_init_as_package(tmp_path: Path):
    # No __init__.py anywhere. Without extra_roots, archy sees nothing.
    app = tmp_path / "app"
    libs = app / "libs"
    libs.mkdir(parents=True)
    (libs / "db.py").write_text("import os\n")
    (app / "main.py").write_text("from app.libs.db import x\n")
    assert build_graph(tmp_path).number_of_nodes() == 0
    g = build_graph(tmp_path, extra_roots=("app",))
    internal = _internal_nodes(g)
    assert "app.main" in internal
    assert "app.libs.db" in internal
    assert g.has_edge("app.main", "app.libs.db")


def test_extra_roots_demotes_inner_init_packages(tmp_path: Path):
    # `app/` is namespace; `app/routers/` has __init__.py. Without extra_roots
    # archy sees a top-level `routers` package. With extra_roots=("app",) the
    # routers package nests under app, matching how Python actually imports it.
    app = tmp_path / "app"
    routers = app / "routers"
    routers.mkdir(parents=True)
    (routers / "__init__.py").write_text("")
    (routers / "user.py").write_text("")

    g_default = build_graph(tmp_path)
    assert "routers.user" in g_default.nodes
    assert "app.routers.user" not in g_default.nodes

    g_rooted = build_graph(tmp_path, extra_roots=("app",))
    assert "app.routers.user" in g_rooted.nodes
    assert "routers.user" not in g_rooted.nodes


def test_extra_roots_missing_directory_is_skipped(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("")
    g = build_graph(tmp_path, extra_roots=("nonexistent",))
    assert "pkg.a" in g.nodes


def test_effective_max_modules_resolution():
    assert effective_max_modules(None) == DEFAULT_MAX_MODULES
    assert effective_max_modules(0) is None
    assert effective_max_modules(2500) == 2500


def test_a_compiled_extension_wins_over_a_stub_for_the_same_module(tmp_path: Path):
    """`fast.so` beside `fast.pyi` is one module, and the binary is the file recorded."""
    from archy.graph import discover_modules

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "fast.pyi").write_text("")
    (pkg / "fast.so").write_text("")

    modules = {m.qualname: m for m in discover_modules(tmp_path)}

    assert modules["pkg.fast"].path.name == "fast.so"
    assert [m.qualname for m in modules.values()].count("pkg.fast") == 1


def test_a_soft_ignored_name_is_skipped_when_the_path_is_outside_the_scan_root(tmp_path: Path):
    """No directory can be probed for an `__init__.py` there, so the name decides (`venv`)."""
    from archy.graph import is_ignored_path

    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "venv" / "mod.py"

    assert is_ignored_path(outside, DEFAULT_IGNORED_DIRS, root) is True
