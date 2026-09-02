"""Measure the co-change demotion (#242) across the bench: how many primary
duplicate clusters move to the `independent` variant tier when git co-change is
consulted, and the source/test composition of what's demoted.

This is the volume + composition half of the FP gate for the co-change layer.
The accuracy half is a manual spot-check (hand-classify a sample of demoted
clusters as genuinely-benign parallel copies vs real refactorable duplication
wrongly hidden) recorded alongside in `docs/research/RESEARCH_METRICS.md` §12f.
The demotion runs whole-repo (co-change lives across the whole tree); the
`is_test_path` bucketing shows how much of the demotion is test-code parallelism
the #247 path signal did not already catch (pytest's `testing/` dir, etc.).

Usage:
    uv run --with pyyaml python bench/duplicates_cochange_sweep.py
    uv run --with pyyaml python bench/duplicates_cochange_sweep.py --stdout

archy:owns        main, run_project
"""

from __future__ import annotations

import argparse
import datetime as dt
import statistics
import sys
from pathlib import Path

from _common import REPO_ROOT, clone_or_update, load_manifest

from archy.coupling import git_cochange
from archy.duplicates import (
    classify_variants,
    compute_duplicates,
    demote_independent,
    is_test_path,
)
from archy.graph import parse_project

RESULTS = REPO_ROOT / "bench" / "duplicates_cochange_results.md"


def run_project(root: Path) -> dict | None:
    try:
        modules, results = parse_project(root, max_modules=0)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {str(exc)[:60]}"}
    rows = classify_variants(compute_duplicates(modules, results))
    cochange = git_cochange(root, keep_paths=frozenset(str(m.path) for m in modules))
    if cochange is None:
        return {"error": "not a git repo / git unavailable"}
    demoted = demote_independent(rows, counts=cochange.counts, pair_support=cochange.pair_support)
    before = sum(1 for g in rows if g.category == "duplicate")
    after = sum(1 for g in demoted if g.category == "duplicate")
    indep = [g for g in demoted if g.category == "variant" and g.variant_reason == "independent"]
    # A demotion is "source" if every member file is non-test (the highest-value
    # demotion - real parallel source implementations, not leaked test code).
    src_indep = sum(1 for g in indep if not any(is_test_path(m.path) for m in g.members))
    return {
        "primary_before": before,
        "primary_after": after,
        "demoted": len(indep),
        "demoted_source": src_indep,
    }


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
        print(
            f"  primary {result['primary_before']:4d}->{result['primary_after']:4d}  "
            f"demoted={result['demoted']:3d} (source={result['demoted_source']})",
            file=sys.stderr,
        )

    out: list[str] = []
    out.append("# Duplicate co-change demotion sweep (#242)")
    out.append("")
    out.append(
        f"Output of `uv run --with pyyaml python bench/duplicates_cochange_sweep.py`. "
        f"Captured {dt.date.today().isoformat()}. Thresholds: the shipped defaults "
        "(min_support 3, min_confidence 0.3, min_evidence 5)."
    )
    out.append("")
    out.append(
        "| project | primary before | primary after | demoted independent | of which source-only |"
    )
    out.append("| --- | ---: | ---: | ---: | ---: |")
    for r in rows:
        out.append(
            f"| {r['name']} | {r['primary_before']} | {r['primary_after']} | "
            f"{r['demoted']} | {r['demoted_source']} |"
        )
    out.append("")
    demoted_pct = [100 * r["demoted"] / r["primary_before"] for r in rows if r["primary_before"]]
    if demoted_pct:
        out.append(
            f"Median demotion: {statistics.median(demoted_pct):.0f}% of the primary tier "
            f"(range {min(demoted_pct):.0f}-{max(demoted_pct):.0f}%). Total demoted across "
            f"the corpus: {sum(r['demoted'] for r in rows)} "
            f"({sum(r['demoted_source'] for r in rows)} source-only)."
        )
    out.append("")
    out.append("## FP spot-check")
    out.append("")
    out.append(
        "_Manual, not produced by this script._ Hand-classify a sample of the "
        "`independent`-demoted clusters (member modules) as genuinely-benign "
        "parallel copies (per-backend implementations, symmetric methods) vs real "
        "refactorable duplication wrongly hidden; the recall risk is a *recently* "
        "forked copy that has not yet had a chance to co-change (the evidence "
        "guard only catches rarely-touched files). See RESEARCH_METRICS §12f."
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
