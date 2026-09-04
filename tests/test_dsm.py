from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest
from pydantic import ValidationError

from archy.dsm import (
    DSM,
    SUMMARY_BACK_EDGE_SAMPLE,
    DSMCell,
    DSMGroup,
    DSMSummary,
    build_dsm,
    diff_dsm,
    dsm_from_dict,
    read_dsm,
    render_ascii,
    render_diff_text,
    render_json,
    summarize_dsm,
    write_dsm,
)


def _g(*edges: tuple[str, str], calls: dict[tuple[str, str], int] | None = None) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for u, v in edges:
        cc = (calls or {}).get((u, v), 0)
        g.add_edge(u, v, lines=(1,), call_count=cc)
    return g


def _externalize(g: nx.DiGraph, *names: str) -> nx.DiGraph:
    for n in names:
        g.add_node(n, external=True)
    return g


# --- summarize_dsm ------------------------------------------------------------


def test_summarize_dsm_derives_counts_and_back_edges():
    # a<->b is a 2-cycle (one direction is a back-edge in any ordering); c->b is
    # an extra forward edge. summarize_dsm must reproduce the DSM's own counts.
    dsm = build_dsm(_g(("a", "b"), ("b", "a"), ("c", "b")), group_by="topological")
    summary = summarize_dsm(dsm)

    assert isinstance(summary, DSMSummary)
    assert summary.group_by == "topological"
    assert summary.module_count == len(dsm.ordering) == 3
    assert summary.cell_count == len(dsm.cells) == 3
    assert summary.group_count == len(dsm.groups)
    assert tuple(g.size for g in summary.groups) == tuple(len(g.members) for g in dsm.groups)
    assert sum(g.size for g in summary.groups) == summary.module_count

    # Back-edges: cells whose source sits later than the target in the ordering
    # (row > col) -- the same test diff_dsm uses. Recompute to cross-check.
    expected_back = [(dsm.ordering[c.row], dsm.ordering[c.col]) for c in dsm.cells if c.row > c.col]
    assert summary.back_edge_count == len(expected_back)
    assert set(summary.back_edges) <= set(expected_back)
    assert len(summary.back_edges) == min(len(expected_back), SUMMARY_BACK_EDGE_SAMPLE)


def test_summarize_dsm_counts_cross_group_coupling():
    """Two disjoint communities joined by a single edge, asserted as literals.

    The earlier version rebuilt `group_of` by walking `dsm.groups` and recounted
    the mismatch exactly as `summarize_dsm` does, so both sides moved together.
    Collapsing community detection to one group made the expected count 0 and
    the actual count 0, and the test passed (#439). The grouping itself has to
    be pinned, or the count it feeds means nothing.
    """
    dsm = build_dsm(_g(("a", "b"), ("c", "d"), ("b", "c")), group_by="community")

    summary = summarize_dsm(dsm)

    # Order-independent: which community networkx enumerates first is an
    # artifact of greedy modularity's merge order, not a contract, and pinning
    # it would fail on a tie-breaking change that leaves the count correct.
    assert {g.members for g in dsm.groups} == {("a", "b"), ("c", "d")}
    assert summary.group_count == 2
    # Only `b -> c` spans the two communities; `a -> b` and `c -> d` are internal.
    assert summary.cross_group_edge_count == 1


def test_summarize_dsm_back_edge_sample_is_capped():
    # A fully-connected 8-node graph has many back-edges; the count is exact but
    # the listed sample is bounded.
    nodes = "abcdefgh"
    edges = [(u, v) for u in nodes for v in nodes if u != v]
    summary = summarize_dsm(build_dsm(_g(*edges), group_by="topological"))
    assert summary.back_edge_count > SUMMARY_BACK_EDGE_SAMPLE
    assert len(summary.back_edges) == SUMMARY_BACK_EDGE_SAMPLE


# --- Builder: basic shapes ----------------------------------------------------


def test_build_dsm_empty_graph():
    dsm = build_dsm(nx.DiGraph())
    assert dsm.ordering == ()
    assert dsm.cells == ()
    assert dsm.groups == ()


def test_build_dsm_single_node():
    g = nx.DiGraph()
    g.add_node("a")
    dsm = build_dsm(g)
    assert dsm.ordering == ("a",)
    assert dsm.cells == ()
    assert len(dsm.groups) == 1


def test_build_dsm_excludes_external_nodes():
    g = _g(("a", "b"))
    _externalize(g, "stdlib_thing")
    g.add_edge("a", "stdlib_thing")
    dsm = build_dsm(g)
    assert "stdlib_thing" not in dsm.ordering
    assert all(
        dsm.ordering[c.row] != "stdlib_thing" and dsm.ordering[c.col] != "stdlib_thing"
        for c in dsm.cells
    )


