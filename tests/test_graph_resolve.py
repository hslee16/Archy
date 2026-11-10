"""Tests for graph: resolve."""

from __future__ import annotations

from pathlib import Path

from archy.graph import (
    _add_kind,
    build_graph,
    graph_to_dict,
)
from tests.graph_helpers import _edge, _internal_nodes, _mkpkg, _src_layout


def test_mixed_relative_and_absolute_pair_reports_relative(tmp_path: Path, pkg: Path):
    """`is_relative` is `any`, not first-seen (#450).

    A module can reach the same target twice, once absolute and once relative.
    The flag documents whether the edge involves leading-dot syntax, and one of
    these two imports does, so the answer is True whichever was written first.
    Before the fix this was decided by whichever import created the edge, so
    the same pair answered False here and True in the next test.
    """
    (pkg / "b.py").write_text("")
    (pkg / "a.py").write_text("\nfrom pkg.b import x\nfrom .b import y\n")

    g = build_graph(tmp_path)

    # The fixture has to reach the extend branch, or the `any` has nothing to
    # aggregate and this passes on the create branch alone.
    assert g["pkg.a"]["pkg.b"]["lines"] == (2, 3)
    assert g["pkg.a"]["pkg.b"]["is_relative"] is True


def test_the_mixed_pair_answers_the_same_written_the_other_way_round(tmp_path: Path, pkg: Path):
    """The other source order, which is the whole point: an agent cannot see
    which import came first, so the flag must not depend on it."""
    (pkg / "b.py").write_text("")
    (pkg / "a.py").write_text("\nfrom .b import y\nfrom pkg.b import x\n")

    g = build_graph(tmp_path)

    assert g["pkg.a"]["pkg.b"]["lines"] == (2, 3)
    assert g["pkg.a"]["pkg.b"]["is_relative"] is True


def test_the_plain_import_and_from_dot_import_forms_aggregate_too(tmp_path: Path, pkg: Path):
    """The other way a real module writes this pair. `import pkg.b` and
    `from . import b` take a different route through the parser than the two
    `from X import name` forms above, and both have to reach the same edge for
    the aggregation to mean anything."""
    (pkg / "b.py").write_text("")
    (pkg / "a.py").write_text("\nimport pkg.b\nfrom . import b\n")

    g = build_graph(tmp_path)

    assert g["pkg.a"]["pkg.b"]["lines"] == (2, 3)
    assert g["pkg.a"]["pkg.b"]["is_relative"] is True


def test_a_later_absolute_import_does_not_clear_the_flag(tmp_path: Path, pkg: Path):
    """Three sites, relative in the middle. `any` has to hold across every
    later extend, not just the one that follows the relative import."""
    (pkg / "b.py").write_text("")
    (pkg / "a.py").write_text("\nfrom pkg.b import x\nfrom .b import y\nfrom pkg.b import z\n")

    g = build_graph(tmp_path)

    assert g["pkg.a"]["pkg.b"]["lines"] == (2, 3, 4)
    assert g["pkg.a"]["pkg.b"]["is_relative"] is True


def test_an_all_absolute_pair_still_reports_not_relative(tmp_path: Path, pkg: Path):
    """The counterpart that keeps `any` from collapsing into `True`. An OR that
    is never False would satisfy both tests above."""
    (pkg / "b.py").write_text("")
    (pkg / "a.py").write_text("\nfrom pkg.b import x\nfrom pkg.b import y\n")

    g = build_graph(tmp_path)

    assert g["pkg.a"]["pkg.b"]["lines"] == (2, 3)
    assert g["pkg.a"]["pkg.b"]["is_relative"] is False


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
    internal = _internal_nodes(g)
    assert internal == {"proj", "proj.regular", "proj.sub"}


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


