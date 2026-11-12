"""Labels in, pre-registered verdict out, for #587.

The rules are the ones in `docs/research/EMBEDDING_ORACLE_RECALL.md`, each in one
small function so a boundary cannot drift:

- A pair is TRUE only if BOTH labelers say TRUE (strict); either labeler's UNSCORABLE
  drops it, never scores it FALSE.
- A (PR, new function) unit is TRUE if any labeled pair for it is TRUE; found by A or B
  by which generator emitted a TRUE pair; only by B if found by B and not by A.
- The headline share drops exact-tier pairs BEFORE units are formed.
- B precision is over all labeled B pairs; its interval is a bootstrap clustered by PR.
- A bar is applied to the point estimate only when its interval does not straddle it;
  otherwise the verdict is null.

Recall here is relative to the pooled set, never absolute.

archy:owns        BPrecision, OracleVerdict, Share, b_precision, resolve_labels, share,
                  verdict, wilson
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence

from embedding_oracle_pairs import Pair  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
from pydantic import BaseModel, ConfigDict

PASS_PRECISION = 0.60
KILL_PRECISION = 0.40
PASS_SHARE = 0.30
KILL_SHARE = 0.10
MIN_B_SCORABLE = 30
MIN_B_REPOS = 10
MIN_TRUE_UNITS = 40
MIN_TRUE_REPOS = 15
MIN_TRUE_PRS = 20
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 587
Z95 = 1.959963984540054


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


def wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson 95% interval for k of n; the whole unit interval when n is 0."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + Z95**2 / n
    centre = (p + Z95**2 / (2 * n)) / denom
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


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


def share(pairs: Sequence[Pair], truth: Mapping[str, bool]) -> Share:
    """Units from labeled, non-exact pairs; the floors decide whether the share is measurable."""
    labeled = [p for p in pairs if p.pair_id in truth and not p.exact_tier]
    units: dict[str, dict[str, set[str]]] = {}
    for p in labeled:
        if truth[p.pair_id]:
            slot = units.setdefault(p.unit_id, {"gens": set(), "repos": set(), "prs": set()})
            slot["gens"].update(p.generators)
            slot["repos"].add(p.repo)
            slot["prs"].add(p.pr)
    only_b = sum(s["gens"] == {"B"} for s in units.values())
    only_a = sum(s["gens"] == {"A"} for s in units.values())
    n = len(units)
    repos = len({r for s in units.values() for r in s["repos"]})
    prs = len({r for s in units.values() for r in s["prs"]})
    measurable = n >= MIN_TRUE_UNITS and repos >= MIN_TRUE_REPOS and prs >= MIN_TRUE_PRS
    low, high = wilson(only_b, n) if n else (None, None)
    return Share(
        true_units=n,
        only_b=only_b,
        only_a=only_a,
        both=n - only_b - only_a,
        repos=repos,
        prs=prs,
        measurable=measurable,
        point=only_b / n if n else None,
        low=low,
        high=high,
    )


class BPrecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    scorable: int
    repos: int
    true: int
    point: float | None
    low: float | None
    high: float | None


def b_precision(pairs: Sequence[Pair], truth: Mapping[str, bool]) -> BPrecision:
    """Precision over every labeled B pair (exact tier included), with a PR-clustered interval."""
    scored = [p for p in pairs if "B" in p.generators and p.pair_id in truth]
    clusters: dict[str, list[bool]] = {}
    for p in scored:
        clusters.setdefault(p.pr, []).append(truth[p.pair_id])
    n = len(scored)
    k = sum(truth[p.pair_id] for p in scored)
    low, high = _cluster_bootstrap(list(clusters.values()))
    return BPrecision(
        scorable=n,
        repos=len({p.repo for p in scored}),
        true=k,
        point=k / n if n else None,
        low=low,
        high=high,
    )


def _cluster_bootstrap(clusters: Sequence[Sequence[bool]]) -> tuple[float | None, float | None]:
    """Percentile interval of precision, resampling whole PRs with a fixed seed."""
    if not clusters:
        return None, None
    rng = random.Random(BOOTSTRAP_SEED)
    stats: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        draw = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        total = sum(len(c) for c in draw)
        if total:
            stats.append(sum(sum(c) for c in draw) / total)
    stats.sort()
    return stats[int(0.025 * len(stats))], stats[min(len(stats) - 1, int(0.975 * len(stats)))]


class OracleVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: str  # pass | kill | null | not_measurable
    reason: str


def _definite_kill(prec: BPrecision, headline: Share) -> str | None:
    """Why a bar is crossed with its whole interval on the kill side, or None."""
    if prec.high is not None and prec.high < KILL_PRECISION:
        return f"B precision {prec.point:.3f}, interval top {prec.high:.3f} < 0.40"
    if headline.measurable and headline.high is not None and headline.high < KILL_SHARE:
        return f"only-by-B share {headline.point:.3f}, interval top {headline.high:.3f} < 0.10"
    return None


def _definite_pass(prec: BPrecision, headline: Share) -> bool:
    """Both bars cleared with the whole interval above them, and the share measurable."""
    lows = (prec.low, headline.low)
    if not headline.measurable or any(v is None for v in lows):
        return False
    return (prec.low or 0.0) >= PASS_PRECISION and (headline.low or 0.0) >= PASS_SHARE


def verdict(prec: BPrecision, headline: Share) -> OracleVerdict:
    """The pre-registered rule.

    Not measurable below B's scorable floor. Kill is EITHER bar crossed with its whole
    interval on the kill side, and stands whether or not the other bar is measurable.
    Pass needs BOTH bars cleared with the whole interval above them, so an unmeasurable
    share can never pass. Anything else is null, or not measurable when the share's
    floor is what stopped a verdict. An interval that straddles a bar is never decided
    by its point estimate.
    """
    if prec.scorable < MIN_B_SCORABLE or prec.repos < MIN_B_REPOS:
        return OracleVerdict(
            outcome="not_measurable",
            reason=f"{prec.scorable} scorable B pairs from {prec.repos} repos; "
            f"need {MIN_B_SCORABLE} from {MIN_B_REPOS}",
        )
    killed = _definite_kill(prec, headline)
    if killed is not None:
        return OracleVerdict(outcome="kill", reason=killed)
    if _definite_pass(prec, headline):
        return OracleVerdict(
            outcome="pass", reason="precision and only-by-B share clear their bars"
        )
    if not headline.measurable:
        return OracleVerdict(
            outcome="not_measurable",
            reason="B precision is not a kill but the TRUE pool is below its floor, so no "
            "pass or share verdict is declared",
        )
    return OracleVerdict(outcome="null", reason="between the bars, or an interval straddles one")
