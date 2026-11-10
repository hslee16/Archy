"""Tests for conventions: docs."""

from __future__ import annotations

from pathlib import Path

from archy.conventions import (
    compute_conventions,
)
from tests.conventions_helpers import _documented_project, _new_project, _project


def test_mirrored_names_report_every_module_defining_them(tmp_path: Path):
    project = _project(
        tmp_path,
        {"a.py": "class Cycle: pass\n", "b.py": "class Cycle: pass\n"},
    )
    report = compute_conventions(project)
    mirrored = next(s for s in report.surfaces if s.kind == "mirrored" and s.stem == "Cycle")
    assert mirrored.surfaces == ("pkg.a", "pkg.b")
    assert mirrored.module == ""


def test_export_gap_reads_pep484_re_exports_when_there_is_no_dunder_all(tmp_path: Path):
    """`click` publishes its API with `from .x import Y as Y` and no `__all__`.
    Reading only `__all__` reports that such a package exports nothing."""
    root = _project(
        tmp_path,
        {
            "errors.py": (
                "class Base(Exception): pass\n"
                "class First(Base): pass\n"
                "class Second(Base): pass\n"
                "class Forgotten(Base): pass\n"
            ),
            "__init__.py": (
                "from .errors import Base as Base\n"
                "from .errors import First as First\n"
                "from .errors import Second as Second\n"
                # a plain import is an implementation detail, not a promise
                "from .errors import Forgotten\n"
            ),
        },
    )
    report = compute_conventions(root)
    gap = next(g for g in report.export_gaps if g.family == "Base")
    assert gap.missing == ("Forgotten",)


def test_doc_gap_finds_a_member_its_own_docs_do_not_name(tmp_path: Path):
    """`pytest` ships `PytestFDWarning` exported and with no `autoclass`
    entry -- the same half-wired defect as a missing re-export."""
    root = _documented_project(
        tmp_path,
        ".. autoexception:: Base\n.. autoexception:: First\n.. autoexception:: Second\n",
    )
    report = compute_conventions(root)
    gap = next(g for g in report.doc_gaps if g.family == "Base")
    assert gap.missing == ("Undocumented",)
    assert (gap.documented, gap.defined) == (3, 4)


def test_doc_gap_stays_silent_when_the_docs_are_complete(tmp_path: Path):
    root = _documented_project(
        tmp_path,
        ".. autoexception:: Base\n.. autoexception:: First\n"
        ".. autoexception:: Second\n.. autoexception:: Undocumented\n",
    )
    assert not compute_conventions(root).doc_gaps


def test_doc_gap_ignores_a_family_the_docs_barely_touch(tmp_path: Path):
    """One member mentioned in passing is not a promise to document the rest;
    reporting it would bury the real gaps."""
    root = _documented_project(tmp_path, "See ``First`` for details.\n")
    assert not compute_conventions(root).doc_gaps


def test_doc_gap_matches_a_slug_in_prose_but_not_a_bare_identifier(tmp_path: Path):
    """Strictness scales with ambiguity. `mypy` documents each of its 79 codes
    as `[arg-type]` in a section heading and six of them are ordinary English
    words, so both a hyphen rule and a bracket rule are needed; matching a bare
    CamelCase word in a sentence would call anything documented."""
    _project(
        tmp_path,
        {
            "codes.py": (
                "class ErrorCode:\n"
                "    def __init__(self, code): ...\n"
                'A = ErrorCode("arg-type")\n'
                'B = ErrorCode("override")\n'
                'C = ErrorCode("attr-defined")\n'
                'D = ErrorCode("no-untyped-call")\n'
            )
        },
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "codes.rst").write_text(
        "Check argument types [arg-type]\n"
        "Check overrides [override]\n"
        "An attr-defined problem is reported when...\n"
        # no-untyped-call is named nowhere
    )
    report = compute_conventions(tmp_path)
    gap = next(g for g in report.doc_gaps if g.family == "ErrorCode(...)")
    assert gap.missing == ("no-untyped-call",)


def test_doc_gap_does_not_report_private_members(tmp_path: Path):
    """A project is entitled to leave `_PydanticGeneralMetadata` out on purpose."""
    _project(
        tmp_path,
        {
            "m.py": (
                "class Base: pass\nclass First(Base): pass\n"
                "class Second(Base): pass\nclass _Private(Base): pass\n"
            )
        },
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text("`Base` `First` `Second`\n")
    assert not compute_conventions(tmp_path).doc_gaps


def test_consumer_family_crosses_modules_that_share_no_name_stem():
    """The CLI renders a result through `_x_to_text` / `_x_to_json`, which share
    a stem; the MCP surface renders the same result through a handler that
    shares nothing with them. What the three have in common is the symbol they
    consume, so a stem-keyed census can only ever find two of them."""
    root = _new_project(
        {
            "layers.py": "def find_violations():\n    return []\n",
            "cli.py": (
                "from pkg.layers import find_violations\n"
                "def _violations_to_text(v): ...\n"
                "def _violations_to_json(v): ...\n"
            ),
            "mcp.py": "from pkg.layers import find_violations\ndef _run_check(): ...\n",
        },
    )
    report = compute_conventions(root)
    fam = next(s for s in report.surfaces if s.kind == "consumer" and s.stem == "find_violations")
    assert set(fam.surfaces) == {"pkg.cli", "pkg.mcp"}
    # and the stem-keyed family still finds the CLI pair, which is the half a
    # consumer family does not replace
    helper = next(s for s in report.surfaces if s.kind == "helper" and s.stem == "_violations_to")
    assert set(helper.surfaces) == {"json", "text"}


def test_consumer_family_reads_the_module_an_import_names():
    """Keying on the bare name and taking the first definition reported the
    wrong home: importers of `pkg.b.foo` were listed as consuming `pkg.a`,
    pointing an agent at a module it must not edit. This repository carries
    seven such collisions, `_load_graph` and `_score_to_dict` among them."""
    root = _new_project(
        {
            "a.py": "def foo(): return 1\n",
            "b.py": "def foo(): return 2\n",
            "c.py": "from pkg.b import foo\n",
            "d.py": "from pkg.b import foo\n",
        },
    )
    fam = next(s for s in compute_conventions(root).surfaces if s.kind == "consumer")
    assert fam.stem == "foo"
    assert fam.module == "pkg.b"
    assert set(fam.surfaces) == {"pkg.c", "pkg.d"}


def test_consumer_family_stays_silent_when_the_home_is_ambiguous():
    """A relative import does not say which module it reached, so a colliding
    name cannot be resolved. Saying nothing beats naming the wrong module:
    this section exists to tell an agent what to wire, and a confident wrong
    answer sends it to edit the wrong file."""
    root = _new_project(
        {
            "a.py": "def foo(): return 1\n",
            "b.py": "def foo(): return 2\n",
            "c.py": "from .b import foo\n",
            "d.py": "from .b import foo\n",
        },
    )
    assert not [s for s in compute_conventions(root).surfaces if s.kind == "consumer"]


def test_consumer_family_ignores_a_widely_used_utility():
    """A co-update set is small. Sixteen modules import the graph builder here;
    forgetting one is not a failure mode, that is infrastructure. Without the
    cap this section ranks the most-imported helper first and never reaches the
    sets it exists to name."""
    root = _new_project(
        {"util.py": "def build_graph(): ...\n"}
        | {f"m{i}.py": "from pkg.util import build_graph\n" for i in range(9)},
    )
    report = compute_conventions(root)
    assert not [s for s in report.surfaces if s.kind == "consumer" and s.stem == "build_graph"]
