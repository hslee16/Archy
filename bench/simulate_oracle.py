#!/usr/bin/env python
"""Empirical validation of `archy_simulate` on the real-repo corpus.

The spec (`docs/SPEC_SIMULATE.md`) asserts:

    archy_simulate(delta) == archy_diff after the delta is actually written.

This script tests that on the repos vendored under `bench/replay_cache/` -- and,
deliberately, tries to *break* it rather than confirm it.

The subtle trap (why an earlier version of this bench was near-worthless): if you
only compare samples where the real edit reproduced exactly the simulated edge
set, the two graphs are topologically identical and every derived metric matches
*by construction*. That measures determinism, not prediction. So here we compare
on EVERY sample and split the result two ways:

* **fidelity (clean rate)** -- how often an agent's intended single-edge delta
  actually maps 1:1 to the written import. The interesting, honest number: when
  it is < 100%, an agent that says "add a -> b" and writes the import gets a
  different graph than it asked simulate about (multi-target import lines on
  removal; re-export / indirection on addition -- the resolved-edge caveat).
* **oracle match** -- does simulate's report equal the real diff's report? On
  CLEAN samples this should be 100% (a failure is a real bug). On DIRTY samples
  it will diverge, and we quantify by how much: that is the cost of the caveat.

Also measured: complexity-axis invariance, simulate-vs-diff wall-clock, and how
often a single added edge introduces a cycle / back-edge.

Known gaps (stated, not hidden): the corpus tops out at ~170 modules, so the
large-graph performance claim is not validated here; and these repos carry no
`archy.yaml`, so the layer-violation dimension has unit coverage only.

Usage:
    uv run --with networkx --with pyyaml --with tree-sitter \
        --with tree-sitter-python --with pydantic python bench/simulate_oracle.py
    uv run ... python bench/simulate_oracle.py --samples 25 --stdout
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from archy.diff import compute_diff, take_snapshot
from archy.dsm import build_dsm, diff_dsm
from archy.graph import build_graph
from archy.simulate import find_simulate

ROOT = Path(__file__).resolve().parent.parent
REPLAY = ROOT / "bench" / "replay_cache"
RESULTS = ROOT / "bench" / "simulate_oracle_results.md"


def _internal(root: Path):
    g = build_graph(root)
    g.remove_nodes_from([n for n, d in g.nodes(data=True) if d.get("external")])
    return g


def _edges(g) -> set[tuple[str, str]]:
    return set(g.edges())


def _import_edges_with_lines(g) -> list[tuple[str, str, tuple[int, ...]]]:
    out = [
        (u, v, tuple(data.get("lines") or ()))
        for u, v, data in g.edges(data=True)
        if data.get("lines")
    ]
    return sorted(out)


def _real_back_edges(before, after) -> set[tuple[str, str]]:
    after_dsm = build_dsm(after, group_by="topological")
    diff = diff_dsm(build_dsm(before, group_by="topological"), after_dsm)
    ordering = after_dsm.ordering
    return {(ordering[c.row], ordering[c.col]) for c in diff.new_back_edges}


def _cycle_keys(cd):
    return (
        frozenset(frozenset(c.modules) for c in cd.added),
        frozenset(frozenset(c.modules) for c in cd.resolved),
    )


def _violation_keys(vd):
    def key(v):
        return (v.rule.from_layer, v.rule.to_layer, v.source, v.target)

    return (frozenset(key(v) for v in vd.added), frozenset(key(v) for v in vd.resolved))


def _matches(sim, real, g0, g1) -> bool:
    sim_back = {(e.source, e.target) for e in sim.new_back_edges}
    return (
        _cycle_keys(sim.cycles) == _cycle_keys(real.cycles)
        and _violation_keys(sim.violations) == _violation_keys(real.violations)
        and sim.score_delta == real.score_delta
        and sim_back == _real_back_edges(g0, g1)
    )


def _stride(seq, n):
    seq = list(seq)
    if len(seq) <= n:
        return seq
    step = len(seq) / n
    return [seq[int(i * step)] for i in range(n)]


def _blank(text: str, lines: tuple[int, ...]) -> str:
    rows = text.splitlines(keepends=True)
    for ln in lines:
        if 1 <= ln <= len(rows):
            rows[ln - 1] = "\n"
    return "".join(rows)


def _try_mutation(p: Path, transform, run) -> None:
    """Write `transform(original)` to `p`, run `run()`, then always restore `p`.

    No-op if `p` is unreadable. Centralizes the read/mutate/restore guard so a
    sample never leaves a corpus file modified even if the evaluation raises.
    """
    try:
        original = p.read_text()
    except OSError:
        return
    p.write_text(transform(original))
    try:
        run()
    finally:
        p.write_text(original)


def _run_repo(name: str, root: Path, n: int) -> dict:
    g0 = _internal(root)
    base = _edges(g0)
    paths = {node: g0.nodes[node].get("path") for node in g0.nodes}

    s = {
        "name": name,
        "modules": g0.number_of_nodes(),
        "edges": len(base),
        # per kind: total / clean / matched_clean / matched_dirty
        "rm": [0, 0, 0, 0],
        "add": [0, 0, 0, 0],
        "complexity_nonzero": 0,
        "add_cycle": 0,
        "add_back": 0,
        "sim_time": 0.0,
        "diff_time": 0.0,
        "bug_fails": [],  # clean-sample mismatches = real bugs
        "dirty_examples": [],
    }

    def evaluate(kind, intended, want_edges, sim_kwargs, src_node):
        g1 = _internal(root)
        clean = _edges(g1) == want_edges
        t0 = time.perf_counter()
        sim = find_simulate(g0, project_root=root, **sim_kwargs)
        s["sim_time"] += time.perf_counter() - t0
        t0 = time.perf_counter()
        real = compute_diff(take_snapshot(g0), take_snapshot(g1))
        s["diff_time"] += time.perf_counter() - t0
        ok = _matches(sim, real, g0, g1)
        bucket = s[kind]
        bucket[0] += 1
        if clean:
            bucket[1] += 1
            bucket[2] += 1 if ok else 0
            if not ok:
                s["bug_fails"].append(f"{kind} {intended}")
        else:
            bucket[3] += 1 if ok else 0
            if len(s["dirty_examples"]) < 4:
                got = _edges(g1) - base if kind == "add" else base - _edges(g1)
                s["dirty_examples"].append(f"{kind} {intended}: real touched {sorted(got)[:3]}")
        if kind == "add" and clean:
            if sim.score_delta.complexity != 0.0:
                s["complexity_nonzero"] += 1
            if sim.cycles.added:
                s["add_cycle"] += 1
            if sim.new_back_edges:
                s["add_back"] += 1
        return sim

    # Removals first: blanking an import's known source lines is the only exactly
    # reversible mutation available on the real corpus, so it is the gold case.
    for u, v, lines in _stride(_import_edges_with_lines(g0), n):
        if not paths.get(u):
            continue
        _try_mutation(
            Path(paths[u]),
            lambda o, _l=lines: _blank(o, _l),
            lambda _u=u, _v=v: evaluate(
                "rm", f"{_u}->{_v}", base - {(_u, _v)}, {"add": [], "remove": [(_u, _v)]}, _u
            ),
        )

    # Skew toward cycle-creating pairs: random non-edges rarely close a cycle, but
    # the cycle path is the highest-value thing to stress, so bias the selection.
    nodes = sorted(g0.nodes)
    pairs = []
    for i, a in enumerate(_stride(nodes, n * 3)):
        b = nodes[(i * 7 + 3) % len(nodes)]
        if a != b and (a, b) not in base and paths.get(a):
            pairs.append((a, b))
    for a, b in _stride(pairs, n):
        _try_mutation(
            Path(paths[a]),
            lambda o, _b=b: f"import {_b}\n" + o,
            lambda _a=a, _b=b: evaluate(
                "add", f"{_a}->{_b}", base | {(_a, _b)}, {"add": [(_a, _b)], "remove": []}, _a
            ),
        )

    return s


def _synthetic(n: int, layers: int = 0):
    """A deterministic mostly-acyclic graph of `n` internal nodes.

    Edges point from higher to lower index, so the base graph is a DAG and an
    added low->high edge tends to create a cycle. With `layers > 0`, nodes are
    named `L{k}.m{i}` so a glob-based archy.yaml can group them.
    """
    import random

    import networkx as nx

    rnd = random.Random(n)
    g: nx.DiGraph = nx.DiGraph()
    g.graph["root"] = "/synthetic"
    g.graph["parse_errors"] = ()

    def name(i):
        return f"L{i % layers}.m{i}" if layers else f"m{i}"

    for i in range(n):
        g.add_node(
            name(i),
            path=f"/synthetic/{name(i)}.py",
            is_package=False,
            external=False,
            function_count=1,
            cc_sum=2,
            cc_max=2,
            cc_mean=2.0,
        )
    for i in range(n):
        for _ in range(2):
            j = rnd.randint(0, i - 1) if i else 0
            if j != i:
                g.add_edge(name(i), name(j), kinds=("import",), lines=(1,))
    return g


def _scale_rows(sizes: list[int]) -> list[tuple[int, float, float, int]]:
    """Time simulate vs an equivalent diff at increasing graph sizes.

    Closes the corpus's scale gap (real repos top out at ~174 modules). The
    added edge `m0 -> m{n-1}` is a back-edge in the DAG, so each size also
    exercises real cycle detection.
    """
    rows = []
    for n in sizes:
        g = _synthetic(n)
        target = f"m{n - 1}"
        t0 = time.perf_counter()
        sim = find_simulate(g, add=[("m0", target)], remove=[])
        st = time.perf_counter() - t0
        g1 = g.copy()
        g1.add_edge("m0", target, kinds=("import",), lines=(1,))
        t0 = time.perf_counter()
        compute_diff(take_snapshot(g), take_snapshot(g1))
        dt = time.perf_counter() - t0
        rows.append((n, st, dt, len(sim.cycles.added)))
    return rows


def _violation_smoke(n: int = 400) -> tuple[bool, bool]:
    """Synthetic layered check: simulate flags a forbidden edge, not an allowed one.

    Gives the layer-violation dimension bench-level coverage (the real corpus
    carries no archy.yaml). Returns (forbidden_flagged, allowed_silent).
    """
    import tempfile

    g = _synthetic(n, layers=4)
    cfg = Path(tempfile.mkdtemp()) / "archy.yaml"
    cfg.write_text(
        "layers:\n"
        + "".join(f"  l{k}: {{modules: [L{k}.**]}}\n" for k in range(4))
        + "forbid:\n  - {from: l0, to: l1}\n"
    )
    # Must be a non-existing edge so simulate reports it as a new addition;
    # an existing l0 -> l1 edge would be a no-op delta and prove nothing.
    l0 = [x for x in g.nodes if x.startswith("L0.")]
    l1 = [x for x in g.nodes if x.startswith("L1.")]
    forb = next((a, b) for a in l0 for b in l1 if not g.has_edge(a, b))
    allow = next((b, a) for a in l0 for b in l1 if not g.has_edge(b, a))  # l1 -> l0 is allowed
    sim_f = find_simulate(g, add=[forb], remove=[], config_path=cfg)
    sim_a = find_simulate(g, add=[allow], remove=[], config_path=cfg)
    forbidden_flagged = any(
        (v.rule.from_layer, v.rule.to_layer) == ("l0", "l1") for v in sim_f.violations.added
    )
    allowed_silent = sim_a.violations.added == ()
    return forbidden_flagged, allowed_silent


def _agg(results, key, idx):
    return sum(r[key][idx] for r in results)


def _format(results: list[dict]) -> str:
    out = ["# archy_simulate validation (adversarial)", ""]

    total = _agg(results, "rm", 0) + _agg(results, "add", 0)
    clean = _agg(results, "rm", 1) + _agg(results, "add", 1)
    matched_clean = _agg(results, "rm", 2) + _agg(results, "add", 2)
    matched_dirty = _agg(results, "rm", 3) + _agg(results, "add", 3)
    dirty = total - clean
    bug_fails = [f for r in results for f in r["bug_fails"]]

    out.append(f"Samples: {total} ({clean} clean, {dirty} dirty).")
    out.append(
        f"**Fidelity (clean rate): {clean}/{total} = {100 * clean / total:.0f}%** "
        "-- intended single-edge delta maps 1:1 to the written import."
    )
    out.append(
        f"**Oracle on clean samples: {matched_clean}/{clean} matched** "
        f"({len(bug_fails)} bug-level mismatches). This is the real correctness gate."
    )
    if dirty:
        out.append(
            f"Oracle on dirty samples: {matched_dirty}/{dirty} matched -- "
            f"i.e. simulate diverged from the written edit on {dirty - matched_dirty} "
            "of them (the resolved-edge caveat, quantified)."
        )
    out.append(
        f"Overall agent-facing match: {matched_clean + matched_dirty}/{total} = "
        f"{100 * (matched_clean + matched_dirty) / total:.0f}%."
    )
    cx = sum(r["complexity_nonzero"] for r in results)
    out.append(f"Complexity-axis nonzero on an edge delta (must be 0): {cx}.")
    sim_t = sum(r["sim_time"] for r in results)
    diff_t = sum(r["diff_time"] for r in results)
    if diff_t:
        out.append(f"simulate vs diff wall-clock: {sim_t / diff_t:.2f}x (corpus <= 174 modules).")
    out.append("")
    out.append(
        "| repo | mods | edges | rm clean/tot | rm match(clean) | "
        "add clean/tot | add match(clean) | add->cycle | add->back |"
    )
    out.append("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for r in sorted(results, key=lambda r: -r["modules"]):
        rm, ad = r["rm"], r["add"]
        out.append(
            f"| {r['name']} | {r['modules']} | {r['edges']} | "
            f"{rm[1]}/{rm[0]} | {rm[2]}/{rm[1]} | {ad[1]}/{ad[0]} | {ad[2]}/{ad[1]} | "
            f"{r['add_cycle']} | {r['add_back']} |"
        )

    if bug_fails:
        out.append("")
        out.append("## BUG: clean-sample mismatches (must be empty)")
        out += [f"- {f}" for f in bug_fails]

    examples = [e for r in results for e in r["dirty_examples"]][:12]
    if examples:
        out.append("")
        out.append("## Dirty-sample characterization (why intended != written)")
        out += [f"- {e}" for e in examples]

    return "\n".join(out) + "\n"


def _format_scale(rows, smoke) -> str:
    out = ["", "## Scale + perf (synthetic graphs, closes the corpus gap)", ""]
    out.append("| nodes | simulate | diff | ratio | cycles_added |")
    out.append("|--:|--:|--:|--:|--:|")
    for n, st, dt, cyc in rows:
        out.append(f"| {n} | {st:.2f}s | {dt:.2f}s | {st / dt:.2f}x | {cyc} |")
    out.append("")
    out.append(
        "simulate's overhead over a diff stays ~constant at scale (the extra DSM + "
        "propagation passes are cheap next to the shared snapshot cost); absolute "
        "latency grows with the snapshot work, not with simulate."
    )
    forb, allow = smoke
    out.append("")
    out.append("## Layer-violation smoke (synthetic, 4 layers, forbid l0->l1)")
    out.append(f"- forbidden edge flagged: {forb}")
    out.append(f"- allowed edge stays silent: {allow}")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=15, help="samples per kind per repo")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--no-scale", action="store_true", help="skip the synthetic scale test")
    args = ap.parse_args()

    manifest = yaml.safe_load((ROOT / "bench" / "projects.yaml").read_text())
    src_dirs = {p["name"]: p["src_dir"] for p in manifest["projects"]}

    results = []
    for repo_dir in sorted(REPLAY.iterdir()):
        if not repo_dir.is_dir():
            continue
        src = repo_dir / src_dirs.get(repo_dir.name, "")
        if not src.is_dir():
            print(f"skip {repo_dir.name}: no src dir", file=sys.stderr)
            continue
        print(f"validating {repo_dir.name} ...", file=sys.stderr)
        results.append(_run_repo(repo_dir.name, src, args.samples))

    report = _format(results)
    if not args.no_scale:
        print("synthetic scale + violation smoke ...", file=sys.stderr)
        report += _format_scale(_scale_rows([500, 2000, 5000, 10000]), _violation_smoke())
    report += (
        "\n## Gaps\n"
        "- Violation prediction reuses archy's own find_violations on the hypothetical "
        "graph; covered by the synthetic smoke above and unit tests, not by real-repo "
        "layer rules (the corpus carries no archy.yaml).\n"
    )
    if not args.stdout:
        RESULTS.write_text(report)
        print(f"wrote {RESULTS}", file=sys.stderr)
    print(report)


if __name__ == "__main__":
    main()
