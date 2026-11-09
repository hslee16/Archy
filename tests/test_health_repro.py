"""The archy-side detectors for #870, pinned to what `cs review` did in calibration.

Every expectation here was observed from the CodeScene CLI on the same source
on 2026-11-09 and is recorded in
``docs/research/HEALTH_REPRODUCIBILITY_PREREG.md``. A test failing means the
detector drifted from the tool it is being compared with, which would quietly
change the experiment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# bench/ is appended, never prepended, so a bench module cannot shadow stdlib
# or installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import health_repro as hr  # ty: ignore[unresolved-import]  (added to sys.path above)

COMPLEX = "Complex Method"
ARGS = "Excess Number of Function Arguments"
LARGE = "Large Method"
NEST = "Deep, Nested Complexity"
COND = "Complex Conditional"


def flagged(source: str, smell: str) -> set[str]:
    return {name for name, _ in hr.smells_in_source(source)[smell]}


def ifs(n: int) -> str:
    body = "".join(f"    if x == {i}:\n        x += {i}\n" for i in range(n))
    return f"def f(x):\n{body}    return x\n"


def test_cyclomatic_nine_is_flagged_and_eight_branches_below_it_is_not():
    # Eight sequential ifs is cyclomatic 9; five is 6. Observed: cs flags 8, not 5.
    assert flagged(ifs(8), COMPLEX) == {"f"}
    assert flagged(ifs(5), COMPLEX) == set()


def test_self_and_cls_are_not_counted_as_arguments():
    four = "class C:\n    def m(self, a, b, c, d):\n        return a\n"
    five = "class C:\n    def m(self, a, b, c, d, e):\n        return a\n"
    classm = "class C:\n    @classmethod\n    def m(cls, a, b, c, d):\n        return a\n"
    assert flagged(four, ARGS) == set()
    assert flagged(five, ARGS) == {"m"}
    assert flagged(classm, ARGS) == set()


def test_star_and_keyword_only_arguments_are_counted():
    star = "def f(a, b, c, *args, **kwargs):\n    return a\n"
    kwonly = "def f(a, b, *, c, d, e):\n    return a\n"
    assert flagged(star, ARGS) == {"f"}
    assert flagged(kwonly, ARGS) == {"f"}


def test_a_plain_function_named_self_first_parameter_is_still_counted():
    # `self` is dropped only for methods; a free function's first parameter is real.
    free = "def f(self, a, b, c, d):\n    return a\n"
    assert flagged(free, ARGS) == {"f"}


def test_a_function_nested_in_a_function_is_not_checked():
    nested = "def outer(a):\n    def inner(a, b, c, d, e):\n        return a\n    return inner\n"
    assert flagged(nested, ARGS) == set()


def test_blank_and_comment_lines_do_not_count_toward_method_length():
    blanks = (
        "def f():\n    x = 0\n" + "".join(f"    x += {i}\n\n" for i in range(40)) + "    return x\n"
    )
    comments = (
        "def f():\n    x = 0\n"
        + "".join(f"    # c{i}\n    x += {i}\n" for i in range(40))
        + "    return x\n"
    )
    # Both have 42 code lines but 82 physical lines: the fixture reaches the branch.
    assert len(blanks.splitlines()) > hr.MAX_CODE_LINES
    assert len(comments.splitlines()) > hr.MAX_CODE_LINES
    assert flagged(blanks, LARGE) == set()
    assert flagged(comments, LARGE) == set()


def test_docstring_lines_count_toward_method_length():
    doc = "\n".join(f"doc line {i}" for i in range(70))
    source = f'def f():\n    """{doc}"""\n    return 1\n'
    assert flagged(source, LARGE) == {"f"}


def test_four_nested_loops_or_ifs_are_flagged_and_three_are_not():
    four = (
        "def f(xs):\n    for a in xs:\n        for b in xs:\n            for c in xs:\n"
        "                for d in xs:\n                    print(d)\n"
    )
    three = (
        "def f(xs):\n    for a in xs:\n        for b in xs:\n            for c in xs:\n"
        "                print(c)\n"
    )
    assert flagged(four, NEST) == {"f"}
    assert flagged(three, NEST) == set()


def test_with_and_try_do_not_add_nesting():
    source = (
        "def f(xs):\n    for a in xs:\n        while a:\n            with open(a) as h:\n"
        "                try:\n                    print(h)\n                except Exception:\n"
        "                    pass\n"
    )
    assert flagged(source, NEST) == set()


def test_an_elif_chain_is_not_nesting():
    chain = "def f(x):\n    if x == 0:\n        return 0\n"
    chain += "".join(f"    elif x == {i}:\n        return {i}\n" for i in range(1, 6))
    chain += "    return -1\n"
    # Six branches in the ast nest five Ifs deep through orelse; cs counts none.
    assert chain.count("elif") == 5
    assert flagged(chain, NEST) == set()


def test_an_if_under_else_is_real_nesting():
    # `else:` then `if` is a genuine extra level, unlike `elif`.
    source = (
        "def f(x):\n    if x == 0:\n        return 0\n    else:\n        if x == 1:\n"
        "            return 1\n        else:\n            if x == 2:\n                return 2\n"
        "            else:\n                if x == 3:\n                    return 3\n"
    )
    assert flagged(source, NEST) == {"f"}


def test_comprehension_filters_do_not_nest():
    source = "def f(xs):\n    return [a for a in xs if a if a > 1 if a > 2 if a > 3]\n"
    assert flagged(source, NEST) == set()


def test_two_boolean_operators_in_an_if_or_while_are_flagged():
    three_terms = "def f(a, b, c):\n    if a and b and c:\n        return 1\n    return 0\n"
    mixed = "def f(a, b, c):\n    if a and b or c:\n        return 1\n    return 0\n"
    loop = "def f(a, b, c):\n    while a and b and c:\n        a = 0\n"
    assert flagged(three_terms, COND) == {"f"}
    assert flagged(mixed, COND) == {"f"}
    assert flagged(loop, COND) == {"f"}


def test_one_operator_or_one_in_a_return_or_assignment_is_not_flagged():
    one = "def f(a, b):\n    if not a and not b:\n        return 1\n    return 0\n"
    ret = "def f(a, b, c):\n    return a and b and c\n"
    assign = "def f(a, b, c):\n    x = a and b and c\n    return x\n"
    assert flagged(one, COND) == set()
    assert flagged(ret, COND) == set()
    assert flagged(assign, COND) == set()


def test_a_finding_resolves_by_name_and_start_line_within_the_decorator_window():
    table = [("f", 10, 20), ("g", 30, 40)]
    assert hr._resolve(table, "f", 12, None) == ("f", 10)
    assert hr._resolve(table, "f", 14, None) is None  # outside the 3-line window
    assert hr._resolve(table, "nope", 10, None) is None  # a name that matched nothing


def test_a_conditional_finding_resolves_to_the_function_containing_its_line():
    # Complex Conditional is reported against the condition's line, not the def line.
    table = [("f", 10, 20), ("f", 50, 60)]
    assert hr._resolve(table, "f", None, 55) == ("f", 50)
    assert hr._resolve(table, "f", None, 35) is None


def test_cs_findings_that_resolve_to_nothing_are_listed_not_dropped():
    review = [
        {
            "path": "a.py",
            "score": 9.0,
            "review": [
                {
                    "category": COMPLEX,
                    "functions": [
                        {"title": "f", "start-line": 2},
                        {"title": "ghost", "start-line": 9},
                    ],
                }
            ],
        }
    ]
    result = hr.cs_flags(review, {"a.py": [("f", 2, 5)]})
    assert result["flags"]["a.py"][COMPLEX] == {("f", 2)}
    assert result["unresolved"] == [("a.py", COMPLEX, "ghost")]


def test_jaccard_is_worked_by_hand_and_undefined_for_two_empty_sets():
    # |a n b| = 2, |a u b| = 4, so 0.5. The 92-of-129 baseline in the prereg is 0.713.
    assert hr.jaccard({1, 2, 3}, {2, 3, 4}) == 0.5
    assert round(92 / (112 + 109 - 92), 3) == 0.713
    assert hr.jaccard(set(), set()) is None


def test_spearman_is_minus_one_for_a_reversed_order_and_handles_ties():
    assert hr.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert hr.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    # Average ranks for ties: x ranks 1.5, 1.5, 3; y ranks 1, 2, 3. rho = 0.866...
    assert round(hr.spearman([1, 1, 2], [1, 2, 3]) or 0, 3) == 0.866
    assert hr.spearman([1, 1, 1], [1, 2, 3]) is None  # no variance, no correlation
    assert hr.spearman([1, 2], [2, 1]) is None  # too few points to say


def test_only_ok_rows_count_as_done_so_a_stall_is_retried(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rows = [
        {"project": "a", "status": "ok"},
        {"project": "b", "status": "stalled"},
        {"project": "c", "status": "error"},
    ]
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert hr._done(ledger) == {"a"}


def test_a_method_finding_titled_class_dot_method_resolves_to_the_method():
    # `cs` reports methods as `Class.method`, and a conditional as `Class.method:LINE`.
    review = [
        {
            "path": "a.py",
            "score": 9.0,
            "review": [
                {
                    "category": COMPLEX,
                    "functions": [{"title": "Editor.edit", "start-line": 4}],
                },
                {"category": COND, "functions": [{"title": "Editor.edit:7"}]},
            ],
        }
    ]
    result = hr.cs_flags(review, {"a.py": [("edit", 4, 9)]})
    assert result["unresolved"] == []
    assert result["flags"]["a.py"][COMPLEX] == {("edit", 4)}
    assert result["flags"]["a.py"][COND] == {("edit", 4)}
