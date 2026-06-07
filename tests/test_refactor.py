from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
from click.testing import CliRunner

from archy.cli import main
from archy.refactor import DEFAULT_MIN_RISK, compute_refactor_priorities


def _g(
    *edges: tuple[str, str],
    external: tuple[str, ...] = (),
    paths: dict[str, str] | None = None,
    cc: dict[str, int] | None = None,
) -> nx.DiGraph:
    """Build a graph with optional per-node path/cc_sum for the hotspot lens."""
    paths = paths or {}
    cc = cc or {}
    g = nx.DiGraph()
    nodes = {u for e in edges for u in e}
    for n in nodes:
        g.add_node(
            n,
            external=n in external,
            path=paths.get(n),
            cc_sum=cc.get(n, 0),
        )
    for u, v in edges:
        g.add_edge(u, v)
    return g


# A central+volatile module like `m` below (imported by x/y/z, importing dep)
# has all three edit-risk terms non-zero; its composite is well above the 0.15
# default floor, which the tests rely on.
def _central_volatile_graph() -> nx.DiGraph:
    return _g(
        ("x", "m"),
        ("y", "m"),
        ("z", "m"),
        ("m", "dep"),
        paths={
            "x": "/repo/x.py",
            "m": "/repo/m.py",
            "dep": "/repo/dep.py",
        },
        cc={"x": 10, "m": 8, "dep": 3},
    )


def test_both_lens_beats_equal_magnitude_single_lens():
    # The core value prop: given two modules with the *same* hotspot magnitude,
    # the one that also clears the structural floor wins, because it earns a
    # contribution from both lenses. `hub` is central+fragile + churned+complex;
    # `leaf` is a pure sink-importer (fan_in 0 -> risk 0) with an identical
    # cc_sum x churn. Same norm_hotspot, so the edit-risk term breaks it.
    g = _g(
        ("x", "hub"),
        ("y", "hub"),
        ("z", "hub"),
        ("hub", "dep"),
        ("leaf", "dep"),
        paths={
            "hub": "/repo/hub.py",
            "leaf": "/repo/leaf.py",
            "dep": "/repo/dep.py",
        },
        cc={"hub": 5, "leaf": 5},
    )
    churn = {"/repo/hub.py": 4, "/repo/leaf.py": 4}
    rows = compute_refactor_priorities(g, churn=churn, min_risk=0.05)
    by_module = {r.module: r for r in rows}

    assert by_module["hub"].lenses == ("hotspot", "edit_risk")
    assert by_module["leaf"].lenses == ("hotspot",)
    assert by_module["hub"].hotspot_score == by_module["leaf"].hotspot_score
    assert rows[0].module == "hub"
    assert by_module["hub"].priority > by_module["leaf"].priority


def test_dominant_single_lens_can_outrank_modest_both_lens():
    # The fused priority is intentionally NOT a strict tier. A giant hotspot at
    # the import-graph leaves (fan_in 0 -> risk 0) is the highest-leverage
    # refactor target and may outrank a module that merely happens to fire on
    # both lenses with small magnitudes. `n` (highest risk) keeps `m` off the
    # risk max so `m` is below the max on *both* lenses; `big` then beats it.
    g = _g(
        ("x", "m"),
        ("y", "m"),
        ("z", "m"),
        ("m", "dep"),
        ("p", "n"),
        ("q", "n"),
        ("r", "n"),
        ("s", "n"),
        ("n", "dep"),
        ("big", "dep"),
        paths={"m": "/m.py", "n": "/n.py", "big": "/big.py"},
        cc={"m": 4, "n": 2, "big": 60},
    )
    churn = {"/m.py": 6, "/big.py": 60}
    rows = compute_refactor_priorities(g, churn=churn, min_risk=0.05)
    by_module = {r.module: r for r in rows}

    assert by_module["m"].lenses == ("hotspot", "edit_risk")
    assert by_module["big"].lenses == ("hotspot",)
    # `big` is below the max on the structural lens (risk 0) yet outranks the
    # both-lens `m` because its hotspot signal dominates. Documents that a
    # dominant single lens is allowed to win.
    assert by_module["big"].priority > by_module["m"].priority
    assert rows.index(by_module["big"]) < rows.index(by_module["m"])


def test_edit_risk_only_when_module_not_churned():
    # `m` is risky but has no churn entry -> structural lens only.
    g = _central_volatile_graph()
    rows = compute_refactor_priorities(g, churn={"/repo/x.py": 5})
    by_module = {r.module: r for r in rows}
    assert by_module["m"].lenses == ("edit_risk",)
    assert by_module["m"].cc_sum == 0
    assert by_module["m"].churn == 0
    assert by_module["m"].edit_risk > 0.0


def test_git_absent_falls_back_to_structural_lens():
    # churn=None mirrors `archy_hotspots`' git-absent behavior: the behavioral
    # lens is skipped entirely and every candidate is edit-risk-only.
    g = _central_volatile_graph()
    rows = compute_refactor_priorities(g, churn=None)
    assert rows  # `m` still surfaces structurally
    assert all(r.lenses == ("edit_risk",) for r in rows)
    assert all(r.hotspot_score == 0 for r in rows)


