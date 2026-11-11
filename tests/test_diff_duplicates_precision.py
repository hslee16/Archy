"""Tests for the #548 diff-scoped duplicates harness.

The firing definition is two conditions, and each must be able to fail:
A (a member's def line is inside an added hunk) and B (another member already
existed outside the added hunks). Each test below is aimed at one of them, on a
hand-built two-commit git fixture, and asserts the fixture really contains the
thing under test before asserting the outcome.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import diff_duplicates_precision as ddp  # ty: ignore[unresolved-import]  (added to sys.path above)

pytestmark = pytest.mark.filterwarnings("ignore")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "work"
    repo.mkdir()
    ddp._git(repo, "init", "-q")
    return repo


def _copy(name: str) -> str:
    return ddp._ORIGINAL.replace("summarize", name)


def test_added_line_ranges_parses_hunks_and_skips_deletions() -> None:
    diff = (
        "diff --git a/p/x.py b/p/x.py\n"
        "--- a/p/x.py\n"
        "+++ b/p/x.py\n"
        "@@ -3,0 +4,2 @@ def f():\n"
        "+a\n"
        "+b\n"
        "@@ -10,2 +11,0 @@\n"
        "-gone\n"
        "-gone\n"
        "@@ -20 +21 @@\n"
        "+one\n"
    )
    assert ddp.added_line_ranges(diff) == {"p/x.py": [(4, 5), (21, 21)]}


def test_copy_into_new_file_fires(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = ddp._commit_fixture(repo, {"pkg/__init__.py": "", "pkg/a.py": ddp._ORIGINAL}, "base")
    head = ddp._commit_fixture(repo, {"pkg/b.py": _copy("summarise_again")}, "copy")

    out = ddp.analyze_checkout(repo, base, head)

    # The fixture must hold a pre-existing member: one member NOT in the diff.
    assert out["firings"], out
    members = out["firings"][0]["members"]
    assert [m["new"] for m in members].count(False) == 1
    assert [m["new"] for m in members].count(True) == 1


def test_two_new_copies_with_no_preexisting_member_do_not_fire(tmp_path: Path) -> None:
    """Kills removal of condition B: both members are added, none existed at base."""
    repo = _repo(tmp_path)
    base = ddp._commit_fixture(repo, {"pkg/__init__.py": ""}, "base")
    head = ddp._commit_fixture(
        repo, {"pkg/a.py": _copy("one"), "pkg/b.py": _copy("two")}, "two new copies"
    )

    # Assert the fixture reaches the branch: the detector DOES cluster the pair.
    clusters = ddp.detect_clusters(repo)
    assert any(len(g.members) == 2 for g in clusters)

    assert ddp.analyze_checkout(repo, base, head)["firings"] == []


def test_backlog_cluster_untouched_by_the_diff_does_not_fire(tmp_path: Path) -> None:
    """Kills removal of condition A: the pair is wholly at base and the PR adds an
    unrelated file, so no member's def line is inside an added hunk."""
    repo = _repo(tmp_path)
    base = ddp._commit_fixture(
        repo,
        {"pkg/__init__.py": "", "pkg/a.py": _copy("one"), "pkg/b.py": _copy("two")},
        "base with backlog duplicate",
    )
    head = ddp._commit_fixture(repo, {"pkg/c.py": "VALUE = 1\n"}, "unrelated addition")

    clusters = ddp.detect_clusters(repo)
    assert any(len(g.members) == 2 for g in clusters)  # the backlog cluster exists

    assert ddp.analyze_checkout(repo, base, head)["firings"] == []


def test_editing_an_existing_function_does_not_fire(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    base = ddp._commit_fixture(repo, {"pkg/__init__.py": "", "pkg/a.py": ddp._ORIGINAL}, "base")
    head = ddp._commit_fixture(
        repo, {"pkg/a.py": ddp._ORIGINAL.replace('"big"', '"huge"')}, "edit only"
    )
    assert ddp.analyze_checkout(repo, base, head)["firings"] == []


def test_canary_passes() -> None:
    assert ddp.canary() == []


def test_cross_tier_cluster_with_a_test_member_does_not_fire(tmp_path: Path) -> None:
    """The pre-registration drops a cluster with ANY test member, not only a wholly
    test one: a new source copy of a function that already lives in tests/ is out."""
    repo = _repo(tmp_path)
    base = ddp._commit_fixture(
        repo,
        {"pkg/__init__.py": "", "tests/helpers.py": _copy("one")},
        "base with a test-side function",
    )
    head = ddp._commit_fixture(repo, {"pkg/b.py": _copy("two")}, "source copy")

    # The shipped detector keeps this cluster (cross-tier stays primary) and the
    # diff makes it a firing by A and B, so only the path filter can stop it.
    raw = ddp.compute_duplicates(*ddp.parse_project(repo), min_size=ddp.MIN_NODES)
    assert any(len(g.members) == 2 for g in raw)

    assert ddp.analyze_checkout(repo, base, head)["firings"] == []
