"""Sweep `archy coupling` across the bench projects to size the finding volume
and its source/test composition, so the shipping `--min-support` /
`--min-confidence` defaults can be picked at a defensible knee (the FP-gate
discipline of `bench/duplicates_sweep.py` and §12). `max_commit_files` is held
fixed at the shipped default while the strength thresholds are swept.

There is no labelled ground truth for "these two modules are genuinely
behaviorally coupled", so this script does NOT prove precision. It measures two
things that DO inform the defaults:

* **Threshold sensitivity of the pair volume** - how fast the surfaced-pair
  count falls as `--min-support` and `--min-confidence` rise, so the default
  sits where coincidental low-support pairs have dropped but genuine coupling
  remains.
* **Source/test composition** - each surfaced pair is classified src<->src /
  src<->test / test<->test (reusing the #247 `duplicates.is_test_path`), to
  decide whether source<->test co-change (a test changing with the module it
  tests) is signal worth keeping or noise to scope out by default.

The accuracy half is a manual spot-check (top pairs on a diverse trio,
hand-classified genuine-vs-coincidental) recorded alongside in
`bench/coupling_results.md`.

Usage:
    uv run --with pyyaml python bench/coupling_sweep.py
    uv run --with pyyaml python bench/coupling_sweep.py --stdout
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys
from pathlib import Path

from _common import REPO_ROOT, clone_or_update, load_manifest

from archy.coupling import compute_coupling, git_cochange, internal_module_paths
from archy.duplicates import is_test_path
from archy.graph import build_graph

RESULTS = REPO_ROOT / "bench" / "coupling_results.md"

MAX_COMMIT_FILES = 30
# (min_support, min_confidence) grid.
GRID: tuple[tuple[int, float], ...] = (
    (3, 0.3),
    (5, 0.3),
    (5, 0.5),
    (8, 0.5),
    (5, 0.7),
)


def _bucket(path_a: str, path_b: str) -> str:
    ta, tb = is_test_path(path_a), is_test_path(path_b)
    if ta and tb:
        return "test_test"
    if ta or tb:
        return "src_test"
    return "src_src"


def run_project(root: Path) -> dict | None:
    try:
        graph = build_graph(root, max_modules=0)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:60]}"}
    keep = frozenset(internal_module_paths(graph))
    cochange = git_cochange(root, max_commit_files=MAX_COMMIT_FILES, keep_paths=keep)
    if cochange is None:
        return {"error": "not a git repo / git unavailable"}
    row: dict = {}
    for support, conf in GRID:
        pairs = compute_coupling(graph, cochange, min_support=support, min_confidence=conf)
        buckets = {"src_src": 0, "src_test": 0, "test_test": 0}
        for p in pairs:
            buckets[_bucket(p.path_a, p.path_b)] += 1
        row[(support, conf)] = {"total": len(pairs), **buckets}
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest()
    print(
        f"# coupling sweep over {len(manifest)} projects (max_commit_files={MAX_COMMIT_FILES})",
        file=sys.stderr,
    )
    rows: list[dict] = []
    for proj in manifest:
        name = proj["name"]
        print(f"# {name:14s}", end="", file=sys.stderr, flush=True)
        root = clone_or_update(proj)
        if root is None:
            print("  SKIP (clone failed)", file=sys.stderr)
            continue
        if root.resolve() == REPO_ROOT.resolve():
            # The `archy` self-entry resolves to the repo root, whose whole-repo
            # scan would climb into `bench/repo_cache` (40k vendored files, the
            # #213 trap) because this bench calls the library directly, bypassing
            # the CLI's archy.yaml exclude discovery. Self co-change is not a
            # useful bench point anyway; skip it.
            print("  SKIP (self)", file=sys.stderr)
            continue
        result = run_project(root)
        if result is None or "error" in result:
            print(f"  SKIP ({(result or {}).get('error', 'none')})", file=sys.stderr)
            continue
        rows.append({"name": name, "sha": proj["sha"], "cells": result})
        headline = result[(5, 0.5)]
        print(
            f"  @(5,0.5) total={headline['total']:4d} "
            f"src_src={headline['src_src']:3d} src_test={headline['src_test']:3d} "
            f"test_test={headline['test_test']:3d}",
            file=sys.stderr,
        )

    out: list[str] = []

    def emit(line: str = "") -> None:
        out.append(line)

    today = dt.date.today().isoformat()
    emit("# Change-coupling threshold + composition sweep")
    emit()
    emit(f"Output of `uv run --with pyyaml python bench/coupling_sweep.py`. Captured {today}.")
    emit(f"`max_commit_files={MAX_COMMIT_FILES}`. Each cell = total surfaced pairs (src<->src).")
    emit()
    emit("## Per-project (total / src_src) by (min_support, min_confidence)")
    emit()
    cols = ["project"] + [f"({s},{c})" for s, c in GRID]
    emit("| " + " | ".join(cols) + " |")
    emit("| " + " | ".join("---" if col == "project" else "---:" for col in cols) + " |")
    for r in rows:
        cells = [r["name"]]
        for key in GRID:
            c = r["cells"][key]
            cells.append(f"{c['total']} / {c['src_src']}")
        emit("| " + " | ".join(cells) + " |")

    emit()
    emit("## Aggregate (median across projects)")
    emit()
    emit("| (min_support, min_confidence) | median total | median src_src | median src_test |")
    emit("| --- | ---: | ---: | ---: |")
    for key in GRID:
        totals = [r["cells"][key]["total"] for r in rows]
        srcsrc = [r["cells"][key]["src_src"] for r in rows]
        srctest = [r["cells"][key]["src_test"] for r in rows]
        emit(
            f"| {key} | {statistics.median(totals):.0f} | "
            f"{statistics.median(srcsrc):.0f} | {statistics.median(srctest):.0f} |"
        )
    emit()
    emit("## FP spot-check")
    emit()
    emit(
        "_Manual, not produced by this script._ At the chosen default, draw the "
        "top ~15 surfaced pairs each from a diverse trio, hand-classify genuinely "
        "coupled vs coincidental, record the N/15 rate + FP taxonomy. Template: "
        "the duplicates FP spot-check (`bench/duplicates_results.md`)."
    )

    report = "\n".join(out) + "\n"
    if args.stdout:
        sys.stdout.write(report)
    else:
        RESULTS.write_text(report)
        print(f"# wrote {RESULTS.relative_to(REPO_ROOT)}", file=sys.stderr)

    # Composition summary to stderr for calibration.
    print("\n=== COMPOSITION @ grid (summed across repos) ===", file=sys.stderr)
    for key in GRID:
        tot = sum(r["cells"][key]["total"] for r in rows)
        ss = sum(r["cells"][key]["src_src"] for r in rows)
        st = sum(r["cells"][key]["src_test"] for r in rows)
        tt = sum(r["cells"][key]["test_test"] for r in rows)
        frac = 100 * ss / tot if tot else 0
        print(
            f"  {key}: total={tot} src_src={ss} ({frac:.0f}%) src_test={st} test_test={tt}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
