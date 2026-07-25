from __future__ import annotations

import networkx as nx
import pytest

from archy.dsm import build_dsm
from archy.history import HistoryRow
from archy.render import DEFAULT_MAX_NODES, render_dsm_html, render_trend_html


def _g(*edges: tuple[str, str]) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for u, v in edges:
        g.add_node(u, external=False)
        g.add_node(v, external=False)
        g.add_edge(u, v)
    return g


def _row(
    timestamp: str,
    overall: float,
    *,
    complexity: float | None = 0.8,
    commit: str | None = "abc1234def",
) -> HistoryRow:
    return HistoryRow(
        timestamp=timestamp,
        commit=commit,
        branch="main",
        overall=overall,
        modularity=0.5,
        acyclicity=0.9,
        depth=0.7,
        equality=0.6,
        complexity=complexity,
        module_count=10,
        edge_count=12,
        cycle_count=1,
        tangle_ratio=0.1,
        max_depth=3,
        community_count=2,
    )


# --- self-containment (the whole point of the format) --------------------------


@pytest.mark.parametrize("view", ["dsm", "trend"])
def test_html_makes_no_external_request(view: str) -> None:
    if view == "dsm":
        html = render_dsm_html(build_dsm(_g(("a", "b"), ("b", "c"))))
    else:
        html = render_trend_html([_row("2026-01-01T00:00:00Z", 0.5)], last_n=10)

    # The only absolute URL allowed is the SVG namespace, which is an XML
    # identifier the browser never resolves over the network.
    assert "https://" not in html
    assert html.count("http://") == html.count('xmlns="http://www.w3.org/2000/svg"')
    assert "<script" not in html
    assert "src=" not in html
    assert "@import" not in html


@pytest.mark.parametrize("view", ["dsm", "trend"])
def test_render_is_byte_stable(view: str) -> None:
    """Byte-stability is what makes the output snapshot-testable and PR-diffable."""
    if view == "dsm":
        graph = _g(("a", "b"), ("b", "c"), ("c", "a"))
        first = render_dsm_html(build_dsm(graph))
        second = render_dsm_html(build_dsm(graph))
    else:
        rows = [_row("2026-01-01T00:00:00Z", 0.5), _row("2026-01-02T00:00:00Z", 0.6)]
        first = render_trend_html(rows, last_n=10)
        second = render_trend_html(rows, last_n=10)

    assert first == second


# --- dsm ----------------------------------------------------------------------


def test_dsm_marks_back_edges_distinctly_under_topological_ordering() -> None:
    dsm = build_dsm(_g(("a", "b"), ("b", "c"), ("c", "a")), group_by="topological")
    html = render_dsm_html(dsm)

    back_edges = [c for c in dsm.cells if c.row > c.col]
    assert back_edges, "fixture must contain at least one back-edge"
    assert html.count("var(--flagged)") == len(back_edges) + 1  # cells + legend swatch
    assert "(back-edge)" in html


def test_dsm_reports_back_edge_count_under_topological_ordering() -> None:
    dsm = build_dsm(_g(("a", "b"), ("b", "c"), ("c", "a")), group_by="topological")
    expected = sum(1 for c in dsm.cells if c.row > c.col)

    assert f"back-edges <b>{expected}</b>" in render_dsm_html(dsm)


def test_dsm_does_not_claim_back_edges_under_community_grouping() -> None:
    """Block order is not a dependency order, so row > col means nothing there.

    Flagging it anyway painted most of a real project's matrix red (168 of 243
    cells on archy itself) for a property the ordering does not encode.
    """
    dsm = build_dsm(_g(("a", "b"), ("b", "c"), ("c", "a")), group_by="community")
    html = render_dsm_html(dsm)

    assert "(back-edge)" not in html  # no cell claims to be one
    assert "back-edges <b>" not in html  # no count is reported
    assert "cycle seed" not in html
    assert "crosses a block boundary" in html
    assert "--group=topological" in html  # points at the ordering that does encode it


