"""Score-shape redesign empirics.

Workflow:
1. `collect`: for each of the 27 manifest projects + governingdocs/backend
   (28th data point), invoke `archy score --format json` AND
   `archy graph --format json`, cache both to bench/cache/{name}.json.
   This is the rigorous-recompute step; subsequent candidate evaluations
   are pure post-processing on the cached graph + axis inputs, so a single
   collect-run feeds every candidate. Re-run `collect` to refresh.

2. `evaluate`: load the cache, compute every candidate axis-formula and
   aggregator, emit per-candidate score tables and pairwise Pearson
   correlation matrices. Output written to bench/score_redesign_results.md.

Run:
    uv run --with networkx --with pyyaml python bench/score_redesign.py collect
    uv run --with networkx --with pyyaml python bench/score_redesign.py evaluate

The script deliberately lives in bench/ and never modifies src/archy. The
companion empirics doc is docs/SCORE_SHAPE_REDESIGN_EMPIRICS.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import statistics
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import networkx as nx  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "bench" / "projects.yaml"
CACHE = REPO_ROOT / "bench" / "cache"
RESULTS = REPO_ROOT / "bench" / "score_redesign_results.md"
WORKDIR = Path("/tmp/archy_bench")

AXIS_ORDER: tuple[str, ...] = (
    "modularity",
    "acyclicity",
    "depth",
    "equality",
    "complexity",
)

GUINEA_PIG = {
    "name": "governingdocs",
    "repo": "local:governingdocs/backend",
    "sha": "local",
    "local_path": "/Users/hosanglee/governingdocs/backend",
    "src_dir": ".",
    "why": "guinea pig: validator/parser-heavy backend with cc_mean ~6.5",
}


def projects() -> list[dict]:
    manifest: list[dict] = yaml.safe_load(MANIFEST.read_text())["projects"]
    return [*manifest, GUINEA_PIG]


def resolve_root(proj: dict) -> Path:
    if "local_path" in proj:
        return Path(proj["local_path"])
    if proj["name"] == "archy" and proj["sha"] == "HEAD":
        return REPO_ROOT
    target = WORKDIR / proj["name"]
    if not target.exists():
        WORKDIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{proj['repo']}.git", str(target)],
            check=True,
        )
    res = subprocess.run(
        ["git", "-C", str(target), "cat-file", "-e", proj["sha"]], capture_output=True
    )
    if res.returncode != 0:
        subprocess.run(["git", "-C", str(target), "fetch", "--quiet", "origin"], check=False)
    subprocess.run(["git", "-C", str(target), "checkout", "--quiet", proj["sha"]], check=True)
    subprocess.run(
        ["git", "-C", str(target), "reset", "--hard", "--quiet", proj["sha"]], check=True
    )
    subprocess.run(["git", "-C", str(target), "clean", "-fdx", "--quiet"], check=True)
    return target


def archy_call(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(
        ["uv", "run", "--project", str(REPO_ROOT), "archy", *args], cwd=cwd, text=True
    )


def collect_project(proj: dict) -> dict | None:
    root = resolve_root(proj)
    src = root if proj.get("src_dir") == "." else root / proj["src_dir"]
    if not src.exists():
        print(f"  SRC MISSING: {src}", file=sys.stderr)
        return None
    cwd = root
    try:
        score = json.loads(archy_call(["score", "--format", "json", str(src)], cwd))
        graph = json.loads(archy_call(["graph", "--format", "json", str(src)], cwd))
    except subprocess.CalledProcessError as exc:
        print(f"  ARCHY FAILED: {exc}", file=sys.stderr)
        return None
    return {"project": proj, "score": score, "graph": graph}


def collect_all() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    manifest = projects()
    print(f"# collecting over {len(manifest)} projects", file=sys.stderr)
    for proj in manifest:
        out = CACHE / f"{proj['name']}.json"
        print(f"# {proj['name']:14s} ", end="", file=sys.stderr, flush=True)
        if out.exists():
            print("  cached", file=sys.stderr)
            continue
        rec = collect_project(proj)
        if rec is None:
            continue
        out.write_text(json.dumps(rec))
        modules = rec["score"]["inputs"]["module_count"]
        overall = rec["score"]["overall"]
        print(f"  overall={overall:.3f}  modules={modules}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Per-project graph derivations (cycle/SCC/feedback metrics not in score JSON)
# ---------------------------------------------------------------------------


def build_internal_graph(graph: dict) -> nx.DiGraph:
    external = {n["id"] for n in graph["nodes"] if n.get("external")}
    g: nx.DiGraph = nx.DiGraph()
    for n in graph["nodes"]:
        if not n.get("external"):
            g.add_node(n["id"])
    for e in graph["edges"]:
        s, t = e["source"], e["target"]
        if s in external or t in external:
            continue
        if s == t:
            # self-loops counted as cycles by archy; keep for parity
            g.add_edge(s, t)
            continue
        g.add_edge(s, t)
    return g


def scc_metrics(g: nx.DiGraph) -> dict:
    n = g.number_of_nodes()
    if n == 0:
        return {
            "n_nodes": 0,
            "n_edges": 0,
            "scc_sizes": [],
            "nodes_in_cycles": 0,
            "largest_scc": 0,
            "scc_count": 0,
            "feedback_edges_lb": 0,
        }
    sccs = [c for c in nx.strongly_connected_components(g)]
    nontrivial = [len(c) for c in sccs if len(c) >= 2]
    # self-loops on singletons also count as cycles per archy
    self_loop_singletons = sum(
        1 for c in sccs if len(c) == 1 and next(iter(c)) in g.successors(next(iter(c)))
    )
    nic = sum(nontrivial) + self_loop_singletons
    largest = max(nontrivial, default=0)
    # Feedback edges lower bound: edges entirely inside non-trivial SCCs
    # are candidates for the minimum feedback arc set; exact MFAS is NP-hard,
    # but `edges_inside_scc - (scc_size - 1)` is a tight lower bound for each
    # SCC since you need at least that many removals to expose a spanning tree.
    fb_lb = 0
    sccs_by_node = {}
    for i, c in enumerate(sccs):
        for v in c:
            sccs_by_node[v] = i
    inside_counts: dict[int, int] = {}
    for u, v in g.edges():
        if sccs_by_node[u] == sccs_by_node[v] and len(sccs[sccs_by_node[u]]) >= 2:
            inside_counts[sccs_by_node[u]] = inside_counts.get(sccs_by_node[u], 0) + 1
    for i, count in inside_counts.items():
        fb_lb += max(0, count - (len(sccs[i]) - 1))
    wccs = list(nx.weakly_connected_components(g))
    largest_wcc = max((len(c) for c in wccs), default=0)
    return {
        "n_nodes": n,
        "n_edges": g.number_of_edges(),
        "scc_sizes": sorted(nontrivial, reverse=True),
        "nodes_in_cycles": nic,
        "largest_scc": largest,
        "largest_wcc": largest_wcc,
        "scc_count": len(nontrivial) + (1 if self_loop_singletons else 0),
        "feedback_edges_lb": fb_lb,
    }


# ---------------------------------------------------------------------------
# Candidate axis formulas (acyclicity replacements)
# ---------------------------------------------------------------------------


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def axis_baseline_tangle(s: dict, sm: dict) -> float:
    """v0.23 status quo: 1 - nodes_in_cycles / total_nodes."""
    n = max(1, sm["n_nodes"])
    return _clamp01(1.0 - sm["nodes_in_cycles"] / n)


def axis_largest_scc(s: dict, sm: dict) -> float:
    """1 - largest_scc / total_nodes. Decouples from depth: doesn't count
    every node in every SCC, only the worst single tangle."""
    n = max(1, sm["n_nodes"])
    return _clamp01(1.0 - sm["largest_scc"] / n)


def axis_feedback_edges(s: dict, sm: dict) -> float:
    """1 - feedback_edges_lb / total_edges. Edge-centric instead of node-
    centric: counts the minimal number of import-removals needed to make
    the graph a DAG. Independent of how big each SCC is."""
    m = max(1, sm["n_edges"])
    return _clamp01(1.0 - sm["feedback_edges_lb"] / m)


def axis_log_cycle_count(s: dict, sm: dict) -> float:
    """1 / (1 + log(1 + cycle_count)). Cycle-count-centric with logarithmic
    saturation: one cycle isn't 10x better than ten cycles."""
    c = sm["scc_count"]
    return _clamp01(1.0 / (1.0 + math.log1p(c)))


