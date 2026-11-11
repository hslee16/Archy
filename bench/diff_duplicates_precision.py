"""Harness for #548: firing rate and precision of a diff-scoped duplicates finding.

Pre-registration: `docs/research/DIFF_DUPLICATES_PRECISION.md`. Read it first; this
file implements the firing definition there and nothing else. It never edits the
detector and ships no CLI flag.

A cluster FIRES for a PR iff, computed from `git diff base..head` on the head tree:

- A (new): at least one member's `def` line falls inside an added hunk;
- B (pre-existing): at least one OTHER member's `def` line does not, so it was
  already there at base.

Modes:

    python bench/diff_duplicates_precision.py --canary

The canary must pass before any real run: a fixture PR that copies an existing
function into a NEW file must fire, and one that only edits an existing function
must not (both of Study 7's harness bugs produced publishable-looking numbers).

archy:owns        added_line_ranges, analyze_checkout, canary, detect_clusters,
                  is_firing, main
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Sequence
from pathlib import Path

from archy.coupling import git_cochange
from archy.duplicates import (
    DuplicateGroup,
    _is_vendored_path,
    classify_variants,
    compute_duplicates,
    compute_near_duplicates,
    demote_independent,
    is_test_path,
)
from archy.graph import ScanTooLargeError, parse_project
from archy.scan_config import resolve_max_modules

MIN_NODES = 30  # shipped `archy duplicates --min-nodes` default

AddedRanges = dict[str, list[tuple[int, int]]]

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_NEW_FILE = re.compile(r"^\+\+\+ b/(.+)$")


def added_line_ranges(diff: str) -> AddedRanges:
    """Parse `git diff -U0 --no-renames` into `{path: [(first, last), ...]}` of ADDED
    lines in the new file (1-indexed, inclusive).

    `--no-renames` is required: with rename detection a moved file would show no
    added lines and a copy into a new file would look like an edit. A pure deletion
    hunk (`+N,0`) adds nothing and is skipped.
    """
    ranges: AddedRanges = {}
    path: str | None = None
    for line in diff.splitlines():
        m = _NEW_FILE.match(line)
        if m:
            path = m.group(1)
            continue
        added = _hunk_added_range(line)
        if added is None or path is None:
            continue
        ranges.setdefault(path, []).append(added)
    return ranges


def _hunk_added_range(line: str) -> tuple[int, int] | None:
    """The added `(first, last)` of a `@@` hunk header, or None (not a header, or a
    pure deletion `+N,0` that adds nothing)."""
    h = _HUNK.match(line)
    if h is None:
        return None
    start = int(h.group(1))
    count = int(h.group(2)) if h.group(2) is not None else 1
    return (start, start + count - 1) if count > 0 else None


def _in_added(path: str, line: int, added: AddedRanges) -> bool:
    return any(lo <= line <= hi for lo, hi in added.get(path, ()))


def is_firing(group: DuplicateGroup, added: AddedRanges, root: Path) -> bool:
    """True iff one member's def line is added AND another member is pre-existing."""
    new = [_in_added(_rel(m.path, root), m.line, added) for m in group.members]
    return any(new) and not all(new)


def _rel(path: str, root: Path) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _path_excluded(rel: str) -> bool:
    """Test or vendored path, judged on the path RELATIVE to the checkout."""
    return is_test_path(rel) or _is_vendored_path(rel)


def _source_only(groups: list[DuplicateGroup], root: Path) -> list[DuplicateGroup]:
    """Drop any cluster with ANY member in test or vendored code, as pre-registered.

    Stricter than the shipped `classify_variants`, which demotes only clusters lying
    WHOLLY in test or vendored code and keeps cross-tier ones (a source function that
    copies isolated code). The pre-registration drops those too, for exact and
    near-miss alike, so a cross-tier cluster never reaches the labelers.
    """
    return [g for g in groups if not any(_path_excluded(_rel(m.path, root)) for m in g.members)]


def detect_clusters(root: Path) -> list[DuplicateGroup]:
    """The shipped pipeline on `root`: exact + co-change demotion + near-miss, source-only."""
    modules, parse_results = parse_project(root, max_modules=resolve_max_modules(root))
    rows = classify_variants(compute_duplicates(modules, parse_results, min_size=MIN_NODES))
    cochange = git_cochange(root, keep_paths=frozenset(str(m.path) for m in modules))
    if cochange is not None:
        rows = demote_independent(rows, counts=cochange.counts, pair_support=cochange.pair_support)
    exact = [g for g in rows if g.category == "duplicate"]
    near = compute_near_duplicates(modules, parse_results, min_size=MIN_NODES)
    return _source_only(exact + near, root)


