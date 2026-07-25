"""Self-contained HTML export of the DSM and score-trend views (`archy render`).

Branch A of the visualization surface (`docs/SPEC_VISUALIZATION.md` §3), scoped
to the two views that need no layout engine. Both render as inline SVG with
inline CSS: no JavaScript, no vendored bundle, no external request, no embedded
wall-clock time. That combination is what makes the output offline-safe,
attachable to a PR, and byte-stable for a fixed input, so a snapshot test can
assert on it and a human can diff two exports.

The `graph` view is deliberately absent. It is the one view that forces a
vendored force-layout engine into the wheel, and it scores lowest against the
anti-theater gate (§2): a node-link diagram is present in every tool, while a
DSM's back-edge positions and a score trajectory are signals archy computes and
text conveys poorly. It stays deferred behind a usage signal, matching the
`archy_render` MCP tool deferral in §6.3.

Each view still clears the gate on its own: `dsm` encodes back-edges (gate
signal 3), `trend` encodes score trajectory (gate signal 4).
"""

from __future__ import annotations

from html import escape

from archy.dsm import DSM, summarize_dsm
from archy.history import HistoryRow

# HTML rendering stays useful far past the point ASCII does (a browser scrolls,
# and a rect grid does not wrap), but a dense matrix still emits one SVG node
# per edge, so the cap bounds file size rather than legibility. 300 modules of
# typical density lands well under a megabyte.
DEFAULT_MAX_NODES = 300

# Geometry, in CSS pixels. Monospace advance width at the label font size is
# approximated rather than measured: SVG has no text-metrics API at render
# time, and over-reserving the gutter by a few pixels is invisible.
_CELL = 13
_CHAR_W = 6.5
_LABEL_MAX_CHARS = 44
_RULER_H = 26
_INDEX_EVERY = 5

_SPARK_W = 460
_SPARK_H = 54

_STYLE = """
:root {
  --bg: #ffffff; --fg: #1c1e21; --muted: #6b7280; --rule: #d8dbe0;
  --panel: #f6f7f9; --flagged: #d1443c; --edge: #4a6fa5; --diag: #c9ccd1;
  --accent: #1c1e21;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181c; --fg: #e6e8eb; --muted: #9aa1ab; --rule: #33373d;
    --panel: #1e2126; --flagged: #e8635a; --edge: #7ea2d6; --diag: #3a3f46;
    --accent: #e6e8eb;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px; background: var(--bg); color: var(--fg);
  font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Helvetica, sans-serif;
}
h1 { font-size: 18px; margin: 0 0 4px; font-weight: 600; }
.sub { color: var(--muted); font-size: 13px; margin: 0 0 18px; }
.facts { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 18px; padding: 0; list-style: none; }
.facts li {
  background: var(--panel); border: 1px solid var(--rule); border-radius: 6px;
  padding: 6px 10px; font-size: 12px;
}
.facts b { font-weight: 600; }
.scroll { overflow-x: auto; border: 1px solid var(--rule); border-radius: 8px; padding: 12px; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; margin: 14px 0 0; padding: 0;
  list-style: none; font-size: 12px; color: var(--muted); }
.legend span { display: inline-block; width: 11px; height: 11px; border-radius: 2px;
  vertical-align: -1px; margin-right: 6px; }
.note { color: var(--muted); font-size: 12px; margin-top: 14px; max-width: 62ch; }
table { border-collapse: collapse; font-size: 12px; margin-top: 8px; }
th, td { text-align: right; padding: 4px 10px; border-bottom: 1px solid var(--rule);
  font-variant-numeric: tabular-nums; }
th { color: var(--muted); font-weight: 600; }
th:first-child, td:first-child { text-align: left; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.charts { display: flex; flex-wrap: wrap; gap: 18px; }
.chart { border: 1px solid var(--rule); border-radius: 8px; padding: 12px 14px; }
.chart h2 { font-size: 13px; margin: 0 0 2px; font-weight: 600; }
.chart .range { color: var(--muted); font-size: 11px; margin: 0 0 6px;
  font-variant-numeric: tabular-nums; }
.empty { color: var(--muted); }
"""