def test_wildcard_reexport_does_not_crash(tmp_path: Path, pkg: Path):
    (pkg / "__init__.py").write_text("from .impl import *\n")
    (pkg / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    # Wildcards are unresolvable statically; the consumer falls back to `pkg`,
    # the package's own re-export from .impl still creates an edge to pkg.impl.
    assert g.has_edge("pkg", "pkg.impl")
    assert g.has_edge("pkg.consumer", "pkg")


def test_reexport_chain_two_hops(tmp_path: Path, pkg: Path):
    sub = _mkpkg(pkg, "sub", "from .impl import Foo\n")
    (pkg / "__init__.py").write_text("from .sub import Foo\n")
    (sub / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.sub.impl")
    assert not g.has_edge("pkg.consumer", "pkg.sub")
    assert not g.has_edge("pkg.consumer", "pkg")


def test_reexport_chain_three_hops(tmp_path: Path, pkg: Path):
    sub = _mkpkg(pkg, "sub", "from .deep import Foo\n")
    deep = _mkpkg(sub, "deep", "from .impl import Foo\n")
    (pkg / "__init__.py").write_text("from .sub import Foo\n")
    (deep / "impl.py").write_text("class Foo: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.sub.deep.impl")


def test_reexport_chain_circular_does_not_loop(tmp_path: Path, pkg: Path):
    # pkg/__init__.py re-exports Foo from pkg.a, and pkg.a/__init__.py
    # re-exports Foo from pkg (an evil-twin loop). The chain follower
    # must not infinite-loop; it should bail and leave the entry pointing
    # at the first hop.
    _mkpkg(pkg, "a", "from pkg import Foo\n")
    (pkg / "__init__.py").write_text("from .a import Foo\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    # Just assert build_graph terminates; the resulting edge can land at
    # either pkg or pkg.a depending on how the cycle is broken, but the
    # important property is that the resolver returns at all.
    g = build_graph(tmp_path)
    assert g.number_of_nodes() > 0


def test_reexport_chain_does_not_resolve_different_names(tmp_path: Path, pkg: Path):
    # pkg/__init__.py re-exports Foo from pkg.sub, but pkg.sub/__init__.py
    # only has a re-export for Bar, not Foo. The chain follower must not
    # mis-attribute Foo to wherever Bar lives.
    sub = _mkpkg(pkg, "sub", "from .impl import Bar\n")
    (pkg / "__init__.py").write_text("from .sub import Foo\n")
    (sub / "impl.py").write_text("class Bar: ...\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\n")
    g = build_graph(tmp_path)
    # Foo doesn't chain through pkg.sub because pkg.sub doesn't re-export Foo.
    assert g.has_edge("pkg.consumer", "pkg.sub")
    assert not g.has_edge("pkg.consumer", "pkg.sub.impl")


def test_call_edge_attached_to_existing_import_edge(tmp_path: Path, pkg: Path):
    # Calls that flow along an existing import edge enrich it in place
    # rather than creating a parallel edge.
    (pkg / "a.py").write_text("from pkg import b\nb.do(); b.do()\nb.do()\n")
    (pkg / "b.py").write_text("def do():\n    pass\n")
    g = build_graph(tmp_path)
    edge = g["pkg.a"]["pkg.b"]
    assert edge["kinds"] == ("import", "call")
    assert edge["call_count"] == 3
    assert edge["call_lines"] == (2, 2, 3)


def test_call_edge_resolves_deeper_than_import_edge(tmp_path: Path, pkg: Path):
    # `import pkg; pkg.sub.foo()` imports the top-level package only, but
    # the call resolves to pkg.sub when that submodule is internal - the
    # depth differential LocAgent (ACL 2025) cites as the invoke-edge signal.
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (pkg / "consumer.py").write_text("import pkg\npkg.sub.do()\npkg.sub.do()\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg")
    assert "call_lines" not in g["pkg.consumer"]["pkg"]
    assert g.has_edge("pkg.consumer", "pkg.sub")
    call_only = g["pkg.consumer"]["pkg.sub"]
    assert call_only["kinds"] == ("call",)
    assert call_only["call_count"] == 2
    assert call_only["call_lines"] == (2, 3)


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


def test_call_through_reexport_chain(tmp_path: Path, pkg: Path):
    # Re-exports must route calls to the canonical source module the same
    # way imports do; landing them on the re-exporting package instead
    # would inflate that package's coupling metrics with traffic that
    # logically belongs elsewhere.
    (pkg / "__init__.py").write_text("from .impl import Foo\n")
    (pkg / "impl.py").write_text("class Foo:\n    pass\n")
    (pkg / "consumer.py").write_text("from pkg import Foo\nFoo()\n")
    g = build_graph(tmp_path)
    edge = g["pkg.consumer"]["pkg.impl"]
    assert edge["call_count"] == 1


def test_submodule_beats_a_same_named_reexport_for_edges_and_calls(tmp_path: Path, pkg: Path):
    """`from pkg import sub` where `sub` is BOTH a submodule and a re-exported name.

    The import edge and the call edge route through one shared rule, so they
    must agree. Hand-worked: `pkg.sub` exists, and `pkg/__init__.py` also
    re-exports a name `sub` from `pkg.impl`; the submodule wins. If the rule
    were reversed both edges would land on `pkg.impl`.
    """
    (pkg / "__init__.py").write_text("from .impl import sub\n")
    (pkg / "impl.py").write_text("sub = 1\n")
    (pkg / "sub.py").write_text("def do():\n    pass\n")
    (pkg / "consumer.py").write_text("from pkg import sub\nsub.do()\n")
    g = build_graph(tmp_path)
    assert g.has_edge("pkg.consumer", "pkg.sub")  # fixture reaches the both-apply branch
    assert not g.has_edge("pkg.consumer", "pkg.impl")
    assert g["pkg.consumer"]["pkg.sub"]["call_count"] == 1


def test_add_kind_defaults_a_missing_kinds_to_import_for_either_kind():
    """A kinds-less edge predates call edges, so it was an import edge.

    Unreachable through `build_graph` (every builder-made edge carries `kinds`),
    which is why this goes to the helper directly: the default is the only
    behavior the helper adds over a membership check.
    """
    call: dict = {}
    _add_kind(call, "call")
    assert call["kinds"] == ("import", "call")
    already: dict = {"kinds": ("call",)}
    _add_kind(already, "import")
    assert already["kinds"] == ("call", "import")
    _add_kind(already, "import")
    assert already["kinds"] == ("call", "import")  # idempotent


def test_from_external_import_name_call_edges_pin_external_targets(tmp_path: Path, pkg: Path):
    # #705: `from <external> import name` must bind calls through
    # `_external_target`. The `import json` form was already pinned; this
    # form (the common third-party call shape) was not.
    (pkg / "a.py").write_text(
        "from requests import get\n"
        "from os.path import join\n"
        "from requests import get as g\n"
        "import json\n"
        "def f():\n"
        "    get('x')\n"
        "    join('a', 'b')\n"
        "    g('y')\n"
        "    json.dumps(1)\n"
    )
    data = graph_to_dict(build_graph(tmp_path))
    # dotted external collapses to its top-level package
    get_edge = _edge(data, "pkg.a", "requests")
    assert get_edge["call_count"] == 2  # get + g alias
    assert "call" in get_edge["kinds"]
    assert "import" in get_edge["kinds"]
    join_edge = _edge(data, "pkg.a", "os")
    assert join_edge["call_count"] == 1
    assert "call" in join_edge["kinds"]
    json_edge = _edge(data, "pkg.a", "json")
    assert json_edge["call_count"] == 1


def test_relative_import_past_project_root_creates_no_alias_or_call_edge(tmp_path: Path, pkg: Path):
    # #705: an unresolvable relative base must leave the name unbound so no
    # call edge is invented, and the failed import must not drop the module.
    (pkg / "a.py").write_text("from ......missing import ghost\nghost()\n")
    g = build_graph(tmp_path)
    assert not any(d.get("call_count") for _, _, d in g.edges(data=True))
    assert "pkg.a" in g


def test_src_package_import_root_resolves_infra_imports_internal(tmp_path: Path):
    """#853: `from infra.cfg import helper` reaches `src.infra.cfg`, not an external `infra`.

    Before the fix this graph had no internal edge at all (definit-dsa: 0 edges where
    `ruff analyze graph` found 2563), and nothing said the imports had gone unresolved.
    """
    root = _src_layout(tmp_path)
    (root / "main.py").write_text("from infra.cfg import helper\nhelper()\n")
    (root / "src" / "app.py").write_text("import infra.cfg\nprint(infra.cfg.helper())\n")
    graph = build_graph(root)
    assert "infra" not in graph and "infra.cfg" not in graph, "must not be external nodes"
    assert graph.has_edge("main", "src.infra.cfg")
    assert graph.has_edge("src.app", "src.infra.cfg")
    # the call edge goes through the same alias table, so it resolves too.
    assert "call" in graph.edges["main", "src.infra.cfg"]["kinds"]


def test_src_package_import_root_leaves_explicit_src_and_real_packages_alone(tmp_path: Path):
    root = _src_layout(tmp_path)
    # a real top-level package named `infra` must win over the `src.infra` alias.
    (root / "infra").mkdir()
    (root / "infra" / "__init__.py").write_text("")
    (root / "infra" / "cfg.py").write_text("")
    (root / "main.py").write_text("from infra.cfg import x\n")
    (root / "explicit.py").write_text("from src.infra.cfg import helper\n")
    (root / "ext.py").write_text("import requests\n")
    graph = build_graph(root)
    assert graph.has_edge("main", "infra.cfg")
    assert not graph.has_edge("main", "src.infra.cfg")
    assert graph.has_edge("explicit", "src.infra.cfg")
    assert graph.nodes["requests"]["external"] is True


def test_a_reexport_cycle_stops_at_the_first_repeat_not_after_a_fixed_number_of_hops():
    """a -> b -> c -> a for one name: following hops blindly for 8 steps would land on `a`."""
    from archy.graph import _follow_reexport_chains

    maps = {"a": {"X": "b"}, "b": {"X": "c"}, "c": {"X": "a"}}

    _follow_reexport_chains(maps)

    assert maps["a"]["X"] == "c"
