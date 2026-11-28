"""The ASCII DSM's cell glyphs, which `--weight calls` pushes past one digit (#706)."""

from __future__ import annotations

import pytest

from archy.dsm import DSM, DSMCell, Weight, render_ascii


def _row_for(weight: float, mode: Weight) -> str:
    """The rendered glyph of the one cell, read out of the matrix itself."""
    dsm = DSM(
        ordering=("pkg.a", "pkg.b"),
        groups=(),
        cells=(DSMCell(row=0, col=1, weight=weight),),
        group_by="topological",
        weight=mode,
    )
    (row,) = [line for line in render_ascii(dsm).splitlines() if line.startswith("1 pkg.a")]
    diagonal, cell = row.split()[-2:]
    assert diagonal == "\\"
    return cell


@pytest.mark.parametrize(
    ("weight", "glyph"),
    [
        (-1.0, "."),
        (0.0, "."),
        (0.4, "."),
        (1.0, "1"),
        (9.0, "9"),
        (9.4, "9"),
        (9.6, "+"),
        (10.0, "+"),
        (250.0, "+"),
    ],
)
def test_a_call_weight_is_one_digit_a_dot_or_a_plus(weight: float, glyph: str):
    assert _row_for(weight, "calls") == glyph


def test_an_import_matrix_marks_any_weight_with_x():
    assert _row_for(12.0, "imports") == "X"


def test_a_three_module_call_matrix_renders_every_cell_in_place():
    """Weights 12, 3 and 0.2 at known positions, with an empty cell between them."""
    dsm = DSM(
        ordering=("pkg.a", "pkg.b", "pkg.c"),
        groups=(),
        cells=(
            DSMCell(row=0, col=1, weight=12.0),
            DSMCell(row=1, col=2, weight=3.0),
            DSMCell(row=2, col=0, weight=0.2),
        ),
        group_by="topological",
        weight="calls",
    )

    rows = [line for line in render_ascii(dsm).splitlines() if " pkg." in line]
    grid = [line.split()[-3:] for line in rows if not line.startswith(" ")]

    assert grid == [["\\", "+", "."], [".", "\\", "3"], [".", ".", "\\"]]