def test_build_dsm_cells_cover_all_internal_edges():
    g = _g(("a", "b"), ("b", "c"), ("a", "c"))
    dsm = build_dsm(g)
    assert len(dsm.cells) == 3
    edge_names = {(dsm.ordering[c.row], dsm.ordering[c.col]) for c in dsm.cells}
    assert edge_names == {("a", "b"), ("b", "c"), ("a", "c")}


# --- Builder: weight modes ----------------------------------------------------


def test_build_dsm_imports_weight_is_one():
    g = _g(("a", "b"), calls={("a", "b"): 17})
    dsm = build_dsm(g, weight="imports")
    assert dsm.cells[0].weight == 1.0


def test_build_dsm_calls_weight_uses_call_count():
    g = _g(("a", "b"), calls={("a", "b"): 17})
    dsm = build_dsm(g, weight="calls")
    assert dsm.cells[0].weight == 17.0


def test_build_dsm_calls_weight_falls_back_to_one_for_import_only_edges():
    g = _g(("a", "b"))  # call_count=0
    dsm = build_dsm(g, weight="calls")
    assert dsm.cells[0].weight == 1.0


# --- Builder: grouping modes --------------------------------------------------


def test_group_by_topological_puts_dag_nodes_in_topo_order():
    g = _g(("a", "b"), ("b", "c"))
    dsm = build_dsm(g, group_by="topological")
    pos = {n: i for i, n in enumerate(dsm.ordering)}
    assert pos["a"] < pos["b"] < pos["c"]


def test_group_by_topological_isolates_scc_into_its_own_group():
    g = _g(("a", "b"), ("b", "a"), ("b", "c"))
    dsm = build_dsm(g, group_by="topological")
    scc_groups = [g for g in dsm.groups if g.label.startswith("SCC-")]
    assert len(scc_groups) == 1
    assert set(scc_groups[0].members) == {"a", "b"}


def test_group_by_topological_dag_only_no_scc_groups():
    g = _g(("a", "b"), ("b", "c"))
    dsm = build_dsm(g, group_by="topological")
    assert all(not group.label.startswith("SCC-") for group in dsm.groups)


def test_group_by_community_produces_at_least_one_group():
    g = _g(("a", "b"), ("c", "d"), ("e", "f"))
    dsm = build_dsm(g, group_by="community")
    assert len(dsm.groups) >= 1
    members = {m for grp in dsm.groups for m in grp.members}
    assert members == set(dsm.ordering)


def test_group_by_layer_assigns_depths():
    g = _g(("a", "b"), ("b", "c"), ("c", "d"))
    dsm = build_dsm(g, group_by="layer")
    layer_labels = [grp.label for grp in dsm.groups]
    assert any("Layer-0" in lbl for lbl in layer_labels)
    assert any("Layer-3" in lbl for lbl in layer_labels)


# --- Builder: focus filter ----------------------------------------------------


def test_focus_filter_includes_only_neighborhood():
    g = _g(("a", "b"), ("b", "c"), ("c", "d"), ("e", "f"))
    dsm = build_dsm(g, focus="b", focus_depth=1)
    assert set(dsm.ordering) == {"a", "b", "c"}


def test_focus_filter_depth_two_grows_neighborhood():
    g = _g(("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"))
    dsm = build_dsm(g, focus="c", focus_depth=2)
    assert set(dsm.ordering) == {"a", "b", "c", "d", "e"}


def test_focus_filter_unknown_node_returns_empty():
    g = _g(("a", "b"))
    dsm = build_dsm(g, focus="nope")
    assert dsm.ordering == ()


# --- Builder: package filter --------------------------------------------------


def test_package_filter_keeps_prefix_matches():
    g = _g(("pkg.a", "pkg.b"), ("pkg.b", "other.c"))
    dsm = build_dsm(g, package="pkg")
    assert set(dsm.ordering) == {"pkg.a", "pkg.b"}


def test_package_filter_exact_match_is_kept():
    g = nx.DiGraph()
    g.add_node("pkg")
    g.add_edge("pkg", "pkg.sub")
    dsm = build_dsm(g, package="pkg")
    assert "pkg" in dsm.ordering
    assert "pkg.sub" in dsm.ordering


def test_package_filter_does_not_match_substring_inside_name():
    g = _g(("mypkg.a", "pkg.b"))
    dsm = build_dsm(g, package="pkg")
    assert "mypkg.a" not in dsm.ordering