def render_dsm_html(dsm: DSM, *, max_nodes: int = DEFAULT_MAX_NODES) -> str:
    """Render a DSM as a standalone HTML page with an inline SVG matrix.

    Raises ValueError above `max_nodes` rather than emitting a page that says
    it gave up: the caller asked for a file to hand to someone, and a file
    whose content is an apology is worse than an error at the terminal.
    """
    n = len(dsm.ordering)
    if n > max_nodes:
        raise ValueError(
            f"{n} modules exceeds max_nodes={max_nodes} for HTML rendering. "
            "Narrow with --focus=<module> or --package=<prefix>, or raise --max-nodes."
        )

    subtitle = f"group={dsm.group_by}, weight={dsm.weight}"
    if n == 0:
        return _page(
            "archy DSM",
            subtitle,
            '<p class="empty">Empty graph: no internal modules after filtering.</p>',
        )

    summary = summarize_dsm(dsm)
    flags_back_edges = dsm.group_by == "topological"

    counts = [
        ("modules", str(summary.module_count)),
        ("edges", str(summary.cell_count)),
    ]
    if flags_back_edges:
        counts.append(("back-edges", str(summary.back_edge_count)))
    counts.append(("cross-block edges", str(summary.cross_group_edge_count)))
    counts.append(("blocks", str(summary.group_count)))
    facts = _facts(counts)

    flagged_label = "back-edge (cycle seed)" if flags_back_edges else "crosses a block boundary"
    legend = _legend(
        [
            ("var(--flagged)", flagged_label),
            ("var(--edge)", "within-block edge" if not flags_back_edges else "forward edge"),
            ("var(--diag)", "diagonal"),
        ]
    )

    if flags_back_edges:
        note = (
            "Red cells sit below the diagonal: the source is ordered after the target, "
            "so the edge points against topological order. Those are the cycle seeds. "
            "Position is the signal, since the row and column name the two modules "
            "involved, which no scalar preserves."
        )
    else:
        note = (
            "Red cells cross a block boundary. Under <code>--group=layer</code> those "
            "are the cross-layer dependencies; under <code>--group=community</code> they "
            "are the coupling that breaks the detected block structure. "
            "Below-diagonal position carries no meaning in these groupings, since the "
            "block order is not a dependency order, so re-run with "
            "<code>--group=topological</code> to read back-edges."
        )

    body = (
        f"{facts}\n"
        f'<div class="scroll">{_dsm_svg(dsm)}</div>\n'
        f"{legend}\n"
        f'<p class="note">{note}</p>\n'
        f"{_dsm_index(dsm)}"
    )
    return _page("archy DSM", subtitle, body)


def render_trend_html(rows: list[HistoryRow], *, last_n: int) -> str:
    """Render score history as standalone HTML: one sparkline per axis, plus the table."""
    if not rows:
        return _page(
            "archy trend",
            "no history yet",
            '<p class="empty">No archy score history yet. '
            "Run <code>archy score --record</code> first.</p>",
        )

    window = rows[-last_n:] if last_n > 0 else rows
    subtitle = f"last {len(window)} of {len(rows)} records"

    series: list[tuple[str, list[float | None]]] = [
        ("overall", [r.overall for r in window]),
        ("modularity", [r.modularity for r in window]),
        ("acyclicity", [r.acyclicity for r in window]),
        ("depth", [r.depth for r in window]),
        ("equality", [r.equality for r in window]),
        ("complexity", [r.complexity for r in window]),
    ]
    charts = "".join(_chart(label, values) for label, values in series)

    note = (
        "Each axis is scaled to its own observed range, printed above the line, so "
        "small drift stays visible. Compare a line against its own range, never "
        "against another axis's."
    )

    body = f'<div class="charts">{charts}</div>\n<p class="note">{note}</p>\n{_trend_table(window)}'
    return _page("archy trend", subtitle, body)


# --- page skeleton ------------------------------------------------------------


def _page(title: str, subtitle: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape(title)}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        f"<h1>{escape(title)}</h1>\n"
        f'<p class="sub">{escape(subtitle)}</p>\n'
        f"{body}\n"
        "</body>\n</html>\n"
    )


def _facts(pairs: list[tuple[str, str]]) -> str:
    items = "".join(f"<li>{escape(k)} <b>{escape(v)}</b></li>" for k, v in pairs)
    return f'<ul class="facts">{items}</ul>'


