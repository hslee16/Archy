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
    "remove": ('__all__ = ["A", "B"]\n__all__.remove("B")', {"A"}, True),
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
    assert isinstance(members, list)
    assert set(members) == names


@pytest.mark.parametrize(
    "source", ["x = 1", "print(__all__)", "__all__: list[str]", "def f():\n    return __all__"]
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