def axis_sentrux_legacy(s: dict, sm: dict) -> float:
    """1 / (1 + cycle_count). The pre-Structure101 form. Included as a
    baseline to confirm the v0.20 switch to tangle-ratio wasn't the cause."""
    return _clamp01(1.0 / (1.0 + sm["scc_count"]))


def axis_modular_tangle(s: dict, sm: dict) -> float:
    """B3: 1 - largest_scc / largest_weakly_connected_component.

    Baldwin/MacCormack/Rusnak (2014) variant; archy-specific normalization
    by the largest WCC rather than total node count. Removes the |V|
    denominator that mechanically scales with depth in elongated trees.
    """
    n_wcc = sm.get("largest_wcc", sm["n_nodes"])
    w = max(1, n_wcc)
    return _clamp01(1.0 - sm["largest_scc"] / w)


def axis_feedback_x_tangle(s: dict, sm: dict) -> float:
    """Geometric mean of feedback-edge fraction and node-tangle fraction.

    Combines the edge-centric and node-centric views: a project pays for
    BOTH 'how many imports must I delete to make this a DAG' (feedback
    edges) AND 'how much of my surface is currently tangled' (nodes in
    cycles). Less sensitive to extreme single-graph patterns than either
    alone.
    """
    a = axis_feedback_edges(s, sm)
    b = axis_baseline_tangle(s, sm)
    if a <= 0 or b <= 0:
        return 0.0
    return math.sqrt(a * b)


