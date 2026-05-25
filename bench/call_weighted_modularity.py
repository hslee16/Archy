"""Bench experiment: does call-weighted Newman Q improve the modularity axis?

For each project in `projects.yaml`, builds the import graph and computes
two values of Newman Q:

- `Q_unweighted`: the current `compute_modularity` shape; every edge
  counts equally.
- `Q_weighted`: same algorithm, edges weighted by `call_count` (zero for
  import-only edges).

Reports per-project Q, the delta, and the absolute-rank shift. Also
computes Pearson correlation of `Q_weighted_normalized` against the
other four score axes to check whether call-weighting preserves the
v0.20 orthogonality picture.

Read by `docs/research/AXIS_REVIEW.md` recommendation 2; output drives the
decision on whether to refine the modularity axis with call weights.

Usage:
    uv run --with networkx --with pyyaml --with tree-sitter \
        --with tree-sitter-python --with pydantic --with click \
        python bench/call_weighted_modularity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

# Reuse the bench scaffolding for clone-and-checkout so this script does
# not drift from `bench/run.py`'s reproducibility guarantees.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import clone_or_update, load_manifest, pearson

# Importing archy from the working tree (we are inside the repo).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from archy.graph import build_graph
from archy.score import (
    compute_acyclicity,
    compute_complexity,
    compute_depth,
    compute_equality,
    compute_modularity,
)


def _project_src_root(project_dir: Path, name: str) -> Path:
    """Best-effort guess at the importable Python source root.

    Matches what `archy score` does when invoked on the project: pass the
    repo root and let `build_graph` walk for packages. Some projects keep
    their package under `src/`, others at the repo root. `build_graph`
    handles both via `_discover_modules`, so we just hand it the repo
    root and let it sort itself out. The exception is name overrides
    like sqlalchemy which lives at `lib/sqlalchemy`; for now we accept
    the repo-root default and let `build_graph` pick up whatever it
    finds, matching `bench/run.py`'s behavior.
    """
    return project_dir


def _qw(graph: nx.DiGraph) -> tuple[float, int]:
    """Call-weighted Newman Q. Returns (Q, n_communities).

    Weighted on `call_count`. Edges without `call_count` (import-only
    edges) get the NetworkX default behavior under weighted aggregation,
    which depends on the algorithm; greedy_modularity_communities and
    modularity both interpret a missing attribute as weight 0 only if
    `weight` is passed and the attribute is absent. To avoid silent
    weight=0 surprises, we materialize a `_w` attribute on every edge:
    `call_count` when present and positive, else 1 (the unweighted
    fallback for import-only edges that nevertheless carry structural
    signal).

    Two alternative weight policies were considered and rejected:
    - All-edges-weight-zero-when-no-calls. This collapses pure plug-in
      shapes (starlette, scrapy) to trivial Q values because most of
      their edges become weight-zero. The structural information is
      real and shouldn't be discarded.
    - `call_count` directly, missing-as-zero. Same problem: pure
      import-only edges vanish from the modularity computation, biasing
      against shapes whose coupling is attribute-access rather than
      function-call. Falling back to weight=1 for those edges keeps
      every structural edge in play; calls just amplify the
      already-counted ones.
    """
    if graph.number_of_edges() == 0 or graph.number_of_nodes() < 2:
        return 1.0, max(graph.number_of_nodes(), 1)
    weighted = graph.copy()
    for _, _, data in weighted.edges(data=True):
        cc = data.get("call_count", 0)
        data["_w"] = cc if cc > 0 else 1
    communities = list(nx.community.greedy_modularity_communities(weighted, weight="_w"))
    raw_q = float(nx.community.modularity(weighted, communities, weight="_w"))
    return raw_q, len(communities)


def _normalize_q(q: float) -> float:
    """Match `compute_modularity`'s normalization: (Q + 0.5) / 1.5 -> [0,1]."""
    return max(0.0, min(1.0, (q + 0.5) / 1.5))


