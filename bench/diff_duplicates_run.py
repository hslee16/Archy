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
) -> int:
    """Process every PR the ledger does not already hold as `ok`. Returns the exit code."""
    try:
        for pr in prs:
            key = str(pr.id)
            if ledger.is_done(key):
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


def summarize(ledger_path: Path) -> dict:
    """Counts from the ledger's `ok` rows (last row per key wins). No firing content."""
    rows: dict[str, dict] = {}
    for raw in ledger_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if row.get("status") == "ok":
            rows[row["key"]] = row
    dropped = Counter(r["dropped"].split(":")[0] for r in rows.values() if "dropped" in r)
    measured = [r for r in rows.values() if "dropped" not in r]
    firing_prs = [r for r in measured if r.get("firings")]
    secs = [r["seconds"] for r in measured if "seconds" in r]
    return {
        "rows_ok": len(rows),
        "measured": len(measured),
        "dropped": dict(dropped),
        "prs_with_firing": len(firing_prs),
        "firings": sum(len(r["firings"]) for r in firing_prs),
        "repos_with_firing": len({r["repo"] for r in firing_prs}),
        "firing_rate": len(firing_prs) / len(measured) if measured else None,
        "mean_seconds_per_pr": sum(secs) / len(secs) if secs else None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true", help="run the 20-PR pilot")
    mode.add_argument("--run", type=int, metavar="N", help="run the main sample of N PRs")
    mode.add_argument("--summary", type=Path, metavar="LEDGER", help="print counts and exit")
    args = parser.parse_args(argv)
    if args.summary:
        print(json.dumps(summarize(args.summary), indent=2))
        return 0
    failures = canary()
    if failures:
        print("CANARY FAILED, refusing to run:\n" + "\n".join(failures))
        return 1
    pool = load_pool()
    if args.pilot:
        ledger_path, prs = PILOT_LEDGER, draw_pilot(pool)
    else:
        ledger_path, prs = MAIN_LEDGER, draw_main(pool, args.run)
    code = run_loop(prs, Ledger(ledger_path))
    print(json.dumps(summarize(ledger_path), indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