def analyze_checkout(root: Path, base: str, head: str) -> dict:
    """Check out `head` in `root` and return the PR's ledger payload.

    Raises nothing for an unmeasurable tree: returns `{"dropped": reason}` so the
    caller records it and the denominator excludes it (never scored clean).
    """
    try:
        _git(root, "checkout", "--quiet", "--force", "--detach", head)
        _git(root, "clean", "-fdxq")
        diff = _git(root, "diff", "-U0", "--no-renames", base, head, "--", "*.py")
    except subprocess.CalledProcessError as exc:
        return {"dropped": f"git: {exc.stderr.strip()[:80] if exc.stderr else exc.returncode}"}
    if is_test_path(str(root.resolve() / "probe.py")):
        # Path-based test detection reads ABSOLUTE member paths, so a checkout that
        # lives under a `tests/` directory would mark every cluster as test code.
        return {"dropped": "checkout path contains a test segment"}
    added = added_line_ranges(diff)
    if not added:
        return {"dropped": "no added python lines"}
    try:
        clusters = detect_clusters(root)
    except ScanTooLargeError:
        return {"dropped": "scan too large"}
    except Exception as exc:  # a parse crash is a drop, not a clean PR
        return {"dropped": f"{type(exc).__name__}: {str(exc)[:80]}"}
    firings = [
        {
            "category": g.category,
            "exact": g.exact,
            "similarity": g.similarity,
            "size": g.size,
            "members": [
                {
                    "path": _rel(m.path, root),
                    "qualified_name": m.qualified_name,
                    "line": m.line,
                    "new": _in_added(_rel(m.path, root), m.line, added),
                }
                for m in g.members
            ],
        }
        for g in clusters
        if is_firing(g, added, root)
    ]
    return {"firings": firings, "added_files": sorted(added)}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
    ).stdout


# --- canary -------------------------------------------------------------------

_ORIGINAL = textwrap.dedent(
    '''\
    def summarize(rows, limit):
        """Total the positive values per key, keeping the top few."""
        totals = {}
        for key, value in rows:
            if value > 0:
                totals[key] = totals.get(key, 0) + value
            elif value < 0:
                totals.setdefault(key, 0)
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        out = []
        for key, total in ranked[:limit]:
            if total > 100:
                out.append((key, total, "big"))
            else:
                out.append((key, total, "small"))
        return out
    '''
)


def _commit_fixture(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=c",
        "-c",
        "user.email=c@c",
        "commit",
        "-q",
        "-m",
        message,
    )
    return _git(repo, "rev-parse", "HEAD").strip()


def canary() -> list[str]:
    """Return a list of failures (empty means the canary passed)."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        _git(repo, "init", "-q")
        base = _commit_fixture(repo, {"pkg/__init__.py": "", "pkg/a.py": _ORIGINAL}, "base")

        copy_head = _commit_fixture(
            repo,
            {"pkg/b.py": _ORIGINAL.replace("summarize", "summarise_again")},
            "copy into a new file",
        )
        copied = analyze_checkout(repo, base, copy_head)
        if not copied.get("firings"):
            failures.append(f"copy into a new file did not fire: {copied}")

        _git(repo, "checkout", "-q", "--detach", base)
        edit_head = _commit_fixture(
            repo,
            {"pkg/a.py": _ORIGINAL.replace('"big"', '"huge"')},
            "edit an existing function only",
        )
        edited = analyze_checkout(repo, base, edit_head)
        if edited.get("firings") or "dropped" in edited:
            failures.append(f"an edit-only PR fired or was dropped: {edited}")
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--canary", action="store_true", help="run the canary and exit")
    args = parser.parse_args(argv)
    if args.canary:
        failures = canary()
        for f in failures:
            print(f"CANARY FAIL: {f}")
        print("canary passed" if not failures else "canary FAILED")
        return 1 if failures else 0
    parser.error("only --canary exists so far; the PR loop lands in a later change")
    return 2


if __name__ == "__main__":
    sys.exit(main())
