"""Checkpointed runner for generator B of #587: score, calibrate, emit.

    uv run python bench/embedding_oracle_generate_run.py score     --model M --shard calibration
    uv run python bench/embedding_oracle_generate_run.py calibrate --model M --target T \\
        --target-source WHY
    uv run python bench/embedding_oracle_generate_run.py emit      --model M --shard pilot

`score` writes one ledger row per PR (every new function's top-1 neighbour and cosine, before
any cutoff). `calibrate` turns the calibration ledger into a cutoff for THAT model only and
refuses to run on an incomplete ledger. `emit` applies it to a shard whose vectors came from
the same configuration, then caps. The target is an argument, never a default: the post-pilot
addendum fixes it. The cutoff file says `frozen: false`; freezing is a document, not a flag.

The shard choices are `pilot` and `calibration`; the evaluation shard cannot be named here.

Exit: 0 done | 5 refused (vectors, config, incomplete ledger) | 6 a PR errored | 130 interrupted.

archy:owns        cmd_calibrate, cmd_emit, cmd_score, main, run_prs, universe
archy:gates       exit 130 (hardcoded)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path

from _supervise import Ledger
from embedding_oracle_generate import (
    DENOMINATORS,
    PrScores,
    Scored,
    b_pairs,
    calibrate,
    calibration_scores,
    score_pr,
    volume_report,
)
from embedding_oracle_units import UnitFn, UnitIndexError, check_schema, load_units, split_pr
from embedding_oracle_vectors import (
    PINS,
    SHARDS,
    VectorError,
    load_vectors,
    read_shards_meta,
    require_same_config,
)

ROOT = Path(__file__).resolve().parent.parent / "bench" / "cache" / "emb587"
NO_NEW_FUNCTION = "no new function clears the floor"


def _meta() -> dict[str, object]:
    return read_shards_meta(ROOT)


def universe(shard: str) -> tuple[list[str], list[str], dict[str, str]]:
    """`(PRs that ship units, measured PRs with no new function, unmeasurable PRs and why)`."""
    entry = _meta()["shards"][shard]
    empty = sorted(pr for pr, why in entry["drops"].items() if why == NO_NEW_FUNCTION)
    dropped = {pr: why for pr, why in entry["drops"].items() if why != NO_NEW_FUNCTION}
    return sorted(entry["pr_keys"]), empty, dropped


def _ledger_path(model: str, shard: str) -> Path:
    return ROOT / "ledger" / f"{model}.{shard}.score.jsonl"


def _key(model: str, shard: str, pr: str) -> str:
    return f"{model}|{shard}|{pr}"


def _payload(ps: PrScores) -> dict[str, object]:
    return {
        "pr": ps.pr,
        "repo": ps.repo,
        "n_new": ps.n_new,
        "no_neighbour": ps.no_neighbour,
        "lost_not_at_head": ps.lost_not_at_head,
        "unshipped": ps.unshipped,
        "scored": [
            {"new": asdict(s.new), "existing": asdict(s.existing), "sim": s.sim} for s in ps.scored
        ],
    }


def _scores_from(row: dict[str, object]) -> PrScores:
    scored = tuple(
        Scored(UnitFn(**s["new"]), UnitFn(**s["existing"]), s["sim"]) for s in row["scored"]
    )
    return PrScores(
        row["pr"],
        row["repo"],
        row["n_new"],
        scored,
        row["no_neighbour"],
        row["lost_not_at_head"],
        row["unshipped"],
    )


def run_prs(
    prs: Sequence[str],
    keyfn: Callable[[str], str],
    work: Callable[[str], dict[str, object]],
    ledger: Ledger,
) -> int:
    """One ledger row per PR. A crash in one PR is recorded and the loop goes on; an interrupt
    stops cleanly. Returns how many PRs errored."""
    errors = 0
    for pr in prs:
        if ledger.is_done(keyfn(pr)):
            continue
        try:
            ledger.record(keyfn(pr), work(pr))
        except KeyboardInterrupt:
            print(
                f"interrupted after {ledger.completed} PRs; rerun the same command to resume",
                file=sys.stderr,
            )
            raise SystemExit(130) from None
        except Exception as exc:
            errors += 1
            ledger.record(
                keyfn(pr), {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}, status="error"
            )
    return errors


def cmd_score(model: str, shard: str) -> int:
    try:
        vs = load_vectors(ROOT, model, shard)
        shipped, _empty, dropped = universe(shard)
        units = load_units(ROOT / f"{shard}.units.jsonl")
        for rows in units.values():
            check_schema(rows)
    except (VectorError, UnitIndexError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 5
    ledger = Ledger(_ledger_path(model, shard))
    errors = run_prs(
        shipped,
        lambda pr: _key(model, shard, pr),
        lambda pr: _payload(score_pr(split_pr(pr, units[pr]), vs)),
        ledger,
    )
    rows = [ledger.get(_key(model, shard, pr)) for pr in shipped]
    unshipped = sum(r["unshipped"] for r in rows if r)  # type: ignore[index]
    note = f"; WARNING {unshipped} ids had no vector (not shipped)" if unshipped else ""
    print(
        f"{model} {shard}: {ledger.completed}/{len(shipped)} PRs scored, {errors} errored, "
        f"{len(dropped)} unmeasurable{note}"
    )
    return 6 if errors else 0


def _complete_rows(model: str, shard: str) -> dict[str, dict[str, object]] | None:
    """Every shipped PR's ledger row, or None if the ledger is incomplete."""
    shipped, _e, _d = universe(shard)
    ledger = Ledger(_ledger_path(model, shard))
    rows = {pr: ledger.get(_key(model, shard, pr)) for pr in shipped}
    return None if any(r is None for r in rows.values()) else rows  # type: ignore[return-value]


