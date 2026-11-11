"""Reported tables for the #548 results: reasons, subgroups, git oracle, Sonar proxy.

    uv run --with pyarrow python bench/diff_duplicates_finalize.py \\
        --ledgers L1 L2 ... --labels-a A.jsonl --labels-b B.jsonl --key key.json

NONE of this is decisive. The verdict is `diff_duplicates_score.verdict` on the strict
reading, already rendered by `diff_duplicates_report.py`. These tables say WHY, and what
the secondary measures add. Subgroups are reported in full and never rescue a result.

archy:owns        main, oracle_vs_primary, reason_tally, subgroup_precision
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from diff_duplicates_oracles import (  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
    classify_git,
    function_lines,
    pr_density,
    ref_after_merge,
    sonar_would_fail,
)
from diff_duplicates_packets import flatten_firings  # ty: ignore[unresolved-import]
from diff_duplicates_pr import CACHE, CLONES  # ty: ignore[unresolved-import]
from diff_duplicates_report import _in_new_file  # ty: ignore[unresolved-import]
from diff_duplicates_run import _ok_rows  # ty: ignore[unresolved-import]


def _labels(path: Path) -> dict[str, dict]:
    """`{id: row}` for a labeler's ok rows, the last row per id winning."""
    out: dict[str, dict] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw) if raw.strip() else {}
        if row.get("status", "ok") == "ok" and "label" in row:
            out[row["id"]] = row
    return out


def reason_tally(labels: dict[str, dict]) -> Counter:
    """FALSE reasons one labeler gave; TRUE and UNSCORABLE carry none."""
    return Counter(r["reason"] for r in labels.values() if r["label"] == "FALSE")


def subgroup_precision(
    group_of: dict[str, str], strict_true: set[str], lenient_true: set[str]
) -> dict[str, tuple[int, int, int]]:
    """`{group: (strict TRUE, lenient TRUE, firings)}` over the firings in `group_of`."""
    out: dict[str, list[int]] = {}
    for fid, group in group_of.items():
        counts = out.setdefault(group, [0, 0, 0])
        counts[0] += fid in strict_true
        counts[1] += fid in lenient_true
        counts[2] += 1
    return {g: (c[0], c[1], c[2]) for g, c in sorted(out.items())}


def oracle_vs_primary(outcome_of: dict[str, str], strict_true: set[str]) -> dict[str, Counter]:
    """Git-oracle outcomes split by whether the primary oracle said TRUE."""
    split: dict[str, Counter] = {"primary TRUE": Counter(), "primary FALSE": Counter()}
    for fid, outcome in outcome_of.items():
        split["primary TRUE" if fid in strict_true else "primary FALSE"][outcome] += 1
    return split


def _merged_at(parquet: Path) -> dict[int, str | None]:
    import pyarrow.parquet as pq  # ty: ignore[unresolved-import]  (optional dependency)

    table = pq.read_table(parquet, columns=["id", "merged_at"])
    return {r["id"]: r["merged_at"] for r in table.to_pylist()}


def _git_outcome(fid: str, row: dict, firing: dict, merged_at: dict) -> str:
    stamp = merged_at.get(int(fid.split(":")[0]))
    if not stamp:
        return "unscorable"  # never merged: there is no later default-branch state
    repo_dir = CLONES / row["repo"].replace("/", "__")
    ref = ref_after_merge(repo_dir, stamp)
    return classify_git(repo_dir, firing, ref) if ref else "unscorable"


def _sonar(fid: str, row: dict, firing: dict) -> dict:
    repo_dir = CLONES / row["repo"].replace("/", "__")
    new = next(m for m in firing["members"] if m["new"])
    lines = function_lines(repo_dir, row["head"], new)
    density = pr_density(repo_dir, row)
    return {
        "lines": lines,
        "density": density,
        "fails": sonar_would_fail(exact=firing["exact"], copy_lines=lines, density=density),
    }


def _md_counter(title: str, counts: Counter) -> str:
    lines = [f"**{title}**", "", "| value | n |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ledgers", type=Path, nargs="+", required=True)
    parser.add_argument("--labels-a", type=Path, required=True)
    parser.add_argument("--labels-b", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument(
        "--pull-requests", type=Path, default=CACHE / "pull_request.parquet", help="AIDev parquet"
    )
    args = parser.parse_args(argv)
    rows: dict[str, dict] = {}
    for path in args.ledgers:
        rows.update(_ok_rows(path))
    firings = flatten_firings(rows)
    a, b = _labels(args.labels_a), _labels(args.labels_b)
    scorable = sorted(
        i for i in set(a) & set(b) if "UNSCORABLE" not in (a[i]["label"], b[i]["label"])
    )
    strict = {i for i in scorable if a[i]["label"] == "TRUE" and b[i]["label"] == "TRUE"}
    lenient = {i for i in scorable if "TRUE" in (a[i]["label"], b[i]["label"])}

    def tier(i: str) -> str:
        f = firings[i][1]
        return "exact" if f["exact"] else f["category"]

    groups = {
        "tier": {i: tier(i) for i in scorable},
        "file": {i: "new file" if _in_new_file(*firings[i]) else "existing file" for i in scorable},
    }
    merged_at = _merged_at(args.pull_requests)
    git = {i: _git_outcome(i, *firings[i], merged_at) for i in scorable}
    out = [
        "## FALSE reasons (per labeler)",
        _md_counter("labeler A", reason_tally(a)),
        _md_counter("labeler B", reason_tally(b)),
        "",
        "## Precision by subgroup (reported in full, never results)",
    ]
    for title, group_of in groups.items():
        header = "| group | strict TRUE | lenient TRUE | firings |\n| --- | --- | --- | --- |"
        out.append(f"**by {title}**\n\n{header}")
        out += [
            f"| {g} | {s} | {le} | {n} |"
            for g, (s, le, n) in subgroup_precision(group_of, strict, lenient).items()
        ]
        out.append("")
    out.append("## Git oracle (secondary, never decisive)")
    for name, counts in oracle_vs_primary(git, strict).items():
        out.append(_md_counter(name, counts))
    sonar = {i: _sonar(i, *firings[i]) for i in sorted(strict)}
    failing = [i for i, s in sonar.items() if s["fails"]]
    out += [
        "",
        "## Sonar proxy over the strict-TRUE firings (a proxy, never decisive)",
        f"- strict-TRUE firings: {len(sonar)}; would also fail Sonar's 3% gate: {len(failing)}; "
        f"only archy would add: {len(sonar) - len(failing)}",
    ]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
