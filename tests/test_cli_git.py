"""The git-history commands: hotspots, coupling, duplicates, what-to-refactor-next."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import (
    _coupling_to_text,
    _duplicates_to_text,
    _hotspots_to_text,
    main,
)
from archy.coupling import CouplingPair
from archy.hotspots import Hotspot
from tests.cli_helpers import (
    _dup_group,
    _fresh_git_repo_without_commits,
    _make_empty_package_project,
)


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_hotspots_top_rejects_invalid_values(tmp_path: Path, bad: str):
    # --top was unvalidated: 0 showed nothing and -1 sliced off the
    # lowest-score row while mislabeling the count. The guard runs before the
    # git check, so a non-git tmp dir still exercises it.
    _make_empty_package_project(tmp_path)
    result = CliRunner().invoke(main, ["hotspots", str(tmp_path), "--top", bad])
    assert result.exit_code != 0
    assert f"--top must be >= 1; got {bad}" in result.output


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_what_to_refactor_next_top_rejects_invalid_values(tmp_path: Path, bad: str):
    _make_empty_package_project(tmp_path)
    result = CliRunner().invoke(main, ["what-to-refactor-next", str(tmp_path), "--top", bad])
    assert result.exit_code != 0
    assert f"--top must be >= 1; got {bad}" in result.output


@pytest.mark.parametrize("bad", ["1.5", "-0.1"])
def test_what_to_refactor_next_min_risk_rejects_out_of_range(tmp_path: Path, bad: str):
    _make_empty_package_project(tmp_path)
    result = CliRunner().invoke(main, ["what-to-refactor-next", str(tmp_path), "--min-risk", bad])
    assert result.exit_code != 0
    assert "--min-risk must be in [0, 1]" in result.output


def test_duplicates_text_sections_have_header_blank_columns_then_rows():
    rows = [
        _dup_group("duplicate", exact=True),
        _dup_group("variant"),
        _dup_group("near_miss", similarity=0.75),
    ]
    text = _duplicates_to_text(rows, top_n=5, min_nodes=5)
    assert text.splitlines() == [
        "# 1 exact duplicate(s) (byte-identical; high confidence); showing top 1 (min-nodes 5)",
        "",
        "  redund  size  count  members",
        "      12    12      2  pkg.a:3 alpha",
        "                       pkg.b:7 beta",
        "",
        "# 1 likely-intentional variant(s) (same-class / boilerplate / "
        "test / vendored / independent; showing top 1)",
        "",
        "  redund  size  count  reason      members",
        "      12    12      2  test        pkg.a:3 alpha",
        "                                   pkg.b:7 beta",
        "",
        "# 1 possible near-miss (Type-3, gapped) cluster(s) (LOWER confidence; showing top 1)",
        "",
        "   sim  count  members",
        "  0.75      2  pkg.a:3 alpha",
        "               pkg.b:7 beta",
    ]


@pytest.mark.parametrize(("since", "suffix"), [(None, ""), ("2026-01-01", " since 2026-01-01")])
def test_hotspots_and_coupling_headers_count_total_and_shown(since, suffix):
    hotspots = [
        Hotspot(module=f"pkg.m{i}", path=f"m{i}.py", cc_sum=2, churn=3, score=6) for i in range(3)
    ]
    pairs = [
        CouplingPair(
            module_a=f"pkg.a{i}",
            module_b=f"pkg.b{i}",
            path_a="a.py",
            path_b="b.py",
            support=4,
            confidence=0.5,
            count_a=8,
            count_b=8,
        )
        for i in range(3)
    ]
    hot = _hotspots_to_text(hotspots, top_n=2, since=since).splitlines()[0]
    cpl = _coupling_to_text(pairs, top_n=2, since=since, min_support=2).splitlines()[0]
    assert hot == f"# 3 hotspot(s); showing top 2{suffix}"
    assert cpl == f"# 3 hidden-coupling pair(s); showing top 2{suffix}"


def test_hotspots_names_missing_commit_history_not_missing_repo(tmp_path: Path):
    # #648: a fresh `git init` (no commits) is a repository, not a missing
    # one. The error must point the user at committing, not at their git
    # install, and must not leak a traceback.
    repo = tmp_path / "repo"
    _fresh_git_repo_without_commits(repo)
    result = CliRunner().invoke(main, ["hotspots", str(repo)])
    assert result.exit_code != 0
    assert "no commit history" in result.output
    assert "Traceback" not in result.output


def test_what_to_refactor_next_names_missing_commit_history(tmp_path: Path):
    # #648: fused lens on a zero-commit repo degrades to structural-only, but
    # the degraded header must not claim there is no git repository.
    repo = tmp_path / "repo"
    _fresh_git_repo_without_commits(repo)
    result = CliRunner().invoke(main, ["what-to-refactor-next", str(repo)])
    assert result.exit_code == 0, result.output
    assert "not a git repo" not in result.output.lower()
    assert "commits" in result.output
