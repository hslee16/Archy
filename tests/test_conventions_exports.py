"""`__all__` must be read in full or reported as partial, never silently short (#755).

Expected sets are what CPython leaves in `__all__` for each source, worked out
by hand; `test_expected_sets_match_cpython` checks that hand-work by running them.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.conventions import compute_conventions, compute_module_view
from archy.conventions_exports import read_exports

# source, members, whether they were all readable
CASES = {
    "plain": ('__all__ = ["A"]', {"A"}, True),
    "augmented": ('__all__ = ["A"]\n__all__ += ["B"]', {"A", "B"}, True),
    "extend": ('__all__ = ["P"]\n__all__.extend(["Q"])', {"P", "Q"}, True),
    "append_insert_free": ('__all__ = []\n__all__.append("Q")', {"Q"}, True),
    # A set cannot say whether a duplicate survives the removal.
    "remove": ('__all__ = ["A", "B"]\n__all__.remove("B")', {"A", "B"}, False),
    "reads": (
        '__all__ = ["A"]\nfor n in __all__:\n    pass\nif "A" in __all__ and len(__all__):\n'
        '    x = __all__ + ["B"]\n    y = ",".join(__all__[:])\n'
        "def __dir__():\n    return __all__",
        {"A"},
        True,
    ),
    "sorted": ('__all__ = ["B", "A"]\n__all__ = sorted(__all__)\n__all__.sort()', {"A", "B"}, True),
    "literal_concat": ('__all__ = ["A"] + ["B"]', {"A", "B"}, True),
    "literal_unpack": ('__all__ = [*["A"], "B"]', {"A", "B"}, True),
    "self_concat": ('__all__ = ["A"]\n__all__ = __all__ + ["B"]', {"A", "B"}, True),
    "annotated_only": ('__all__: list[str]\n__all__ = ["A"]', {"A"}, True),
    "reassigned": ('__all__ = other()\n__all__ = ["A"]', {"A"}, True),
    "starred_name": ('__all__ = [*sub.__all__, "Y"]', {"Y"}, False),
    "concat_attribute": ('__all__ = ["X"] + sub.__all__', {"X"}, False),
    "augmented_name": ('__all__ = ["X"]\n__all__ += sub.__all__', {"X"}, False),
    "call": ("__all__ = build()", set(), False),
    "conditional_value": ('__all__ = ["A"] if flag else []', set(), False),
    "conditional_block": ('__all__ = ["A"]\nif flag:\n    __all__ += ["B"]', {"A"}, False),
    "only_in_blocks": ('if flag:\n    __all__ = ["A"]', set(), False),
    "decorator": (
        '__all__ = ["A"]\ndef export(f):\n    __all__.append(f.__name__)\n    return f',
        {"A"},
        False,
    ),
    "item_assignment": ('__all__ = ["A"]\n__all__[0] = name', {"A"}, False),
    "imported": ('from sub import __all__\n__all__ += ["A"]', {"A"}, False),
    "imported_in_block": ('__all__ = ["A"]\nif flag:\n    from y import __all__', {"A"}, False),
    "set_literal": ('__all__ = {"A"}', {"A"}, True),
    "non_string": ('__all__ = ["A", name]', {"A"}, False),
    "appended_name": ('__all__ = ["A"]\n__all__.append(name)', {"A"}, False),
    "subtracted": ('__all__ = ["A"]\n__all__ -= ["A"]', {"A"}, False),
    **{
        f"escape_{n}": (f'__all__ = ["A"]\n{line}', {"A"}, False)
        for n, line in {
            "helper": "register(__all__)",
            "keyword": "register(names=__all__)",
            "alias": "a = __all__",
            "chained": "__all__ = a = __all__",
            "bound_method": "add = __all__.append",
            "unbound_method": 'list.append(__all__, "B")',
            "dunder": '__all__.__iadd__(["B"])',
            "insert": '__all__.insert(0, "B")',
            "pop": "__all__.pop()",
            "clear": "__all__.clear()",
            "in_tuple": "for item in (__all__,):\n    item.append(x)",
            "walrus": '(a := __all__).append("B")',
            "or": '(__all__ or []).append("B")',
            "globals_item": 'globals()["__all__"] = ["B"]',
            "globals_update": 'globals().update({"__all__": ["B"]})',
            "vars_item": 'vars()["__all__"] += ["B"]',
            "setattr": 'setattr(sys.modules[__name__], "__all__", ["B"])',
            "module_attr": 'sys.modules[__name__].__all__.append("B")',
            "module_dict": 'sys.modules[__name__].__dict__["__all__"] = []',
            "exec": "exec(code)",
            "match": "match x:\n    case __all__:\n        pass",
            "except_as": "try:\n    pass\nexcept E as __all__:\n    pass",
            "def": "def __all__():\n    pass",
        }.items()
    },
    "second_name_for_the_literal": ('__all__ = other = ["A"]', {"A"}, False),
    "shadowed_wrapper": ('def sorted(x):\n    return []\n__all__ = sorted(["A"])', set(), False),
}


@pytest.mark.parametrize(("source", "names", "complete"), CASES.values(), ids=CASES)
def test_read_exports(source: str, names: set[str], complete: bool):
    declared = read_exports(ast.parse(source))

    assert declared is not None
    assert (set(declared.names), declared.complete) == (names, complete)


@pytest.mark.parametrize(
    ("source", "names"),
    [(s, n) for s, n, complete in CASES.values() if complete],
    ids=[k for k, v in CASES.items() if v[2]],
)
def test_expected_sets_match_cpython(source: str, names: set[str]):
    namespace: dict[str, object] = {"other": lambda: []}
    exec(source, namespace)

    members = namespace["__all__"]
    assert isinstance(members, list | set)
    assert set(members) == names


@pytest.mark.parametrize(
    "source",
    ["x = 1", "print(__all__)", "__all__: list[str]", "def __dir__():\n    return __all__"],
)
def test_reading_or_declaring_a_type_is_not_a_declaration(source: str):
    assert read_exports(ast.parse(source)) is None


def _project(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / "pkg" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def test_module_view_says_when_exports_is_partial(tmp_path: Path):
    root = _project(
        tmp_path,
        {
            "__init__.py": '__all__ = ["A"]\n__all__ += ["B"]\nA = B = 1\n',
            "extra.py": 'from . import ext\n__all__ = ["X"] + ext.__all__\nX = 1\n',
            "dynamic.py": "__all__ = build()\n",
            "ext.py": '__all__ = ["P"]\n',
        },
    )

    whole = compute_module_view(root, "pkg").model_dump()
    partial = compute_module_view(root, "pkg.extra").model_dump()

    assert (whole["exports"], whole["exports_complete"]) == (("A", "B"), True)
    assert (partial["exports"], partial["exports_complete"]) == (("X",), False)

    def run(*args: str) -> str:
        result = CliRunner().invoke(main, ["conventions", str(root), "--module", *args])
        assert result.exit_code == 0, result.output
        return result.output

    assert "every list above is COMPLETE" in run("pkg")
    text = run("pkg.extra")
    assert "X (partial)" in text
    assert "every list above is COMPLETE" not in text
    assert "except exports is COMPLETE" in text
    unknown = run("pkg.dynamic")
    assert "(unknown)" in unknown and "(none)\n  name families" not in unknown
    assert json.loads(run("pkg.extra", "--format", "json"))["exports_complete"] is False


def test_no_export_gap_for_a_partly_read_all(tmp_path: Path):
    models = "class Base: pass\n" + "".join(f"class {n}(Base): pass\n" for n in "ABCD")
    listed = 'from .models import Base, A, B, C, D\n__all__ = ["Base", "A", "B"]\n'
    # Control: with the literal alone the family really is partly exported.
    control = _project(tmp_path / "control", {"models.py": models, "__init__.py": listed})
    assert compute_conventions(control).export_gaps

    folded = _project(
        tmp_path / "folded",
        {"models.py": models, "__init__.py": listed + '__all__ += ["C", "D"]\n'},
    )
    unreadable = _project(
        tmp_path / "unreadable",
        {"models.py": models, "__init__.py": listed + "__all__ += more\n"},
    )

    assert compute_conventions(folded).export_gaps == ()
    assert compute_conventions(unreadable).export_gaps == ()


def test_a_namespace_write_alone_is_a_declaration():
    """Otherwise the view says "no __all__", which is as wrong as a short list."""
    declared = read_exports(ast.parse('globals()["__all__"] = ["A"]'))

    assert declared is not None and not declared.complete


def test_a_long_concatenation_does_not_exhaust_the_stack():
    declared = read_exports(ast.parse("__all__ = " + " + ".join(['["a"]'] * 1500)))

    assert declared is not None
    assert (declared.names, declared.complete) == (frozenset({"a"}), True)


def test_an_unparsed_module_does_not_claim_its_exports(tmp_path: Path):
    root = _project(tmp_path, {"__init__.py": "", "bad.py": "def (:\n"})

    view = compute_module_view(root, "pkg.bad")

    assert (view.exports, view.exports_complete) == (None, False)