def cmd_calibrate(model: str, target: float, source: str, denominator: str) -> int:
    rows = _complete_rows(model, "calibration")
    if rows is None:
        print("REFUSED: the calibration ledger is incomplete; run `score` first", file=sys.stderr)
        return 5
    config = load_vectors(ROOT, model, "calibration").config
    _shipped, empty, dropped = universe("calibration")
    prs = [_scores_from(r) for r in rows.values()]
    per_pr = {ps.pr: [s.sim for s in ps.scored] for ps in prs}
    results = {d: calibrate(calibration_scores(per_pr, empty, d), target) for d in DENOMINATORS}
    chosen = results[denominator]
    out = {
        "model": model,
        "frozen": False,
        "target": target,
        "target_source": source,
        "denominator": denominator,
        "cutoff": chosen.cutoff,
        "reached_target": chosen.reached_target,
        "mean_at_cutoff": chosen.mean_at_cutoff,
        "cutoff_by_denominator": {d: r.cutoff for d, r in results.items()},
        "config": json.loads(json.dumps(config)),
        "extractor_version": _meta()["shards"]["calibration"]["extractor_version"],
        "volume": volume_report(prs),
        "calibration_prs": {"scored": len(rows), "empty": len(empty), "unmeasurable": dropped},
    }
    (ROOT / f"cutoff.{model}.json").write_text(
        json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: out[k] for k in ("cutoff", "reached_target", "cutoff_by_denominator")}))
    return 0


def _checked_cutoff(model: str, shard: str) -> dict[str, object]:
    """The cutoff file, once this shard's vectors match the configuration behind it."""
    cut = json.loads((ROOT / f"cutoff.{model}.json").read_text(encoding="utf-8"))
    vs = load_vectors(ROOT, model, shard)
    if json.loads(json.dumps(vs.config)) != cut["config"]:
        raise VectorError(
            "this shard's vectors are from a different configuration than the one that "
            "calibrated the cutoff"
        )
    require_same_config(vs, load_vectors(ROOT, model, "calibration"))
    if _meta()["shards"][shard]["extractor_version"] != cut["extractor_version"]:
        raise VectorError(
            "this shard was written by a different extractor version than the calibration shard"
        )
    return cut


def cmd_emit(model: str, shard: str) -> int:
    try:
        cut = _checked_cutoff(model, shard)
    except (VectorError, FileNotFoundError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 5
    rows = _complete_rows(model, shard)
    if rows is None:
        print(
            "REFUSED: the ledger for this shard is incomplete; run `score` first", file=sys.stderr
        )
        return 5
    pairs = b_pairs([_scores_from(r) for r in rows.values()], float(cut["cutoff"]))  # type: ignore[arg-type]
    dest = ROOT / f"pairs.{model}.{shard}.jsonl"
    body = "".join(json.dumps(p.model_dump(), sort_keys=True) + "\n" for p in pairs)
    dest.write_text(body, encoding="utf-8")
    print(f"{model} {shard}: {len(pairs)} capped B pairs at cutoff {cut['cutoff']:.6f}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("score", "emit"):
        p = sub.add_parser(name)
        p.add_argument("--model", required=True, choices=sorted(PINS))
        p.add_argument("--shard", required=True, choices=SHARDS)
    c = sub.add_parser("calibrate")
    c.add_argument("--model", required=True, choices=sorted(PINS))
    c.add_argument("--target", required=True, type=float)
    c.add_argument("--target-source", required=True)
    c.add_argument("--denominator", default="all-measured", choices=DENOMINATORS)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "score":
            return cmd_score(a.model, a.shard)
        if a.cmd == "emit":
            return cmd_emit(a.model, a.shard)
        return cmd_calibrate(a.model, a.target, a.target_source, a.denominator)
    except (VectorError, UnitIndexError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