ACYCLICITY_CANDIDATES = {
    "baseline_tangle": axis_baseline_tangle,
    "largest_scc": axis_largest_scc,
    "modular_tangle": axis_modular_tangle,
    "feedback_edges": axis_feedback_edges,
    "feedback_x_tangle": axis_feedback_x_tangle,
    "log_cycle_count": axis_log_cycle_count,
    "sentrux_legacy": axis_sentrux_legacy,
}


# ---------------------------------------------------------------------------
# Candidate depth formulas (depth is the other half of the moderate pair)
# ---------------------------------------------------------------------------
#
# The depth axis is computed on the SCC-condensation, which is the
# mechanical coupler to acyclicity: a project with lots of nodes in
# one big SCC has a short condensed-DAG, which inflates the depth score.
# These alternatives test whether a depth formulation that accounts for
# in-SCC traversal decouples from acyclicity.


def _depth_score_from_raw(max_depth: int) -> float:
    return 1.0 / (1.0 + max_depth / 8.0)


def depth_baseline(s: dict, sm: dict) -> float:
    """v0.23 status quo: longest path on condensation DAG."""
    return _depth_score_from_raw(s["inputs"]["max_depth"])


def depth_with_scc_penalty(s: dict, sm: dict) -> float:
    """Add the largest SCC's size to the condensed longest path.

    Treat the largest SCC as if every node in it were a chain link
    (because, from a 'how far does a change propagate' standpoint,
    a 50-module SCC IS at least 50 hops deep (every node reaches
    every other). This deliberately couples depth and acyclicity in
    the SAME direction, so any reduction in the moderate-negative
    pair will reflect the SCC mechanism collapsing rather than the
    pair flipping sign.
    """
    return _depth_score_from_raw(s["inputs"]["max_depth"] + sm["largest_scc"])


def depth_size_relative(s: dict, sm: dict) -> float:
    """Depth as a fraction of the graph's module count.

    A 10-deep chain in a 1000-module graph is mild; the same chain in
    a 15-module graph is pathological. This formulation normalizes
    by graph size, so 'depth ratio' rather than 'absolute depth' is
    what gets scored.
    """
    n = max(1, sm["n_nodes"])
    ratio = s["inputs"]["max_depth"] / n
    # Map [0, 0.5] -> [1, 0] linearly; >50% of the graph in one chain is
    # already pathological.
    return _clamp01(1.0 - 2 * ratio)


DEPTH_CANDIDATES = {
    "depth_baseline": depth_baseline,
    "depth_with_scc_penalty": depth_with_scc_penalty,
    "depth_size_relative": depth_size_relative,
}


# ---------------------------------------------------------------------------
# Aggregator candidates
# ---------------------------------------------------------------------------