def test_dsm_flags_cross_block_edges_under_community_grouping() -> None:
    # Two cliques joined by a single edge: community detection separates them,
    # so exactly one edge crosses a block boundary.
    graph = _g(("a", "b"), ("b", "a"), ("c", "d"), ("d", "c"), ("b", "c"))
    dsm = build_dsm(graph, group_by="community")
    html = render_dsm_html(dsm)

    group_of: dict[int, int] = {}
    offset = 0
    for index, group in enumerate(dsm.groups):
        for pos in range(offset, offset + len(group.members)):
            group_of[pos] = index
        offset += len(group.members)
    crossing = [c for c in dsm.cells if group_of[c.row] != group_of[c.col]]

    assert html.count("var(--flagged)") == len(crossing) + 1  # cells + legend swatch
    assert html.count("(crosses block)") == len(crossing)


def test_dsm_escapes_module_names() -> None:
    dsm = build_dsm(_g(("<script>", "b")))
    html = render_dsm_html(dsm)

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_dsm_indexes_every_module() -> None:
    dsm = build_dsm(_g(("a", "b"), ("b", "c")))
    html = render_dsm_html(dsm)

    for name in dsm.ordering:
        assert f">{name}</td>" in html


def test_dsm_empty_graph_renders_a_page() -> None:
    html = render_dsm_html(build_dsm(nx.DiGraph()))

    assert "Empty graph" in html
    assert html.startswith("<!DOCTYPE html>")


def test_dsm_refuses_oversized_matrix() -> None:
    graph = _g(*[(f"m{i}", f"m{i + 1}") for i in range(6)])

    with pytest.raises(ValueError, match="exceeds max_nodes=3"):
        render_dsm_html(build_dsm(graph), max_nodes=3)


def test_dsm_default_cap_is_generous_enough_for_a_real_project() -> None:
    # Guards the constant against being tightened to ASCII's 80 by reflex; the
    # browser scrolls, so the cap only bounds file size.
    assert DEFAULT_MAX_NODES >= 200


# --- trend --------------------------------------------------------------------


def test_trend_without_history_explains_how_to_record() -> None:
    html = render_trend_html([], last_n=10)

    assert "archy score --record" in html


def test_trend_plots_every_axis() -> None:
    rows = [_row("2026-01-01T00:00:00Z", 0.5), _row("2026-01-02T00:00:00Z", 0.6)]
    html = render_trend_html(rows, last_n=10)

    for axis in ("overall", "modularity", "acyclicity", "depth", "equality", "complexity"):
        assert f"<h2>{axis}</h2>" in html


def test_trend_shows_the_delta_across_the_window() -> None:
    rows = [_row("2026-01-01T00:00:00Z", 0.500), _row("2026-01-02T00:00:00Z", 0.600)]

    assert "0.500 -&gt; 0.600 (+0.100)" in render_trend_html(rows, last_n=10)


def test_trend_honors_last_n() -> None:
    rows = [_row(f"2026-01-0{i}T00:00:00Z", 0.5) for i in range(1, 4)]
    html = render_trend_html(rows, last_n=2)

    assert "last 2 of 3 records" in html
    assert "2026-01-01" not in html


def test_trend_flat_series_still_draws_a_line() -> None:
    rows = [_row("2026-01-01T00:00:00Z", 0.5), _row("2026-01-02T00:00:00Z", 0.5)]
    html = render_trend_html(rows, last_n=10)

    assert "<polyline" in html
    assert "0.500 -&gt; 0.500 (+0.000)" in html


def test_trend_single_record_renders_a_point() -> None:
    html = render_trend_html([_row("2026-01-01T00:00:00Z", 0.5)], last_n=10)

    assert "<circle" in html
    assert "<polyline" not in html  # one point is not a line


def test_trend_missing_complexity_renders_an_empty_panel_not_a_dropped_axis() -> None:
    rows = [_row("2026-01-01T00:00:00Z", 0.5, complexity=None)]
    html = render_trend_html(rows, last_n=10)

    assert "<h2>complexity</h2>" in html
    assert "no data in this window" in html
    assert "<td>-</td>" in html


def test_trend_tolerates_missing_commit() -> None:
    rows = [_row("2026-01-01T00:00:00Z", 0.5, commit=None)]

    assert ">?</td>" in render_trend_html(rows, last_n=10)
