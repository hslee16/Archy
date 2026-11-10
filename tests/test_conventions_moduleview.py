"""Tests for conventions: moduleview."""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.cli_render import _module_view_to_text
from archy.conventions import (
    compute_module_view,
)
from tests.conventions_helpers import _new_project, _project


def test_module_view_refusal_suggests_modules_containing_the_name():
    root = _new_project({"risk.py": "", "risk_utils.py": ""})
    with pytest.raises(LookupError) as caught:
        compute_module_view(root, "risk")
    assert str(caught.value) == (
        f"no module 'risk' under {root}; did you mean pkg.risk, pkg.risk_utils?"
    )


def test_module_view_refusal_suggests_the_spelling_it_was_probably_meant_to_be():
    """`pkg.risj` is in no module name, so this is answered by the misspelling leg."""
    root = _new_project({"risk.py": ""})
    with pytest.raises(LookupError) as caught:
        compute_module_view(root, "pkg.risj")
    assert str(caught.value) == f"no module 'pkg.risj' under {root}; did you mean pkg.risk?"


def test_module_view_refusal_with_no_near_miss_names_none():
    root = _new_project({"risk.py": ""})
    with pytest.raises(LookupError) as caught:
        compute_module_view(root, "elsewhere.thing")
    assert str(caught.value) == f"no module 'elsewhere.thing' under {root}"


def test_module_view_answers_a_negative(tmp_path: Path):
    """The whole point. "Does `risk` import `hotspots`?" is answered by the
    ABSENCE of hotspots from a complete list; against the ranked report the same
    question scored zero, because `150; showing 12` makes absence meaningless."""
    root = _project(
        tmp_path,
        {
            "risk.py": "from pkg.instability import inst\n",
            "instability.py": "def inst(): ...\n",
            "hotspots.py": "def hot(): ...\n",
        },
    )
    view = compute_module_view(root, "pkg.risk")
    assert view.imports_internal == ("pkg.instability",)
    assert "pkg.hotspots" not in view.imports_internal


def test_module_view_resolves_relative_imports(tmp_path: Path):
    """A relative import is invisible to a plain name match, so an unresolved one
    would answer "no, it does not import that" about a module that plainly
    does -- the worst error a negative-answering lookup can make."""
    root = _project(
        tmp_path,
        {
            "a.py": "from . import b\nfrom .c import thing\n",
            "b.py": "",
            "c.py": "def thing(): ...\n",
        },
    )
    view = compute_module_view(root, "pkg.a")
    assert set(view.imports_internal) == {"pkg.b", "pkg.c"}


def test_module_view_resolves_relative_imports_inside_a_package_init(tmp_path: Path):
    """A package's own `__init__.py` is its own `__package__`, so `from . import x`
    there means the sibling INSIDE it. Stripping a level as if it were a plain
    submodule resolved to a name no module has, and the unknown name was then
    filtered out silently -- a false negative in the aggregating `__init__.py`
    that is the commonest place in a project to write a relative import."""
    root = _project(
        tmp_path, {"sub/__init__.py": "from . import x\n", "sub/x.py": "def f(): ...\n"}
    )
    assert compute_module_view(root, "pkg.sub").imports_internal == ("pkg.sub.x",)


def test_module_view_attributes_a_submodule_import_to_the_submodule(tmp_path: Path):
    """`from pkg import mod` is an edge to the MODULE, not to the package.

    Recording both made the lookup disagree with archy's own dependency graph:
    it reported `archy.install.base` importing `archy.install`, which `archy
    impact` says nothing does. A false positive is as corrosive here as a false
    negative, since the whole claim is that this list can be trusted exactly.
    """
    root = _project(
        tmp_path,
        {"sub/__init__.py": "", "sub/mod.py": "", "a.py": "from pkg.sub import mod\n"},
    )
    assert compute_module_view(root, "pkg.a").imports_internal == ("pkg.sub.mod",)


