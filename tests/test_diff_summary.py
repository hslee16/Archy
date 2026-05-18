from __future__ import annotations

from pathlib import Path

from archy.diff import compute_diff, take_snapshot
from archy.diff_summary import summarize_diff
from archy.graph import build_graph


def _make_clean(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    return tmp_path


def _make_cycle_summary(tmp_path: Path):
    project = _make_clean(tmp_path)
    baseline = take_snapshot(build_graph(project))
    (project / "pkg" / "b.py").write_text("from pkg.a import thing\n")
    current_graph = build_graph(project)
    current = take_snapshot(current_graph)
    diff = compute_diff(baseline, current)
    return summarize_diff(diff, current_graph)


def test_summary_on_clean_diff_is_empty_lists(tmp_path: Path):
    project = _make_clean(tmp_path)
    g = build_graph(project)
    snap = take_snapshot(g)
    diff = compute_diff(snap, snap)
    summary = summarize_diff(diff, g)
    assert summary.top_regressions == ()
    assert summary.top_improvements == ()
    assert "overall +0.000" in summary.headline


def test_summary_ranks_new_cycle_as_regression(tmp_path: Path):
    summary = _make_cycle_summary(tmp_path)
    kinds = [item.kind for item in summary.top_regressions]
    assert "cycle_added" in kinds
    assert summary.top_improvements == () or all(
        item.kind != "cycle_added" for item in summary.top_improvements
    )


def test_summary_ranks_resolved_cycle_as_improvement(tmp_path: Path):
    project = _make_clean(tmp_path)
    (project / "pkg" / "b.py").write_text("from pkg.a import thing\n")
    baseline = take_snapshot(build_graph(project))
    (project / "pkg" / "b.py").write_text("")
    current_graph = build_graph(project)
    current = take_snapshot(current_graph)
    diff = compute_diff(baseline, current)
    summary = summarize_diff(diff, current_graph)
    kinds = [item.kind for item in summary.top_improvements]
    assert "cycle_resolved" in kinds


def test_summary_top_n_cutoff(tmp_path: Path):
    # Build a project with many small cycles so the regression list overflows top_n.
    project = tmp_path
    pkg = project / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    pair_count = 8
    for i in range(pair_count):
        (pkg / f"a{i}.py").write_text("")
        (pkg / f"b{i}.py").write_text("")
    baseline = take_snapshot(build_graph(project))
    for i in range(pair_count):
        (pkg / f"a{i}.py").write_text(f"from pkg.b{i} import thing\n")
        (pkg / f"b{i}.py").write_text(f"from pkg.a{i} import thing\n")
    current_graph = build_graph(project)
    current = take_snapshot(current_graph)
    diff = compute_diff(baseline, current)
    summary = summarize_diff(diff, current_graph, top_n=3)
    assert len(summary.top_regressions) <= 3


def test_summary_orders_by_risk_descending(tmp_path: Path):
    summary = _make_cycle_summary(tmp_path)
    risks = [item.risk for item in summary.top_regressions]
    assert risks == sorted(risks, reverse=True)


def test_summary_headline_mentions_overall_delta(tmp_path: Path):
    summary = _make_cycle_summary(tmp_path)
    assert "overall" in summary.headline
    assert "cycles +1/-0" in summary.headline


def test_score_component_drop_appears_as_regression(tmp_path: Path):
    summary = _make_cycle_summary(tmp_path)
    kinds = {item.kind for item in summary.top_regressions}
    assert "score_component_drop" in kinds


def test_giant_cycle_description_is_truncated(tmp_path: Path):
    # Builds a 10-module cycle so the description hits the truncation path.
    # `modules` field must still carry the full list.
    project = tmp_path
    pkg = project / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    n = 10
    for i in range(n):
        (pkg / f"m{i}.py").write_text("")
    baseline = take_snapshot(build_graph(project))
    for i in range(n):
        nxt = (i + 1) % n
        (pkg / f"m{i}.py").write_text(f"from pkg.m{nxt} import x\n")
    current_graph = build_graph(project)
    current = take_snapshot(current_graph)
    diff = compute_diff(baseline, current)
    summary = summarize_diff(diff, current_graph)
    [cycle_item] = [it for it in summary.top_regressions if it.kind == "cycle_added"]
    assert "more)" in cycle_item.description
    assert len(cycle_item.modules) == n