# --- ASCII renderer -----------------------------------------------------------


def test_render_ascii_empty():
    dsm = build_dsm(nx.DiGraph())
    out = render_ascii(dsm)
    assert "empty graph" in out


def test_render_ascii_rejects_oversized():
    g = nx.DiGraph()
    for i in range(10):
        g.add_node(f"m{i}")
    dsm = build_dsm(g)
    out = render_ascii(dsm, max_nodes=5)
    assert "exceeds max_nodes" in out
    assert "--focus" in out


def test_render_ascii_basic_grid_includes_module_names():
    g = _g(("a", "b"), ("b", "c"))
    dsm = build_dsm(g, group_by="topological")
    out = render_ascii(dsm)
    # Assert the legend and grid LINES. `"a" in out` and `"c" in out` were both
    # satisfied by the header string `group=topological` on its own, so they
    # passed with the entire legend and matrix deleted (#441).
    squeezed = [" ".join(line.split()) for line in out.splitlines()]
    assert "1: a" in squeezed
    assert "2: b" in squeezed
    assert "3: c" in squeezed
    # Row i marks an X in the column of each module it imports: a -> b, b -> c.
    assert "1 a \\ X ." in squeezed
    assert "2 b . \\ X" in squeezed
    assert "3 c . . \\" in squeezed


def test_render_ascii_diagonal_uses_backslash_marker():
    g = _g(("a", "b"))
    dsm = build_dsm(g, group_by="topological")
    out = render_ascii(dsm)
    assert "\\" in out


# --- JSON renderer ------------------------------------------------------------


def test_render_json_roundtrip():
    g = _g(("a", "b"), ("b", "c"))
    dsm = build_dsm(g, group_by="community", weight="imports")
    payload = render_json(dsm)
    restored = dsm_from_dict(payload)
    assert restored.ordering == dsm.ordering
    assert restored.cells == dsm.cells
    assert restored.groups == dsm.groups
    assert restored.group_by == dsm.group_by
    assert restored.weight == dsm.weight


def test_render_json_keys_are_stable():
    g = _g(("a", "b"))
    dsm = build_dsm(g)
    payload = render_json(dsm)
    assert set(payload.keys()) == {"n", "group_by", "weight", "ordering", "groups", "cells"}
    assert payload["n"] == 2


# --- Persistence --------------------------------------------------------------


def test_write_and_read_dsm_roundtrip(tmp_path: Path):
    g = _g(("a", "b"), ("b", "c"))
    dsm = build_dsm(g)
    out = tmp_path / "nested" / "dsm.json"
    write_dsm(dsm, out)
    assert out.exists()
    loaded = read_dsm(out)
    assert loaded == dsm


def test_read_dsm_missing_file_returns_none(tmp_path: Path):
    assert read_dsm(tmp_path / "absent.json") is None


# --- Diff ---------------------------------------------------------------------


def test_diff_dsm_identifies_added_edge():
    before = build_dsm(_g(("a", "b")))
    after = build_dsm(_g(("a", "b"), ("b", "c")))
    diff = diff_dsm(before, after)
    assert any(after.ordering[c.row] == "b" and after.ordering[c.col] == "c" for c in diff.added)
    assert not diff.removed


def test_diff_dsm_identifies_removed_edge():
    before = build_dsm(_g(("a", "b"), ("b", "c")))
    after = build_dsm(_g(("a", "b")))
    diff = diff_dsm(before, after)
    assert len(diff.removed) == 1
    assert not diff.added


def test_diff_dsm_detects_new_back_edge():
    before = build_dsm(_g(("a", "b"), ("b", "c")), group_by="topological")
    after = build_dsm(_g(("a", "b"), ("b", "c"), ("c", "a")), group_by="topological")
    diff = diff_dsm(before, after)
    assert diff.new_back_edges, "introducing c->a (a cycle) must be flagged"


def test_diff_dsm_detects_weight_change():
    before = build_dsm(_g(("a", "b"), calls={("a", "b"): 1}), weight="calls")
    after = build_dsm(_g(("a", "b"), calls={("a", "b"): 5}), weight="calls")
    diff = diff_dsm(before, after)
    assert len(diff.weight_changed) == 1
    before_cell, after_cell = diff.weight_changed[0]
    assert before_cell.weight == 1.0
    assert after_cell.weight == 5.0


def test_diff_dsm_reports_node_additions_and_removals():
    before = build_dsm(_g(("a", "b")))
    g_after = _g(("a", "b"))
    g_after.add_node("c")
    after = build_dsm(g_after)
    diff = diff_dsm(before, after)
    assert "c" in diff.nodes_added
    assert not diff.nodes_removed