def agg_geomean(axes: list[float]) -> float:
    """Status quo: 5th-root of product."""
    if any(a <= 0 for a in axes):
        return 0.0
    return math.exp(sum(math.log(a) for a in axes) / len(axes))


def agg_arith(axes: list[float]) -> float:
    """Arithmetic mean baseline (fully compensatory). Comparator."""
    return sum(axes) / len(axes)


def agg_min(axes: list[float]) -> float:
    """Worst-axis (lexicographic). Maximally non-compensatory.

    Strong theoretical decoupling property: the overall depends on only one
    axis, so cross-axis correlations cannot smear into the score. The cost
    is volatility: the overall ranking is determined by whichever single
    axis happens to be lowest.
    """
    return min(axes)


def agg_mpi(axes: list[float]) -> float:
    """Mazziotta-Pareto-style penalty applied to the arithmetic mean.

    Original MPI formula (Mazziotta & Pareto, ISTAT 2013) is defined on
    z-scores and signed by direction-of-improvement. For unit-interval
    axes that are all positively oriented, the adapted form is:

        MPI = mean - sigma * cv

    where `cv = sigma / mean` is the horizontal coefficient of variation.
    The `-sigma * cv` term penalizes axis imbalance: two profiles with the
    same arithmetic mean but different spreads score differently. Bounded
    in [0, mean]; equals the arithmetic mean only when all axes are equal.

    The "AMPI" (Adjusted MPI) variant uses signed cv based on direction;
    for archy all axes are higher=better, so the sign reduces to -.
    """
    m = sum(axes) / len(axes)
    if m <= 0:
        return 0.0
    sd = statistics.pstdev(axes)
    cv = sd / m
    return max(0.0, m - sd * cv)


def agg_penalty_geomean(axes: list[float]) -> float:
    """Geomean times (1 - alpha * sigma_axes). Combines non-compensatory
    geomean with an explicit penalty for imbalance, separately from the
    multiplicative effect geomean already provides.

    alpha = 1.0 makes the penalty visible without dominating; sigma is
    bounded in [0, 0.5] for axes in [0,1], so 1 - sigma is bounded in
    [0.5, 1.0].
    """
    g = agg_geomean(axes)
    sd = statistics.pstdev(axes)
    return max(0.0, g * (1.0 - sd))


def agg_harmonic(axes: list[float]) -> float:
    """A7 with p=-1: harmonic mean. One step more non-compensatory than
    geometric mean in the generalized-power-mean family. Single-line
    code change and no new free parameter.

    Lowest-friction next step in arith -> geom -> harmonic -> min.
    """
    if any(a <= 0 for a in axes):
        return 0.0
    return len(axes) / sum(1.0 / a for a in axes)


def agg_pgm(axes: list[float]) -> float:
    """A3: Mariani-Ciommi penalized geometric mean.

        PGM = g * exp(-lambda * sigma^2_log)

    where sigma^2_log is the horizontal variance of ln(axes). Penalty
    fires exactly when correlated axes diverge. lambda = 1.0 here as a
    starting calibration; 0 reduces to plain geomean, large values
    approach min.
    """
    if any(a <= 0 for a in axes):
        return 0.0
    g = agg_geomean(axes)
    logs = [math.log(a) for a in axes]
    sigma2 = statistics.pvariance(logs)
    lam = 1.0
    return g * math.exp(-lam * sigma2)


AGGREGATOR_CANDIDATES = {
    "geomean": agg_geomean,
    "arith": agg_arith,
    "min": agg_min,
    "harmonic": agg_harmonic,
    "mpi": agg_mpi,
    "pgm": agg_pgm,
    "penalty_geomean": agg_penalty_geomean,
}


# ---------------------------------------------------------------------------
# Evaluation harness
# ---------------------------------------------------------------------------


def pearson(xs: list[float], ys: list[float]) -> float:
    m = len(xs)
    if m < 2:
        return float("nan")
    mx = sum(xs) / m
    my = sum(ys) / m
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return float("nan")
    return num / (dx * dy)