def test_module_view_falls_back_to_the_package_for_a_symbol_import(tmp_path: Path):
    """`from pkg import Symbol` really does import the package: no imported name
    is a module, so the package is what gets executed. The fallback exists for
    this case and must not be lost to the submodule rule above."""
    root = _project(
        tmp_path,
        {"sub/__init__.py": "class Thing: pass\n", "a.py": "from pkg.sub import Thing\n"},
    )
    assert compute_module_view(root, "pkg.a").imports_internal == ("pkg.sub",)


def test_module_view_follows_a_package_reexport_to_the_defining_module(tmp_path: Path):
    """`from pkg import Symbol` where the package re-exports Symbol is an edge to
    the module that DEFINES it, which is what `archy impact` reports.

    Reproduced on archy's own source before the fix: `archy/cli.py` does `from
    archy.install import run_install`, `archy/install/__init__.py` re-exports it
    from `archy.install.runner`, and the view said cli did not import runner
    while the dependency graph said it did. A false negative is the worst error
    this lookup can make, since its whole claim is that absence is an answer.
    """
    root = _project(
        tmp_path,
        {
            "sub/__init__.py": "from pkg.sub.impl import run\n",
            "sub/impl.py": "def run(): ...\n",
            "a.py": "from pkg.sub import run\n",
        },
    )
    view = compute_module_view(root, "pkg.a")
    assert view.imports_internal == ("pkg.sub.impl",)
    assert compute_module_view(root, "pkg.sub.impl").imported_by == ("pkg.a", "pkg.sub")


def test_module_view_reports_a_name_defined_in_the_package_init(tmp_path: Path):
    """`from . import NAME` where NAME lives in the `__init__.py` itself imports
    the PACKAGE. Suppressing the package fallback for every bare relative import
    made this report no imports at all, while `archy impact` reported the edge.
    The sibling-module case is already handled by preferring a real submodule,
    so the suppression bought nothing and cost a false negative.
    """
    root = _project(tmp_path, {"sub.py": "from . import DIRECT\n"})
    (tmp_path / "pkg" / "__init__.py").write_text("DIRECT = 1\n")
    assert compute_module_view(root, "pkg.sub").imports_internal == ("pkg",)
    assert compute_module_view(root, "pkg").imported_by == ("pkg.sub",)


def test_module_view_drops_a_relative_import_past_the_project_root(tmp_path: Path):
    """`from ... import x` in a shallow package escapes the root. Python rejects
    it at runtime and `graph._resolve_relative_base` drops it; emitting the bare
    suffix instead would invent an edge to whatever top-level module shares the
    name."""
    root = _project(tmp_path, {"a.py": "from ... import b\n", "b.py": ""})
    assert compute_module_view(root, "pkg.a").imports_internal == ()


def test_module_view_ignores_a_reexport_from_outside_the_package(tmp_path: Path):
    """Only a SELF-referential import re-exports. `graph._reexport_source` skips
    anything else, and treating a cross-package import as a re-export routed
    `from pkgA import Thing` straight to `pkgB.impl` and reported NOTHING
    importing `pkgA` -- while `archy impact` reports the `pkgA` edge. The
    existing re-export test only covers the same-package case, where the two
    rules happen to agree, so the divergence was invisible.
    """
    pkg_b = tmp_path / "pkgB"
    pkg_b.mkdir()
    (pkg_b / "__init__.py").write_text("")
    (pkg_b / "impl.py").write_text("class Thing: ...\n")
    pkg_a = tmp_path / "pkgA"
    pkg_a.mkdir()
    (pkg_a / "__init__.py").write_text("from pkgB.impl import Thing\n")
    (tmp_path / "consumer.py").write_text("from pkgA import Thing\n")
    assert compute_module_view(tmp_path, "consumer").imports_internal == ("pkgA",)
    assert compute_module_view(tmp_path, "pkgA").imported_by == ("consumer",)


def test_module_view_sees_plain_import_statements(tmp_path: Path):
    root = _project(tmp_path, {"a.py": "import pkg.b\n", "b.py": ""})
    assert compute_module_view(root, "pkg.a").imports_internal == ("pkg.b",)