def _legend(entries: list[tuple[str, str]]) -> str:
    items = "".join(
        f'<li><span style="background:{color}"></span>{escape(label)}</li>'
        for color, label in entries
    )
    return f'<ul class="legend">{items}</ul>'


# --- dsm ----------------------------------------------------------------------


def _dsm_svg(dsm: DSM) -> str:
    n = len(dsm.ordering)
    gutter = _gutter_width(dsm.ordering)
    width = gutter + n * _CELL + 1
    height = _RULER_H + n * _CELL + 1
    boundaries = _boundaries(dsm)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="dependency structure matrix">'
    ]

    for i in range(n):
        if i % _INDEX_EVERY == 0:
            x = gutter + i * _CELL + _CELL / 2
            parts.append(
                f'<text x="{_num(x)}" y="{_RULER_H - 8}" font-size="9.5" '
                f'text-anchor="middle" fill="var(--muted)" '
                f'font-family="ui-monospace, Menlo, monospace">{i + 1}</text>'
            )

    for r in range(n):
        y = _RULER_H + r * _CELL
        label = _truncate(dsm.ordering[r])
        parts.append(
            f'<text x="{gutter - 6}" y="{_num(y + _CELL - 3.5)}" font-size="10" '
            f'text-anchor="end" fill="var(--fg)" '
            f'font-family="ui-monospace, Menlo, monospace">{escape(label)}</text>'
        )
        parts.append(
            f'<rect x="{gutter + r * _CELL}" y="{y}" width="{_CELL}" height="{_CELL}" '
            f'fill="var(--diag)"/>'
        )

    # What counts as the flagged cell depends on the ordering. Only a
    # topological ordering makes "below the diagonal" mean anything (the edge
    # runs against dependency order); under community or layer grouping the
    # block order is arbitrary with respect to dependencies, so flagging
    # row > col there would paint most of the matrix red for no reason. The
    # signal that survives in those groupings is crossing a block boundary.
    group_of = _group_of(dsm)
    flags_back_edges = dsm.group_by == "topological"

    for cell in dsm.cells:
        x = gutter + cell.col * _CELL
        y = _RULER_H + cell.row * _CELL
        if flags_back_edges:
            flagged = cell.row > cell.col
            kind = " (back-edge)" if flagged else ""
        else:
            flagged = group_of.get(cell.row) != group_of.get(cell.col)
            kind = " (crosses block)" if flagged else ""
        fill = "var(--flagged)" if flagged else "var(--edge)"
        src = escape(dsm.ordering[cell.row])
        tgt = escape(dsm.ordering[cell.col])
        parts.append(
            f'<rect x="{x}" y="{y}" width="{_CELL}" height="{_CELL}" fill="{fill}">'
            f"<title>{src} -&gt; {tgt}{kind}</title></rect>"
        )

    # Block boundaries are drawn last so they sit above the cells: under
    # community or layer grouping they are the reading aid that turns a cloud
    # of cells into "inside the block" versus "crossing it".
    for b in sorted(boundaries):
        x = gutter + b * _CELL
        y = _RULER_H + b * _CELL
        parts.append(
            f'<line x1="{x}" y1="{_RULER_H}" x2="{x}" y2="{height - 1}" '
            f'stroke="var(--accent)" stroke-width="1" opacity="0.45"/>'
        )
        parts.append(
            f'<line x1="{gutter}" y1="{y}" x2="{width - 1}" y2="{y}" '
            f'stroke="var(--accent)" stroke-width="1" opacity="0.45"/>'
        )

    parts.append(
        f'<rect x="{gutter}" y="{_RULER_H}" width="{n * _CELL}" height="{n * _CELL}" '
        f'fill="none" stroke="var(--rule)" stroke-width="1"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _dsm_index(dsm: DSM) -> str:
    """Index -> module name, so a column number in the ruler can be resolved."""
    rows = "".join(
        f'<tr><td class="mono">{i}</td><td class="mono">{escape(name)}</td></tr>'
        for i, name in enumerate(dsm.ordering, start=1)
    )
    return f"<table><thead><tr><th>#</th><th>module</th></tr></thead><tbody>{rows}</tbody></table>"


def _group_of(dsm: DSM) -> dict[int, int]:
    """Position -> group index. Groups are contiguous slices of `ordering`."""
    positions: dict[int, int] = {}
    offset = 0
    for index, group in enumerate(dsm.groups):
        for pos in range(offset, offset + len(group.members)):
            positions[pos] = index
        offset += len(group.members)
    return positions


def _boundaries(dsm: DSM) -> set[int]:
    boundaries: set[int] = set()
    offset = 0
    for group in dsm.groups:
        if offset != 0:
            boundaries.add(offset)
        offset += len(group.members)
    return boundaries


def _gutter_width(ordering: tuple[str, ...]) -> int:
    longest = max((len(_truncate(name)) for name in ordering), default=0)
    return int(longest * _CHAR_W) + 12


def _truncate(name: str) -> str:
    if len(name) <= _LABEL_MAX_CHARS:
        return name
    # Keep the tail: the leaf module name discriminates, the package prefix
    # repeats across every row.
    return "..." + name[-(_LABEL_MAX_CHARS - 3) :]


# --- trend --------------------------------------------------------------------


def _chart(label: str, values: list[float | None]) -> str:
    known = [v for v in values if v is not None]
    if not known:
        # complexity is absent on rows written before v0.20; an empty panel is
        # honest, whereas dropping the axis hides that the history is partial.
        return (
            f'<div class="chart"><h2>{escape(label)}</h2>'
            f'<p class="range">no data in this window</p></div>'
        )

    lo, hi = min(known), max(known)
    span = hi - lo
    first, last = known[0], known[-1]
    delta = last - first
    sign = "+" if delta >= 0 else ""
    range_text = f"{lo:.3f} to {hi:.3f} | {first:.3f} -> {last:.3f} ({sign}{delta:.3f})"

    pad = 6
    inner_h = _SPARK_H - 2 * pad
    step = _SPARK_W / (len(values) - 1) if len(values) > 1 else 0.0

    points: list[str] = []
    for i, value in enumerate(values):
        if value is None:
            continue
        x = i * step if len(values) > 1 else _SPARK_W / 2
        # A flat series has no range to scale into; pin it to the middle so the
        # line reads as "flat" rather than collapsing onto an edge.
        ratio = 0.5 if span == 0 else (value - lo) / span
        y = pad + (1 - ratio) * inner_h
        points.append(f"{_num(x)},{_num(y)}")

    marks = "".join(
        f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="1.6" fill="var(--edge)"/>'
        for p in points
    )
    line = (
        f'<polyline points="{" ".join(points)}" fill="none" stroke="var(--edge)" '
        f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
        if len(points) > 1
        else ""
    )

    return (
        f'<div class="chart"><h2>{escape(label)}</h2>'
        f'<p class="range">{escape(range_text)}</p>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_SPARK_W}" height="{_SPARK_H}" '
        f'viewBox="0 0 {_SPARK_W} {_SPARK_H}" role="img" '
        f'aria-label="{escape(label)} over time">{line}{marks}</svg></div>'
    )


def _trend_table(window: list[HistoryRow]) -> str:
    head = (
        "<tr><th>when</th><th>commit</th><th>score</th><th>mod</th><th>acy</th>"
        "<th>dep</th><th>eq</th><th>cpx</th></tr>"
    )
    rows: list[str] = []
    for row in window:
        when = row.timestamp[:16].replace("T", " ")
        commit = (row.commit or "?")[:7]
        cpx = f"{row.complexity:.3f}" if row.complexity is not None else "-"
        rows.append(
            f'<tr><td class="mono">{escape(when)}</td>'
            f'<td class="mono">{escape(commit)}</td>'
            f"<td>{row.overall:.3f}</td><td>{row.modularity:.3f}</td>"
            f"<td>{row.acyclicity:.3f}</td><td>{row.depth:.3f}</td>"
            f"<td>{row.equality:.3f}</td><td>{cpx}</td></tr>"
        )
    return f"<table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table>"


def _num(value: float) -> str:
    """Fixed-precision coordinate, so output is byte-stable across platforms."""
    return f"{value:.2f}".rstrip("0").rstrip(".")
