"""Fused refactor-priority ranking: hotspots x edit-risk in one list.

archy already ships two refactor-priority signals through separate tools,
each with a different lens:

* ``archy_hotspots`` is *behavioral* - cyclomatic complexity x git churn
  (Tornhill / CodeScene's "Code Red"). It answers "where is the
  refactoring leverage?" and needs git history.
* ``archy_high_risk_modules`` is *structural* - the ``edit_risk`` composite
  (geometric mean of propagation cost, normalized fan-in, and instability).
  It answers "is this edit dangerous?" and needs no git.

An agent asked "what should I clean up first?" otherwise has to call both
and merge them by hand. ``compute_refactor_priorities`` does that fusion:
it gathers candidates from both lenses, ranks them by a fused priority,
and attaches a one-line rationale naming which lenses fired and why.

This is pure aggregation over the two existing primitives - no new
analysis. It deliberately does **not** invent a cross-project absolute
"refactor score": the ``priority`` field is a within-project normalized
blend (each lens scaled to its own project maximum, then summed), useful
only for ordering this project's candidates. Each row also carries the
raw component signals so the caller can see the absolute numbers.

Because the two normalized lens scores are *summed*, a module flagged by
both lenses earns a contribution from each, so all else equal it outranks
a comparable single-lens module. It is **not** a strict tier, though: a
dominant single-lens signal can still rank first - a file that churns
constantly and is highly complex but sits at the leaves of the import
graph (``edit_risk`` near zero) is genuinely the highest-leverage refactor
target and should beat a small module that merely happens to score on both
lenses. The sum captures "worth refactoring *and* dangerous to touch"
without burying a giant hotspot under trivial both-lens modules.

The honest-null case is load-bearing. The candidate set is empty when
*nothing* qualifies: no file is both complex and frequently changed
(behavioral lens), and no module clears the ``min_risk`` floor on the
structural lens. That is a real answer for the two lenses' coverage - a
small, young, or structurally clean project has nothing for these signals
to prioritize - so the function returns an empty list rather than
manufacturing a phantom #1 by rank-normalizing a set of near-zero values.
"Empty" is scoped to what the two lenses can see, not a claim that the
code is perfect: the structural lens inherits ``edit_risk``'s deliberate
blind spot for stable load-bearing sinks (instability 0 -> composite 0),
which the behavioral lens only catches once such a module starts churning.

The ``min_risk`` floor is what makes the structural side capable of being
empty; ``edit_risk`` is an absolute [0, 1] composite, so an absolute floor
is meaningful where a pure percentile would always leave a top entry. The
default of ``0.15`` is a conservative heuristic, not a cross-project
constant with absolute meaning: it trims the long tail of barely-registering
modules on a typical project, but dense graphs push many modules above it
(``top_n`` then caps the list) while very clean or small graphs may leave
it empty. Tune it per call when a project's risk distribution warrants it.
"""

from __future__ import annotations

import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy.hotspots import compute_hotspots
from archy.instability import compute_instability
from archy.reach import compute_propagation_cost
from archy.risk import compute_edit_risk

#: Default structural floor: a module must clear this ``edit_risk`` to be
#: surfaced on the structural lens. See the module docstring for the
#: rationale; overridable per call.
DEFAULT_MIN_RISK = 0.15


class RefactorPriority(BaseModel):
    """One ranked refactor candidate, fused across both lenses.

    ``lenses`` names which signal(s) flagged the module: ``"hotspot"``
    (behavioral CC x churn), ``"edit_risk"`` (structural central+fragile),
    or both. ``priority`` is the within-project ordering blend (sum of the
    two lens scores each normalized to the project maximum, so it ranges
    [0, 2] with 2 meaning "top of both lenses"); it is not comparable
    across projects, and a module strong on a single lens can outscore one
    that is weak on both lenses. The component fields carry the raw absolute
    signals.
    """

    model_config = ConfigDict(frozen=True)

    module: str
    path: str | None
    lenses: tuple[str, ...]
    priority: float
    # Behavioral lens (CC x churn). Zero when the module is not a hotspot
    # (or when git history is unavailable).
    cc_sum: int
    churn: int
    hotspot_score: int
    # Structural lens (edit-risk composite and its three terms).
    edit_risk: float
    propagation_cost: float
    instability: float
    fan_in: int
    rationale: str


