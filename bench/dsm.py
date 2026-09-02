"""Bench experiment: what does a Design Structure Matrix reveal that the
existing score axes don't?

For each project in `projects.yaml`, builds the import graph, orders the
internal nodes (SCC-condensed topological order with deterministic
tie-breaking), and extracts four DSM-derived scalar signals:

- `feedback_fraction`: share of internal edges that land above the
  diagonal in the topological ordering. Direct measure of how much of
  the graph violates a clean layered shape. Distinct from `cycle_count`
  (which only counts SCCs of size > 1) and from `acyclicity` (which
  weights by tangle size, not edge count).
- `bandwidth_norm`: mean `|i - j| / N` over internal edges in the
  topological ordering. Captures how local dependencies are. Low values
  mean dependencies tend to be between adjacent layers; high values mean
  long-range coupling.
- `block_density_community`: fraction of internal edges that fall inside
  Newman-community block-diagonal blocks. Compare against `modularity`
  to check whether this is just Q in disguise.
- `block_density_layer`: fraction of internal edges that stay within
  the same depth-bucketed layer. Distinct from depth (which is the
  longest path) and from acyclicity.

Reports per-project values plus Pearson correlation against each
existing axis (modularity, acyclicity, depth, equality, complexity) and
propagation_cost. The discriminant-validity question: is any DSM signal
both meaningful and not already captured?

Usage:
    uv run --with networkx --with pyyaml --with tree-sitter \
        --with tree-sitter-python --with pydantic --with click \
        python bench/dsm.py

archy:owns        main
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import clone_or_update, load_manifest, pearson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from archy.graph import build_graph
from archy.reach import compute_propagation_cost
from archy.score import (
    compute_acyclicity,
    compute_complexity,
    compute_depth,
    compute_equality,
    compute_modularity,
)


def _internal_subgraph(graph: nx.DiGraph) -> nx.DiGraph:
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]
    return graph.subgraph(internal).copy()


def _scc_topo_order(graph: nx.DiGraph) -> list[str]:
    """Deterministic node ordering: SCC-condensed topological, ties by name.

    Inside an SCC the internal order is alphabetical. This makes the DSM
    well-defined even when cycles exist, and avoids randomness across
    runs.
    """
    sccs = list(nx.strongly_connected_components(graph))
    cond = nx.condensation(graph, scc=sccs)
    # cond.nodes are integers indexing into sccs; condensation is a DAG.
    topo = list(nx.topological_sort(cond))
    order: list[str] = []
    for scc_idx in topo:
        order.extend(sorted(sccs[scc_idx]))
    return order


def _feedback_and_bandwidth(graph: nx.DiGraph, order: list[str]) -> tuple[float, float]:
    if graph.number_of_edges() == 0 or len(order) < 2:
        return 0.0, 0.0
    pos = {name: i for i, name in enumerate(order)}
    n = len(order)
    above = 0
    band_total = 0
    edges = 0
    for u, v in graph.edges():
        if u not in pos or v not in pos:
            continue
        i, j = pos[u], pos[v]
        if i == j:
            continue
        edges += 1
        # Convention: row = source, col = target. An edge (u -> v) with
        # pos[u] > pos[v] sits above the diagonal when rows/cols share
        # the same ordering (lower row index = earlier in topo order).
        # We use the inverse: above the diagonal when source comes
        # *after* target in topo order, i.e. an edge points "back".
        if i > j:
            above += 1
        band_total += abs(i - j)
    if edges == 0:
        return 0.0, 0.0
    return above / edges, (band_total / edges) / n


def _community_blocks(graph: nx.DiGraph) -> dict[str, int]:
    if graph.number_of_edges() == 0 or graph.number_of_nodes() < 2:
        return {n: 0 for n in graph.nodes()}
    communities = list(nx.community.greedy_modularity_communities(graph))
    mapping: dict[str, int] = {}
    for idx, comm in enumerate(communities):
        for node in comm:
            mapping[node] = idx
    return mapping


def _block_density(graph: nx.DiGraph, label: dict[str, int]) -> float:
    """Fraction of edges that stay inside the same block."""
    if graph.number_of_edges() == 0:
        return 0.0
    inside = 0
    total = 0
    for u, v in graph.edges():
        if u not in label or v not in label:
            continue
        total += 1
        if label[u] == label[v]:
            inside += 1
    return inside / total if total else 0.0


def _depth_buckets(graph: nx.DiGraph) -> dict[str, int]:
    """Bucket each node by its longest-path depth in the condensed DAG.

    Matches the spirit of the `depth` axis (longest path), but instead
    of a scalar produces a layer label per node, suitable for
    layer-grouped DSM analysis.
    """
    if graph.number_of_nodes() == 0:
        return {}
    sccs = list(nx.strongly_connected_components(graph))
    scc_of = {n: i for i, comp in enumerate(sccs) for n in comp}
    cond = nx.condensation(graph, scc=sccs)
    depth: dict[int, int] = dict.fromkeys(cond.nodes(), 0)
    for scc_idx in nx.topological_sort(cond):
        for pred in cond.predecessors(scc_idx):
            if depth[pred] + 1 > depth[scc_idx]:
                depth[scc_idx] = depth[pred] + 1
    return {n: depth[scc_of[n]] for n in graph.nodes()}


def main() -> int:
    rows: list[dict] = []
    for proj in load_manifest():
        name = proj["name"]
        print(f"# {name}", file=sys.stderr)
        try:
            graph = build_graph(clone_or_update(proj))
        except Exception as exc:
            print(f"#   SKIPPED ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue

        # Existing axes for the orthogonality math.
        mod_norm, _, _ = compute_modularity(graph)
        acy_norm, _, _ = compute_acyclicity(graph)
        dep_norm, _ = compute_depth(graph)
        eq_norm, _ = compute_equality(graph)
        n_funcs = 0
        cc_total = 0
        for _, data in graph.nodes(data=True):
            cnt = data.get("function_count", 0)
            if cnt == 0:
                continue
            n_funcs += cnt
            cc_total += data.get("cc_sum", 0)
        cc_mean = (cc_total / n_funcs) if n_funcs else 0.0
        cpx_norm = compute_complexity(cc_mean, n_funcs)
        pc, _ = compute_propagation_cost(graph)

        ig = _internal_subgraph(graph)
        order = _scc_topo_order(ig)
        feedback, bandwidth = _feedback_and_bandwidth(ig, order)
        community_label = _community_blocks(ig)
        layer_label = _depth_buckets(ig)
        block_comm = _block_density(ig, community_label)
        block_layer = _block_density(ig, layer_label)

        rows.append(
            {
                "name": name,
                "modules": ig.number_of_nodes(),
                "edges": ig.number_of_edges(),
                "feedback": feedback,
                "bandwidth": bandwidth,
                "block_comm": block_comm,
                "block_layer": block_layer,
                "mod": mod_norm,
                "acy": acy_norm,
                "dep": dep_norm,
                "eq": eq_norm,
                "cpx": cpx_norm,
                "pc": pc,
            }
        )

    rows.sort(key=lambda r: -r["feedback"])

    print("# DSM-derived signals vs the existing score axes\n")
    print("Bench: 27 projects pinned in `bench/projects.yaml`. Captured locally.\n")
    print(
        "Internal-only subgraph. Ordering: SCC-condensed topological, alphabetical inside SCCs.\n"
    )

    print("## Per-project DSM signals\n")
    print("| project | modules | edges | feedback | bandwidth | block_comm | block_layer |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in rows:
        print(
            f"| {r['name']} | {r['modules']} | {r['edges']} | "
            f"{r['feedback']:.3f} | {r['bandwidth']:.3f} | "
            f"{r['block_comm']:.3f} | {r['block_layer']:.3f} |"
        )

    print("\nLegend:")
    print("- `feedback`: above-diagonal edge fraction in topo order (0 = pure DAG layering).")
    print("- `bandwidth`: mean `|i-j|/N` across edges (low = local dependencies).")
    print("- `block_comm`: edges inside Newman-community blocks.")
    print("- `block_layer`: edges inside depth-bucketed layer blocks.")

    print("\n## Pearson r of each DSM signal against existing axes + propagation_cost\n")
    print("Values with `|r| < 0.7` are below the OECD redundancy threshold (distinct signal).\n")

    targets = {
        "modularity": [r["mod"] for r in rows],
        "acyclicity": [r["acy"] for r in rows],
        "depth": [r["dep"] for r in rows],
        "equality": [r["eq"] for r in rows],
        "complexity": [r["cpx"] for r in rows],
        "propagation_cost": [r["pc"] for r in rows],
    }
    axes_order = (
        "modularity",
        "acyclicity",
        "depth",
        "equality",
        "complexity",
        "propagation_cost",
    )
    header = "| signal | " + " | ".join(f"vs {a}" for a in axes_order) + " |"
    print(header)
    print("| --- | " + " | ".join("---:" for _ in axes_order) + " |")
    for sig in ("feedback", "bandwidth", "block_comm", "block_layer"):
        vals = [r[sig] for r in rows]
        cells = [f"{pearson(vals, targets[t]):+.3f}" for t in axes_order]
        print(f"| {sig} | " + " | ".join(cells) + " |")

    print("\n## Pairwise Pearson r among the four DSM signals\n")
    sigs = ("feedback", "bandwidth", "block_comm", "block_layer")
    print("| pair | r |")
    print("| --- | ---: |")
    for i, a in enumerate(sigs):
        for b in sigs[i + 1 :]:
            r_val = pearson([r[a] for r in rows], [r[b] for r in rows])
            print(f"| {a} ↔ {b} | {r_val:+.3f} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
