"""The pooled pair set for #587's labeling: A's expanded pairs plus the primary's B pairs.

Reads no label and no pair text. For the 250 registered evaluation PRs it expands A's
firings to pairs (the same rules as the A-target recount, which re-parses base and head),
reads the primary's capped B pairs, pools them under one id each, and applies the
pre-registered budget: every pair when there are at most 300, else a seeded draw of 300
over the sorted pair ids (`random.Random(587)`), taken before any packet exists.

archy:owns        PoolPlan, arm_draw, budget_draw, main, plan_pool, read_pairs
archy:mirrored-by read_pairs -> bench.embedding_oracle_packets,
                  bench.embedding_oracle_report
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from embedding_oracle_a_target import InfoAt, expand_pr  # ty: ignore[unresolved-import]
from embedding_oracle_pairs import Pair, pool_pairs  # ty: ignore[unresolved-import]
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent / "bench" / "cache" / "emb587"
PRIMARY = "jina-v2-base-code"
LABEL_CAP = 300
LABEL_SEED = 587


class PoolPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    a_pairs: tuple[Pair, ...]
    b_pairs: tuple[Pair, ...]
    pool: tuple[Pair, ...]
    drawn: tuple[Pair, ...]
    unresolved_prs: tuple[str, ...]


def budget_draw(pairs: Sequence[Pair], cap: int = LABEL_CAP, seed: int = LABEL_SEED) -> list[Pair]:
    """Every pair when there are at most `cap`, else a seeded sample of `cap` by pair id."""
    by_id = {p.pair_id: p for p in pairs}
    ids = sorted(by_id)
    if len(ids) <= cap:
        return [by_id[i] for i in ids]
    return [by_id[i] for i in sorted(random.Random(seed).sample(ids, cap))]


ARMS = ("unixcoder-base", "jina-code-0.5b", "jina-code-1.5b", "bge-code-v1")
ARM_CAP = 100


def arm_draw(
    arm_pairs: Sequence[Pair],
    pool_ids: set[str],
    cap: int = ARM_CAP,
    seed: int = LABEL_SEED,
) -> list[Pair]:
    """One arm's labeled pairs: those outside the pre-draw primary pool, at most `cap`.

    Pairs A or the primary already emit never enter an arm (they are labeled once, in the
    primary pool), so an arm's precision is over arm-only pairs. Over the cap, a seeded
    sample by sorted pair id, drawn independently per arm.
    """
    return budget_draw([p for p in arm_pairs if p.pair_id not in pool_ids], cap, seed)


def plan_pool(
    rows: Sequence[Mapping[str, object]],
    resolver: Callable[[Mapping[str, object]], InfoAt | None],
    b_pairs: Sequence[Pair],
) -> PoolPlan:
    """A's pairs for the rows that fired, pooled with `b_pairs`, then the budget draw.

    A row whose repository cannot be re-parsed is listed, never silently skipped: its A
    pairs are absent from the pool, so the caller must see the count.
    """
    a: list[Pair] = []
    unresolved: list[str] = []
    for row in rows:
        if not row["firings"]:
            continue
        info_at = resolver(row)
        if info_at is None:
            unresolved.append(str(row["key"]))
            continue
        a.extend(expand_pr(dict(row), info_at).kept)
    pool = pool_pairs(a, b_pairs)
    return PoolPlan(
        a_pairs=tuple(a),
        b_pairs=tuple(b_pairs),
        pool=tuple(pool),
        drawn=tuple(budget_draw(pool)),
        unresolved_prs=tuple(unresolved),
    )


def read_pairs(path: Path) -> list[Pair]:
    return [Pair.model_validate(json.loads(line)) for line in path.read_text().splitlines()]


def main(argv: Sequence[str] | None = None) -> int:
    from embedding_oracle_a_target import _resolver  # ty: ignore[unresolved-import]
    from embedding_oracle_evaluation import (  # ty: ignore[unresolved-import]
        EvaluationDrawError,
        evaluation_rows,
    )

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true", help="write the drawn pair ids")
    args = parser.parse_args(argv)
    try:
        rows = evaluation_rows()
    except EvaluationDrawError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 5
    plan = plan_pool(rows, _resolver, read_pairs(ROOT / f"pairs.{PRIMARY}.evaluation.jsonl"))
    print(json.dumps(_plan_summary(plan), sort_keys=True))
    if plan.unresolved_prs:
        print("NOT FINAL: some firing PRs could not be re-parsed", file=sys.stderr)
        return 6
    pool_ids = {p.pair_id for p in plan.pool}
    arms = {m: arm_draw(read_pairs(ROOT / f"pairs.{m}.evaluation.jsonl"), pool_ids) for m in ARMS}
    print(json.dumps(_arm_summary(arms), sort_keys=True))
    if args.write:
        _write(ROOT / "label_pool.evaluation.jsonl", plan.drawn)
        for m, ps in arms.items():
            _write(ROOT / f"label_arm.{m}.evaluation.jsonl", ps)
    return 0


def _plan_summary(plan: PoolPlan) -> dict[str, object]:
    return {
        "a_pairs": len(plan.a_pairs),
        "b_pairs": len(plan.b_pairs),
        "pool": len(plan.pool),
        "emitted_by_both": sum(1 for p in plan.pool if len(p.generators) > 1),
        "drawn": len(plan.drawn),
        "unresolved_prs": list(plan.unresolved_prs),
    }


def _arm_summary(arms: Mapping[str, Sequence[Pair]]) -> dict[str, object]:
    union = {p.pair_id for ps in arms.values() for p in ps}
    return {"arm_drawn": {m: len(ps) for m, ps in arms.items()}, "arm_union": len(union)}


def _write(dest: Path, pairs: Sequence[Pair]) -> None:
    dest.write_text(
        "".join(json.dumps(p.model_dump(), sort_keys=True) + "\n" for p in pairs), encoding="utf-8"
    )


if __name__ == "__main__":
    sys.exit(main())
