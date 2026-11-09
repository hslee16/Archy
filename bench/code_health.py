#!/usr/bin/env python
"""archy's own CodeScene score, weighted by code lines (the epic's metric, #896).

CodeScene's project figure is a LoC-weighted average of per-file scores, because a
5,000-line file at 2.0 outweighs two tiny perfect ones (their docs: 2.0 at 5,000 lines
plus 10.0 at 100 and 10.0 at 10 is 7.33 unweighted and far lower weighted). The
unweighted mean of file scores flatters a codebase whose small files are all 10, so
this reports both and says which one counts.

Files `cs review` returns no score for (no scorable code) are dropped, never counted
as 10. "Code lines" here are non-blank, non-comment lines, a close stand-in for
CodeScene's own "comments stripped away" figure, not identical to it.

Needs the CodeScene CLI (`cs`) and `cs auth`; it is a dev tool, not run in CI.

Usage:
    uv run python bench/code_health.py
    uv run python bench/code_health.py --top 15
    uv run python bench/code_health.py --json

archy:owns        code_lines, gap_table, main, measure, unweighted_mean, weighted_mean
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

Row = tuple[str, float, int]  # path, score, code lines


def code_lines(text: str) -> int:
    """Lines that hold code: not blank, not comment-only. Docstring lines count."""
    return sum(1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))


def weighted_mean(rows: list[Row]) -> float | None:
    """Mean of scores weighted by code lines; None when there is nothing to weigh."""
    total = sum(loc for _, _, loc in rows)
    return sum(score * loc for _, score, loc in rows) / total if total else None


def unweighted_mean(rows: list[Row]) -> float | None:
    return sum(score for _, score, _ in rows) / len(rows) if rows else None


def gap_table(rows: list[Row], top: int) -> list[tuple[str, float, int, float, float]]:
    """The files that cost the most: (path, score, lines, share of gap, cumulative share).

    A file's cost is its lines times its distance from 10, so a big mediocre file
    outranks a small bad one, which is the order the weighted average rewards.
    """
    ranked = sorted(rows, key=lambda r: (10 - r[1]) * r[2], reverse=True)
    gap = sum((10 - score) * loc for _, score, loc in rows)
    out, running = [], 0.0
    for path, score, loc in ranked[:top]:
        share = (10 - score) * loc / gap if gap else 0.0
        running += share
        out.append((path, score, loc, share, running))
    return out


def _tracked_sources(root: Path) -> list[Path]:
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "src/archy/*.py", "src/archy/**/*.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [root / p for p in listing]


def measure(root: Path) -> list[Row]:
    """Score every tracked source file with `cs review`; unscored files are dropped."""
    files = _tracked_sources(root)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write("\n".join(str(f) for f in files))
        listing = Path(fh.name)
    try:
        proc = subprocess.run(
            ["cs", "review", "--file-list", str(listing), "--output-format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        listing.unlink(missing_ok=True)
    reviewed = json.loads(proc.stdout)
    return [
        (Path(entry["path"]).relative_to(root).as_posix(), entry["score"], code_lines(_read(entry)))
        for entry in reviewed
        if entry.get("score") is not None
    ]


def _read(entry: dict) -> str:
    return Path(entry["path"]).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top", type=int, default=10, help="files to list in the gap table")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    rows = measure(root)
    weighted, plain = weighted_mean(rows), unweighted_mean(rows)
    table = gap_table(rows, args.top)
    if args.json:
        print(
            json.dumps(
                {
                    "files": len(rows),
                    "code_lines": sum(r[2] for r in rows),
                    "weighted": weighted,
                    "unweighted": plain,
                    "gap": [
                        dict(zip(("path", "score", "lines", "share", "cumulative"), t, strict=True))
                        for t in table
                    ],
                },
                indent=2,
            )
        )
        return 0
    if weighted is None or plain is None:
        print("no scorable files", file=sys.stderr)
        return 1
    print(f"files {len(rows)}   code lines {sum(r[2] for r in rows)}")
    print(f"LoC-weighted {weighted:.2f}   (this is the number that counts)")
    print(f"unweighted   {plain:.2f}   (flatters: small perfect files offset big bad ones)")
    print("\nlargest contributors to the gap (lines x distance from 10):")
    for path, score, loc, share, running in table:
        print(f"  {path:34} score {score:5.2f}  lines {loc:5}  ", end="")
        print(f"share {share:6.1%}  cumulative {running:6.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