def test_module_view_says_why_a_module_was_set_aside(tmp_path: Path):
    """A set-aside module must say so. Otherwise its emptiness reads as
    "nothing to report" when the truth is "not looked at"."""
    root = _project(tmp_path, {"tests/__init__.py": "", "tests/test_it.py": "def test_x(): ...\n"})
    assert "--include-tests" in compute_module_view(root, "pkg.tests.test_it").status
    assert compute_module_view(root, "pkg.tests.test_it", include_tests=True).status == "censused"


def test_module_view_counts_test_importers_even_when_tests_are_set_aside(tmp_path: Path):
    """ "Who imports me" is a question about the whole project. A module imported
    only by tests IS imported, and reporting it as unused because tests were set
    aside would be a false negative of exactly the kind this view prevents."""
    root = _project(
        tmp_path,
        {
            "thing.py": "def f(): ...\n",
            "tests/__init__.py": "",
            "tests/test_thing.py": "from pkg.thing import f\n",
        },
    )
    view = compute_module_view(root, "pkg.thing")
    assert view.imported_by == ("pkg.tests.test_thing",)


def test_module_view_suggests_near_matches_for_an_unknown_module(tmp_path: Path):
    root = _project(tmp_path, {"layers.py": ""})
    with pytest.raises(LookupError, match=r"pkg\.layers"):
        compute_module_view(root, "layers")


def test_module_view_drops_complete_claim_when_an_importer_is_unparsed(tmp_path: Path):
    # #666: an unparsable importer must not leave ModuleView claiming COMPLETE
    # with a truncated imported_by list. A SyntaxError fixture (always invalid)
    # stands in for newer syntax this interpreter cannot parse.
    project = _project(
        tmp_path,
        {
            "store.py": "class Store: pass\n",
            "cli.py": "from pkg.store import Store\n",
            # Final content: imports store so a successful parse would list it,
            # then a SyntaxError so this interpreter skips the module (#666).
            "cache.py": "from pkg.store import Store\n\ndef broken(\n",
        },
    )
    view = compute_module_view(project, "pkg.store")
    dumped = view.model_dump()
    assert dumped["census_complete"] is False
    assert list(dumped["imported_by"]) == ["pkg.cli"]
    assert [u["module"] for u in dumped["unparsed_modules"]] == ["pkg.cache"]
    assert all("python" in u and "reason" in u and "path" in u for u in dumped["unparsed_modules"])
    text = _module_view_to_text(view)
    assert "every list above is COMPLETE" not in text
    assert "pkg.cache" in text
    assert "INCOMPLETE" in text


def test_module_view_of_an_unparsed_module_names_the_skip_not_lookuperror(tmp_path: Path):
    project = _project(tmp_path, {"cache.py": "def broken(\n"})
    view = compute_module_view(project, "pkg.cache")
    dumped = view.model_dump()
    assert dumped["census_complete"] is False
    assert dumped["module"] == "pkg.cache"
    assert "could not be parsed" in dumped["status"]
    assert dumped["unparsed_modules"][0]["module"] == "pkg.cache"
    assert list(dumped["classes"]) == []


def test_module_view_sees_an_importer_hidden_behind_a_latin1_cookie(tmp_path: Path):
    """A Latin-1 module with a coding cookie died in read_text as a
    UnicodeDecodeError and silently vanished from a COMPLETE "imported by"
    answer, which is exactly the answer an agent acts on (#649)."""
    root = tmp_path
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    legacy = (
        '# -*- coding: latin-1 -*-\nGREETING = "Ol\xe1, caf\xe9"\nfrom pkg.store import Store\n'
    )
    legacy_path = pkg / "legacy.py"
    legacy_path.write_bytes(legacy.encode("latin-1"))
    with pytest.raises(UnicodeDecodeError):
        legacy_path.read_bytes().decode("utf-8")  # the fixture must exercise the bug
    (pkg / "store.py").write_text("class Store:\n    pass\n")
    view = compute_module_view(root, "pkg.store")
    assert view.imported_by == ("pkg.legacy",)