def main() -> int:
    rows: list[dict] = []
    for proj in load_manifest():
        name = proj["name"]
        print(f"# {name}", file=sys.stderr)
        try:
            src_root = _project_src_root(clone_or_update(proj), name)
            graph = build_graph(src_root)
        except Exception as exc:
            print(f"#   SKIPPED ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue

        # Reference axes for orthogonality math.
        mod_norm, _, raw_q_unweighted = compute_modularity(graph)
        acy_norm, _, _ = compute_acyclicity(graph)
        dep_norm, _ = compute_depth(graph)
        eq_norm, _ = compute_equality(graph)

        # CC for the complexity axis.
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

        # The experimental signal.
        raw_q_weighted, _ = _qw(graph)
        q_weighted_norm = _normalize_q(raw_q_weighted)

        rows.append(
            {
                "name": name,
                "modules": graph.number_of_nodes(),
                "edges": graph.number_of_edges(),
                "q_unweighted": raw_q_unweighted,
                "q_weighted": raw_q_weighted,
                "mod_norm": mod_norm,
                "q_weighted_norm": q_weighted_norm,
                "acy": acy_norm,
                "dep": dep_norm,
                "eq": eq_norm,
                "cpx": cpx_norm,
            }
        )

    rows.sort(key=lambda r: -r["q_weighted"])

    print("# Call-weighted Newman Q vs unweighted (the current `modularity` axis)\n")
    print("Bench: 27 projects pinned in `bench/projects.yaml`. Captured locally.\n")
    print("## Per-project Q comparison\n")
    print("| project | modules | edges | Q_unweighted | Q_weighted | delta |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for r in rows:
        delta = r["q_weighted"] - r["q_unweighted"]
        print(
            f"| {r['name']} | {r['modules']} | {r['edges']} | "
            f"{r['q_unweighted']:+.3f} | {r['q_weighted']:+.3f} | "
            f"{delta:+.3f} |"
        )

    deltas = [r["q_weighted"] - r["q_unweighted"] for r in rows]
    sorted_dn = sorted(deltas)
    n = len(sorted_dn)
    median = sorted_dn[n // 2] if n else 0.0
    print(
        f"\nMean delta = {sum(deltas) / n:+.3f}; "
        f"median = {median:+.3f}; "
        f"min = {min(deltas):+.3f}; max = {max(deltas):+.3f}.\n"
    )

    # Rank shift: how much does the project ordering change between the two Q values?
    by_un = {r["name"]: i for i, r in enumerate(sorted(rows, key=lambda r: -r["q_unweighted"]))}
    by_w = {r["name"]: i for i, r in enumerate(sorted(rows, key=lambda r: -r["q_weighted"]))}
    shifts = sorted(
        ((name, by_w[name] - by_un[name]) for name in by_un),
        key=lambda x: -abs(x[1]),
    )
    print("## Rank shifts (negative = moved up in the weighted ranking)\n")
    print("| project | unweighted rank | weighted rank | delta |")
    print("| --- | ---: | ---: | ---: |")
    for name, shift in shifts:
        print(f"| {name} | {by_un[name] + 1} | {by_w[name] + 1} | {shift:+d} |")

    # Orthogonality picture under the weighted signal.
    print("\n## Pearson r of Q_weighted_normalized against the other axes\n")
    print("(Compare against the unweighted-Q correlations in `RESEARCH_METRICS.md` sec 16.)\n")
    qw = [r["q_weighted_norm"] for r in rows]
    for label, vals in [
        ("acyclicity", [r["acy"] for r in rows]),
        ("depth", [r["dep"] for r in rows]),
        ("equality", [r["eq"] for r in rows]),
        ("complexity", [r["cpx"] for r in rows]),
        ("modularity_unweighted", [r["mod_norm"] for r in rows]),
    ]:
        r_val = pearson(qw, vals)
        print(f"- {label:25s} r = {r_val:+.3f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
