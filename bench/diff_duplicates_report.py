"""Results tables for the #548 study, from the ledgers and the two labelers' files.

    uv run python bench/diff_duplicates_report.py --ledgers L1 L2 ... \\
        --labels-a A.jsonl --labels-b B.jsonl --key key.json

Everything here is arithmetic over data the other modules produced; the decision is
`diff_duplicates_score.verdict`. Headroom is reported two ways, as pre-registered:
with unmeasurable PRs dropped from the denominator, and with every dropped PR that WAS
a Python PR counted as a non-firing (a PR with no added `.py` line was never a Python
PR, so it is in neither). Subgroups (exact vs near-miss, new file vs existing file) are
reported in full and are never results.

archy:owns        headroom, main, render, subgroup_counts
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from diff_duplicates_pr import CLONES  # ty: ignore[unresolved-import]
from diff_duplicates_run import _ok_rows  # ty: ignore[unresolved-import]  (bench/ is sys.path[0])
from diff_duplicates_score import score_labels, verdict  # ty: ignore[unresolved-import]

NON_PYTHON = "no added python lines"


def headroom(rows: dict[str, dict]) -> dict:
    """Firing rate over measured PRs, and over measured plus dropped Python PRs."""
    dropped = Counter(r["dropped"].split(":")[0] for r in rows.values() if "dropped" in r)
    measured = [r for r in rows.values() if "dropped" not in r]
    firing_prs = [r for r in measured if r.get("firings")]
    python_drops = sum(n for reason, n in dropped.items() if reason != NON_PYTHON)
    return {
        "rows": len(rows),
        "measured": len(measured),
        "dropped": dict(dropped),
        "prs_with_firing": len(firing_prs),
        "firings": sum(len(r["firings"]) for r in firing_prs),
        "repos_with_firing": len({r["repo"] for r in firing_prs}),
        "rate": len(firing_prs) / len(measured) if measured else 0.0,
        "rate_drops_as_clean": (
            len(firing_prs) / (len(measured) + python_drops) if measured else 0.0
        ),
    }


def subgroup_counts(firings: list[dict]) -> dict[str, Counter]:
    """Counts by tier, by whether the copy is in a new file, and by near-miss budget hit."""
    return {
        "tier": Counter("exact" if f["exact"] else f["category"] for f in firings),
        "file": Counter("new file" if f["new_file"] else "existing file" for f in firings),
        "budget": Counter("budget hit" if f["budget_hit"] else "complete" for f in firings),
    }


def _table(title: str, counts: Counter) -> str:
    lines = [f"**{title}**", "", "| group | firings |", "| --- | --- |"]
    lines += [f"| {name} | {n} |" for name, n in sorted(counts.items())]
    return "\n".join(lines)


def render(head: dict, firings: list[dict], scored: dict | None) -> str:
    """The markdown body of the results file (no prose beyond the numbers)."""
    parts = [
        "## Headroom",
        f"- Rows: {head['rows']}; measured Python PRs: {head['measured']}; "
        f"dropped: {head['dropped']}",
        f"- PRs with a firing: {head['prs_with_firing']} "
        f"({head['firings']} firings, {head['repos_with_firing']} repos)",
        f"- Rate, drops excluded: {head['rate']:.4f}; "
        f"with dropped Python PRs counted as clean: {head['rate_drops_as_clean']:.4f}",
        "",
        "## Firing subgroups (reported in full, not results)",
    ]
    parts += [_table(title, c) for title, c in subgroup_counts(firings).items()]
    if scored is not None:
        n = scored["precision_strict"] or 0.0
        got = verdict(
            firing_rate=head["rate"],
            scorable=scored["scorable"],
            repos=scored["repos"],
            precision=n,
        )
        parts += [
            "",
            "## Primary oracle (two blind labelers)",
            f"- Scorable: {scored['scorable']} from {scored['repos']} repos; "
            f"unscorable dropped: {scored['unscorable_dropped']}",
            f"- Strict (both TRUE): {scored['strict_true']} -> "
            f"{scored['precision_strict']}; lenient (either): {scored['lenient_true']} -> "
            f"{scored['precision_lenient']}",
            f"- Cohen's kappa: {scored['kappa']}",
            "",
            f"## Verdict: {got.outcome}",
            got.reason,
        ]
    return "\n".join(parts) + "\n"


def _in_new_file(row: dict, firing: dict) -> bool:
    """True if the new member's file did not exist at the PR's base commit."""
    new = next(m for m in firing["members"] if m["new"])
    repo_dir = CLONES / row["repo"].replace("/", "__")
    probe = subprocess.run(
        ["git", "-C", str(repo_dir), "cat-file", "-e", f"{row['base']}:{new['path']}"],
        capture_output=True,
        check=False,
    )
    return probe.returncode != 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ledgers", type=Path, nargs="+", required=True)
    parser.add_argument("--labels-a", type=Path)
    parser.add_argument("--labels-b", type=Path)
    parser.add_argument("--key", type=Path)
    args = parser.parse_args(argv)
    rows: dict[str, dict] = {}
    for path in args.ledgers:
        rows.update(_ok_rows(path))
    head = headroom(rows)
    scored = None
    if args.labels_a and args.labels_b and args.key:
        key = json.loads(args.key.read_text(encoding="utf-8"))
        scored = score_labels(args.labels_a, args.labels_b, {k: v["repo"] for k, v in key.items()})
    firings = [
        {
            "exact": f["exact"],
            "category": f["category"],
            "new_file": _in_new_file(r, f),
            "budget_hit": bool(r.get("near_miss_budget_hit")),
        }
        for r in rows.values()
        for f in r.get("firings") or []
    ]
    print(render(head, firings, scored))
    return 0


if __name__ == "__main__":
    sys.exit(main())
