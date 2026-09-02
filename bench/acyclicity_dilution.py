#!/usr/bin/env python
"""Acyclicity-term dilution study for the score composite (issue #192).

`bench/delta_direction.py` (issue #178) established that a single injected
2-cycle's contribution to `overall` shrinks with graph size, because the
acyclicity axis is `1 - tangle_ratio` (the *fraction* of nodes inside a cycle)
and a small isolated cycle is a small fraction of a large graph. PR #191 pinned
that dilution as intended and asserted the FP-free per-edge signals
(`acyclicity` delta sign, `cycles.added`) instead.

Issue #192 asks the deeper design question that left open: *should `overall`
itself be made to reflect a single structural regression at scale*, e.g. by
blending a count-sensitive term into the acyclicity axis? This harness answers
it empirically by comparing the current axis against three count-sensitive
candidates across the corpus + synthetic sizes, on three axes of evidence:

1. **Clean-graph penalty (the decisive cost).** What each candidate does to the
   acyclicity axis of a *healthy* graph that carries a few stock cycles. A
   count term penalizes a large, near-acyclic codebase (low `tangle_ratio`,
   nonzero `cycle_count`) as heavily as a tiny tangled one, inverting the
   proportional-pathology rationale.
2. **Single-cycle response (does the change even do what it is for).** The
   ABSOLUTE `overall` delta from injecting exactly one 2-cycle, current vs each
   candidate. The count candidates DO raise it (that is not in dispute); the
   question is whether the cost in (1) is worth it.
3. **Corpus rank stability.** Spearman of the candidate's clean-graph `overall`
   ranking against the current ranking, to size the trend-continuity break.

Candidates (acyclicity as a function of `tangle_ratio`, `cycle_count`):
  * current     : 1 - tangle_ratio                               (proportional)
  * A_countlin  : 1 - clamp(tangle_ratio + 0.05 * cycle_count)   (linear count)
  * B_floor     : 1 - max(tangle_ratio, min(0.5, 0.05*cycle))    (per-cycle floor)
  * C_logcount  : 1 - clamp(tangle_ratio + 0.06 * ln(1+cycle))   (log count)

The composite `overall` is recomputed as the unchanged five-axis geometric mean
with only the acyclicity term swapped, so the comparison isolates the axis
change. This bench does NOT modify `src/archy/score.py`; it is an offline
study supporting the #192 decision recorded in
`docs/research/ACYCLICITY_DILUTION_EMPIRICS.md`.

Usage:
    uv run --with networkx --with pyyaml --with tree-sitter \
        --with tree-sitter-python --with pydantic python bench/acyclicity_dilution.py
    uv run ... python bench/acyclicity_dilution.py --stdout

archy:owns        acy_countlin, acy_current, acy_floor, acy_logcount, format_report,
                  inject_two_cycle, internal_graph, main, measure, run, synthetic_dag
"""

from __future__ import annotations

import argparse
import math
import statistics as st
import sys
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from archy import score as S  # noqa: E402
from archy.cycles import find_cycles  # noqa: E402
from archy.graph import build_graph  # noqa: E402

REPLAY = ROOT / "bench" / "replay_cache"
RESULTS = ROOT / "bench" / "acyclicity_dilution_results.md"
SYNTHETIC_SIZES = (300, 1500, 5000)


# --- candidate acyclicity formulas -------------------------------------------


def acy_current(tr: float, cc: int) -> float:
    return 1.0 - tr


def acy_countlin(tr: float, cc: int, beta: float = 0.05) -> float:
    return 1.0 - min(1.0, tr + beta * cc)


def acy_floor(tr: float, cc: int, delta: float = 0.05, cap: float = 0.5) -> float:
    return 1.0 - max(tr, min(cap, delta * cc))


def acy_logcount(tr: float, cc: int, gamma: float = 0.06) -> float:
    return 1.0 - min(1.0, tr + gamma * math.log1p(cc))


CANDS = {
    "current": acy_current,
    "A_countlin": acy_countlin,
    "B_floor": acy_floor,
    "C_logcount": acy_logcount,
}


# --- graph construction & mutation (shared shape with delta_direction.py) -----


def internal_graph(root: Path) -> nx.DiGraph:
    g = build_graph(root)
    g.remove_nodes_from([n for n, d in g.nodes(data=True) if d.get("external")])
    return g


def synthetic_dag(n: int) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for i in range(n):
        g.add_node(f"m{i}")
    for i in range(1, n):
        g.add_edge(f"m{i}", f"m{i - 1}", kinds=("import",), lines=(1,))
        if i >= 2:
            g.add_edge(f"m{i}", f"m{i - 2}", kinds=("import",), lines=(1,))
    return g


