#!/usr/bin/env python
"""Score-delta DIRECTION validation on corpus-scale graphs (issue #178).

archy's core pitch is the agent edit loop: *did my edit improve or regress
structure?* Every other bench scores each snapshot once (`run.py`), measures
prevalence over wild commits without a known sign (`inloop_prevalence.py`), or
checks prediction fidelity simulate==diff (`simulate_oracle.py`). None of them
applies a *known-sign* structural mutation to a real-world-sized graph and
asserts the score moves the correct way. The unit tests cover direction only on
3-8 node toy graphs where a single cycle dominates `tangle_ratio`; they cannot
catch a dilution failure on a 281-module graph. This harness closes that gap.

What it asserts (the monotonic, guaranteed-correct signals):

* Inject one back-edge that creates exactly one 2-cycle -> the `acyclicity`
  axis STRICTLY drops and `archy_diff` reports `cycles.added == 1` with the
  correct module pair.
* On a SEPARATE graph built natively with a 2-cycle, removing it -> `acyclicity`
  STRICTLY rises and `archy_diff` reports `cycles.resolved == 1`. A distinct
  scenario, not the inject pair re-diffed backwards: `compute_diff` is
  antisymmetric, so re-diffing the same two snapshots swapped would pass by
  construction and prove nothing.
* Add a forbidden layer edge against a synthetic `archy.yaml` -> `archy_diff`
  reports `violations.added == 1`; remove it -> `violations.resolved == 1`.

What it deliberately does NOT assert -- the pinned design decision for #178:

  Should `overall` be allowed to RISE when a single cycle is added to a large
  graph? Yes, by design. `overall` is the geometric mean of five axes. A single
  back-edge moves `acyclicity` by `tangle_ratio = nodes_in_cycles / total`,
  which is intentionally tiny on a large graph (a small isolated cycle in a
  10k-module repo is a smaller pathology than the same cycle in a 5-module one;
  see score.py). The same edge also perturbs modularity, equality, and depth,
  so `overall`'s per-edge sign is NOT guaranteed at scale. That is exactly why
  `archy_diff` surfaces `acyclicity` and `cycles.added` INDEPENDENTLY of
  `overall`: an agent reads those for the direction signal, not the composite.
  This bench therefore HARD-asserts the acyclicity axis and the cycle counts,
  and only MEASURES + REPORTS the `overall` delta so the dilution stays
  visible (and a future formula change that flips the *acyclicity* response is
  caught by the asserts, not by the noisy composite).

Usage:
    uv run --with networkx --with pyyaml --with tree-sitter \
        --with tree-sitter-python --with pydantic python bench/delta_direction.py
    uv run ... python bench/delta_direction.py --stdout
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from archy.cycles import find_cycles  # noqa: E402
from archy.diff import compute_diff, take_snapshot  # noqa: E402
from archy.graph import build_graph  # noqa: E402

REPLAY = ROOT / "bench" / "replay_cache"
RESULTS = ROOT / "bench" / "delta_direction_results.md"

# Synthetic graph sizes that bracket the "real-world-sized, not a toy" regime
# the unit tests can't reach. 281 was the dilution example in the #178 probe.
SYNTHETIC_SIZES = (300, 1500, 5000)


# --- graph construction & mutation -------------------------------------------


def internal_graph(root: Path) -> nx.DiGraph:
    """Build a project's import graph with external nodes dropped."""
    g = build_graph(root)
    g.remove_nodes_from([n for n, d in g.nodes(data=True) if d.get("external")])
    return g


def synthetic_dag(n: int) -> nx.DiGraph:
    """A deterministic DAG of `n` nodes with light fan-out.

    Every edge points from a higher to a lower index, so the base graph is
    acyclic and any low->high edge is a back-edge. No RNG: the same `n` always
    yields the same graph, keeping the bench reproducible.
    """
    g: nx.DiGraph = nx.DiGraph()
    for i in range(n):
        g.add_node(f"m{i}")
    for i in range(1, n):
        g.add_edge(f"m{i}", f"m{i - 1}", kinds=("import",), lines=(1,))
        if i >= 2:
            g.add_edge(f"m{i}", f"m{i - 2}", kinds=("import",), lines=(1,))
    return g


