"""Resumable runner for the #548 study: pilot and main run over sampled AIDev PRs.

    uv run --with pyarrow python bench/diff_duplicates_run.py --pilot
    uv run --with pyarrow python bench/diff_duplicates_run.py --run N     # after the addendum

The canary runs first and a failure refuses the run (Study 7's harness bugs both
produced publishable-looking numbers). One ledger row per PR, appended with its
newline in a single write; only `status="ok"` counts as done, so `error` rows (a
stalled or failed git, a network blip) are retried on the next run, while a PR that
is permanently unreproducible is recorded as an `ok` row with a `dropped` reason and
leaves the denominator. A crash in one PR never ends the loop, and Ctrl-C exits
cleanly saying how to resume.

The pilot reports counts only. It reads no firing's contents: the pilot measures
firing rate and harness bugs, and the pre-registration forbids reading labels.

archy:owns        main, process_pr, run_loop, summarize
archy:gates       non-zero exit (hardcoded)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from _supervise import Ledger  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
from diff_duplicates_pr import (  # ty: ignore[unresolved-import]
    REPO_ROOT,
    PoolPR,
    Unresolvable,
    draw_main,
    draw_pilot,
    load_pool,
    resolve,
    take_shard,
)
from diff_duplicates_precision import (  # ty: ignore[unresolved-import]
    analyze_checkout,
    canary,
)

PILOT_LEDGER = REPO_ROOT / "bench" / "diff_duplicates_pilot.jsonl"
MAIN_LEDGER = REPO_ROOT / "bench" / "diff_duplicates_precision.jsonl"


def process_pr(pr: PoolPR) -> dict:
    """Resolve, check out and analyze one PR; the payload for its ledger row."""
    resolved = resolve(pr)
    result = analyze_checkout(resolved.repo_dir, resolved.base, resolved.head)
    return {"base": resolved.base, "head": resolved.head, **result}


def run_loop(
    prs: Iterable[PoolPR],
    ledger: Ledger,
    process: Callable[[PoolPR], dict] = process_pr,
    skip_keys: frozenset[str] = frozenset(),
) -> int:
    """Process every PR the ledger (or `skip_keys`, done elsewhere) does not hold as `ok`.

    Returns the exit code."""
    try:
        for pr in prs:
            key = str(pr.id)
            if ledger.is_done(key) or key in skip_keys:
                continue
            _one(pr, key, ledger, process)
    except KeyboardInterrupt:
        print(
            "\ninterrupted; everything written is kept. Re-run the same command to resume "
            f"({ledger.path})"
        )
        return 130
    return 0


def _one(pr: PoolPR, key: str, ledger: Ledger, process: Callable[[PoolPR], dict]) -> None:
    meta = {"repo": pr.repo, "number": pr.number, "agent": pr.agent, "merged": pr.merged}
    started = time.monotonic()
    try:
        payload = process(pr)
    except Unresolvable as exc:
        ledger.record(key, {**meta, "dropped": f"unresolvable: {exc}"})
        return
    except Exception as exc:  # one bad PR must not end an hours-long loop
        ledger.record(
            key, {**meta, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}, status="error"
        )
        return
    ledger.record(key, {**meta, **payload, "seconds": round(time.monotonic() - started, 1)})


def _ok_rows(ledger_path: Path) -> dict[str, dict]:
    """The ledger's `ok` rows, the last row per key winning; torn lines are skipped."""
    rows: dict[str, dict] = {}
    for raw in ledger_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "ok":
            rows[row["key"]] = row
    return rows


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(*ledger_paths: Path) -> dict:
    """Counts from the `ok` rows of one or more ledgers (shards). Drops leave the
    denominator. No firing content."""
    rows: dict[str, dict] = {}
    for path in ledger_paths:
        rows.update(_ok_rows(path))
    dropped = Counter(r["dropped"].split(":")[0] for r in rows.values() if "dropped" in r)
    measured = [r for r in rows.values() if "dropped" not in r]
    firing_prs = [r for r in measured if r.get("firings")]
    return {
        "rows_ok": len(rows),
        "measured": len(measured),
        "dropped": dict(dropped),
        "prs_with_firing": len(firing_prs),
        "firings": sum(len(r["firings"]) for r in firing_prs),
        "repos_with_firing": len({r["repo"] for r in firing_prs}),
        "firing_rate": len(firing_prs) / len(measured) if measured else None,
        "mean_seconds_per_pr": _mean([r["seconds"] for r in measured if "seconds" in r]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true", help="run the 20-PR pilot")
    mode.add_argument("--run", type=int, metavar="N", help="run the main sample of N PRs")
    mode.add_argument(
        "--summary", type=Path, nargs="+", metavar="LEDGER", help="print counts and exit"
    )
    parser.add_argument("--shard", metavar="K/M", help="with --run: worker K of M, by repository")
    parser.add_argument(
        "--skip-ledger", type=Path, action="append", default=[], help="keys already done there"
    )
    return parser


def _shard(spec: str | None) -> tuple[int, int] | None:
    if spec is None:
        return None
    index, count = (int(x) for x in spec.split("/"))
    if not 0 <= index < count:
        raise SystemExit(f"--shard K/M needs 0 <= K < M; got {spec}")
    return index, count


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.summary:
        print(json.dumps(summarize(*args.summary), indent=2))
        return 0
    failures = canary()
    if failures:
        print("CANARY FAILED, refusing to run:\n" + "\n".join(failures))
        return 1
    pool = load_pool()
    shard = _shard(args.shard)
    ledger_path = PILOT_LEDGER if args.pilot else MAIN_LEDGER
    prs = draw_pilot(pool) if args.pilot else draw_main(pool, args.run)
    if shard:
        prs = take_shard(prs, *shard)
        ledger_path = ledger_path.with_suffix(f".shard{shard[0]}of{shard[1]}.jsonl")
    skip = frozenset(k for p in args.skip_ledger for k in _ok_rows(p))
    code = run_loop(prs, Ledger(ledger_path), skip_keys=skip)
    print(json.dumps(summarize(ledger_path), indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
