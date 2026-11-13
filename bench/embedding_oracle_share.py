"""Strict labels and the headline "found only by B" share for #587.

- A pair is TRUE only if BOTH labelers say TRUE (strict); either labeler's UNSCORABLE
  drops it, never scores it FALSE.
- A (PR, new function) unit is TRUE if any labeled pair for it is TRUE; found by A or B
  by which generator emitted a TRUE pair; only by B if found by B and not by A.
- The headline drops exact-tier pairs BEFORE units are formed.

Recall here is relative to the pooled set, never absolute.

archy:owns        Share, resolve_labels, share
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from embedding_oracle_pairs import Pair  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
from embedding_oracle_stats import interval, ratio  # ty: ignore[unresolved-import]  (same)
from pydantic import BaseModel, ConfigDict

MIN_TRUE_UNITS = 40
MIN_TRUE_REPOS = 15
MIN_TRUE_PRS = 20


def resolve_labels(a: Mapping[str, str], b: Mapping[str, str]) -> dict[str, bool]:
    """Strict truth per pair id: both TRUE. A pair either labeler calls UNSCORABLE is absent.

    The labelers must have seen the same pairs; a mismatch is a refusal, since a pair one
    of them never saw would otherwise be silently scored as the other's answer.
    """
    if set(a) != set(b):
        raise ValueError(f"labelers saw different pairs: {len(set(a) ^ set(b))} ids differ")
    out: dict[str, bool] = {}
    for pid in sorted(a):
        if "UNSCORABLE" in (a[pid], b[pid]):
            continue
        out[pid] = a[pid] == "TRUE" and b[pid] == "TRUE"
    return out


class Share(BaseModel):
    """The headline: how much of the strict TRUE pool only B found (exact-tier excluded)."""

    model_config = ConfigDict(frozen=True)

    true_units: int
    only_b: int
    only_a: int
    both: int
    repos: int
    prs: int
    measurable: bool
    point: float | None
    low: float | None
    high: float | None


class _Unit(BaseModel):
    generators: set[str]
    repos: set[str]
    prs: set[str]


def share(pairs: Sequence[Pair], truth: Mapping[str, bool]) -> Share:
    """Units from labeled, non-exact pairs; the floors decide whether the share is measurable."""
    labeled = [p for p in pairs if p.pair_id in truth and not p.exact_tier]
    units = _true_units([p for p in labeled if truth[p.pair_id]])
    n = len(units)
    only_b = _found_only_by(units, "B")
    only_a = _found_only_by(units, "A")
    repos = len(set().union(*(u.repos for u in units.values())))
    prs = len(set().union(*(u.prs for u in units.values())))
    low, high = interval(only_b, n)
    return Share(
        true_units=n,
        only_b=only_b,
        only_a=only_a,
        both=n - only_b - only_a,
        repos=repos,
        prs=prs,
        measurable=n >= MIN_TRUE_UNITS and repos >= MIN_TRUE_REPOS and prs >= MIN_TRUE_PRS,
        point=ratio(only_b, n),
        low=low,
        high=high,
    )


def _found_only_by(units: Mapping[str, _Unit], generator: str) -> int:
    return sum(u.generators == {generator} for u in units.values())


def _true_units(true_pairs: Sequence[Pair]) -> dict[str, _Unit]:
    """One unit per (PR, new function), remembering which generators found it TRUE."""
    units: dict[str, _Unit] = {}
    for p in true_pairs:
        unit = units.setdefault(p.unit_id, _Unit(generators=set(), repos=set(), prs=set()))
        unit.generators.update(p.generators)
        unit.repos.add(p.repo)
        unit.prs.add(p.pr)
    return units