def test_diff_dsm_identity_diff_is_empty():
    g = _g(("a", "b"), ("b", "c"))
    dsm = build_dsm(g)
    diff = diff_dsm(dsm, dsm)
    assert not diff.added
    assert not diff.removed
    assert not diff.weight_changed
    assert not diff.new_back_edges


def test_render_diff_text_mentions_new_back_edges():
    before = build_dsm(_g(("a", "b"), ("b", "c")), group_by="topological")
    after = build_dsm(_g(("a", "b"), ("b", "c"), ("c", "a")), group_by="topological")
    diff = diff_dsm(before, after)
    text = render_diff_text(diff, after, before)
    assert "back-edge" in text.lower()
    assert "c -> a" in text or "c -&gt; a" in text


def test_render_diff_text_names_removed_edges_from_before_ordering():
    # Removed cells index `before`, not `after`. Rendering them with names (via
    # before) must be correct and symmetric with added edges, and must not be
    # misread as `after` positions even when node removal shifts the ordering.
    before = build_dsm(_g(("a", "b"), ("b", "c")))
    after = build_dsm(_g(("a", "c")))  # b removed; edges a->b and b->c gone
    diff = diff_dsm(before, after)
    text = render_diff_text(diff, after, before)
    assert "Removed edges" in text
    assert "a -> b" in text
    assert "b -> c" in text
    # The bare positional form the old renderer emitted must be gone.
    assert "cell (" not in text


def test_render_diff_text_removed_edges_never_index_after():
    # Regression for the latent blocker: a removed cell's (row, col) is a
    # before-ordering index that can exceed len(after.ordering); rendering must
    # resolve it against before and never raise.
    before = build_dsm(_g(("a", "b"), ("b", "c"), ("c", "d"), ("d", "e")))
    after = build_dsm(_g(("a", "b")))  # most nodes/edges removed
    diff = diff_dsm(before, after)
    text = render_diff_text(diff, after, before)  # must not raise IndexError
    assert "d -> e" in text


def test_group_by_topological_scc_labels_are_sequential():
    # SCC labels must number sequentially (SCC-0, SCC-1, ...) even when DAG
    # groups are interleaved; using len(groups) skipped numbers.
    # Two independent SCCs separated by a DAG singleton chain.
    g = _g(
        ("a", "b"),
        ("b", "a"),  # SCC #0: {a, b}
        ("b", "m"),  # m: DAG singleton between the two SCCs
        ("m", "x"),
        ("x", "y"),
        ("y", "x"),  # SCC #1: {x, y}
    )
    dsm = build_dsm(g, group_by="topological")
    scc_labels = [grp.label for grp in dsm.groups if grp.label.startswith("SCC-")]
    assert scc_labels == [f"SCC-{i}" for i in range(len(scc_labels))]


# --- Model validation ---------------------------------------------------------


def test_dsm_models_are_frozen():
    cell = DSMCell(row=0, col=1, weight=1.0)
    with pytest.raises(ValidationError):
        cell.row = 99  # type: ignore[misc]
    group = DSMGroup(label="x", members=("a",))
    with pytest.raises(ValidationError):
        group.label = "y"  # type: ignore[misc]
    dsm = DSM(
        ordering=("a",), groups=(group,), cells=(cell,), group_by="community", weight="imports"
    )
    with pytest.raises(ValidationError):
        dsm.ordering = ()  # type: ignore[misc]


def test_back_edge_count_is_strict_and_excludes_self_loops():
    """Pin the count as a literal, and put a self-loop in the fixture.

    Every other test of this number recomputes `c.row > c.col` from the same
    cells `build_dsm` produced, so it moves with the implementation. #438
    verified that loosening the predicate to `>=` left the whole suite green,
    which matters because this count is what `archy dsm --diff` uses for
    back-edge regression detection and what `archy render` prints.

    The self-loop is the discriminating half: `b -> b` sits on the diagonal,
    where `row == col`. It is not a back edge, and only a fixture containing
    one can tell a strict comparison from a loose one.
    """
    g = _g(("a", "b"), ("b", "c"), ("c", "a"), ("b", "b"))

    dsm = build_dsm(g, group_by="topological", weight="imports")
    summary = summarize_dsm(dsm)

    assert dsm.ordering == ("a", "b", "c")
    # `c -> a` is the only edge pointing back up the ordering.
    assert summary.back_edge_count == 1
    assert list(summary.back_edges) == [("c", "a")]
    # The diagonal cell exists; it just is not a back edge.
    assert any(c.row == c.col for c in dsm.cells)