def synthetic_layered(per_layer: int = 25, layers: int = 4) -> nx.DiGraph:
    """A layered DAG named `L{k}.m{i}` so a glob `archy.yaml` can group it.

    Base edges only point from a higher layer index to a lower one (`l1 -> l0`),
    which is the *allowed* direction. A forbidden `l0 -> l1` edge is added by the
    mutation, never present in the base.
    """
    g: nx.DiGraph = nx.DiGraph()
    for k in range(layers):
        for i in range(per_layer):
            g.add_node(f"L{k}.m{i}")
    for k in range(1, layers):
        for i in range(per_layer):
            g.add_edge(f"L{k}.m{i}", f"L{k - 1}.m{i}", kinds=("import",), lines=(1,))
    return g


def inject_two_cycle(g: nx.DiGraph) -> tuple[nx.DiGraph, tuple[str, str]]:
    """Return `(g1, (u, v))` where `g1` adds one edge forming exactly the 2-cycle {u, v}.

    Adds the reverse-direction edge `v -> u` to an existing acyclic edge `u -> v`
    while KEEPING `u -> v`, so the result is a fresh back-edge layered on existing
    structure -- exactly how an agent introduces a back-edge between two real
    modules (this is an edge addition, not an edge reversal). Verifies the
    strongly-connected component containing `u` is exactly `{u, v}`, so the
    injection adds a single, unambiguous cycle whose module pair we can assert on.
    Raises if no clean site exists (none of the repos here hit that). On the
    regular synthetic graphs this deterministically picks the lowest-index leaf
    pair `m1 <-> m0`; the acyclicity delta (`tangle_ratio = 2/n`) is
    site-independent, while the measured `overall` delta does depend on the
    chosen site's degree.
    """
    cyclic = {m for c in find_cycles(g) for m in c.modules}
    for u, v in sorted(g.edges()):
        if u in cyclic or v in cyclic or g.has_edge(v, u):
            continue
        g1 = g.copy()
        g1.add_edge(v, u, kinds=("import",), lines=(1,))
        scc = next(s for s in nx.strongly_connected_components(g1) if u in s)
        if scc == {u, v}:
            return g1, (u, v)
    raise RuntimeError("no clean 2-cycle injection site found")


# --- direction assertions ----------------------------------------------------


def check_cycle_direction(g0: nx.DiGraph, label: str) -> dict:
    """Inject one 2-cycle; assert the acyclicity axis and cycles.added move correctly.

    Returns a row of measured magnitudes (acyclicity delta, overall delta, and
    the fraction of the acyclicity signal that survives into `overall`) for the
    report. The resolved/break direction is covered separately by
    `check_resolved_direction`: re-diffing this inject pair backwards would pass
    by construction because `compute_diff` is antisymmetric, so it would add no
    independent coverage.
    """
    g1, (u, v) = inject_two_cycle(g0)
    pair = {u, v}
    base = take_snapshot(g0)
    worse = take_snapshot(g1)

    inj = compute_diff(base, worse)
    # The core contract: a one-edge structural regression must register on the
    # acyclicity axis even as a single cycle in a large, diluting graph.
    assert inj.score_delta.acyclicity < 0, (
        f"{label}: injecting a cycle did NOT lower acyclicity "
        f"(delta={inj.score_delta.acyclicity:+.6f})"
    )
    assert len(inj.cycles.added) == 1 and set(inj.cycles.added[0].modules) == pair, (
        f"{label}: expected exactly one added cycle {sorted(pair)}, "
        f"got {[sorted(c.modules) for c in inj.cycles.added]}"
    )
    assert inj.cycles.resolved == (), f"{label}: spurious resolved cycles on inject"

    d_acy = inj.score_delta.acyclicity
    d_overall = inj.score_delta.overall
    return {
        "label": label,
        "modules": g0.number_of_nodes(),
        "edges": g0.number_of_edges(),
        "pair": f"{u} <-> {v}",
        "d_acyclicity": d_acy,
        "d_overall": d_overall,
        # Fraction of the acyclicity regression that survives into `overall`
        # (|overall delta| / |acyclicity delta|). A LOW value means the composite
        # heavily dilutes the signal; a high value means little dilution.
        "survival": abs(d_overall) / abs(d_acy) if d_acy else 0.0,
    }


