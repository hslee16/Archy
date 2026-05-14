from __future__ import annotations

from archy.complexity import compute_function_complexity


def _by_name(src: bytes) -> dict[str, int]:
    return {f.qualified_name: f.cyclomatic for f in compute_function_complexity(src)}


def test_no_branches_is_one():
    assert _by_name(b"def f():\n    return 1\n") == {"f": 1}


def test_if_adds_one():
    src = b"def f(x):\n    if x:\n        return 1\n    return 0\n"
    assert _by_name(src) == {"f": 2}


def test_elif_adds_one_more():
    src = (
        b"def f(x):\n"
        b"    if x > 0:\n        return 1\n"
        b"    elif x < 0:\n        return -1\n"
        b"    return 0\n"
    )
    assert _by_name(src) == {"f": 3}


def test_for_and_while_each_add_one():
    src = b"def f(xs):\n    for x in xs:\n        pass\n    while True:\n        break\n"
    assert _by_name(src) == {"f": 3}


def test_except_clauses_add_one_each():
    src = (
        b"def f():\n"
        b"    try:\n        x = 1\n"
        b"    except ValueError:\n        return None\n"
        b"    except KeyError:\n        return None\n"
    )
    assert _by_name(src) == {"f": 3}


def test_boolean_operators_each_add_one():
    # `a and b and c` parses as two boolean_operator nodes (binary,
    # left-associative). Two operators -> +2.
    src = b"def f(a, b, c):\n    return a and b and c\n"
    assert _by_name(src) == {"f": 3}


def test_conditional_expression_adds_one():
    src = b"def f(x):\n    return 1 if x else 0\n"
    assert _by_name(src) == {"f": 2}


def test_comprehension_clauses_count():
    # [x for x in xs if x > 0] -> one for_in_clause + one if_clause -> +2
    src = b"def f(xs):\n    return [x for x in xs if x > 0]\n"
    assert _by_name(src) == {"f": 3}


def test_match_case_each_adds_one():
    src = (
        b"def f(x):\n"
        b"    match x:\n"
        b"        case 1:\n            return 'a'\n"
        b"        case 2:\n            return 'b'\n"
        b"        case _:\n            return 'c'\n"
    )
    # Three case_clauses -> CC = 1 + 3 = 4
    assert _by_name(src)["f"] == 4


def test_methods_qualified_with_class_name():
    src = (
        b"class Foo:\n"
        b"    def bar(self, x):\n"
        b"        if x:\n            return 1\n"
        b"        return 0\n"
    )
    assert _by_name(src) == {"Foo.bar": 2}


def test_nested_function_gets_its_own_row():
    # Outer has no branches; inner has one. Outer must not absorb inner's branch.
    src = (
        b"def outer():\n"
        b"    def inner(x):\n"
        b"        if x:\n            return 1\n"
        b"        return 0\n"
        b"    return inner\n"
    )
    result = _by_name(src)
    assert result == {"outer": 1, "outer.inner": 2}


def test_nested_class_method_qualified_through_outer_function():
    src = (
        b"def outer():\n"
        b"    class Local:\n"
        b"        def m(self, x):\n"
        b"            return x and x\n"
        b"    return Local\n"
    )
    result = _by_name(src)
    assert "outer" in result and result["outer"] == 1
    assert result["outer.Local.m"] == 2


def test_class_body_top_level_branches_do_not_count_for_any_function():
    # `if SOMETHING:` at class scope binds a class attribute conditionally;
    # it's not part of any method. The lone method's CC must stay at 1.
    src = b"class Foo:\n    if True:\n        x = 1\n    def m(self):\n        return self.x\n"
    assert _by_name(src) == {"Foo.m": 1}


def test_assert_does_not_count():
    # radon-default behavior; assert can be compiled out at -O.
    src = b"def f(x):\n    assert x > 0\n    return x\n"
    assert _by_name(src) == {"f": 1}


def test_syntax_error_still_returns_what_it_can():
    # Tree-sitter produces a partial tree; the well-formed function should
    # still appear in the output.
    src = b"def f(x):\n    if x:\n        return 1\n\ndef broken(:\n"
    result = _by_name(src)
    assert result.get("f") == 2


def test_functions_returned_in_line_order():
    src = b"def c():\n    pass\ndef a():\n    pass\ndef b():\n    pass\n"
    names = [f.qualified_name for f in compute_function_complexity(src)]
    assert names == ["c", "a", "b"]
