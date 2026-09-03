from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from archy.graph import (
    DEFAULT_MAX_MODULES,
    ScanTooLargeError,
    build_graph,
    effective_max_modules,
    graph_to_dict,
    resolve_modules,
)
from archy.reach import package_init_edges, with_package_init_edges


@pytest.fixture
def pkg(tmp_path: Path) -> Path:
    """An empty `pkg/` package rooted at tmp_path. Tests fill in the .py files.

    Most test bodies need only the `pkg` Path; tests that build the graph
    pass `tmp_path` to `build_graph`. A few tests that exercise non-empty
    `__init__.py` re-exports overwrite it with `(pkg / "__init__.py").write_text(...)`.
    """
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    return pkg_dir


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


def test_package_init_edges_skip_external_nodes(project: Path):
    """`requests` is external and `_external_target` already collapsed it, so
    there is no parent to point at and no claim to make about its contents."""
    g = build_graph(project)

    assert not any(src == "requests" or dst == "requests" for src, dst in package_init_edges(g))


def test_with_package_init_edges_leaves_the_original_untouched(project: Path):
    g = build_graph(project)
    before = (g.number_of_nodes(), g.number_of_edges())

    augmented = with_package_init_edges(g)

    assert (g.number_of_nodes(), g.number_of_edges()) == before
    assert augmented.number_of_edges() > before[1]
    assert augmented["myapp.sub.leaf"]["myapp.sub"]["implicit"] == "package_init"


def test_with_package_init_edges_preserves_a_real_edge(tmp_path: Path):
    """A written `from . import x` edge must keep its line numbers, not be
    overwritten by an implicit one that happens to connect the same pair."""
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "child.py").write_text("import pkg\n")

    augmented = with_package_init_edges(build_graph(tmp_path))

    assert augmented["pkg.child"]["pkg"]["lines"] == (1,)
    assert "implicit" not in augmented["pkg.child"]["pkg"]


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


def test_over_dotted_relative_import_from_package_injects_no_node(tmp_path: Path):
    # Regression for #161: an over-dotted relative import that escapes the
    # project root (`from ...other import X` in a 2-deep package __init__.py,
    # which Python itself rejects at runtime) must resolve to None. Before the
    # graph.py:416 `>` -> `>=` fix it leaked a phantom external node `other`
    # and rerouted edges, silently corrupting instability/propagation_cost/
    # edit_risk on unrelated modules and the project score.
    pkg = tmp_path / "proj"
    sub = pkg / "sub"
    sub.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "regular.py").write_text("import os\n")
    # walk_up == len(src_parts) == 2, suffix='other' -> escapes root.
    (sub / "__init__.py").write_text("from ...other import X\n")
    g = build_graph(tmp_path)
    assert "other" not in g.nodes
    assert not any(t == "other" for _, t in g.edges)
    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}
    assert internal == {"proj", "proj.regular", "proj.sub"}


def test_internal_only_filtering(project: Path):
    g = build_graph(project)
    external = [n for n, d in g.nodes(data=True) if d.get("external")]
    assert set(external) >= {"os", "requests", "json"}


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


def test_reexport_relative_resolves_to_source_module(tmp_path: Path, pkg: Path):
    (pkg / "__init__.py").write_text("from .impl import Foo\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.impl")
    assert not g.has_edge("pkg.consumer", "pkg")


def test_reexport_aliased_resolves_to_source_module(tmp_path: Path, pkg: Path):
    (pkg / "__init__.py").write_text("from .impl import Foo as Bar\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Bar\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.impl")
    assert not g.has_edge("pkg.consumer", "pkg")


def test_reexport_mixed_with_submodule(tmp_path: Path, pkg: Path):
    (pkg / "__init__.py").write_text("from .impl import Foo\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "Sub.py").write_text("")
    (pkg / "consumer.py").write_text("from pkg import Foo, Sub\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.impl")
    assert g.has_edge("pkg.consumer", "pkg.Sub")


def test_reexport_absolute_self_reference(tmp_path: Path, pkg: Path):
    (pkg / "__init__.py").write_text("from pkg.impl import Foo\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.impl")


def test_unknown_name_falls_back_to_package_edge(tmp_path: Path, pkg: Path):
    (pkg / "consumer.py").write_text("from pkg import SomethingDefinedInline\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg")


def test_wildcard_reexport_does_not_crash(tmp_path: Path, pkg: Path):
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


# --- extra_roots (PEP 420 namespace packages) ---------------------------------


def test_extra_roots_treats_directory_without_init_as_package(tmp_path: Path):
    # No __init__.py anywhere. Without extra_roots, archy sees nothing.
    app = tmp_path / "app"
    libs = app / "libs"
    libs.mkdir(parents=True)
    (libs / "db.py").write_text("import os\n")
    (app / "main.py").write_text("from app.libs.db import x\n")
    assert build_graph(tmp_path).number_of_nodes() == 0
    g = build_graph(tmp_path, extra_roots=("app",))
    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}
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


