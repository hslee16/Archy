"""Reach queries over an import graph: propagation cost, and the implicit
package-`__init__` edges any transitive-reach question needs.

MacCormack propagation cost and per-module reverse-reach fractions.

For an internal subgraph with `N` modules, propagation cost is the fraction
of the system that, on average, can be affected by a change to a randomly
chosen module. Formally:

    propagation_cost = sum over internal nodes of `|reverse_closure(n)|` / N^2

where `reverse_closure(n)` is the set of internal modules that transitively
import `n` (so a change to `n` could ripple to them) together with `n`
itself. External nodes are excluded because they are not subject to edits
inside the project.

Per-module value `propagation_cost[n] = |reverse_closure(n)| / N` is the
fraction of the project that depends on `n`. This is the per-module
"blast radius" for editing `n`, useful as an agent-facing diagnostic. The
project-level value equals the mean of the per-module values.

Empirically the most-validated single architectural metric in the
defect-prediction literature: multiple Spearman-significant studies
linking low propagation cost to lower bug rate and lower maintenance
cost, starting with MacCormack-Rusnak-Baldwin (2006) and replicated in
the 2026 architectural-technical-debt literature.

See `docs/research/RESEARCH_METRICS.md` section 3 for the relationship to Lakos's
NCCD (same metric family, different normalization).
"""

from __future__ import annotations

import networkx as nx


def compute_propagation_cost(graph: nx.DiGraph) -> tuple[float, dict[str, float]]:
    """Return (project_propagation_cost, per_module_fractions).

    Operates on internal nodes only (`external=True` nodes are excluded).
    A graph with zero internal nodes returns `(0.0, {})`; single-node
    cases return `1.0` per the natural formula because the one node IS
    the whole project.
    """
    internal = {n for n, d in graph.nodes(data=True) if not d.get("external")}
    n = len(internal)
    if n == 0:
        return 0.0, {}

    per_module: dict[str, float] = {}
    total = 0
    for node in internal:
        ancestors = nx.ancestors(graph, node) & internal
        # Reverse closure includes the node itself, so an isolated module
        # contributes 1/N rather than 0 to propagation cost.
        reach = len(ancestors) + 1
        per_module[node] = reach / n
        total += reach
    return total / (n * n), per_module


def package_init_edges(graph: nx.DiGraph) -> list[tuple[str, str]]:
    """The implicit `submodule -> parent package` edges Python guarantees.

    Importing `a.b.c` executes `a/__init__.py` and `a/b/__init__.py` first, so
    whatever those packages import is reachable from `a.b.c` even though no
    import statement in `a/b/c.py` says so. `build_graph` records only written
    import statements, so these edges are absent from the graph it returns.

    That absence is invisible to most of archy (direct-edge layer checks and the
    metrics do not ask reach questions) but it is fatal to `required:` rules: the
    idiomatic way to guarantee a submodule reaches a registry is to import it
    from the package `__init__.py` once, and without these edges every submodule
    in such a package looks unreachable to its own bootstrap.

    Returned as a list rather than added to the graph on purpose. Adding them
    unconditionally would move edge counts, the DSM, propagation cost, Martin's
    I, and possibly cycle detection on every codebase archy has ever scored, for
    the benefit of one feature. Callers that need reach semantics opt in via
    `with_package_init_edges`; everything else keeps the graph it always had.

    External nodes are skipped: `_external_target` already collapses them to a
    single top-level node, so they have no parents in the graph to speak of.
    """
    edges: list[tuple[str, str]] = []
    for node, data in graph.nodes(data=True):
        if data.get("external"):
            continue
        parts = node.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            if parent == node:
                continue
            parent_data = graph.nodes.get(parent)
            if parent_data is None or parent_data.get("external"):
                continue
            edges.append((node, parent))
    edges.sort()
    return edges


def with_package_init_edges(graph: nx.DiGraph) -> nx.DiGraph:
    """A copy of `graph` carrying `package_init_edges`, for reach queries only.

    The added edges are tagged `implicit="package_init"` so a consumer that
    renders a path can say which hops the source code does not literally state.
    """
    augmented = graph.copy()
    for source, target in package_init_edges(graph):
        if not augmented.has_edge(source, target):
            augmented.add_edge(source, target, implicit="package_init", lines=())
    return augmented
