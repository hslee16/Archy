"""The allow-list in `bench/ruff_differential.py` (#532) explains only what it names.

The risk with an allow-list is that it grows into "explain everything": a rule
loose enough to swallow a real resolver bug turns the diff into a clean report.
Each case below pins one reason AND one near miss that must stay unexplained.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import ruff_differential as rd  # ty: ignore[unresolved-import]  (added to sys.path above)


def test_compare_splits_three_ways():
    both, only_ruff, only_archy = rd.compare(
        {("a.py", "b.py"), ("a.py", "c.py")}, {("a.py", "b.py"), ("a.py", "d.py")}
    )
    assert both == {("a.py", "b.py")}
    assert only_ruff == {("a.py", "d.py")}
    assert only_archy == {("a.py", "c.py")}


def test_implicit_init_needs_the_target_to_be_an_ancestor_package():
    files = set()
    # archy-only edge from a submodule to its own package __init__: explained.
    assert (
        rd.classify(("pkg/sub/m.py", "pkg/__init__.py"), "archy", set(), files) == "implicit_init"
    )
    # a child package's own __init__ runs its parent's too.
    assert (
        rd.classify(("pkg/sub/__init__.py", "pkg/__init__.py"), "archy", set(), files)
        == "implicit_init"
    )
    # a sibling package's __init__ is not an ancestor: a real edge, unexplained.
    assert rd.classify(("pkg/m.py", "other/__init__.py"), "archy", set(), files) is None
    # a prefix of the directory NAME is not an ancestor either (`pk` vs `pkg`).
    assert rd.classify(("pkg/m.py", "pk/__init__.py"), "archy", set(), files) is None
    # and ruff reporting the same shape is not archy's by-design edge.
    assert rd.classify(("pkg/sub/m.py", "pkg/__init__.py"), "ruff", set(), files) is None


def test_init_targeting_requires_the_other_engine_to_reach_the_same_package():
    other = {("app.py", "pkg/models.py")}
    # ruff says app -> pkg/__init__.py, archy says app -> pkg/models.py: same package.
    assert rd.classify(("app.py", "pkg/__init__.py"), "ruff", other, set()) == "init_targeting"
    # the other engine reaches nothing in pkg from this source: unexplained.
    assert (
        rd.classify(("app.py", "pkg/__init__.py"), "ruff", {("app.py", "elsewhere/x.py")}, set())
        is None
    )
    assert rd.classify(("other.py", "pkg/__init__.py"), "ruff", other, set()) is None


def test_script_sibling_only_in_a_directory_without_init():
    plain = {"bench/a.py", "bench/_common.py"}
    assert rd.classify(("bench/a.py", "bench/_common.py"), "ruff", set(), plain) == "script_sibling"
    # the same edge inside a real package is a gap, not a script sibling.
    packaged = plain | {"bench/__init__.py"}
    assert rd.classify(("bench/a.py", "bench/_common.py"), "ruff", set(), packaged) is None
    # archy reporting it is never a script sibling: archy is the engine that skips them.
    assert rd.classify(("bench/a.py", "bench/_common.py"), "archy", set(), plain) is None


def test_trees_inside_the_archy_checkout_are_refused_but_archy_itself_is_not(tmp_path: Path):
    """#856: ruff resolves against ancestor directories, so a nested clone gets archy's `tests/`."""
    assert rd.nested_in_repo(rd.REPO_ROOT / "bench/cache/anything") is True
    assert rd.nested_in_repo(rd.REPO_ROOT) is False
    assert rd.nested_in_repo(tmp_path) is False
    # the default clone location must itself be safe, or the guard refuses the corpus.
    assert rd.nested_in_repo(rd.CLONES) is False