def inject_two_cycle(g: nx.DiGraph) -> nx.DiGraph:
    """Add one edge forming exactly one fresh 2-cycle (same rule as delta_direction)."""
    cyclic = {m for c in find_cycles(g) for m in c.modules}
    for u, v in sorted(g.edges()):
        if u in cyclic or v in cyclic or g.has_edge(v, u):
            continue
        g1 = g.copy()
        g1.add_edge(v, u, kinds=("import",), lines=(1,))
        scc = next(s for s in nx.strongly_connected_components(g1) if u in s)
        if scc == {u, v}:
            return g1
    raise RuntimeError("no clean 2-cycle injection site found")


# --- scoring under a swapped acyclicity term ---------------------------------


def _axes(g: nx.DiGraph) -> tuple[float, float, float, float, float, int, int]:
    """Return (mod, dep, eq, cpx, tangle_ratio, cycle_count, node_count).

    The four fixed axes (mod/dep/eq/cpx) are returned as-is; the acyclicity
    inputs (tangle_ratio, cycle_count) are returned raw so each candidate can
    recompute its own acyclicity term, which is the whole point of the study.
    """
    mod, _, _ = S.compute_modularity(g)
    _, cc, tr = S.compute_acyclicity(g)
    dep, _ = S.compute_depth(g)
    eq, _ = S.compute_equality(g)
    fn, _, _, mean = S._cc_stats(g)
    cpx = S.compute_complexity(mean, fn)
    return mod, dep, eq, cpx, tr, cc, g.number_of_nodes()


def _overall(mod: float, acy: float, dep: float, eq: float, cpx: float) -> float:
    return (mod * acy * dep * eq * cpx) ** 0.2


def measure(g: nx.DiGraph, label: str) -> dict | None:
    mod, dep, eq, cpx, tr, cc, n = _axes(g)
    base = {
        k: {"acy": f(tr, cc), "overall": _overall(mod, f(tr, cc), dep, eq, cpx)}
        for k, f in CANDS.items()
    }
    try:
        g1 = inject_two_cycle(g)
    except RuntimeError:
        return None
    mod1, dep1, eq1, cpx1, tr1, cc1, _ = _axes(g1)
    inj = {}
    for k, f in CANDS.items():
        o0 = _overall(mod, f(tr, cc), dep, eq, cpx)
        o1 = _overall(mod1, f(tr1, cc1), dep1, eq1, cpx1)
        inj[k] = {"d_overall": o1 - o0, "sign_ok": (o1 - o0) < 0}
    return {"label": label, "n": n, "cc": cc, "tr": tr, "base": base, "inj": inj}


