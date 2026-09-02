#!/usr/bin/env python
"""Score an agent's working-tree diff as structurally bad, or not (#348).

This defines the **primary outcome** for the Q1b A/B, so it is deliberately
separate from the code that drives agents: the measure has to be settled and
tested before any paid run depends on it.

## The definition, inherited rather than reinvented

`cycle_regression` is byte-for-byte the definition Q1a used in
`bench/inloop_prevalence.py`:

    cycle_count rose  AND  some module that was acyclic at the base commit now
    sits inside a strongly-connected component

Both halves matter. A rising count alone can be a relabelled pre-existing
tangle; a newly-cyclic module alone can appear when an SCC merely splits. Q1a
established this as archy's FP-free gate, and reusing it verbatim is what makes
the agent arm comparable to the 0.5% human control baseline it must beat.

## What this corpus cannot measure, stated up front

The protocol's primary outcome is "introduced cycle OR declared-layer/contract
violation OR score regression beyond a noise floor". **SWE-bench repositories
have no `archy.yaml`**, so the declared-layer arm cannot fire in THIS file.

That is not a small caveat. Per #316, archy's differentiator is the *normative*
job (rules the user declares), and this corpus supplies no declared intent. The
honest options were to report the narrower claim, or to author `archy.yaml` per
repo, which makes the measurer the author of the intent being measured.

**Resolved since, the second way** (#353/#354/#355): `bench/q1b_layers/` holds
an authored, validated config for all six repos, and `bench/q1b_run.py` gates on
`cycle_regression OR an introduced layer violation`. The bias that creates is
carried in `bench/q1b_layers/README.md`, which is where it belongs. This file
still defines the cycle half alone, deliberately: it is the piece that has to
stay byte-comparable with Q1a's human baseline.

## Score is a supporting outcome, never the gate

Q1a Finding 3: score drops on 29% of commits and 98% of those are under 0.005,
and on the single worst structural event in that corpus (an 8-module SCC) the
composite score moved *up* by 0.012. So `score_regression` is recorded but does
not decide `structurally_bad`. The 0.005 floor is also a human-corpus value and
must be re-derived on agent diffs before it is used for anything.

archy:owns        StructuralMeasure, StructuralVerdict, compare, cyclic_nodes, measure,
                  score_working_tree
archy:mirrored-by StructuralVerdict also in bench.greenfield_eval
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from archy.graph import build_graph
from archy.score import compute_score


def cyclic_nodes(g: nx.DiGraph) -> set[str]:
    """Every module inside a non-trivial SCC, plus self-importers.

    Duplicated from `bench/inloop_prevalence.py` rather than imported, because
    the two benches must stay comparable even if one is later edited: the agent
    arm's numbers are only meaningful against Q1a's human control if both use
    the identical definition. A shared import would make a change to one
    silently redefine the other's published baseline.
    """
    out: set[str] = set()
    for scc in nx.strongly_connected_components(g):
        if len(scc) > 1:
            out |= scc
    for node in g.nodes:
        if g.has_edge(node, node):
            out.add(node)
    return out


class StructuralMeasure(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: float
    acyclicity: float
    cycle_count: int
    modules: int
    edges: int
    cyclic: tuple[str, ...]
    node_scc_size: dict[str, int]


class StructuralVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    structurally_bad: bool
    cycle_regression: bool
    score_regression: bool
    new_cyclic_modules: tuple[str, ...]
    max_new_scc: int
    overall_before: float
    overall_after: float
    overall_delta: float
    cycle_count_before: int
    cycle_count_after: int
    # Null when either graph could not be built (empty package, syntax damage
    # so severe tree-sitter yields nothing). A run that cannot be measured is
    # not a run that passed, and the pilot must drop it rather than score it 0.
    measurable: bool = True


def measure(pkg_path: Path) -> StructuralMeasure | None:
    try:
        g = build_graph(pkg_path)
    except Exception:
        return None
    if g.number_of_nodes() == 0:
        return None
    s = compute_score(g)
    node_scc_size: dict[str, int] = {}
    for scc in nx.strongly_connected_components(g):
        if len(scc) > 1:
            for node in scc:
                node_scc_size[node] = len(scc)
    return StructuralMeasure(
        overall=s.overall,
        acyclicity=s.acyclicity,
        cycle_count=s.inputs.cycle_count,
        modules=g.number_of_nodes(),
        edges=g.number_of_edges(),
        cyclic=tuple(sorted(cyclic_nodes(g))),
        node_scc_size=node_scc_size,
    )


def compare(before: StructuralMeasure, after: StructuralMeasure) -> StructuralVerdict:
    new_cyclic = tuple(sorted(set(after.cyclic) - set(before.cyclic)))
    cycle_reg = after.cycle_count > before.cycle_count and len(new_cyclic) > 0
    return StructuralVerdict(
        # The gate is the cycle signal ALONE. Score is recorded but excluded
        # deliberately: see the module docstring on Q1a Finding 3.
        structurally_bad=cycle_reg,
        cycle_regression=cycle_reg,
        score_regression=after.overall < before.overall - 1e-9,
        new_cyclic_modules=new_cyclic,
        max_new_scc=max((after.node_scc_size.get(m, 0) for m in new_cyclic), default=0),
        overall_before=round(before.overall, 5),
        overall_after=round(after.overall, 5),
        overall_delta=round(after.overall - before.overall, 5),
        cycle_count_before=before.cycle_count,
        cycle_count_after=after.cycle_count,
    )


def _unmeasurable() -> StructuralVerdict:
    return StructuralVerdict(
        structurally_bad=False,
        cycle_regression=False,
        score_regression=False,
        new_cyclic_modules=(),
        max_new_scc=0,
        overall_before=0.0,
        overall_after=0.0,
        overall_delta=0.0,
        cycle_count_before=0,
        cycle_count_after=0,
        measurable=False,
    )


def score_working_tree(repo_dir: Path, pkg: str, base_ref: str) -> StructuralVerdict:
    """Compare the agent-edited working tree against `base_ref`.

    The base measurement runs in a throwaway `git worktree`, never by checking
    out inside `repo_dir`. Stashing or checking out would mutate the very edits
    being measured, and a crash mid-measurement would destroy a paid agent run.
    A detached worktree is read-only with respect to the agent's tree.
    """
    after = measure(repo_dir / pkg)
    if after is None:
        return _unmeasurable()

    with tempfile.TemporaryDirectory(prefix="q1b_base_") as tmp:
        wt = Path(tmp) / "base"
        add = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "worktree",
                "add",
                "--detach",
                "--quiet",
                str(wt),
                base_ref,
            ],
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            return _unmeasurable()
        try:
            before = measure(wt / pkg)
        finally:
            subprocess.run(
                ["git", "-C", str(repo_dir), "worktree", "remove", "--force", str(wt)],
                capture_output=True,
            )
    if before is None:
        return _unmeasurable()
    return compare(before, after)