def check_resolved_direction() -> dict:
    """Independently verify the resolved/break path on a natively-cyclic graph.

    Distinct from the inject path on purpose. `compute_diff(A, B)` and
    `compute_diff(B, A)` are exact mirrors, so re-diffing an injected pair
    backwards proves nothing. Here the baseline graph is built WITH a 2-cycle on
    an interior pair (`m100 <-> m101`, not the leaf the inject path mutates) and
    the current graph WITHOUT it, so `cycles.resolved` and the positive
    score-delta sign are exercised on their own inputs.
    """
    clean = synthetic_dag(200)
    a, b = "m100", "m101"
    # m101 -> m100 already exists (the m{i} -> m{i-1} chain); adding m100 -> m101
    # closes exactly the 2-cycle {m100, m101}.
    assert clean.has_edge(b, a) and not clean.has_edge(a, b)
    cyclic = clean.copy()
    cyclic.add_edge(a, b, kinds=("import",), lines=(1,))

    base = take_snapshot(cyclic)  # baseline HAS the cycle
    fixed = take_snapshot(clean)  # current does NOT
    rep = compute_diff(base, fixed)

    assert rep.score_delta.acyclicity > 0, (
        f"resolved: breaking a cycle did NOT raise acyclicity "
        f"(delta={rep.score_delta.acyclicity:+.6f})"
    )
    assert len(rep.cycles.resolved) == 1 and set(rep.cycles.resolved[0].modules) == {a, b}, (
        f"resolved: expected exactly one resolved cycle {[a, b]}, "
        f"got {[sorted(c.modules) for c in rep.cycles.resolved]}"
    )
    assert rep.cycles.added == (), "resolved: spurious added cycles on break"

    return {"pair": f"{a} <-> {b}", "modules": clean.number_of_nodes()}


def check_violation_direction() -> dict:
    """Add then remove a forbidden layer edge; assert violations.added/resolved direction.

    Synthetic because the vendored corpus carries no `archy.yaml` (same approach
    as `simulate_oracle._violation_smoke`).
    """
    g0 = synthetic_layered()
    cfgdir = Path(tempfile.mkdtemp())
    cfg = cfgdir / "archy.yaml"
    cfg.write_text(
        "layers:\n"
        + "".join(f"  l{k}: {{modules: [L{k}.**]}}\n" for k in range(4))
        + "forbid:\n  - {from: l0, to: l1}\n"
    )
    # A forbidden l0 -> l1 edge that does not already exist.
    a, b = "L0.m0", "L1.m0"
    assert not g0.has_edge(a, b)
    g1 = g0.copy()
    g1.add_edge(a, b, kinds=("import",), lines=(1,))

    base = take_snapshot(g0, config_path=cfg)
    worse = take_snapshot(g1, config_path=cfg)

    add = compute_diff(base, worse)
    assert len(add.violations.added) == 1, (
        f"forbidden edge added: expected 1 violation, got {len(add.violations.added)}"
    )
    assert add.violations.resolved == ()

    rem = compute_diff(worse, base)
    assert len(rem.violations.resolved) == 1, (
        f"forbidden edge removed: expected 1 resolved, got {len(rem.violations.resolved)}"
    )
    assert rem.violations.added == ()

    v = add.violations.added[0]
    return {"rule": f"{v.rule.from_layer}->{v.rule.to_layer}", "edge": f"{v.source}->{v.target}"}


# --- driver & report ---------------------------------------------------------


def run() -> tuple[list[dict], dict, dict]:
    rows: list[dict] = []

    # Synthetic scale always runs (corpus-independent floor for CI parity).
    for n in SYNTHETIC_SIZES:
        rows.append(check_cycle_direction(synthetic_dag(n), label=f"synthetic-{n}"))

    # Real corpus when present (bench/replay_cache is gitignored / regenerable).
    if REPLAY.is_dir():
        for repo in sorted(p for p in REPLAY.iterdir() if p.is_dir()):
            g = internal_graph(repo)
            if g.number_of_edges() == 0:
                continue
            try:
                rows.append(check_cycle_direction(g, label=repo.name))
            except RuntimeError as exc:
                print(f"# {repo.name}: skipped ({exc})", file=sys.stderr)

    resolved = check_resolved_direction()
    violation = check_violation_direction()
    return rows, resolved, violation