def test_floor_gates_structural_membership():
    # Just below the module's own risk -> surfaced; just above -> excluded.
    g = _central_volatile_graph()
    risk_m = compute_refactor_priorities(g, churn=None, min_risk=0.0)
    m_risk = next(r.edit_risk for r in risk_m if r.module == "m")

    included = compute_refactor_priorities(g, churn=None, min_risk=m_risk - 0.01)
    assert any(r.module == "m" for r in included)

    excluded = compute_refactor_priorities(g, churn=None, min_risk=m_risk + 0.01)
    assert all(r.module != "m" for r in excluded)


def test_null_when_no_hotspots_and_floor_excludes_all():
    # The honest-null case: nothing is complex+churned (no churn) and the floor
    # is above every module's risk -> empty list, not a manufactured #1.
    g = _central_volatile_graph()
    assert compute_refactor_priorities(g, churn=None, min_risk=1.0) == []


def test_null_on_empty_and_single_node_graphs():
    assert compute_refactor_priorities(nx.DiGraph(), churn={}) == []
    solo = nx.DiGraph()
    solo.add_node("solo", external=False, path="/repo/solo.py", cc_sum=9)
    # Single internal node -> edit-risk is empty; even a churned, complex file
    # is a hotspot, so it still surfaces behaviorally.
    rows = compute_refactor_priorities(solo, churn={"/repo/solo.py": 4})
    assert [r.module for r in rows] == ["solo"]
    assert rows[0].lenses == ("hotspot",)


def test_stable_sink_is_invisible_to_structural_lens():
    # Honest documentation of the inherited edit-risk blind spot: a pure sink
    # imported by everyone but importing nothing has instability 0, so its
    # edit_risk geometric mean is 0 and it never clears the floor. Without git
    # (no behavioral lens to catch its churn) such a "god sink" yields an empty
    # list -> the null is scoped to what the two lenses can see, not a claim
    # that the module is fine to ignore.
    g = _g(
        ("a", "sink"),
        ("b", "sink"),
        ("c", "sink"),
        ("d", "sink"),
        paths={"sink": "/repo/sink.py"},
        cc={"sink": 20},
    )
    # The sink's structural risk is exactly 0 (instability 0 zeroes the geomean).
    rows = compute_refactor_priorities(g, churn=None, min_risk=0.0)
    sink = next(r for r in rows if r.module == "sink")
    assert sink.edit_risk == 0.0
    # So any positive floor excludes it, and with no churn the result is empty -
    # the null is scoped to the lenses' coverage, not a clean bill of health.
    assert compute_refactor_priorities(g, churn=None, min_risk=0.05) == []


def test_rationale_names_the_firing_lenses():
    g = _central_volatile_graph()
    churn = {"/repo/m.py": 12, "/repo/x.py": 30}
    rows = compute_refactor_priorities(g, churn=churn)
    by_module = {r.module: r for r in rows}
    assert "Both a complexity" in by_module["m"].rationale
    assert "low structural edit-risk" in by_module["x"].rationale


def test_external_predecessors_excluded_from_fan_in():
    g = _g(
        ("a", "m"),
        ("ext", "m"),
        ("m", "dep"),
        external=("ext",),
    )
    rows = compute_refactor_priorities(g, churn=None, min_risk=0.0)
    m = next(r for r in rows if r.module == "m")
    # Only the internal importer `a` counts toward fan_in, not `ext`.
    assert m.fan_in == 1


def test_default_min_risk_is_the_documented_floor():
    # Guard against the documented default drifting silently.
    assert DEFAULT_MIN_RISK == 0.15


# --- CLI surface -----------------------------------------------------------


def _structural_project(tmp_path: Path) -> Path:
    # A central+fragile `hub` with no git history, so only the structural lens
    # runs (git_available=False).
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "dep.py").write_text("")
    (pkg / "hub.py").write_text("from pkg.dep import thing\n")
    for name in ("a", "b", "c"):
        (pkg / f"{name}.py").write_text("from pkg.hub import x\n")
    return tmp_path


def test_cli_text_structural_only_without_git(tmp_path: Path):
    project = _structural_project(tmp_path)
    result = CliRunner().invoke(
        main, ["what-to-refactor-next", str(project), "--min-risk", "0.1"]
    )
    assert result.exit_code == 0
    assert "structural-only" in result.output
    assert "pkg.hub" in result.output


def test_cli_json_includes_note_and_git_available(tmp_path: Path):
    project = _structural_project(tmp_path)
    result = CliRunner().invoke(
        main,
        ["what-to-refactor-next", str(project), "--min-risk", "0.1", "--format", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["git_available"] is False
    # The note is machine-readable on the JSON surface, at parity with MCP.
    assert payload["note"] is not None
    assert "structural-only" in payload["note"]
    assert payload["priorities"]


def test_cli_validates_top_and_min_risk(tmp_path: Path):
    project = _structural_project(tmp_path)
    bad_top = CliRunner().invoke(
        main, ["what-to-refactor-next", str(project), "--top", "0"]
    )
    assert bad_top.exit_code != 0
    assert "--top" in bad_top.output
    bad_risk = CliRunner().invoke(
        main, ["what-to-refactor-next", str(project), "--min-risk", "1.5"]
    )
    assert bad_risk.exit_code != 0
    assert "--min-risk" in bad_risk.output
