"""Secondary oracle and Sonar proxy for #548. Both are REPORTED, NEVER DECISIVE.

The primary oracle is the two blind labelers (`diff_duplicates_score.py`). These two
add context and cannot move a threshold.

Git oracle: a firing scores TRUE if, within 6 months of the merge on the default
branch, the new function is gone or no longer shares a shape with the existing one.
Firings are computed at the PR's FINAL head, so the "later PR revision" arm in the
ticket cannot apply by construction (the copy is present at head); only the 6-month
arm is measured, and the results say so. Outcomes:

- `removed`: the file is there but the new function is not (deleted or consolidated);
- `diverged`: both are there and their normalized shapes now differ;
- `unchanged`: both are there with the same shape;
- `unscorable`: the PR was not merged, a file is gone (a rename looks the same, so it
  is NOT counted as removed, which keeps the oracle biased against the feature), or the
  existing function is gone while the new one remains.

Sonar proxy: would SonarQube's "Sonar way for agentic AI" gate (new code over 3%
duplicated lines) also have failed this PR? PROXY, labeled as one everywhere: Sonar
needs 100 successive tokens over 10 lines with identifiers kept, so a firing counts
only if it is `exact` (byte-identical body) and at least 10 lines; density is the lines
of the PR's added functions in kept clusters over all lines the PR added to `.py` files.

archy:owns        classify_git, function_lines, pr_density, ref_after_merge,
                  sonar_would_fail
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from diff_duplicates_packets import function_source  # ty: ignore[unresolved-import]

from archy.complexity import compute_function_complexity

WINDOW_DAYS = 183  # "6 months", fixed here so it cannot drift
SONAR_MAX_DENSITY = 0.03
SONAR_MIN_LINES = 10


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args], capture_output=True, text=True, check=False
    )


def ref_after_merge(repo_dir: Path, merged_at: str, branch: str = "origin/HEAD") -> str | None:
    """The last commit on `branch` within `WINDOW_DAYS` of `merged_at`, or None."""
    merged = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    cutoff = (merged + timedelta(days=WINDOW_DAYS)).isoformat()
    shown = _git(repo_dir, "rev-list", "-1", f"--before={cutoff}", branch)
    return shown.stdout.strip() or None if shown.returncode == 0 else None


def _shapes(repo_dir: Path, ref: str, path: str) -> dict[str, str] | None:
    """`{qualified_name: shape_hash}` for the file at `ref`, or None if it is absent."""
    shown = _git(repo_dir, "show", f"{ref}:{path}")
    if shown.returncode != 0:
        return None
    return {
        f.qualified_name: f.shape_hash for f in compute_function_complexity(shown.stdout.encode())
    }


def classify_git(repo_dir: Path, firing: dict, later_ref: str) -> str:
    """`removed`, `diverged`, `unchanged` or `unscorable` for one firing at `later_ref`.

    Uses the first new member against the first existing member (members are in a
    stable order), which is every member of the usual two-function cluster.
    """
    new = next(m for m in firing["members"] if m["new"])
    old = next(m for m in firing["members"] if not m["new"])
    new_shapes = _shapes(repo_dir, later_ref, new["path"])
    if new_shapes is None:
        return "unscorable"  # file gone: a rename looks the same, so never counted as removed
    if new["qualified_name"] not in new_shapes:
        return "removed"
    old_shapes = _shapes(repo_dir, later_ref, old["path"])
    if old_shapes is None or old["qualified_name"] not in old_shapes:
        return "unscorable"
    same = new_shapes[new["qualified_name"]] == old_shapes[old["qualified_name"]]
    return "unchanged" if same else "diverged"


def _added_lines(repo_dir: Path, base: str, head: str) -> int:
    shown = _git(repo_dir, "diff", "--numstat", "--no-renames", base, head, "--", "*.py")
    return sum(int(line.split("\t")[0]) for line in shown.stdout.splitlines() if line[:1].isdigit())


def function_lines(repo_dir: Path, head: str, member: dict) -> int:
    shown = _git(repo_dir, "show", f"{head}:{member['path']}")
    source = function_source(shown.stdout, member["line"]) if shown.returncode == 0 else None
    return len(source.splitlines()) if source else 0


def pr_density(repo_dir: Path, row: dict) -> float:
    """Duplicated-line density of the PR's added code (a proxy, see the module doc)."""
    added = _added_lines(repo_dir, row["base"], row["head"])
    seen: set[tuple[str, int]] = set()
    duplicated = 0
    for firing in row.get("firings") or []:
        for m in firing["members"]:
            if m["new"] and (m["path"], m["line"]) not in seen:
                seen.add((m["path"], m["line"]))
                duplicated += function_lines(repo_dir, row["head"], m)
    return duplicated / added if added else 0.0


def sonar_would_fail(*, exact: bool, copy_lines: int, density: float) -> bool:
    """Would Sonar's 3% new-code duplication gate also have failed this PR?"""
    return exact and copy_lines >= SONAR_MIN_LINES and density > SONAR_MAX_DENSITY
