from __future__ import annotations

from pathlib import Path

from archy.diff import compute_diff, take_snapshot
from archy.graph import build_graph
from archy.simulate import find_simulate


def _internal(root: Path):
    g = build_graph(root)
    g.remove_nodes_from([n for n, d in g.nodes(data=True) if d.get("external")])
    return g


def _make_pkg(tmp_path: Path, files: dict[str, str]) -> Path:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name, body in files.items():
        (pkg / name).write_text(body)
    return tmp_path


# --- delta classification -----------------------------------------------------


def test_add_edge_that_closes_a_cycle_is_reported(tmp_path: Path):
    # a -> b, c -> a. Adding b -> c closes the cycle a -> b -> c -> a.
    project = _make_pkg(
        tmp_path,
        {"a.py": "from app.b import x\n", "b.py": "", "c.py": "from app.a import y\n"},
    )
    g = _internal(project)
    result = find_simulate(g, add=[("app.b", "app.c")], remove=[], project_root=project)
    assert [(e.source, e.target) for e in result.applied.added_edges] == [("app.b", "app.c")]
    [cycle] = result.cycles.added
    assert set(cycle.modules) == {"app.a", "app.b", "app.c"}
    assert result.score_delta.acyclicity < 0
    assert result.propagation_cost.after > result.propagation_cost.before


def test_remove_edge_that_breaks_a_cycle_is_reported(tmp_path: Path):
    project = _make_pkg(
        tmp_path,
        {"a.py": "from app.b import x\n", "b.py": "from app.a import y\n"},
    )
    g = _internal(project)
    result = find_simulate(g, add=[], remove=[("app.a", "app.b")], project_root=project)
    [cycle] = result.cycles.resolved
    assert set(cycle.modules) == {"app.a", "app.b"}


def test_unresolved_endpoint_reported_and_skipped(tmp_path: Path):
    project = _make_pkg(tmp_path, {"a.py": "", "b.py": ""})
    g = _internal(project)
    result = find_simulate(g, add=[("app.a", "app.nope")], remove=[], project_root=project)
    assert "app.nope" in result.applied.unresolved
    assert result.applied.added_edges == ()


def test_self_loop_removal_is_simulable(tmp_path: Path):
    # archy's resolver does produce self-edges (e.g. `from . import a as a`),
    # so removing one must be simulable, not rejected. Regression for a bug the
    # oracle bench caught on rich.box.
    project = _make_pkg(tmp_path, {"a.py": "from app import a as a\n"})
    g = _internal(project)
    assert g.has_edge("app.a", "app.a")  # the self-edge exists
    result = find_simulate(g, add=[], remove=[("app.a", "app.a")], project_root=project)
    assert [(e.source, e.target) for e in result.applied.removed_edges] == [("app.a", "app.a")]
    assert result.applied.rejected == ()


def test_duplicate_remove_does_not_crash(tmp_path: Path):
    # Regression: a repeated remove used to call remove_edge twice and raise.
    project = _make_pkg(tmp_path, {"a.py": "from app.b import x\n", "b.py": ""})
    g = _internal(project)
    result = find_simulate(
        g, add=[], remove=[("app.a", "app.b"), ("app.a", "app.b")], project_root=project
    )
    assert [(e.source, e.target) for e in result.applied.removed_edges] == [("app.a", "app.b")]


def test_duplicate_add_is_deduped_in_echo(tmp_path: Path):
    project = _make_pkg(tmp_path, {"a.py": "", "b.py": ""})
    g = _internal(project)
    result = find_simulate(
        g, add=[("app.a", "app.b"), ("app.a", "app.b")], remove=[], project_root=project
    )
    assert [(e.source, e.target) for e in result.applied.added_edges] == [("app.a", "app.b")]


def test_add_and_remove_same_edge_cancels(tmp_path: Path):
    project = _make_pkg(tmp_path, {"a.py": "from app.b import x\n", "b.py": ""})
    g = _internal(project)
    result = find_simulate(
        g, add=[("app.a", "app.b")], remove=[("app.a", "app.b")], project_root=project
    )
    assert result.applied.added_edges == ()
    assert result.applied.removed_edges == ()
    assert any("both add and remove" in r for r in result.applied.rejected)
    # The edge still exists, so nothing structurally changed.
    assert result.cycles.added == () and result.cycles.resolved == ()


