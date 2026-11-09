"""The arithmetic of `bench/code_health.py`, worked by hand.

The weighting is the whole point of the tool (a big mediocre file must outweigh
small perfect ones), so each expectation is a number worked out in the docstring.
"""

from __future__ import annotations

import sys
from pathlib import Path

# bench/ is appended, never prepended, so a bench module cannot shadow stdlib or
# installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import code_health as ch  # ty: ignore[unresolved-import]  (added to sys.path above)


def test_weighted_mean_is_dominated_by_the_big_file():
    """CodeScene's own example: 2.0 at 5,000 lines, 10.0 at 100, 10.0 at 10.

    Unweighted: (2 + 10 + 10) / 3 = 7.333. Weighted: (2*5000 + 10*100 + 10*10) / 5110
    = 11100 / 5110 = 2.1722. (CodeScene's page prints 2.18; the arithmetic is 2.172.)
    """
    rows = [("a.c", 2.0, 5000), ("b.c", 10.0, 100), ("c.c", 10.0, 10)]
    assert round(ch.unweighted_mean(rows) or 0, 3) == 7.333
    assert round(ch.weighted_mean(rows) or 0, 4) == 2.1722
    # The fixture must actually separate the two means, or the test says nothing.
    assert (ch.unweighted_mean(rows) or 0) - (ch.weighted_mean(rows) or 0) > 5


def test_equal_weights_make_the_two_means_agree():
    rows = [("a.py", 4.0, 100), ("b.py", 8.0, 100)]
    assert ch.weighted_mean(rows) == ch.unweighted_mean(rows) == 6.0


def test_nothing_to_weigh_is_none_not_zero_or_ten():
    assert ch.weighted_mean([]) is None
    assert ch.unweighted_mean([]) is None
    assert ch.weighted_mean([("empty.py", 10.0, 0)]) is None


def test_code_lines_skips_blanks_and_comments_but_counts_docstrings():
    text = (
        '"""Doc.\n\nmore\n"""\n# a comment\n\n'
        "x = 1  # trailing comment still code\n   # indented comment\ny = 2\n"
    )
    # Counted: `"""Doc.`, `more`, `"""`, `x = 1  # ...`, `y = 2` -> 5.
    assert ch.code_lines(text) == 5
    assert ch.code_lines("") == 0
    assert ch.code_lines("\n\n   \n# only a comment\n") == 0


def test_gap_table_ranks_by_lines_times_distance_from_ten():
    """big.py: 1000 lines at 5.0 costs 5000. small.py: 100 lines at 1.0 costs 900.

    So the big mediocre file ranks first although the small one scores worse. The
    total gap is 5000 + 900 + 0 = 5900; big's share is 5000 / 5900 = 0.8475.
    """
    rows = [("small.py", 1.0, 100), ("big.py", 5.0, 1000), ("clean.py", 10.0, 500)]
    table = ch.gap_table(rows, top=3)
    assert [t[0] for t in table] == ["big.py", "small.py", "clean.py"]
    assert round(table[0][3], 4) == 0.8475
    assert round(table[1][4], 4) == 1.0  # cumulative reaches 100% once every cost is counted
    assert table[2][3] == 0.0  # a perfect file costs nothing


def test_gap_table_with_no_gap_does_not_divide_by_zero():
    assert ch.gap_table([("a.py", 10.0, 50)], top=5) == [("a.py", 10.0, 50, 0.0, 0.0)]