def format_report(rows: list[dict], resolved: dict, violation: dict) -> str:
    out = ["# Score-delta direction validation (issue #178)", ""]
    out.append(
        "Each row injects exactly one 2-cycle and asserts `acyclicity` strictly "
        "drops with `cycles.added == 1`, then reports the `overall` delta so "
        "per-edge dilution at scale stays visible. The break direction is "
        "asserted separately (see below) on a graph built natively with a cycle, "
        "since `compute_diff` is antisymmetric and re-diffing an inject pair "
        "backwards would pass by construction."
    )
    out.append("")
    out.append(
        "| graph | modules | edges | acyclicity delta | overall delta | overall/acy survival |"
    )
    out.append("|---|--:|--:|--:|--:|--:|")
    for r in sorted(rows, key=lambda r: -r["modules"]):
        out.append(
            f"| {r['label']} | {r['modules']} | {r['edges']} | "
            f"{r['d_acyclicity']:+.6f} | {r['d_overall']:+.6f} | "
            f"{r['survival']:.3f} |"
        )
    out.append("")
    # "overall/acy survival" = |overall delta| / |acyclicity delta|: the fraction
    # of the one-edge acyclicity regression that reaches the composite. LOW means
    # heavily diluted. Report the range and the broad (NOT monotonic) size trend
    # straight from the data, and name a real size-inversion so it is not oversold.
    hi_s = max(rows, key=lambda r: r["survival"])
    lo_s = min(rows, key=lambda r: r["survival"])
    biggest = sorted(rows, key=lambda r: -r["modules"])[:3]
    biggest_vals = ", ".join(f"{g['survival']:.1%}" for g in biggest)
    out.append(
        f"The `overall/acy survival` column is the fraction of the one-edge "
        f"acyclicity regression that reaches `overall`. It is small across all "
        f"{len(rows)} graphs: from {hi_s['survival']:.0%} ({hi_s['label']}, "
        f"{hi_s['modules']} modules) down to {lo_s['survival']:.1%} ({lo_s['label']}, "
        f"{lo_s['modules']} modules). The three largest graphs "
        f"({', '.join(g['label'] for g in biggest)}) carry the least ({biggest_vals}), "
        f"so a single back-edge moves `overall` proportionally less as the graph grows."
    )
    by_size = sorted(rows, key=lambda r: r["modules"])
    inversion = next(
        (
            (sm, lg)
            for i, sm in enumerate(by_size)
            for lg in by_size[i + 1 :]
            if sm["survival"] < lg["survival"]
        ),
        None,
    )
    if inversion:
        sm, lg = inversion
        out.append(
            f"The relationship is not monotonic: survival also depends on how the edge "
            f"perturbs equality/modularity, not size alone. For example {sm['label']} "
            f"({sm['modules']} modules, {sm['survival']:.1%}) is more diluted than the "
            f"larger {lg['label']} ({lg['modules']} modules, {lg['survival']:.1%})."
        )
    out.append(
        "This is the pinned decision for #178: `overall` is a five-axis geometric "
        "mean, so a single back-edge's contribution to it is a small, "
        "structure-dependent fraction of the acyclicity magnitude and can be "
        "swamped (or sign-flipped) by simultaneous moves in "
        "modularity/equality/depth. `overall`'s per-edge sign is therefore NOT a "
        "reliable regression signal; `acyclicity` (asserted strictly negative "
        "above) and `cycles.added` are, which is why archy_diff surfaces them "
        "independently of `overall`. The dilution is intended, not a bug."
    )
    out.append("")
    out.append("## Break direction (separate native-cycle graph)")
    out.append(
        f"- {resolved['modules']}-node graph built with a 2-cycle on "
        f"`{resolved['pair']}`, then broken: 1 resolved, 0 added, acyclicity rises."
    )
    out.append("")
    out.append("## Layer-violation direction (synthetic, forbid l0->l1)")
    out.append(f"- added edge flagged: 1 violation (`{violation['rule']}`, `{violation['edge']}`)")
    out.append("- removed edge: 1 resolved, 0 added")
    out.append("")
    out.append(
        "> The asserted signals (acyclicity sign, cycles.added/resolved counts) are "
        "exact and environment-independent. The `overall` / ratio columns depend on "
        "networkx's community detection and may shift across networkx versions; they "
        "are measured, not asserted."
    )
    out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="write the report to stdout instead of bench/delta_direction_results.md",
    )
    args = parser.parse_args()

    rows, resolved, violation = run()
    report = format_report(rows, resolved, violation)
    if args.stdout:
        sys.stdout.write(report)
    else:
        RESULTS.write_text(report)
        print(f"# wrote {RESULTS.relative_to(ROOT)}", file=sys.stderr)
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
