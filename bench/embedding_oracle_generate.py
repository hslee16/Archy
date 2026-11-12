"""Generator B for #587: top-1 cosine neighbour, calibration volume, cutoff, per-PR cap.

Pure logic over normalised vectors and the per-PR unit split; no I/O, no model.

For each new function the neighbour is the existing function (path and qualified name
present at BOTH base and head) with the highest cosine. A new function never pairs with
itself, nor with another function the same diff added, which cannot be a neighbour because
neighbours come from base. Ties break on the smaller (path, qualified name), so the answer
does not depend on row order.

The cutoff is the pre-registered equal-volume rule (`calibrate_cutoff`). What counts in the
calibration denominator is an AMBIGUITY of the pre-registration, so it is a named parameter:

* `all-measured` (default): every measured calibration PR counts, an empty one as zero. This
  is the denominator the target itself uses (A's capped pairs over ALL 1,049 measured PRs,
  83% of which hold no A pair), so B and A are averaged over the same kind of PR.
* `scored-only`: only PRs with at least one scored new function count. It raises the cutoff.

The cap (K = 10 per PR, largest new function first) is applied AFTER the cutoff.

archy:owns        DENOMINATORS, CutoffResult, PrScores, Scored, b_pairs, calibrate,
                  calibration_scores, mean_capped, score_pr
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np  # ty: ignore[unresolved-import]  (bench-only: `uv run --with numpy`)
from embedding_oracle_pairs import K, Pair, calibrate_cutoff, cap_pairs
from embedding_oracle_units import PrUnits, UnitFn
from embedding_oracle_vectors import VectorError, VectorSet

DENOMINATORS = ("all-measured", "scored-only")
IDENTICAL = 1.0 - 1e-6  # similarity reported as "1.0": float32 cannot say exactly 1.0


@dataclass(frozen=True)
class Scored:
    """A new function and its top-1 neighbour with the cosine between them."""

    new: UnitFn
    existing: UnitFn
    sim: float


@dataclass(frozen=True)
class PrScores:
    """Everything B measured for one PR, before any cutoff."""

    pr: str
    repo: str
    n_new: int
    scored: tuple[Scored, ...]
    no_neighbour: int
    lost_not_at_head: int


@dataclass(frozen=True)
class CutoffResult:
    cutoff: float
    reached_target: bool
    mean_at_cutoff: float


def _vec(vs: VectorSet, fn: UnitFn) -> np.ndarray:
    if not vs.has(fn.id):
        raise VectorError(f"no vector for {fn.path}:{fn.qualname} (id {fn.id[:12]})")
    return vs.get(fn.id)


def _best(sims: np.ndarray, existing: Sequence[UnitFn]) -> int:
    tied = np.flatnonzero(sims == sims.max())
    return int(min(tied, key=lambda i: existing[i].name_key))


def score_pr(units: PrUnits, vs: VectorSet) -> PrScores:
    """Top-1 neighbour of every new function in the PR."""
    if not units.existing:
        return PrScores(
            units.pr, units.repo, len(units.new), (), len(units.new), units.lost_not_at_head
        )
    mat = np.stack([_vec(vs, e) for e in units.existing])
    scored: list[Scored] = []
    for new in units.new:
        sims = mat @ _vec(vs, new)
        for i, e in enumerate(units.existing):
            if e.name_key == new.name_key:
                sims[i] = -np.inf  # never itself
        if np.isinf(sims.max()):
            continue
        j = _best(sims, units.existing)
        scored.append(Scored(new, units.existing[j], float(sims[j])))
    return PrScores(
        units.pr,
        units.repo,
        len(units.new),
        tuple(scored),
        len(units.new) - len(scored),
        units.lost_not_at_head,
    )


def calibration_scores(
    per_pr: Mapping[str, Sequence[float]], empty_prs: Iterable[str], denominator: str
) -> dict[str, list[float]]:
    """Similarities per calibration PR, shaped for `calibrate_cutoff`.

    `empty_prs` are measured PRs with no new function (they count as zero under
    `all-measured`). A PR whose measurement failed is in neither argument: dropped, never
    scored clean.
    """
    if denominator not in DENOMINATORS:
        raise ValueError(f"denominator must be one of {DENOMINATORS}, not {denominator!r}")
    scores = {pr: list(sims) for pr, sims in per_pr.items()}
    if denominator == "scored-only":
        return {pr: sims for pr, sims in scores.items() if sims}
    return {**{pr: [] for pr in empty_prs}, **scores}


def mean_capped(scores: Mapping[str, Sequence[float]], t: float, k: int = K) -> float:
    """Mean over PRs of how many scores reach `t`, a PR counting at most `k`."""
    return sum(min(k, sum(s >= t for s in sims)) for sims in scores.values()) / len(scores)


def calibrate(scores: Mapping[str, Sequence[float]], target: float) -> CutoffResult:
    cutoff = calibrate_cutoff(scores, target)
    mean = mean_capped(scores, cutoff)
    return CutoffResult(cutoff, mean >= target, mean)


def to_pair(s: Scored) -> Pair:
    return Pair(
        pr=s.new.pr,
        repo=s.new.repo,
        new_path=s.new.path,
        new_qualname=s.new.qualname,
        new_nodes=s.new.nodes,
        new_shape=s.new.shape,
        existing_path=s.existing.path,
        existing_qualname=s.existing.qualname,
        existing_shape=s.existing.shape,
        similarity=s.sim,
        generators=("B",),
    )


def b_pairs(prs: Iterable[PrScores], cutoff: float) -> list[Pair]:
    """B's candidates: top-1 pairs at or above the cutoff, then at most K per PR."""
    passing = [to_pair(s) for ps in prs for s in ps.scored if s.sim >= cutoff]
    return cap_pairs(passing)


def volume_report(prs: Sequence[PrScores]) -> dict[str, int]:
    """Counts the results must report beside the cutoff: scored, identical text, similarity ~1.0."""
    scored = [s for ps in prs for s in ps.scored]
    return {
        "scored": len(scored),
        "identical_text": sum(s.new.id == s.existing.id for s in scored),
        "similarity_at_one": sum(s.sim >= IDENTICAL for s in scored),
        "no_neighbour": sum(ps.no_neighbour for ps in prs),
        "lost_not_at_head": sum(ps.lost_not_at_head for ps in prs),
    }
