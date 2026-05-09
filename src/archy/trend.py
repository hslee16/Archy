"""ASCII sparkline + last-N table renderer for archy score history."""

from __future__ import annotations

from archy.history import HistoryRow

# Eight Unicode block elements give the sparkline its resolution. Lowest is
# U+2581 (lower one-eighth block), highest U+2588 (full block).
_SPARK_GLYPHS = "▁▂▃▄▅▆▇█"


def render_text(rows: list[HistoryRow], *, last_n: int) -> str:
    if not rows:
        return "# No archy score history yet. Run `archy score --record` first."

    window = rows[-last_n:] if last_n > 0 else rows
    spark = sparkline([r.overall for r in window])

    lines = [
        f"# archy trend (last {len(window)} of {len(rows)} records)",
        "",
        f"score: {spark}  ({window[0].overall:.3f} -> {window[-1].overall:.3f})",
        "",
        f"{'when':<17} {'commit':<8} {'score':>6} {'mod':>5} {'acy':>5} {'dep':>5} {'eq':>5}",
        f"{'-' * 17} {'-' * 8} {'-' * 6} {'-' * 5} {'-' * 5} {'-' * 5} {'-' * 5}",
    ]
    for row in window:
        when = row.timestamp[:16].replace("T", " ")
        commit = (row.commit or "?")[:7]
        lines.append(
            f"{when:<17} {commit:<8} "
            f"{row.overall:6.3f} {row.modularity:5.3f} {row.acyclicity:5.3f} "
            f"{row.depth:5.3f} {row.equality:5.3f}"
        )
    return "\n".join(lines)


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        # A single point has no range; pin to the middle of the glyph scale
        # so the user sees a glyph rather than an empty string.
        return _SPARK_GLYPHS[len(_SPARK_GLYPHS) // 2]

    lo = min(values)
    hi = max(values)
    span = hi - lo
    if span == 0:
        # Constant series: render the middle glyph so the visual is still
        # informative ("flat") rather than suggesting absence of data.
        return _SPARK_GLYPHS[len(_SPARK_GLYPHS) // 2] * len(values)

    glyph_count = len(_SPARK_GLYPHS)
    out: list[str] = []
    for v in values:
        # Map v in [lo, hi] linearly into [0, glyph_count - 1].
        index = round((v - lo) / span * (glyph_count - 1))
        out.append(_SPARK_GLYPHS[index])
    return "".join(out)
