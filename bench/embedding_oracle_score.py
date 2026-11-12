"""B precision and the pre-registered verdict for #587.

The rules are the ones in `docs/research/EMBEDDING_ORACLE_RECALL.md`:

- B precision is over all labeled B pairs; its interval is a bootstrap clustered by PR.
- A bar is applied to the point estimate only when its interval does not straddle it;
  otherwise the verdict is null.

archy:owns        BPrecision, OracleVerdict, b_precision, verdict
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from embedding_oracle_pairs import Pair  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
from embedding_oracle_share import Share  # ty: ignore[unresolved-import]  (same)
from embedding_oracle_stats import cluster_bootstrap, ratio  # ty: ignore[unresolved-import]  (same)
from pydantic import BaseModel, ConfigDict

PASS_PRECISION = 0.60
KILL_PRECISION = 0.40
PASS_SHARE = 0.30
KILL_SHARE = 0.10
MIN_B_SCORABLE = 30
MIN_B_REPOS = 10


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
    low, high = cluster_bootstrap(list(clusters.values()))
    return BPrecision(
        scorable=n,
        repos=len({p.repo for p in scored}),
        true=k,
        point=ratio(k, n),
        low=low,
        high=high,
    )


class OracleVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: str  # pass | kill | null | not_measurable
    reason: str


def _definite_kill(prec: BPrecision, headline: Share) -> str | None:
    """Why a bar is crossed with its whole interval on the kill side, or None."""
    if prec.high is not None and prec.high < KILL_PRECISION:
        return f"B precision {prec.point:.3f}, interval top {prec.high:.3f} < 0.40"
    return _share_kill(headline)


def _share_kill(headline: Share) -> str | None:
    if not headline.measurable or headline.high is None:
        return None
    if headline.high >= KILL_SHARE:
        return None
    return f"only-by-B share {headline.point:.3f}, interval top {headline.high:.3f} < 0.10"


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
