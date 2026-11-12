"""Pairs, the per-PR cap, A's pair expansion, cutoff calibration and pooling for #587.

Pure logic, no model and no I/O, so the rules in
`docs/research/EMBEDDING_ORACLE_RECALL.md` can be pinned by tests before any
embedding is computed. Every rule below is the pre-registered one; none is tuned.

The labeled item is a PAIR (PR, new function, existing function) for both
generators. A is expanded from #548's clusters; B emits one pair per new function.
Both are capped at K per PR before the cutoff is calibrated, so one PR holding 703
new functions counts for at most K pairs on either side.

archy:owns        Member, Pair, calibrate_cutoff, cap_pairs, expand_a_firing, pool_pairs
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, computed_field

K = 10  # pairs kept per PR, per generator (pre-registered)


class Member(BaseModel):
    """One function of a cluster, as the ledger and a re-parse together describe it."""

    model_config = ConfigDict(frozen=True)

    path: str
    qualname: str
    is_new: bool
    nodes: int
    shape: str


class Pair(BaseModel):
    """A (PR, new function, existing function) pair and which generators emitted it."""

    model_config = ConfigDict(frozen=True)

    pr: str
    repo: str
    new_path: str
    new_qualname: str
    new_nodes: int
    new_shape: str
    existing_path: str
    existing_qualname: str
    existing_shape: str
    similarity: float | None = None
    generators: tuple[str, ...]

    # computed_field, not @property: a payload dump drops a plain property.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def pair_id(self) -> str:
        return f"{self.pr}|{self.new_qualname}|{self.existing_qualname}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def unit_id(self) -> str:
        return f"{self.pr}|{self.new_qualname}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def exact_tier(self) -> bool:
        """Shape-identical by archy's own hasher, whatever either generator emitted."""
        return self.new_shape == self.existing_shape


def cap_pairs(pairs: Sequence[Pair], k: int = K) -> list[Pair]:
    """At most `k` pairs per PR, keeping the largest new functions.

    Ties break on the new function's path then qualified name, so the answer does
    not depend on the order the pairs arrive in.
    """
    by_pr: dict[str, list[Pair]] = defaultdict(list)
    for p in pairs:
        by_pr[p.pr].append(p)
    kept: list[Pair] = []
    for pr in sorted(by_pr):
        ranked = sorted(by_pr[pr], key=lambda p: (-p.new_nodes, p.new_path, p.new_qualname))
        kept.extend(ranked[:k])
    return kept


def expand_a_firing(
    pr: str,
    repo: str,
    members: Sequence[Member],
    *,
    overlap: Callable[[Member, Member], float],
    exists_at_both: Callable[[Member], bool],
) -> tuple[list[Pair], int]:
    """One pair per new member, each with its best existing partner in the cluster.

    The partner is the existing member that exists at base AND head with the highest
    `overlap` with the new function; ties go to the smaller (path, qualname). A new
    member with no such partner is dropped, and the count of drops is returned so the
    caller reports it instead of scoring it clean.
    """
    old = [m for m in members if not m.is_new and exists_at_both(m)]
    pairs: list[Pair] = []
    dropped = 0
    for new in (m for m in members if m.is_new):
        if not old:
            dropped += 1
            continue
        partner = min(old, key=lambda o: (-overlap(new, o), o.path, o.qualname))
        pairs.append(_pair(pr, repo, new, partner, generators=("A",)))
    return pairs, dropped


def _pair(
    pr: str,
    repo: str,
    new: Member,
    old: Member,
    *,
    generators: tuple[str, ...],
    similarity: float | None = None,
) -> Pair:
    return Pair(
        pr=pr,
        repo=repo,
        new_path=new.path,
        new_qualname=new.qualname,
        new_nodes=new.nodes,
        new_shape=new.shape,
        existing_path=old.path,
        existing_qualname=old.qualname,
        existing_shape=old.shape,
        similarity=similarity,
        generators=generators,
    )


def calibrate_cutoff(scores: Mapping[str, Sequence[float]], target: float, k: int = K) -> float:
    """The largest observed similarity whose mean capped B count per PR meets `target`.

    `scores` maps every PR of the calibration subset to the top-1 similarity of each
    eligible new function in it (a PR with none maps to `[]`, so it counts as zero).
    Pairs tied at the cutoff are all included, and a PR contributes at most `k`. When
    no observed similarity reaches the target the lowest one is returned, which keeps
    every candidate; the caller records that this happened.
    """
    if not scores:
        raise ValueError("calibration subset is empty; there is no volume to match")
    observed = sorted({s for per_pr in scores.values() for s in per_pr}, reverse=True)
    if not observed:
        raise ValueError("calibration subset has no scored function; the cutoff is undefined")
    for t in observed:
        mean = sum(min(k, sum(s >= t for s in per_pr)) for per_pr in scores.values()) / len(scores)
        if mean >= target:
            return t
    return observed[-1]


def pool_pairs(a_pairs: Sequence[Pair], b_pairs: Sequence[Pair]) -> list[Pair]:
    """The union of A's and B's pairs, one entry per pair id, sorted by id.

    A pair both generators emit is one entry tagged with both, carrying B's
    similarity, so it is labeled once and is blind to which generator found it.
    """
    merged: dict[str, Pair] = {}
    for p in [*a_pairs, *b_pairs]:
        seen = merged.get(p.pair_id)
        if seen is None:
            merged[p.pair_id] = p
            continue
        tags = tuple(sorted(set(seen.generators) | set(p.generators)))
        sim = seen.similarity if seen.similarity is not None else p.similarity
        merged[p.pair_id] = seen.model_copy(update={"generators": tags, "similarity": sim})
    return [merged[i] for i in sorted(merged)]
