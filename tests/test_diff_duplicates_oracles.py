"""Tests for the #548 git oracle and Sonar proxy (both reported, never decisive).

Numbers are hand-worked in the docstrings. Each fixture asserts it holds the thing
under test (a pre-existing match, a function of at least 10 lines) before asserting
the outcome, so a drifting fixture cannot quietly stop testing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import diff_duplicates_oracles as orc  # ty: ignore[unresolved-import]  (added to sys.path above)
import diff_duplicates_precision as ddp  # ty: ignore[unresolved-import]  (added to sys.path above)

ORIGINAL = ddp._ORIGINAL
COPY = ORIGINAL.replace("summarize", "summarise_again")
FIRING = {
    "members": [
        {"path": "pkg/a.py", "qualified_name": "summarize", "line": 1, "new": False},
        {"path": "pkg/b.py", "qualified_name": "summarise_again", "line": 1, "new": True},
    ]
}


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    """base has pkg/a.py; head adds the copy pkg/b.py."""
    repo = tmp_path / "r"
    repo.mkdir()
    ddp._git(repo, "init", "-q")
    base = ddp._commit_fixture(repo, {"pkg/__init__.py": "", "pkg/a.py": ORIGINAL}, "base")
    head = ddp._commit_fixture(repo, {"pkg/b.py": COPY}, "copy")
    return repo, base, head


def test_oracle_outcomes(tmp_path: Path) -> None:
    repo, _base, head = _repo(tmp_path)
    assert orc.classify_git(repo, FIRING, head) == "unchanged"  # same shape at head itself

    diverged = ddp._commit_fixture(
        repo, {"pkg/b.py": COPY.replace("total > 100", "total > 100 and key").replace(
            "big", "large")}, "diverge"
    )  # fmt: skip
    assert orc.classify_git(repo, FIRING, diverged) == "diverged"

    removed = ddp._commit_fixture(repo, {"pkg/b.py": "VALUE = 1\n"}, "consolidate")
    assert orc.classify_git(repo, FIRING, removed) == "removed"


def test_a_deleted_or_renamed_file_is_unscorable_not_removed(tmp_path: Path) -> None:
    repo, _base, _head = _repo(tmp_path)
    ddp._git(repo, "rm", "-q", "pkg/b.py")
    gone = ddp._commit_fixture(repo, {"pkg/c.py": COPY}, "rename-like")
    assert orc.classify_git(repo, FIRING, gone) == "unscorable"


def test_ref_after_merge_uses_the_six_month_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merge 2025-01-15 + 183 days = 2025-07-17. Of commits on 01-01, 03-01 and 09-01,
    the last one inside the window is 03-01."""
    repo = tmp_path / "r"
    repo.mkdir()
    ddp._git(repo, "init", "-q")
    shas = {}
    for day in ("2025-01-01", "2025-03-01", "2025-09-01"):
        for var in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
            monkeypatch.setenv(var, f"{day}T12:00:00+00:00")
        shas[day] = ddp._commit_fixture(repo, {f"f{day}.txt": day}, day)
    got = orc.ref_after_merge(repo, "2025-01-15T00:00:00Z", branch="HEAD")
    assert got == shas["2025-03-01"]
    assert got != shas["2025-09-01"]  # the fixture has a commit past the window to exclude


@pytest.mark.parametrize(
    ("exact", "copy_lines", "density", "expected"),
    [
        (True, 15, 15 / 600, False),  # 2.5% < 3%: one 15-line copy in a 600-line PR passes
        (True, 15, 15 / 400, True),  # 3.75% > 3%: the same copy in a 400-line PR fails
        (True, 9, 0.5, False),  # under 10 lines is not a Sonar duplicated block
        (False, 40, 0.5, False),  # renamed identifiers are not duplicates to Sonar
    ],
)
def test_sonar_proxy_boundaries(
    exact: bool, copy_lines: int, density: float, expected: bool
) -> None:
    assert orc.sonar_would_fail(exact=exact, copy_lines=copy_lines, density=density) is expected


def test_pr_density_is_hand_worked(tmp_path: Path) -> None:
    """Head adds the copy (L lines) and a 3-line file: added = L + 3, duplicated = L,
    so density = L / (L + 3)."""
    repo, base, _head = _repo(tmp_path)
    head = ddp._commit_fixture(repo, {"pkg/c.py": "A = 1\nB = 2\nC = 3\n"}, "more")
    length = len(COPY.splitlines())
    assert length >= 10  # the fixture really holds a Sonar-sized copy
    row = {"base": base, "head": head, "firings": [FIRING]}
    assert orc.pr_density(repo, row) == pytest.approx(length / (length + 3))