def test_no_op_add_and_remove_classified(tmp_path: Path):
    project = _make_pkg(tmp_path, {"a.py": "from app.b import x\n", "b.py": ""})
    g = _internal(project)
    result = find_simulate(
        g,
        add=[("app.a", "app.b")],  # already exists
        remove=[("app.b", "app.a")],  # does not exist
        project_root=project,
    )
    assert [(e.source, e.target) for e in result.applied.no_op_adds] == [("app.a", "app.b")]
    assert [(e.source, e.target) for e in result.applied.no_op_removes] == [("app.b", "app.a")]
    assert result.applied.added_edges == ()
    assert result.applied.removed_edges == ()


def test_added_layer_violation_is_surfaced(tmp_path: Path):
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for sub in ("core", "cli"):
        (pkg / sub).mkdir()
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "core" / "api.py").write_text("")
    (pkg / "cli" / "runner.py").write_text("")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    g = _internal(tmp_path)
    result = find_simulate(
        g,
        add=[("myapp.core.api", "myapp.cli.runner")],
        remove=[],
        config_path=tmp_path / "archy.yaml",
        project_root=tmp_path,
    )
    [v] = result.violations.added
    assert (v.rule.from_layer, v.rule.to_layer) == ("core", "cli")


def test_prompts_are_conditional_mood(tmp_path: Path):
    project = _make_pkg(
        tmp_path,
        {"a.py": "from app.b import x\n", "b.py": "", "c.py": "from app.a import y\n"},
    )
    g = _internal(project)
    result = find_simulate(g, add=[("app.b", "app.c")], remove=[], project_root=project)
    cycle_item = next(i for i in result.summary.top_regressions if i.kind == "cycle_added")
    assert "would" in cycle_item.prompt.lower()
    assert "Proceed" in cycle_item.prompt


# --- the validation oracle: simulate(delta) == diff(after writing delta) ------


def test_oracle_add_matches_real_edit(tmp_path: Path):
    # Three sinks so an added edge is a clean, re-export-free import.
    project = _make_pkg(tmp_path, {"a.py": "", "b.py": "", "c.py": ""})
    g0 = _internal(project)
    sim = find_simulate(g0, add=[("app.a", "app.b")], remove=[], project_root=project)

    # Materialize the simulated edit so the oracle compares against a real re-parse.
    (project / "app" / "a.py").write_text("import app.b\n")
    g1 = _internal(project)
    real = compute_diff(take_snapshot(g0), take_snapshot(g1))

    assert sim.cycles == real.cycles
    assert sim.violations == real.violations
    assert sim.sdp_violations == real.sdp_violations
    assert sim.score_delta == real.score_delta


def test_oracle_remove_matches_real_edit(tmp_path: Path):
    project = _make_pkg(
        tmp_path,
        {"a.py": "from app.b import x\n", "b.py": "from app.a import y\n"},
    )
    g0 = _internal(project)
    sim = find_simulate(g0, add=[], remove=[("app.a", "app.b")], project_root=project)

    # Materialize the simulated removal so the oracle compares against a real re-parse.
    (project / "app" / "a.py").write_text("")
    g1 = _internal(project)
    real = compute_diff(take_snapshot(g0), take_snapshot(g1))

    assert sim.cycles == real.cycles
    assert sim.violations == real.violations
    assert sim.score_delta == real.score_delta


def test_oracle_complexity_axis_is_always_zero_for_edge_delta(tmp_path: Path):
    # The equivalence boundary: an import-only delta cannot move the
    # content-derived complexity axis, on either the simulated or the real side.
    project = _make_pkg(tmp_path, {"a.py": "", "b.py": ""})
    g0 = _internal(project)
    sim = find_simulate(g0, add=[("app.a", "app.b")], remove=[], project_root=project)
    assert sim.score_delta.complexity == 0.0
