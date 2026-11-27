"""Two classes that share a name are two classes (#758).

Expected families are read off the fixture sources by hand.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from archy.conventions import compute_conventions

HTTP = "class Error(Exception): pass\nclass NotFound(Error): pass\nclass Timeout(Error): pass\n"
DB = "class Error(Exception): pass\nclass Deadlock(Error): pass\nclass Integrity(Error): pass\n"


def _report(root: Path, files: dict[str, str]) -> dict:
    for name, body in files.items():
        path = root / "pkg" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    for folder in [root / "pkg", *(p for p in (root / "pkg").rglob("*") if p.is_dir())]:
        init = folder / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
    return compute_conventions(root).model_dump()


def _families(report: dict) -> set[tuple[str, str, tuple[str, ...]]]:
    return {(b["base"], b["home_module"], b["members"]) for b in report["bases"]}


def test_two_hierarchies_with_one_name_stay_apart(tmp_path: Path):
    # The fixture really collides: the same class name defined twice.
    roots = [ast.parse(source).body[0] for source in (HTTP, DB)]
    assert [node.name for node in roots if isinstance(node, ast.ClassDef)] == ["Error", "Error"]

    report = _report(
        tmp_path,
        {
            "http/errors.py": HTTP,
            "db/errors.py": DB,
            "http/__init__.py": (
                "from .errors import Error, NotFound, Timeout\n"
                '__all__ = ["Error", "NotFound", "Timeout"]\n'
            ),
        },
    )

    assert _families(report) == {
        ("Error", "pkg.http.errors", ("Error", "NotFound", "Timeout")),
        ("Error", "pkg.db.errors", ("Deadlock", "Error", "Integrity")),
    }
    assert report["export_gaps"] == ()


def test_siblings_with_the_same_names_do_not_make_a_gap_across_packages(tmp_path: Path):
    """Both hierarchies have `Error` and `NotFound`; a name match would cross them."""
    shared = "class Error(Exception): pass\nclass NotFound(Error): pass\n"
    report = _report(
        tmp_path,
        {
            "http/errors.py": shared + "class Timeout(Error): pass\n",
            "db/errors.py": shared + "class Deadlock(Error): pass\n",
            "http/__init__.py": (
                "from .errors import Error, NotFound, Timeout\n"
                '__all__ = ["Error", "NotFound", "Timeout"]\n'
            ),
        },
    )

    assert len(report["bases"]) == 2
    assert report["export_gaps"] == ()


def test_a_real_gap_is_still_reported_for_the_family_it_is_in(tmp_path: Path):
    report = _report(
        tmp_path,
        {
            "http/errors.py": HTTP,
            "db/errors.py": DB,
            "http/__init__.py": (
                'from .errors import Error, NotFound\n__all__ = ["Error", "NotFound"]\n'
            ),
        },
    )

    assert [(g["export_module"], g["missing"]) for g in report["export_gaps"]] == [
        ("pkg.http", ("Timeout",))
    ]


@pytest.mark.parametrize(
    "child",
    [
        "from pkg.base import Error\nclass B(Error): pass\nclass C(Error): pass\n",
        "from .base import Error as Root\nclass B(Root): pass\nclass C(Root): pass\n",
        "from pkg import base\nclass B(base.Error): pass\nclass C(base.Error): pass\n",
        "import pkg.base\nclass B(pkg.base.Error): pass\nclass C(pkg.base.Error): pass\n",
        "import pkg.base as b\nclass B(b.Error): pass\nclass C(b.Error): pass\n",
        "from pkg.api import Error\nclass B(Error): pass\nclass C(Error): pass\n",
        "from pkg.base import *\nclass B(Error): pass\nclass C(Error): pass\n",
    ],
    ids=["absolute", "renamed", "submodule", "dotted", "module-alias", "re-export", "star"],
)
def test_one_hierarchy_split_across_modules_is_still_one_family(tmp_path: Path, child: str):
    report = _report(
        tmp_path,
        {
            "base.py": "class Error(Exception): pass\n",
            "api/__init__.py": "from pkg.base import Error\n",
            "child.py": child,
        },
    )

    assert _families(report) == {("Error", "pkg.child", ("B", "C", "Error"))}


def test_an_outside_class_with_a_project_name_joins_nothing(tmp_path: Path):
    report = _report(
        tmp_path,
        {
            "base.py": "class Error(Exception): pass\nclass A(Error): pass\nclass Z(Error): pass\n",
            "other.py": (
                "from click import Error\nimport vendor\n"
                "class B(Error): pass\nclass C(vendor.Error): pass\n"
            ),
        },
    )

    assert _families(report) == {("Error", "pkg.base", ("A", "Error", "Z"))}


def test_a_class_named_like_its_imported_base_derives_from_the_import(tmp_path: Path):
    report = _report(
        tmp_path,
        {
            "base.py": "class Error(Exception): pass\n",
            "a.py": (
                "from pkg.base import Error\nclass Local(Error): pass\n"
                "class Error(Error): pass\nclass Other(Error): pass\n"
            ),
        },
    )

    # The imported root takes in the local class named after it, which in turn
    # roots a family of its own, as any intermediate class does.
    assert _families(report) == {
        ("Error", "pkg.a", ("Error", "Local", "Other")),
    }
    assert sorted(b["count"] for b in report["bases"]) == [3, 3]


def test_a_base_reached_through_a_star_re_export_is_followed(tmp_path: Path):
    """`networkx` publishes its exceptions this way; two `Error`s rule out guessing."""
    report = _report(
        tmp_path,
        {
            "lib/errors.py": "class Error(Exception): pass\n",
            "lib/__init__.py": "from pkg.lib.errors import *\n",
            "elsewhere.py": "class Error(Exception): pass\n",
            "child.py": (
                "import pkg.lib as lib\nclass B(lib.Error): pass\nclass C(lib.Error): pass\n"
            ),
        },
    )

    assert _families(report) == {("Error", "pkg.child", ("B", "C", "Error"))}


def test_a_cycle_of_re_exports_ends(tmp_path: Path):
    report = _report(
        tmp_path,
        {
            "a.py": "from pkg.b import *\n",
            "b.py": "from pkg.a import *\n",
            "child.py": "from pkg.a import Gone\nclass B(Gone): pass\nclass C(Gone): pass\n",
        },
    )

    assert report["bases"] == ()


def test_a_star_import_of_nothing_does_not_pick_between_two_names(tmp_path: Path):
    report = _report(
        tmp_path,
        {
            "one.py": "class Error(Exception): pass\n",
            "two.py": "class Error(Exception): pass\n",
            "child.py": "from vendor import *\nclass B(Error): pass\nclass C(Error): pass\n",
        },
    )

    assert report["bases"] == ()
