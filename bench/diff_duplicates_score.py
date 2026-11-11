"""Scoring for the #548 study: labels in, pre-registered verdict out.

The decision rule is the one in `docs/research/DIFF_DUPLICATES_PRECISION.md` and is
written as one small function so a boundary cannot drift: 0.60 passes, below 0.40
kills, in between is a null; fewer than 30 scorable firings, fewer than 10
repositories, or a headroom below 1 PR in 50 is the headroom kill and decides first.

Labels are one JSONL row per (labeler, firing): `{"id", "label"}` with label one of
`TRUE`, `FALSE`, `UNSCORABLE`. A firing is TRUE under the primary oracle only if BOTH
labelers say TRUE (strict, the headline); the either-says-TRUE reading is the reported
upper bound. A firing either labeler marks UNSCORABLE is dropped, never scored FALSE.

archy:owns        Verdict, cohen_kappa, score_labels, verdict
archy:mirrored-by Verdict also in bench.greenfield_prereg
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict

HEADROOM_MIN_RATE = 1 / 50
MIN_SCORABLE = 30
MIN_REPOS = 10
PASS_PRECISION = 0.60
KILL_PRECISION = 0.40


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: str  # headroom_kill | precision_pass | precision_kill | null
    reason: str


def verdict(*, firing_rate: float, scorable: int, repos: int, precision: float) -> Verdict:
    """The pre-registered rule. Headroom decides first, then precision."""
    if firing_rate < HEADROOM_MIN_RATE:
        return Verdict(
            outcome="headroom_kill",
            reason=f"firing rate {firing_rate:.4f} is below 1 PR in 50 ({HEADROOM_MIN_RATE})",
        )
    if scorable < MIN_SCORABLE or repos < MIN_REPOS:
        return Verdict(
            outcome="headroom_kill",
            reason=f"{scorable} scorable firings from {repos} repos; need "
            f"{MIN_SCORABLE} from {MIN_REPOS}",
        )
    if precision >= PASS_PRECISION:
        return Verdict(outcome="precision_pass", reason=f"precision {precision:.3f} >= 0.60")
    if precision < KILL_PRECISION:
        return Verdict(outcome="precision_kill", reason=f"precision {precision:.3f} < 0.40")
    return Verdict(outcome="null", reason=f"precision {precision:.3f} is between 0.40 and 0.60")


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa for two aligned label lists; None when chance agreement is 1."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def _read_labels(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        # A labeler ledger also holds `error` rows; only a recorded verdict is a label.
        if row.get("status", "ok") == "ok" and "label" in row:
            labels[row["id"]] = row["label"]
    return labels


def score_labels(
    labels_a: Path, labels_b: Path, repo_of: dict[str, str]
) -> dict[str, float | int | None]:
    """Strict and lenient precision over the firings BOTH labelers scored.

    `repo_of` maps a firing id to its repository so the repo count is over scorable
    firings only. Firings missing from either file, or marked UNSCORABLE by either,
    are dropped and counted, never scored FALSE.
    """
    a, b = _read_labels(labels_a), _read_labels(labels_b)
    both = sorted(set(a) & set(b))
    scorable = [i for i in both if "UNSCORABLE" not in (a[i], b[i])]
    strict = sum(a[i] == "TRUE" and b[i] == "TRUE" for i in scorable)
    lenient = sum("TRUE" in (a[i], b[i]) for i in scorable)
    n = len(scorable)
    return {
        "labelled_by_both": len(both),
        "scorable": n,
        "unscorable_dropped": len(both) - n,
        "repos": len({repo_of[i] for i in scorable}),
        "strict_true": strict,
        "lenient_true": lenient,
        "precision_strict": strict / n if n else None,
        "precision_lenient": lenient / n if n else None,
        "kappa": cohen_kappa([a[i] for i in scorable], [b[i] for i in scorable]),
    }
