from __future__ import annotations

from archy.history import HistoryRow
from archy.trend import render_text, sparkline


def _row(
    overall: float, when: str = "2026-05-09T13:45:07Z", commit: str | None = "abc1234"
) -> HistoryRow:
    return HistoryRow(
        timestamp=when,
        commit=commit,
        branch="main",
        overall=overall,
        modularity=0.6,
        acyclicity=0.5,
        depth=0.7,
        equality=0.4,
        module_count=10,
        edge_count=20,
        cycle_count=1,
        max_depth=2,
        community_count=3,
    )


# --- sparkline ---------------------------------------------------------------


def test_sparkline_empty_is_empty_string():
    assert sparkline([]) == ""


def test_sparkline_single_value_is_one_glyph():
    out = sparkline([0.5])
    assert len(out) == 1
    assert out in "▁▂▃▄▅▆▇█"


def test_sparkline_constant_series_renders_flat():
    out = sparkline([0.5, 0.5, 0.5, 0.5])
    assert len(out) == 4
    assert len(set(out)) == 1


_SPARK_GLYPHS = "▁▂▃▄▅▆▇█"


def _glyph_indices(rendered: str) -> list[int]:
    return [_SPARK_GLYPHS.index(ch) for ch in rendered]


def test_sparkline_monotonic_increase_is_ascending():
    indices = _glyph_indices(sparkline([0.1, 0.3, 0.5, 0.7, 0.9]))
    assert indices == sorted(indices)
    assert indices[0] == 0
    assert indices[-1] == len(_SPARK_GLYPHS) - 1


def test_sparkline_monotonic_decrease_is_descending():
    indices = _glyph_indices(sparkline([0.9, 0.7, 0.5, 0.3, 0.1]))
    assert indices == sorted(indices, reverse=True)


# --- render_text -------------------------------------------------------------


def test_render_text_empty_history_explains_how_to_record():
    out = render_text([], last_n=10)
    assert "No archy score history" in out
    assert "--record" in out


def test_render_text_includes_table_columns_and_sparkline():
    rows = [
        _row(0.4, when="2026-05-08T10:00:00Z", commit="aaaaaaa"),
        _row(0.5, when="2026-05-09T10:00:00Z", commit="bbbbbbb"),
        _row(0.6, when="2026-05-10T10:00:00Z", commit="ccccccc"),
    ]
    out = render_text(rows, last_n=10)
    assert "0.400 -> 0.600" in out
    assert "aaaaaaa" in out
    assert "bbbbbbb" in out
    assert "ccccccc" in out
    for column in ["score", "mod", "acy", "dep", "eq"]:
        assert column in out


def test_render_text_truncates_to_last_n():
    rows = [_row(i / 10) for i in range(20)]
    out = render_text(rows, last_n=5)
    assert "last 5 of 20" in out


def test_render_text_handles_missing_commit():
    rows = [_row(0.5, commit=None)]
    out = render_text(rows, last_n=10)
    assert "?" in out
