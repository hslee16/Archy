from __future__ import annotations

from pathlib import Path

import pytest

from archy.graph import build_graph


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A small fake project with a src layout and a few modules."""
    pkg = tmp_path / "src" / "myapp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("import os\nfrom myapp import utils\n")
    (pkg / "utils.py").write_text("from . import core\nimport requests\n")
    (pkg / "cli.py").write_text("from myapp.core import thing\nfrom .. import escapes\n")
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "leaf.py").write_text("from ..utils import helper\nimport json\n")
    return tmp_path


def test_discovers_internal_modules(project: Path):
    g = build_graph(project)
    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}
    assert internal == {
        "myapp",
        "myapp.core",
        "myapp.utils",
        "myapp.cli",
        "myapp.sub",
        "myapp.sub.leaf",
    }


def test_internal_edges_resolve_correctly(project: Path):
    g = build_graph(project)
    assert g.has_edge("myapp.core", "myapp.utils")
    assert g.has_edge("myapp.utils", "myapp.core")
    assert g.has_edge("myapp.cli", "myapp.core")
    assert g.has_edge("myapp.sub.leaf", "myapp.utils")


def test_external_imports_recorded_as_external_nodes(project: Path):
    g = build_graph(project)
    assert g.nodes["os"]["external"] is True
    assert g.nodes["requests"]["external"] is True
    assert g.nodes["json"]["external"] is True


def test_relative_import_escaping_root_is_dropped(project: Path):
    # `from .. import escapes` from myapp.cli walks above the package.
    g = build_graph(project)
    assert "escapes" not in g.nodes


def test_internal_only_filtering(project: Path):
    g = build_graph(project)
    external = [n for n, d in g.nodes(data=True) if d.get("external")]
    assert set(external) >= {"os", "requests", "json"}


def test_syntax_errors_dont_kill_the_run(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good.py").write_text("import os\n")
    (pkg / "bad.py").write_text("import os\ndef broken(:\n")
    g = build_graph(tmp_path)
    assert "pkg.good" in g.nodes
    assert "pkg.bad" in g.nodes
    assert "pkg.bad" in g.graph["parse_errors"]
    assert g.has_edge("pkg.good", "os")
    assert g.has_edge("pkg.bad", "os")


def test_ignored_dirs_are_skipped(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "real.py").write_text("import os\n")
    venv_pkg = tmp_path / ".venv" / "lib" / "fake"
    venv_pkg.mkdir(parents=True)
    (venv_pkg / "__init__.py").write_text("")
    (venv_pkg / "noise.py").write_text("import sys\n")
    g = build_graph(tmp_path)
    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}
    assert "pkg" in internal and "pkg.real" in internal
    assert not any(n.startswith("fake") for n in internal)


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
    assert g.has_edge("beta.main", "alpha.core")  # cross-package internal edge


def test_wildcard_import_creates_single_edge_to_module(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "consumer.py").write_text("from pkg.utils import *\n")
    (pkg / "utils.py").write_text("")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.utils")


def test_function_local_imports_appear_in_graph(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "lazy.py").write_text("def go():\n    from pkg import other\n    return other\n")
    (pkg / "other.py").write_text("")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.lazy", "pkg.other")


def test_multiple_imports_same_target_aggregate_lines(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import os\nimport os.path\nfrom os import sep\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.a", "os")
    assert len(g["pkg.a"]["os"]["lines"]) == 3


def test_reexport_relative_resolves_to_source_module(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .impl import Foo\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.impl")
    assert not g.has_edge("pkg.consumer", "pkg")


def test_reexport_aliased_resolves_to_source_module(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .impl import Foo as Bar\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Bar\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.impl")
    assert not g.has_edge("pkg.consumer", "pkg")


def test_reexport_mixed_with_submodule(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .impl import Foo\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "Sub.py").write_text("")
    (pkg / "consumer.py").write_text("from pkg import Foo, Sub\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.impl")
    assert g.has_edge("pkg.consumer", "pkg.Sub")


def test_reexport_absolute_self_reference(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from pkg.impl import Foo\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.impl")


def test_unknown_name_falls_back_to_package_edge(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "consumer.py").write_text("from pkg import SomethingDefinedInline\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg")


def test_wildcard_reexport_does_not_crash(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .impl import *\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    # Wildcards are unresolvable statically; the consumer falls back to `pkg`,
    # the package's own re-export from .impl still creates an edge to pkg.impl.
    assert g.has_edge("pkg", "pkg.impl")
    assert g.has_edge("pkg.consumer", "pkg")


def test_reexport_chain_two_hops(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    sub = pkg / "sub"
    sub.mkdir()
    (pkg / "__init__.py").write_text("from .sub import Foo\n")
    (sub / "__init__.py").write_text("from .impl import Foo\n")
    (sub / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.sub.impl")
    assert not g.has_edge("pkg.consumer", "pkg.sub")
    assert not g.has_edge("pkg.consumer", "pkg")


def test_reexport_chain_three_hops(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    sub = pkg / "sub"
    sub.mkdir()
    deep = sub / "deep"
    deep.mkdir()
    (pkg / "__init__.py").write_text("from .sub import Foo\n")
    (sub / "__init__.py").write_text("from .deep import Foo\n")
    (deep / "__init__.py").write_text("from .impl import Foo\n")
    (deep / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.sub.deep.impl")


def test_reexport_chain_circular_does_not_loop(tmp_path: Path):
    # pkg/__init__.py re-exports Foo from pkg.a, and pkg.a/__init__.py
    # re-exports Foo from pkg (an evil-twin loop). The chain follower
    # must not infinite-loop; it should bail and leave the entry pointing
    # at the first hop.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    a = pkg / "a"
    a.mkdir()
    (pkg / "__init__.py").write_text("from .a import Foo\n")
    (a / "__init__.py").write_text("from pkg import Foo\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    # Just assert build_graph terminates; the resulting edge can land at
    # either pkg or pkg.a depending on how the cycle is broken, but the
    # important property is that the resolver returns at all.
    g = build_graph(tmp_path)
    assert g.number_of_nodes() > 0


def test_reexport_chain_does_not_resolve_different_names(tmp_path: Path):
    # pkg/__init__.py re-exports Foo from pkg.sub, but pkg.sub/__init__.py
    # only has a re-export for Bar, not Foo. The chain follower must not
    # mis-attribute Foo to wherever Bar lives.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    sub = pkg / "sub"
    sub.mkdir()
    (pkg / "__init__.py").write_text("from .sub import Foo\n")
    (sub / "__init__.py").write_text("from .impl import Bar\n")
    (sub / "impl.py").write_text("class Bar: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    # Foo doesn't chain through pkg.sub because pkg.sub doesn't re-export Foo.
    assert g.has_edge("pkg.consumer", "pkg.sub")
    assert not g.has_edge("pkg.consumer", "pkg.sub.impl")
