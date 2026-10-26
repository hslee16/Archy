from __future__ import annotations

from pathlib import Path

from archy.diff import compute_diff, take_snapshot
from archy.diff_summary import summarize_diff
from archy.graph import build_graph
from archy.layers import ReachViolation, RequiredRule


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


def test_every_summary_item_carries_a_judgment_prompt(tmp_path: Path):
    summary = _make_cycle_summary(tmp_path)
    items = summary.top_regressions + summary.top_improvements
    assert items  # the new-cycle diff produces at least one regression
    for item in items:
        assert item.prompt
        assert item.prompt != item.description


def test_new_cycle_prompt_is_a_causal_question(tmp_path: Path):
    summary = _make_cycle_summary(tmp_path)
    [cycle_item] = [it for it in summary.top_regressions if it.kind == "cycle_added"]
    # The prompt states the "because" (the cycle) and asks for a decision,
    # rather than just reporting a number.
    assert "cycle" in cycle_item.prompt.lower()
    assert "?" in cycle_item.prompt


def test_layer_violation_prompt_names_the_forbidden_boundary(tmp_path: Path):
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    core = pkg / "core"
    core.mkdir()
    (core / "__init__.py").write_text("")
    (core / "api.py").write_text("")
    cli = pkg / "cli"
    cli.mkdir()
    (cli / "__init__.py").write_text("")
    (cli / "runner.py").write_text("")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    config_path = tmp_path / "archy.yaml"
    baseline = take_snapshot(build_graph(tmp_path), config_path=config_path)
    (core / "api.py").write_text("from myapp.cli.runner import go\n")
    current_graph = build_graph(tmp_path)
    current = take_snapshot(current_graph, config_path=config_path)
    summary = summarize_diff(compute_diff(baseline, current), current_graph)
    [v_item] = [it for it in summary.top_regressions if it.kind == "violation_added"]
    assert "core -> cli" in v_item.prompt
    assert "?" in v_item.prompt


def test_dead_entrypoints_rule_is_named_with_its_scope_and_cause(tmp_path: Path):
    """A dead rule has no module, so the summary names the rule, `within`
    included (two scoped rules would otherwise read alike), and gives the
    detail as the cause. "No longer reaches" would send the reader looking for
    a removed import when the cause is a config or pyproject problem."""
    project = _make_clean(tmp_path)
    graph = build_graph(project)
    baseline = take_snapshot(graph)
    rule = RequiredRule(source="@entrypoints", must_reach="pkg.b", within="app.**")
    dead = ReachViolation(rule=rule, module=None, detail="pyproject.toml could not be read")
    current = baseline.model_copy(update={"required_violations": (dead,)})

    summary = summarize_diff(compute_diff(baseline, current), graph)

    [item] = [i for i in summary.top_regressions if i.kind == "required_reach_violation_added"]
    assert item.description == (
        "required-reach rule can no longer be checked "
        "(@entrypoints within app.** must reach pkg.b): pyproject.toml could not be read"
    )
    assert "bootstrap import" not in item.prompt


def test_headline_without_a_baseline_score_says_so(tmp_path: Path):
    """A findings-only baseline has no score, so the headline cannot state an
    overall delta and must not invent one."""
    project = _make_clean(tmp_path)
    graph = build_graph(project)
    current = take_snapshot(graph)
    baseline = current.model_copy(update={"score": None})

    summary = summarize_diff(compute_diff(baseline, current), graph)

    assert summary.headline.startswith("score not in baseline")
    assert not any(i.kind.startswith("score_component") for i in summary.top_regressions)