def _spearman(a: list[float], b: list[float]) -> float:
    def rank(v: list[float]) -> list[int]:
        order = sorted(range(len(v)), key=lambda i: v[i])
        rk = [0] * len(v)
        for pos, i in enumerate(order):
            rk[i] = pos
        return rk

    ra, rb = rank(a), rank(b)
    ma, mb = st.mean(ra), st.mean(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (da * db) if da and db else 1.0


# --- driver & report ---------------------------------------------------------


def run() -> list[dict]:
    graphs = [(synthetic_dag(n), f"synthetic-{n}") for n in SYNTHETIC_SIZES]
    if REPLAY.is_dir():
        for repo in sorted(p for p in REPLAY.iterdir() if p.is_dir()):
            g = internal_graph(repo)
            if g.number_of_edges():
                graphs.append((g, repo.name))
    return [r for r in (measure(g, label) for g, label in graphs) if r]


def format_report(rows: list[dict]) -> str:
    cands = [k for k in CANDS if k != "current"]
    by_size = sorted(rows, key=lambda r: -r["n"])
    out = ["# Acyclicity-term dilution study (issue #192)", ""]
    out.append(
        "Should the score composite be changed so a single structural regression "
        "registers on `overall` at scale? This compares the current acyclicity axis "
        "(`1 - tangle_ratio`) against three count-sensitive candidates. Only the "
        "acyclicity term is swapped; the other four axes and the geometric mean are "
        "unchanged. See `docs/research/ACYCLICITY_DILUTION_EMPIRICS.md` for the decision."
    )
    out.append("")

    out.append("## 1. Clean-graph acyclicity axis, per candidate (the decisive cost)")
    out.append(
        "A healthy graph with a few stock cycles. The count candidates dock a large, "
        "near-acyclic codebase (low `tangle_ratio`, nonzero `cycle_count`) the same "
        "per-cycle penalty as a tiny tangled one, inverting the proportional-pathology "
        "rationale (`tangle_ratio` already says a small isolated cycle in a big graph is "
        "a small pathology)."
    )
    out.append("")
    out.append("| graph | modules | cycles | tangle_ratio | " + " | ".join(CANDS) + " |")
    out.append("|---|--:|--:|--:|" + "--:|" * len(CANDS))
    for r in by_size:
        cells = " | ".join(f"{r['base'][k]['acy']:.4f}" for k in CANDS)
        out.append(f"| {r['label']} | {r['n']} | {r['cc']} | {r['tr']:.3f} | {cells} |")
    out.append("")
    # name the sharpest inversion: largest near-acyclic repo with stock cycles
    near = [r for r in rows if r["cc"] > 0 and r["tr"] < 0.05]
    if near:
        w = max(near, key=lambda r: r["n"])
        drop = w["base"]["current"]["acy"] - w["base"]["A_countlin"]["acy"]
        out.append(
            f"Sharpest inversion: **{w['label']}** ({w['n']} modules, {w['tr']:.1%} of nodes "
            f"tangled, {w['cc']} isolated cycles) is {w['base']['current']['acy']:.3f} acyclic "
            f"under the current axis but only {w['base']['A_countlin']['acy']:.3f} under "
            f"`A_countlin` -- a {drop:.2f} dock on a codebase that is "
            f"{1 - w['tr']:.1%} acyclic. A count term scores it as if those cycles were a "
            f"tenth of its structural health."
        )
    out.append("")

    out.append("## 2. Single-cycle response: absolute `overall` delta on inject")
    out.append(
        "Injecting exactly one fresh 2-cycle, the ABSOLUTE `overall` delta under each "
        "formula (x1e3). The count candidates DO raise the response (this is not in "
        "dispute) -- the question is whether that is worth the cost in section 1, given "
        "the FP-free per-edge signal (`cycles.added`, acyclicity delta sign) already "
        "exists in `archy_diff` regardless of the composite."
    )
    out.append("")
    out.append("| graph | modules | " + " | ".join(f"{k} (x1e3)" for k in CANDS) + " |")
    out.append("|---|--:|" + "--:|" * len(CANDS))
    for r in by_size:
        cells = " | ".join(f"{abs(r['inj'][k]['d_overall']) * 1e3:.4f}" for k in CANDS)
        out.append(f"| {r['label']} | {r['n']} | {cells} |")
    out.append("")
    biggest = by_size[0]
    out.append(
        f"On the largest graph (`{biggest['label']}`, {biggest['n']} modules) the count "
        f"candidates lift a single cycle's `overall` delta from "
        f"{abs(biggest['inj']['current']['d_overall']) * 1e3:.4f}e-3 to "
        f"{abs(biggest['inj']['A_countlin']['d_overall']) * 1e3:.4f}e-3 -- they work, by "
        f"making the axis sensitive to cycle count rather than proportion."
    )
    out.append("")

    out.append("## 3. Sign correctness and corpus rank stability")
    for k in CANDS:
        bad = [r["label"] for r in rows if not r["inj"][k]["sign_ok"]]
        verdict = "all correct" if not bad else f"wrong on {bad}"
        ok = f"{len(rows) - len(bad)}/{len(rows)}"
        line = f"- `{k}` clean single-inject `overall` sign: {verdict} ({ok})."
        if k == "current":
            line += (
                " (Wrong-direction events in the wild come from multi-change commits, "
                "not single edges -- no axis swap fixes those.)"
            )
        out.append(line)
    cur = [r["base"]["current"]["overall"] for r in rows]
    for k in cands:
        ov = [r["base"][k]["overall"] for r in rows]
        rho = _spearman(cur, ov)
        out.append(f"- `{k}` clean-graph rank stability vs current: Spearman rho = {rho:.3f}.")
    out.append("")
    out.append(
        "> The acyclicity axis and the four fixed axes come from archy's own functions; "
        "`overall` deltas depend on networkx community detection and may shift across "
        "versions. The clean-graph axis penalties (section 1) are exact functions of "
        "`tangle_ratio` and `cycle_count` and are version-independent."
    )
    out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stdout", action="store_true", help="write to stdout, not the results file"
    )
    args = parser.parse_args()
    rows = run()
    report = format_report(rows)
    if args.stdout:
        sys.stdout.write(report)
    else:
        RESULTS.write_text(report)
        print(f"# wrote {RESULTS.relative_to(ROOT)}", file=sys.stderr)
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