def _rationale(
    lenses: tuple[str, ...],
    *,
    cc_sum: int,
    churn: int,
    edit_risk: float,
) -> str:
    """One-line judge-role explanation of why a module ranks where it does."""
    if "hotspot" in lenses and "edit_risk" in lenses:
        return (
            f"Both a complexity x churn hotspot (cc_sum={cc_sum}, churn={churn}) "
            f"and a high edit-risk module (risk={edit_risk:.2f}: central and "
            "fragile). Refactoring here cuts the most active and the most "
            "dangerous surface at once - prioritize it."
        )
    if "hotspot" in lenses:
        return (
            f"A complexity x churn hotspot (cc_sum={cc_sum}, churn={churn}) but "
            f"low structural edit-risk (risk={edit_risk:.2f}). Refactor for "
            "maintainability; regression danger is contained."
        )
    return (
        f"High edit-risk (risk={edit_risk:.2f}: central and fragile) but not a "
        "churn hotspot. Edits here are dangerous out of proportion to how often "
        "the file changes - reduce its coupling before it becomes an active "
        "hotspot."
    )


def compute_refactor_priorities(
    graph: nx.DiGraph,
    *,
    churn: dict[str, int] | None,
    min_risk: float = DEFAULT_MIN_RISK,
) -> list[RefactorPriority]:
    """Fuse hotspots and edit-risk into one ranked refactor-priority list.

    ``churn`` is the per-file commit count from :func:`archy.hotspots.git_churn`
    (pass ``None`` when the project is not under git: the behavioral lens is
    then skipped and ranking falls back to the structural lens alone, mirroring
    ``archy_hotspots``' git-absent behavior). ``min_risk`` is the structural
    floor below which a module is not surfaced on the edit-risk lens.

    Returns the full ranked candidate list (callers cap with their own
    ``top_n``). An **empty list** is a meaningful result: nothing is both
    complex and churned, and nothing clears ``min_risk`` - there is genuinely
    nothing to prioritize.
    """
    # Behavioral lens. `compute_hotspots` already filters to files with both
    # cc_sum > 0 and churn > 0, so every returned row is a real hotspot.
    hotspots = compute_hotspots(graph, churn=churn) if churn is not None else []
    hotspot_by_module = {h.module: h for h in hotspots}

    # Structural lens. Mirror `_run_high_risk_modules`: compute the composite
    # plus its three terms so each row can show *why* it ranks.
    edit_risk = compute_edit_risk(graph)
    instability = compute_instability(graph)
    _, propagation_cost = compute_propagation_cost(graph)

    # Candidate set: all hotspots plus every module clearing the structural
    # floor. A module may appear via either or both lenses.
    risky = {m for m, r in edit_risk.items() if r >= min_risk}
    candidates = set(hotspot_by_module) | risky
    if not candidates:
        return []

    # Per-lens project maxima for the normalized ordering blend. Guarded so a
    # single-candidate or single-lens project never divides by zero.
    max_hotspot = max((h.score for h in hotspots), default=0)
    max_risk = max((edit_risk.get(m, 0.0) for m in candidates), default=0.0)

    rows: list[RefactorPriority] = []
    for module in candidates:
        hs = hotspot_by_module.get(module)
        risk = edit_risk.get(module, 0.0)

        lenses: list[str] = []
        if hs is not None:
            lenses.append("hotspot")
        if module in risky:
            lenses.append("edit_risk")

        norm_h = (hs.score / max_hotspot) if (hs and max_hotspot) else 0.0
        norm_r = (risk / max_risk) if max_risk else 0.0

        # Path: prefer the hotspot row's path, else the graph node attribute.
        path = hs.path if hs is not None else graph.nodes.get(module, {}).get("path")

        cc_sum = hs.cc_sum if hs is not None else 0
        churn_n = hs.churn if hs is not None else 0
        rows.append(
            RefactorPriority(
                module=module,
                path=str(path) if path else None,
                lenses=tuple(lenses),
                priority=norm_h + norm_r,
                cc_sum=cc_sum,
                churn=churn_n,
                hotspot_score=hs.score if hs is not None else 0,
                edit_risk=risk,
                propagation_cost=propagation_cost.get(module, 0.0),
                instability=instability.get(module, 0.0),
                fan_in=sum(
                    1
                    for p in graph.predecessors(module)
                    if not graph.nodes[p].get("external")
                ),
                rationale=_rationale(
                    tuple(lenses), cc_sum=cc_sum, churn=churn_n, edit_risk=risk
                ),
            )
        )

    # Highest fused priority first; break ties by raw edit_risk (structural
    # danger is the tie-breaker that matters most to an editing agent), then
    # by hotspot score, then qualname for stable output.
    rows.sort(key=lambda r: (-r.priority, -r.edit_risk, -r.hotspot_score, r.module))
    return rows