def spearman(xs: list[float], ys: list[float]) -> float:
    def rank(zs: list[float]) -> list[float]:
        order = sorted(range(len(zs)), key=lambda i: zs[i])
        ranks = [0.0] * len(zs)
        i = 0
        while i < len(zs):
            j = i
            while j + 1 < len(zs) and zs[order[j + 1]] == zs[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks

    return pearson(rank(xs), rank(ys))


def load_corpus() -> list[dict]:
    corpus = []
    for path in sorted(CACHE.glob("*.json")):
        rec = json.loads(path.read_text())
        g = build_internal_graph(rec["graph"])
        rec["scc"] = scc_metrics(g)
        corpus.append(rec)
    return corpus


def normalize_acyclicity(corpus: list[dict], candidate_name: str) -> list[float]:
    fn = ACYCLICITY_CANDIDATES[candidate_name]
    return [fn(r["score"], r["scc"]) for r in corpus]


def axis_values(
    corpus: list[dict], acyclicity_variant: str, depth_variant: str = "depth_baseline"
) -> dict[str, list[float]]:
    mod = [r["score"]["components"]["modularity"] for r in corpus]
    eq = [r["score"]["components"]["equality"] for r in corpus]
    comp = [r["score"]["components"]["complexity"] for r in corpus]
    acy = normalize_acyclicity(corpus, acyclicity_variant)
    dep_fn = DEPTH_CANDIDATES[depth_variant]
    dep = [dep_fn(r["score"], r["scc"]) for r in corpus]
    return {
        "modularity": mod,
        "acyclicity": acy,
        "depth": dep,
        "equality": eq,
        "complexity": comp,
    }


def overall_for(corpus: list[dict], acyclicity_variant: str, aggregator: str) -> list[float]:
    axes_map = axis_values(corpus, acyclicity_variant)
    fn = AGGREGATOR_CANDIDATES[aggregator]
    out = []
    for i in range(len(corpus)):
        row = [axes_map[a][i] for a in AXIS_ORDER]
        out.append(fn(row))
    return out


def correlation_matrix(axes_map: dict[str, list[float]]) -> dict[tuple[str, str], float]:
    axes = list(axes_map.keys())
    return {(a, b): pearson(axes_map[a], axes_map[b]) for a, b in combinations(axes, 2)}


def evaluate() -> None:
    corpus = load_corpus()
    if not corpus:
        print("# no cached data; run `collect` first", file=sys.stderr)
        sys.exit(1)
    today = dt.date.today().isoformat()
    out: list[str] = []
    emit = out.append
    emit("# Score-shape redesign empirics")
    emit("")
    cmd = "uv run --with networkx --with pyyaml python bench/score_redesign.py evaluate"
    emit(f"Output of `{cmd}`.")
    emit(f"{len(corpus)} projects (27-project bench + governingdocs/backend). Captured {today}.")
    emit("")
    emit("## Per-acyclicity-candidate Pearson correlation matrices")
    emit("")
    emit("Each row is a candidate acyclicity formulation; remaining four axes")
    emit("are unchanged. The two OECD-relevant pairs (acyclicity ↔ depth,")
    emit("modularity ↔ depth) are highlighted; any pair with |r| ≥ 0.5 is")
    emit("a 'moderate' coupling per the OECD handbook commentary.")
    emit("")
    summary_rows: list[tuple[str, float, float, float]] = []
    for cand in ACYCLICITY_CANDIDATES:
        axes_map = axis_values(corpus, cand)
        cm = correlation_matrix(axes_map)
        emit(f"### {cand}")
        emit("")
        emit("| pair | r |")
        emit("| --- | ---: |")
        max_abs = 0.0
        moderate_count = 0
        for (a, b), r in cm.items():
            marker = " **" if abs(r) >= 0.5 else ""
            emit(f"| {a} ↔ {b}{marker} | {r:+.3f}{marker} |")
            if abs(r) > max_abs:
                max_abs = abs(r)
            if abs(r) >= 0.5:
                moderate_count += 1
        ad = cm[("acyclicity", "depth")]
        md = cm[("modularity", "depth")]
        emit("")
        emit(f"- acyclicity ↔ depth: **{ad:+.3f}**")
        emit(f"- modularity ↔ depth: **{md:+.3f}**")
        emit(f"- pairs at |r| ≥ 0.5: **{moderate_count}/10**")
        emit(f"- max |r|: **{max_abs:.3f}**")
        emit("")
        summary_rows.append((cand, ad, md, max_abs))

    emit("## Acyclicity candidate summary")
    emit("")
    emit("| candidate | acyc↔depth | mod↔depth | max \\|r\\| |")
    emit("| --- | ---: | ---: | ---: |")
    for cand, ad, md, mx in summary_rows:
        emit(f"| {cand} | {ad:+.3f} | {md:+.3f} | {mx:.3f} |")
    emit("")

    emit("## Per-depth-candidate Pearson correlation matrices")
    emit("")
    emit("Each row is a candidate depth formulation; acyclicity is held at the")
    emit("status-quo (baseline_tangle). Tests whether the modularity↔depth")
    emit("and acyclicity↔depth pairs respond to depth-side reformulations.")
    emit("")
    depth_summary: list[tuple[str, float, float, float]] = []
    for cand in DEPTH_CANDIDATES:
        axes_map = axis_values(corpus, "baseline_tangle", cand)
        cm = correlation_matrix(axes_map)
        emit(f"### {cand}")
        emit("")
        emit("| pair | r |")
        emit("| --- | ---: |")
        max_abs = 0.0
        for (a, b), r in cm.items():
            marker = " **" if abs(r) >= 0.5 else ""
            emit(f"| {a} ↔ {b}{marker} | {r:+.3f}{marker} |")
            if abs(r) > max_abs:
                max_abs = abs(r)
        ad = cm[("acyclicity", "depth")]
        md = cm[("modularity", "depth")]
        emit(f"\n- acyclicity ↔ depth: **{ad:+.3f}**")
        emit(f"- modularity ↔ depth: **{md:+.3f}**")
        emit(f"- max |r|: **{max_abs:.3f}**\n")
        depth_summary.append((cand, ad, md, max_abs))

    emit("## Depth candidate summary\n")
    emit("| candidate | acyc↔depth | mod↔depth | max \\|r\\| |")
    emit("| --- | ---: | ---: | ---: |")
    for cand, ad, md, mx in depth_summary:
        emit(f"| {cand} | {ad:+.3f} | {md:+.3f} | {mx:.3f} |")
    emit("")

    emit("## Cross-product: best acyclicity x best depth\n")
    emit("If a candidate acyclicity AND a candidate depth both reduce |r|,")
    emit("the combination should compound. This table is the full Cartesian")
    emit("product over (acyclicity-candidate x depth-candidate), reporting")
    emit("only the two OECD-relevant pairs.\n")
    emit("| acyclicity | depth | acyc↔depth | mod↔depth | moderate pairs (\\|r\\| ≥ 0.5) |")
    emit("| --- | --- | ---: | ---: | ---: |")
    for acy_cand in ACYCLICITY_CANDIDATES:
        for dep_cand in DEPTH_CANDIDATES:
            axes_map = axis_values(corpus, acy_cand, dep_cand)
            cm = correlation_matrix(axes_map)
            ad = cm[("acyclicity", "depth")]
            md = cm[("modularity", "depth")]
            moderate = sum(1 for r in cm.values() if abs(r) >= 0.5)
            emit(f"| {acy_cand} | {dep_cand} | {ad:+.3f} | {md:+.3f} | {moderate}/10 |")
    emit("")

    emit("## Aggregator sensitivity (axes held at v0.23 baseline)")
    emit("")
    emit("For each aggregator, overall scores under the **status-quo acyclicity** axis.")
    emit("The Pearson correlation of `overall` against each axis is shown; lower")
    emit("|r| means the aggregator depends less mechanically on that single axis.")
    emit("")
    axes_map = axis_values(corpus, "baseline_tangle")
    header_cells = ["aggregator"] + [f"r(overall, {a[:3]})" for a in AXIS_ORDER]
    emit("| " + " | ".join(header_cells) + " |")
    emit("| --- | ---: | ---: | ---: | ---: | ---: |")
    for agg in AGGREGATOR_CANDIDATES:
        overalls = overall_for(corpus, "baseline_tangle", agg)
        cells = [f"{pearson(overalls, axes_map[a]):+.3f}" for a in axes_map]
        emit(f"| {agg} | " + " | ".join(cells) + " |")
    emit("")
    emit("Interpretation: the closer all five r-values are to one another, the")
    emit("more even the aggregator's sensitivity to each axis. A geomean variant")
    emit("with strongly non-uniform correlations is implicitly weighting some")
    emit("axes more than others, which is the failure mode MPI / penalty-geomean")
    emit("are trying to prevent.")
    emit("")

    emit("## Aggregator score tables (status-quo axes)")
    emit("")
    names = [r["project"]["name"] for r in corpus]
    for agg in AGGREGATOR_CANDIDATES:
        overalls = overall_for(corpus, "baseline_tangle", agg)
        ranked = sorted(zip(names, overalls, strict=True), key=lambda x: -x[1])
        emit(f"### {agg}")
        emit("")
        emit("| project | overall |")
        emit("| --- | ---: |")
        for n, o in ranked:
            emit(f"| {n} | {o:.3f} |")
        emit("")

    emit("## Rank stability of winning axis combinations")
    emit("")
    emit("Spearman rho of each candidate axis-combination's overall (geomean)")
    emit("against the v0.23 baseline. rho near 1 means projects re-rank little;")
    emit("rho < 0.9 means the leaderboard would visibly shake up.")
    emit("")
    baseline_overall = overall_for(corpus, "baseline_tangle", "geomean")
    emit("| acyclicity | depth | spearman rho vs v0.23 |")
    emit("| --- | --- | ---: |")
    interesting_combos = [
        ("baseline_tangle", "depth_baseline"),
        ("feedback_edges", "depth_baseline"),
        ("modular_tangle", "depth_baseline"),
        ("baseline_tangle", "depth_with_scc_penalty"),
        ("feedback_edges", "depth_with_scc_penalty"),
        ("modular_tangle", "depth_with_scc_penalty"),
        ("feedback_x_tangle", "depth_with_scc_penalty"),
        ("baseline_tangle", "depth_size_relative"),
        ("feedback_edges", "depth_size_relative"),
    ]
    for acy_cand, dep_cand in interesting_combos:
        axes_map = axis_values(corpus, acy_cand, dep_cand)
        overalls = [agg_geomean([axes_map[a][i] for a in AXIS_ORDER]) for i in range(len(corpus))]
        rho = spearman(baseline_overall, overalls)
        emit(f"| {acy_cand} | {dep_cand} | {rho:+.3f} |")
    emit("")

    emit("## Rank stability under aggregator changes")
    emit("")
    emit("Spearman rho between aggregator overall-rankings. rho near 1 means the")
    emit("aggregator change re-orders the projects very little; rho < 0.9 means")
    emit("the new aggregator would visibly shake up the leaderboard.")
    emit("")
    overall_by_agg = {
        agg: overall_for(corpus, "baseline_tangle", agg) for agg in AGGREGATOR_CANDIDATES
    }
    aggs = list(AGGREGATOR_CANDIDATES.keys())
    emit("| pair | spearman rho |")
    emit("| --- | ---: |")
    for a, b in combinations(aggs, 2):
        rho = spearman(overall_by_agg[a], overall_by_agg[b])
        emit(f"| {a} ↔ {b} | {rho:+.3f} |")
    emit("")

    emit("## Per-project axis dump (debugging)")
    emit("")
    dump_header = (
        "| project | mod | acy_baseline | acy_largest | acy_feedback "
        "| acy_log | acy_legacy | depth | equality | complexity |"
    )
    emit(dump_header)
    emit("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for rec in corpus:
        n = rec["project"]["name"]
        comp = rec["score"]["components"]
        sm = rec["scc"]
        cells = [
            f"{comp['modularity']:.3f}",
            f"{axis_baseline_tangle(rec['score'], sm):.3f}",
            f"{axis_largest_scc(rec['score'], sm):.3f}",
            f"{axis_feedback_edges(rec['score'], sm):.3f}",
            f"{axis_log_cycle_count(rec['score'], sm):.3f}",
            f"{axis_sentrux_legacy(rec['score'], sm):.3f}",
            f"{comp['depth']:.3f}",
            f"{comp['equality']:.3f}",
            f"{comp['complexity']:.3f}",
        ]
        emit(f"| {n} | " + " | ".join(cells) + " |")
    emit("")

    RESULTS.write_text("\n".join(out) + "\n")
    print(f"# wrote {RESULTS.relative_to(REPO_ROOT)}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("subcommand", choices=["collect", "evaluate"])
    args = parser.parse_args()
    if args.subcommand == "collect":
        collect_all()
    else:
        evaluate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
