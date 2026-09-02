"""Sweep the Type-3 near-miss pass (#246) across the bench: how many clusters it
surfaces at each `min_similarity`, and how long it takes.

The recall gain is proven by `bench/duplicates_recall_experiment.py` (~0% ->
~70-100% Type-3). This is the *precision* half: a similarity threshold over the
tiny normalized-token vocabulary can collide unrelated same-shaped functions, so
the default `min_similarity` must sit high enough that surfaced near-miss
clusters are genuinely gapped clones, not coincidental structural matches. The
volume + timing here sizes the effect; the accuracy half is a manual spot-check
(hand-classify a sample of near-miss clusters genuine-Type-3 vs coincidental)
recorded alongside in RESEARCH_METRICS §12h. Near-miss is opt-in and lower
confidence, so the bar is "mostly plausible", not the ~74% of the exact tiers.

Usage:
    uv run --with pyyaml python bench/near_duplicates_sweep.py
    uv run --with pyyaml python bench/near_duplicates_sweep.py --stdout

archy:owns        main, run_project
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys
import time
from pathlib import Path

from _common import REPO_ROOT, clone_or_update, load_manifest

from archy.duplicates import compute_near_duplicates
from archy.graph import parse_project

RESULTS = REPO_ROOT / "bench" / "near_duplicates_results.md"
THRESHOLDS: tuple[float, ...] = (0.75, 0.8, 0.85, 0.9)


def run_project(root: Path) -> dict | None:
    try:
        modules, results = parse_project(root, max_modules=0)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:60]}"}
    row: dict = {"modules": len(modules)}
    for thr in THRESHOLDS:
        t0 = time.monotonic()
        near = compute_near_duplicates(modules, results, min_similarity=thr)
        row[thr] = {"count": len(near), "secs": round(time.monotonic() - t0, 1)}
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    rows: list[dict] = []
    for proj in load_manifest():
        name = proj["name"]
        print(f"# {name:14s}", end="", file=sys.stderr, flush=True)
        root = clone_or_update(proj)
        if root is None:
            print("  SKIP (clone failed)", file=sys.stderr)
            continue
        if root.resolve() == REPO_ROOT.resolve():
            print("  SKIP (self: repo_cache scan trap)", file=sys.stderr)
            continue
        result = run_project(root)
        if result is None or "error" in result:
            print(f"  SKIP ({(result or {}).get('error', 'none')})", file=sys.stderr)
            continue
        rows.append({"name": name, **result})
        cell = result[0.8]
        print(f"  @0.8 clusters={cell['count']:4d} ({cell['secs']}s)", file=sys.stderr)

    out: list[str] = []
    out.append("# Type-3 near-miss threshold sweep (#246)")
    out.append("")
    out.append(
        f"Output of `uv run --with pyyaml python bench/near_duplicates_sweep.py`. "
        f"Captured {dt.date.today().isoformat()}. `compute_near_duplicates` cluster count "
        f"(and seconds) per `min_similarity`, whole-repo. The default floor is 0.85. "
        "Whole-repo counts are inflated by near-clones in test/example code (as in #247); "
        "the giants (django/numpy/pytorch/home-assistant, 30-44s) hit the comparison cap so "
        "their counts are incomplete (the warning fires). See §12h for the source-only "
        "spot-check and why the count is a poor precision proxy (connected-component "
        "clustering makes it non-monotonic in the threshold)."
    )
    out.append("")
    cols = ["project", "modules"] + [f"@{t}" for t in THRESHOLDS]
    out.append("| " + " | ".join(cols) + " |")
    out.append("| " + " | ".join("---" if c in ("project",) else "---:" for c in cols) + " |")
    for r in rows:
        cells = [r["name"], str(r["modules"])]
        cells += [f"{r[t]['count']} ({r[t]['secs']}s)" for t in THRESHOLDS]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    out.append("| min_similarity | median clusters | max secs |")
    out.append("| ---: | ---: | ---: |")
    for t in THRESHOLDS:
        counts = [r[t]["count"] for r in rows]
        secs = [r[t]["secs"] for r in rows]
        out.append(f"| {t} | {statistics.median(counts):.0f} | {max(secs):.1f} |")
    out.append("")
    out.append("## FP spot-check")
    out.append("")
    out.append(
        "_Manual, not produced by this script._ At the chosen `min_similarity`, draw ~15 "
        "near-miss clusters from a diverse trio, hand-classify genuine-Type-3 (a real gapped "
        "clone) vs coincidental (two unrelated functions the tiny token vocabulary made look "
        "alike), record the rate + the dominant FP pattern. See RESEARCH_METRICS §12h."
    )

    report = "\n".join(out) + "\n"
    if args.stdout:
        sys.stdout.write(report)
    else:
        RESULTS.write_text(report)
        print(f"# wrote {RESULTS.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