def test_extra_roots_missing_directory_is_skipped(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("")
    g = build_graph(tmp_path, extra_roots=("nonexistent",))
    assert "pkg.a" in g.nodes


def test_graph_to_dict_preserves_edge_line_numbers(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # Three import statements at known lines targeting pkg.b. Edge attribute
    # `lines` must collect all three so agents can pinpoint import sites.
    (pkg / "a.py").write_text(
        "from pkg.b import x\n# noise\nfrom pkg.b import y\nfrom pkg.b import z\n"
    )
    (pkg / "b.py").write_text("")

    data = graph_to_dict(build_graph(tmp_path))
    edge = next(e for e in data["edges"] if e["source"] == "pkg.a" and e["target"] == "pkg.b")
    assert edge["lines"] == (1, 3, 4)


def test_graph_to_dict_emits_instability_only_for_internal(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import requests\n")

    data = graph_to_dict(build_graph(tmp_path))
    by_id = {n["id"]: n for n in data["nodes"]}
    # Internal nodes carry instability; the external `requests` node must not,
    # since Ce/Ca aren't meaningful for nodes outside the project.
    assert "instability" in by_id["pkg.a"]
    assert "instability" not in by_id["requests"]
    assert by_id["requests"]["external"] is True


def test_graph_to_dict_is_deterministic(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.c import x\nfrom pkg.b import y\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("")

    data = graph_to_dict(build_graph(tmp_path))
    node_ids = [n["id"] for n in data["nodes"]]
    assert node_ids == sorted(node_ids)
    edges = [(e["source"], e["target"]) for e in data["edges"]]
    assert edges == sorted(edges)


def test_resolve_modules_routes_qualnames_and_paths(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("")
    (pkg / "b.py").write_text("")
    graph = build_graph(tmp_path)

    resolved, unresolved = resolve_modules(
        graph,
        ["pkg.a", str(pkg / "b.py")],
        project_root=tmp_path,
    )
    assert resolved == ["pkg.a", "pkg.b"]
    assert unresolved == []


def test_resolve_modules_deduplicates_preserving_order(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("")
    (pkg / "b.py").write_text("")
    graph = build_graph(tmp_path)

    # pkg.a appears three times via two surface forms (qualname + path).
    # Dedup must keep first-seen order, so the path repetition does not push
    # pkg.b in front of the duplicate-but-already-seen pkg.a.
    resolved, _ = resolve_modules(
        graph,
        ["pkg.a", str(pkg / "a.py"), "pkg.a", "pkg.b"],
        project_root=tmp_path,
    )
    assert resolved == ["pkg.a", "pkg.b"]


def test_resolve_modules_accepts_absolute_path(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("")
    graph = build_graph(tmp_path)

    # Absolute paths bypass project_root resolution entirely.
    resolved, unresolved = resolve_modules(
        graph,
        [str((pkg / "a.py").resolve())],
        project_root=tmp_path / "wrong",  # deliberately wrong; abs path wins
    )
    assert resolved == ["pkg.a"]
    assert unresolved == []


def test_call_edge_attached_to_existing_import_edge(tmp_path: Path, pkg: Path):
    # Calls that flow along an existing import edge enrich it in place
    # rather than creating a parallel edge.
    (pkg / "a.py").write_text("from pkg import b\nb.do()\nb.do()\n")
    (pkg / "b.py").write_text("def do():\n    pass\n")
    g = build_graph(tmp_path)
    edge = g["pkg.a"]["pkg.b"]
    assert edge["kinds"] == ("import", "call")
    assert edge["call_count"] == 2
    assert edge["call_lines"] == (2, 3)


def test_call_edge_resolves_deeper_than_import_edge(tmp_path: Path, pkg: Path):
    # `import pkg; pkg.sub.foo()` imports the top-level package only, but
    # the call resolves to pkg.sub when that submodule is internal - the
    # depth differential LocAgent (ACL 2025) cites as the invoke-edge signal.
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (pkg / "consumer.py").write_text("import pkg\npkg.sub.do()\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg")
    assert g.has_edge("pkg.consumer", "pkg.sub")
    call_only = g["pkg.consumer"]["pkg.sub"]
    assert call_only["kinds"] == ("call",)
    assert call_only["call_count"] == 1


def test_call_to_unimported_name_is_dropped(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("rogue.do()\n")
    g = build_graph(tmp_path)
    # Unresolved heads must not invent edges; doing so would let
    # locals/runtime-injected names corrupt the coupling signal.
    assert not any(d.get("call_count") for _, _, d in g.edges(data=True))


def test_call_through_aliased_import(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("import pkg.b as bb\nbb.do()\n")
    (pkg / "b.py").write_text("def do():\n    pass\n")
    g = build_graph(tmp_path)
    edge = g["pkg.a"]["pkg.b"]
    assert "call" in edge["kinds"]
    assert edge["call_count"] == 1


def test_call_through_from_import(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("from pkg.b import do\ndo()\ndo()\n")
    (pkg / "b.py").write_text("def do():\n    pass\n")
    g = build_graph(tmp_path)
    edge = g["pkg.a"]["pkg.b"]
    assert edge["call_count"] == 2


def test_call_through_reexport_chain(tmp_path: Path):
    # Re-exports must route calls to the canonical source module the same
    # way imports do; landing them on the re-exporting package instead
    # would inflate that package's coupling metrics with traffic that
    # logically belongs elsewhere.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .impl import Foo\n")
    (pkg / "impl.py").write_text("class Foo:\n    pass\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\nFoo()\n")
    g = build_graph(tmp_path)
    edge = g["pkg.consumer"]["pkg.impl"]
    assert edge["call_count"] == 1


def test_graph_to_dict_includes_call_attrs(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("from pkg import b\nb.do()\n")
    (pkg / "b.py").write_text("def do():\n    pass\n")
    data = graph_to_dict(build_graph(tmp_path))
    edge = next(e for e in data["edges"] if e["source"] == "pkg.a" and e["target"] == "pkg.b")
    assert edge["call_count"] == 1
    assert edge["call_lines"] == (2,)
    assert "call" in edge["kinds"]


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


def test_cc_attrs_appear_in_graph_to_dict(tmp_path: Path, pkg: Path):
    (pkg / "a.py").write_text("def f(x):\n    return x and x\n")
    data = graph_to_dict(build_graph(tmp_path))
    by_id = {n["id"]: n for n in data["nodes"]}
    assert by_id["pkg.a"]["function_count"] == 1
    assert by_id["pkg.a"]["cc_max"] == 2


def test_resolve_modules_does_not_match_external_qualnames(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import requests\n")
    graph = build_graph(tmp_path)
    assert "requests" in graph.nodes  # sanity: the external node exists

    # External qualnames aren't legal focus targets; they should fall through
    # to the path-resolution branch and end up in `unresolved` (since
    # 'requests' isn't a real file path under the project either).
    resolved, unresolved = resolve_modules(graph, ["requests"], project_root=tmp_path)
    assert resolved == []
    assert unresolved == ["requests"]


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

    g = build_graph(project)  # must not raise

    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}
    assert "myapp.utils" not in internal  # the vanished module is dropped
    assert "myapp.core" in internal  # the rest of the build survives


# --- scan-size guard (#216) ---------------------------------------------------


def _write_flat_package(root: Path, name: str, n_modules: int) -> None:
    """Create `root/<name>/` with `n_modules` import-free leaf modules."""
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    for i in range(n_modules):
        (pkg / f"mod{i}.py").write_text("x = 1\n")


def test_build_graph_unguarded_by_default(tmp_path: Path):
    # Direct/library callers (max_modules unset) are never guarded.
    _write_flat_package(tmp_path, "pkg", 5)
    g = build_graph(tmp_path)  # must not raise
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
    g = build_graph(tmp_path, max_modules=0)  # 0 = disabled, must not raise
    assert g.number_of_nodes() >= 5


def test_effective_max_modules_resolution():
    assert effective_max_modules(None) == DEFAULT_MAX_MODULES  # unset -> default
    assert effective_max_modules(0) is None  # explicitly disabled
    assert effective_max_modules(2500) == 2500  # custom ceiling


def test_graph_to_dict_shape_is_pinned_by_hand():
    """The wire shape as a literal, because every other test of it is relative.

    `archy graph --format json` and `mcp._graph_payload_from` BOTH call this
    function, so the cross-surface parity test in `tests/test_mcp.py` cannot
    catch a consistent regression here: dropping a field or renaming a key moves
    both sides together and that test still passes (#439). Nothing else pins the
    JSON contract that external consumers actually read.

    The dict below is written out by hand, not captured from a run, which is the
    only version of this test worth having.
    """
    g = nx.DiGraph(root="/p")
    g.add_node("pkg.a", external=False, path="/p/pkg/a.py", is_package=False)
    g.add_node("pkg.b", external=False, path="/p/pkg/b.py", is_package=False)
    g.add_edge("pkg.a", "pkg.b", is_relative=True, lines=(3,), kinds=("import",))

    assert graph_to_dict(g) == {
        "root": "/p",
        "parse_errors": [],
        "nodes": [
            {
                "id": "pkg.a",
                "external": False,
                "path": "/p/pkg/a.py",
                "is_package": False,
                # a imports b and nothing imports a: fully unstable, reaches half
                # the project (itself), and carries no edit risk at zero fan-in.
                "instability": 1.0,
                "propagation_cost": 0.5,
                "edit_risk": 0.0,
            },
            {
                "id": "pkg.b",
                "external": False,
                "path": "/p/pkg/b.py",
                "is_package": False,
                "instability": 0.0,
                "propagation_cost": 1.0,
                "edit_risk": 0.0,
            },
        ],
        "edges": [
            {
                "source": "pkg.a",
                "target": "pkg.b",
                "is_relative": True,
                "lines": (3,),
                "kinds": ("import",),
            }
        ],
    }
