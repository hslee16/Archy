"""Tests for the Q1b primary outcome measure.

This decides whether a paid agent run counts as "structurally bad", so it is
worth more scrutiny than the code that drives the agents. The cases below pin
both directions: a genuine new tangle is caught, and the near-misses that would
inflate p_B are not.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# bench/ is not a package; append it (not insert(0)) so its generic module
# names cannot shadow stdlib/installed imports, mirroring tests/test_agent_footprint.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from q1b_score import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    measure,
    score_working_tree,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    """An acyclic three-module package committed at HEAD."""
    repo = tmp_path / "proj"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "low.py").write_text("VALUE = 1\n")
    (repo / "pkg" / "mid.py").write_text("from pkg.low import VALUE\n")
    (repo / "pkg" / "high.py").write_text("from pkg.mid import VALUE\n")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def test_clean_edit_is_not_structurally_bad(tmp_path: Path):
    repo = _repo(tmp_path)
    # Additive change, no new import edge at all.
    (repo / "pkg" / "high.py").write_text("from pkg.mid import VALUE\n\nEXTRA = VALUE + 1\n")
    v = score_working_tree(repo, "pkg", "HEAD")
    assert v.measurable
    assert not v.structurally_bad
    assert not v.cycle_regression
    assert v.cycle_count_after == 0


def _close_cycle(repo: Path) -> str:
    """Make `low` import `high`, closing low -> mid -> high -> low.

    One definition, because the exact edit is a *fact* two tests depend on: if
    the fixture's module shape changes, both call sites must change together.
    Returns the new `low.py` body so a caller can assert the tree was untouched.
    """
    low = "from pkg.high import EXTRA  # noqa\nVALUE = 1\n"
    (repo / "pkg" / "low.py").write_text(low)
    (repo / "pkg" / "high.py").write_text("from pkg.mid import VALUE\n\nEXTRA = 2\n")
    return low


def test_new_cycle_is_caught_and_sized(tmp_path: Path):
    repo = _repo(tmp_path)
    _close_cycle(repo)
    v = score_working_tree(repo, "pkg", "HEAD")
    assert v.measurable
    assert v.structurally_bad
    assert v.cycle_regression
    assert v.cycle_count_after > v.cycle_count_before
    assert v.max_new_scc >= 2
    assert v.new_cyclic_modules


def test_worktree_measurement_does_not_disturb_the_agent_edits(tmp_path: Path):
    """The base measurement must never touch the working tree it is comparing.

    A stash or checkout here would mutate, and on a crash destroy, a paid run.
    """
    repo = _repo(tmp_path)
    edited = _close_cycle(repo)
    score_working_tree(repo, "pkg", "HEAD")
    assert (repo / "pkg" / "low.py").read_text() == edited
    # And no worktree is left registered behind.
    out = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True
    )
    assert out.stdout.count("\n") == 1, out.stdout


def test_score_only_drop_is_recorded_but_not_the_gate(tmp_path: Path):
    """Q1a Finding 3: score moves are noise 98% of the time and can carry the
    wrong sign on real regressions, so they must not decide the verdict."""
    repo = _repo(tmp_path)
    # Add an isolated module: changes the score, introduces no cycle.
    (repo / "pkg" / "extra.py").write_text("from pkg.low import VALUE\n")
    v = score_working_tree(repo, "pkg", "HEAD")
    assert v.measurable
    assert not v.cycle_regression
    assert not v.structurally_bad, "score movement alone must never gate"


def test_unmeasurable_tree_is_flagged_not_scored_as_clean(tmp_path: Path):
    """A run that cannot be measured must be dropped by the pilot, not counted
    as a pass, or p_B is biased downward by exactly the broken runs."""
    repo = _repo(tmp_path)
    for f in (repo / "pkg").glob("*.py"):
        f.unlink()
    v = score_working_tree(repo, "pkg", "HEAD")
    assert not v.measurable
    assert not v.structurally_bad


def test_measure_returns_none_on_empty_package(tmp_path: Path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    assert measure(empty) is None
