from __future__ import annotations

from pathlib import Path

from archy.diff import compute_diff, read_snapshot, take_snapshot, write_snapshot
from archy.graph import build_graph


def _make_clean(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    return tmp_path


def test_snapshot_round_trip_through_disk(tmp_path: Path):
    project = _make_clean(tmp_path)
    target = project / ".archy" / "baseline.json"
    write_snapshot(take_snapshot(build_graph(project)), target)
    loaded = read_snapshot(target)
    assert loaded is not None
    assert loaded.score.overall > 0


def test_read_snapshot_returns_none_when_missing(tmp_path: Path):
    assert read_snapshot(tmp_path / ".archy" / "baseline.json") is None


def test_diff_clean_baseline_clean_current_is_zero(tmp_path: Path):
    project = _make_clean(tmp_path)
    g = build_graph(project)
    snap = take_snapshot(g)
    result = compute_diff(snap, snap)
    assert result.score_delta.overall == 0.0
    assert result.cycles.added == ()
    assert result.cycles.resolved == ()
    assert result.violations.added == ()
    assert result.violations.resolved == ()


def test_diff_flags_newly_introduced_cycle(tmp_path: Path):
    project = _make_clean(tmp_path)
    baseline = take_snapshot(build_graph(project))
    # Adding `from pkg.a` here turns the existing a -> b chain into a 2-cycle,
    # which is what we want compute_diff to surface as cycles.added.
    (project / "pkg" / "b.py").write_text("from pkg.a import thing\n")
    current = take_snapshot(build_graph(project))
    result = compute_diff(baseline, current)
    assert result.score_delta.acyclicity < 0
    assert len(result.cycles.added) == 1
    assert set(result.cycles.added[0].modules) == {"pkg.a", "pkg.b"}
    assert result.cycles.resolved == ()


def test_diff_flags_resolved_cycle(tmp_path: Path):
    project = _make_clean(tmp_path)
    # The baseline must already contain the cycle; otherwise compute_diff would
    # flag the post-edit state as `added` rather than the baseline-only state
    # as `resolved`.
    (project / "pkg" / "b.py").write_text("from pkg.a import thing\n")
    baseline = take_snapshot(build_graph(project))
    (project / "pkg" / "b.py").write_text("")
    current = take_snapshot(build_graph(project))
    result = compute_diff(baseline, current)
    assert result.cycles.added == ()
    assert len(result.cycles.resolved) == 1


def test_diff_flags_newly_introduced_violation(tmp_path: Path):
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
    config_path = tmp_path / "archy.yaml"
    config_path.write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    baseline = take_snapshot(build_graph(tmp_path), config_path=config_path)
    assert baseline.violations == ()
    # core -> cli is the forbidden direction; this edit lets find_violations
    # report a violation that compute_diff should flag as violations.added.
    (core / "api.py").write_text("from myapp.cli.runner import go\n")
    current = take_snapshot(build_graph(tmp_path), config_path=config_path)
    result = compute_diff(baseline, current)
    assert len(result.violations.added) == 1
    [added] = result.violations.added
    assert added.rule.from_layer == "core"
    assert added.rule.to_layer == "cli"
    assert result.violations.resolved == ()
