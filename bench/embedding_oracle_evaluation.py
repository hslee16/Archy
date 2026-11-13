"""The registered evaluation draw for #587, and the one command that extracts it.

The fourth addendum to `docs/research/EMBEDDING_ORACLE_RECALL.md` fixed the evaluation subset:
the first M = 250 keys of a `random.Random(587)` shuffle of the sorted keys of the measured PRs
that remain after the calibration draw, skipping a PR once its repository holds 25. It also
fixed the sha256 of those keys (sorted, joined by newlines). This module reads no label and runs
no model. It draws the keys, REFUSES any draw whose hash is not the registered one, and only then
hands the rows to the extractor, so the evaluation shard cannot be built from another sample.

    uv run python bench/embedding_oracle_evaluation.py --plan      # print the keys, change nothing
    uv run python bench/embedding_oracle_evaluation.py             # extract the evaluation shard

Its entry joins `SHARDS.json`, whose top-level `ok` covers EVERY shard in it: an evaluation
reproduction or parity mismatch, or an evaluation entry left at an older extractor version, makes
the manifest not `ok` and so refuses the pilot and calibration shards too. That is deliberate (a
manifest that mixes versions or hides a mismatch is not good); re-extract the evaluation shard, or
read its `reproduction_mismatches` and `parity_mismatches`, before suspecting the other two.

The shard is written beside the pilot and calibration shards as `evaluation.jsonl` and
`evaluation.units.jsonl`, with the same contract and the same manifest. The vector loader and the
scoring commands do not name it: embedding it needs its own ticket, and scoring it is a later
change.

archy:owns        EvaluationDrawError, check_registered, draw_evaluation,
                  evaluation_rows, keys_sha256, main
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence

from embedding_oracle_extract import (  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
    DATA,
    PILOT_LEDGER,
    check_disjoint,
    draw_calibration,
    measured_rows,
    run_and_record,
)

EVALUATION_N = 250
EVALUATION_REPO_CAP = 25
# Fixed by the fourth addendum (2026-11-13). Changing either constant is a new addendum.
EVALUATION_KEYS_SHA256 = "dafa1fa79a93da51d3ff808198eab07dd5a8fe4d0533b4ba16f9edf7963582f0"
CALIBRATION_KEYS_SHA256 = "721eff0ad057bc6f79369657ecb03179bb536f8d221449222cae0645ca779f83"


class EvaluationDrawError(ValueError):
    """The drawn keys are not the ones the pre-registration fixed."""


def keys_sha256(keys: Sequence[str]) -> str:
    """The registered fingerprint of a draw: its sorted keys joined by newlines."""
    return hashlib.sha256("\n".join(sorted(keys)).encode()).hexdigest()


def draw_evaluation(
    repo_by_key: Mapping[str, str],
    calibration: Sequence[str],
    n: int = EVALUATION_N,
    cap: int = EVALUATION_REPO_CAP,
) -> list[str]:
    """The first `n` keys, repository cap `cap`, of the measured PRs not in `calibration`.

    Same generator and key order as the calibration draw, over what remains after it.
    """
    taken = set(calibration)
    remaining = {k: repo for k, repo in repo_by_key.items() if k not in taken}
    return draw_calibration(remaining, n=n, cap=cap)


def check_registered(keys: Sequence[str], expected: str | None = None) -> None:
    """Refuse a draw that is not the registered one, naming both fingerprints."""
    expected = EVALUATION_KEYS_SHA256 if expected is None else expected
    got = keys_sha256(keys)
    if got != expected:
        raise EvaluationDrawError(
            f"the evaluation draw hashes to {got}, not the registered {expected}: "
            f"{len(keys)} keys. The measured set or the draw rule changed; a new dated "
            "addendum is needed before anything is extracted"
        )


def evaluation_rows() -> list[dict]:
    """The ledger rows of the registered evaluation PRs, or EvaluationDrawError."""
    measured = measured_rows(sorted(DATA.glob("precision.*.jsonl")))
    repo_by_key = {k: row["repo"] for k, row in measured.items()}
    calibration = draw_calibration(repo_by_key)
    check_registered(calibration, CALIBRATION_KEYS_SHA256)
    picked = draw_evaluation(repo_by_key, calibration)
    check_registered(picked)
    pilot = [str(json.loads(r)["key"]) for r in PILOT_LEDGER.read_text().splitlines()]
    check_disjoint(picked, calibration)
    check_disjoint(picked, pilot)
    return [measured[k] for k in sorted(picked)]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--plan", action="store_true", help="print the keys and stop")
    args = parser.parse_args(argv)
    try:
        rows = evaluation_rows()
    except EvaluationDrawError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 5
    if args.plan:
        print("evaluation", len(rows), [str(r["key"]) for r in rows])
        return 0
    return run_and_record({"evaluation": rows})


if __name__ == "__main__":
    raise SystemExit(main())
