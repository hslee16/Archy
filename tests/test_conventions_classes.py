"""Two classes that share a name are two classes (#758).

Expected families are read off the fixture sources by hand.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from archy.conventions import compute_conventions
from archy.conventions_classes import ClassIndex
from archy.conventions_collect import _collect

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


def _index(root: Path) -> ClassIndex:
    files = sorted((root / "pkg").rglob("*.py"))
    return ClassIndex(
        _collect(
            ast.parse(path.read_bytes()),
            ".".join(path.relative_to(root).with_suffix("").parts).removesuffix(".__init__"),
            is_package=path.name == "__init__.py",
        )
        for path in files
    )


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


TWO_ROOTS = {"a.py": "class Error(Exception): pass\n", "b.py": "class Error(Exception): pass\n"}
CHILDREN = "class B(Error): pass\nclass C(Error): pass\n"


@pytest.mark.parametrize(
    "preamble",
    [
        "from pkg.a import Error\ndef f():\n    from pkg.b import Error\n",
        "from pkg.a import Error\nclass H:\n    class Error: pass\n",
        "from pkg.a import Error\ndef f():\n    class Error: pass\n",
        "if flag:\n    from pkg.a import Error\nelse:\n    from pkg.b import Error\n",
        "try:\n    from pkg.a import Error\nexcept ImportError:\n    from pkg.b import Error\n",
        "from pkg.b import *\nfrom pkg.a import *\n",
        "class Error: pass\nfrom pkg.a import Error\n",
        "from pkg import a, b\nfrom pkg.b import Error\nError = a.Error\n",
        "from pkg import a\nRoot = a.Error\nError = Root\n",
        "with ctx():\n    from pkg.a import Error\n",
    ],
    ids=[
        "function-import",
        "nested-class",
        "function-class",
        "if-else",
        "try-except",
        "later-star",
        "import-after-class",
        "rebound",
        "alias-chain",
        "with",
    ],
)
def test_module_scope_decides_which_class_a_base_names(tmp_path: Path, preamble: str):
    report = _report(tmp_path, {**TWO_ROOTS, "child.py": preamble + CHILDREN})

    (family,) = [b for b in report["bases"] if "B" in b["members"]]
    # Rooted in `a`: its definition is what the family's other members name.
    assert family["members"] == ("B", "C", "Error")
    assert family["home_module"] == "pkg.child"
    index = _index(tmp_path)
    assert index.resolve("pkg.child", "Error") == ("pkg.a", "Error")


@pytest.mark.parametrize(
    "child",
    [
        "class X(TimeoutError): pass\nclass Y(TimeoutError): pass\n",
        "Error = ValueError\n" + CHILDREN,
        "from vendor import Error\n" + CHILDREN,
        "from json import *\nclass X(JSONDecodeError): pass\nclass Y(JSONDecodeError): pass\n",
        CHILDREN,
    ],
    ids=["builtin", "builtin-alias", "outside", "outside-star", "unbound"],
)
def test_a_name_nothing_in_the_project_explains_links_nowhere(tmp_path: Path, child: str):
    others = (
        "class Error(Exception): pass\nclass JSONDecodeError(Exception): pass\n"
        "class H:\n    class TimeoutError(Exception): pass\n"
    )

    report = _report(tmp_path, {"other.py": others, "child.py": child})

    assert report["bases"] == ()


@pytest.mark.parametrize(
    "child",
    [
        "from pkg import base\nclass B(base.Outer.Error): pass\nclass C(base.Outer.Error): pass\n",
        "from pkg.base import Outer\nclass B(Outer.Error): pass\nclass C(Outer.Error): pass\n",
    ],
    ids=["through-module", "through-class"],
)
def test_a_class_nested_in_a_class_is_followed(tmp_path: Path, child: str):
    base = "class Outer:\n    class Error(Exception): pass\n"

    report = _report(tmp_path, {"base.py": base, "child.py": child})

    assert _families(report) == {("Error", "pkg.child", ("B", "C", "Error"))}


def test_a_long_star_chain_ends_without_exhausting_the_stack(tmp_path: Path):
    files = {f"m{i}.py": f"from pkg.m{i + 1} import *\n" for i in range(200)}
    files["child.py"] = "from pkg.m0 import *\n" + CHILDREN

    assert _report(tmp_path, files)["bases"] == ()


def test_no_export_gap_with_nothing_to_add(tmp_path: Path):
    report = _report(
        tmp_path,
        {
            "http/errors.py": HTTP,
            "db/errors.py": HTTP,
            "http/__init__.py": (
                "from pkg.db.errors import Error, NotFound\nfrom pkg.http.errors import Timeout\n"
                '__all__ = ["Error", "NotFound", "Timeout"]\n'
            ),
        },
    )

    assert report["export_gaps"] == ()


def test_two_families_with_one_doc_gap_report_it_once(tmp_path: Path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "errors.md").write_text("`Error` and `NotFound`\n", encoding="utf-8")

    report = _report(tmp_path, {"http/errors.py": HTTP, "db/errors.py": HTTP})

    assert len(report["bases"]) == 2
    assert len(report["doc_gaps"]) == 1


def test_a_wide_package_does_not_use_up_the_chain_limit(tmp_path: Path):
    """The cap counted every module looked in, so 60 star imports hid the 61st."""
    files = {f"lib/m{i}.py": "X = 1\n" for i in range(60)}
    files["lib/errors.py"] = "class Error(Exception): pass\n"
    # The last star is tried first, so the real source goes first.
    stars = ["errors", *(f"m{i}" for i in range(60))]
    files["lib/__init__.py"] = "".join(f"from pkg.lib.{name} import *\n" for name in stars)
    files["child.py"] = (
        "import pkg.lib as lib\nclass B(lib.Error): pass\nclass C(lib.Error): pass\n"
    )

    assert _families(_report(tmp_path, files)) == {("Error", "pkg.child", ("B", "C", "Error"))}


@pytest.mark.parametrize("test", ["TYPE_CHECKING", "typing.TYPE_CHECKING"])
def test_under_type_checking_the_else_branch_is_the_class(tmp_path: Path, test: str):
    child = (
        f"if {test}:\n    from uuid import UUID as Base\nelse:\n"
        "    class Base: pass\n" + "class B(Base): pass\nclass C(Base): pass\n"
    )

    assert _families(_report(tmp_path, {"child.py": child})) == {
        ("Base", "pkg.child", ("B", "Base", "C"))
    }


def test_a_hierarchy_inside_a_function_is_still_a_family(tmp_path: Path):
    child = (
        "def build():\n    class Error(Exception): pass\n"
        "    class B(Error): pass\n    class C(Error): pass\n"
    )

    assert _families(_report(tmp_path, {"child.py": child})) == {
        ("Error", "pkg.child", ("B", "C", "Error"))
    }


def test_a_builtin_is_not_replaced_by_a_nested_class_of_that_name(tmp_path: Path):
    child = (
        "class H:\n    class TimeoutError(Exception): pass\n"
        "class X(TimeoutError): pass\nclass Y(TimeoutError): pass\n"
    )

    assert _report(tmp_path, {"child.py": child})["bases"] == ()


SPEED = "from pkg._speed import Error"
PURE = "from pkg.a import Error"


@pytest.mark.parametrize(
    "preamble",
    [
        f"try:\n    {SPEED}\nexcept ImportError:\n    {PURE}\n",
        f"{PURE}\nif speed:\n    {SPEED}\n",
        f"{PURE}\nif x:\n    try:\n        {SPEED}\n    except ImportError:\n        pass\n",
        f"if x:\n    {SPEED}\nelse:\n    {PURE}\n",
    ],
    ids=["try-fallback", "override-if", "override-nested", "if-else"],
)
def test_an_import_that_leads_nowhere_does_not_hide_the_one_that_does(
    tmp_path: Path, preamble: str
):
    """`wrapt` overrides its Python classes with a C extension when it has one."""
    _report(tmp_path, {"a.py": TWO_ROOTS["a.py"], "child.py": preamble + CHILDREN})

    assert _index(tmp_path).resolve("pkg.child", "Error") == ("pkg.a", "Error")


def test_a_later_plain_import_replaces_every_earlier_choice(tmp_path: Path):
    preamble = (
        "try:\n    from pkg.a import Error\nexcept ImportError:\n    from pkg.b import Error\n"
        "from vendor import Error\n"
    )

    _report(tmp_path, {**TWO_ROOTS, "child.py": preamble + CHILDREN})

    assert _index(tmp_path).resolve("pkg.child", "Error") is None


def test_a_class_remade_by_a_call_keeps_its_family(tmp_path: Path):
    child = (
        "class Setting: pass\nSetting = Meta('Setting', (Setting,), {})\n"
        "class B(Setting): pass\nclass C(Setting): pass\n"
    )

    assert _families(_report(tmp_path, {"child.py": child})) == {
        ("Setting", "pkg.child", ("B", "C", "Setting"))
    }


def test_a_very_long_elif_chain_does_not_exhaust_the_stack(tmp_path: Path):
    chain = "if x == 0:\n    pass\n" + "".join(f"elif x == {i}:\n    pass\n" for i in range(1, 600))

    report = _report(tmp_path, {"a.py": TWO_ROOTS["a.py"], "child.py": chain})

    assert report["modules_unparsed"] == 0
