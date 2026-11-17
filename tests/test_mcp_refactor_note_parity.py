"""Fused refactor notes agree through the CLI text, CLI JSON and MCP payload surfaces."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.gitlog import resolve_repo_root, since_window_empty
from archy.hotspots import NoCommitsError, git_churn
from archy.mcp import create_server
from tests.mcp_helpers import call_tool


@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    return tmp_path


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _notes(
    path: Path,
    *,
    since: str | None = None,
    has_rows: bool = False,
    text_header: str | None = None,
) -> tuple[str, str]:
    args = ["what-to-refactor-next", str(path), "--min-risk", "0.15"]
    if since is not None:
        args += ["--since", since]
    result = CliRunner().invoke(main, [*args, "--format", "json"])
    assert result.exit_code == 0, result.output
    cli = json.loads(result.output)
    _, mcp = call_tool(
        create_server(),
        "archy_what_to_refactor_next",
        {"path": str(path), "lens": "fused", "top_n": 10, "since": since, "min_risk": 0.15},
    )
    assert mcp is not None
    assert bool(cli["priorities"]) is has_rows
    assert bool(mcp["priorities"]) is has_rows
    assert cli["git_available"] is mcp["git_available"]
    text = CliRunner().invoke(main, args)
    assert text.exit_code == 0, text.output
    if has_rows:
        assert text_header is not None
        assert text.output.splitlines()[0] == text_header
    else:
        assert text.output == f"# {cli['note']}\n"
    return cli["note"], mcp["note"]


@pytest.mark.parametrize("git_missing", [False, True], ids=["non-repo", "git-unavailable"])
def test_no_git_note(source_tree: Path, monkeypatch: pytest.MonkeyPatch, git_missing: bool):
    if git_missing:
        _git(source_tree, "init", "-q")
        assert resolve_repo_root(source_tree) == source_tree.resolve()
        # Exercise FileNotFoundError from the real subprocess call, not a git stub.
        monkeypatch.setenv("PATH", str(source_tree / "no-executables"))
    assert resolve_repo_root(source_tree) is None
    assert git_churn(source_tree) is None
    cli, mcp = _notes(source_tree)
    assert (
        cli
        == mcp
        == (
            "Not a git repository (or git is unavailable), so the CC x churn lens was skipped "
            "and only the structural lens ran; no module is central and fragile above "
            "min_risk=0.15. Lower min_risk to widen the structural lens. "
            "Run inside git for the fused view."
        )
    )


def test_no_commits_note(source_tree: Path):
    _git(source_tree, "init", "-q")
    assert resolve_repo_root(source_tree) == source_tree.resolve()
    with pytest.raises(NoCommitsError):
        git_churn(source_tree)
    cli, mcp = _notes(source_tree)
    assert (
        cli
        == mcp
        == (
            "No commit history yet, so the CC x churn lens was skipped and only the structural "
            "lens ran; no module is central and fragile above min_risk=0.15. "
            "Lower min_risk to widen the structural lens. Run inside git for the fused view."
        )
    )


def test_empty_window_note(source_tree: Path):
    _git(source_tree, "init", "-q")
    _git(source_tree, "add", ".")
    _git(source_tree, "commit", "-qm", "initial")
    assert _git(source_tree, "rev-list", "--count", "HEAD") == "1"
    assert git_churn(source_tree, since="HEAD") == {}
    assert since_window_empty(source_tree, "HEAD")
    cli, mcp = _notes(source_tree, since="HEAD")
    assert (
        cli
        == mcp
        == (
            "No commits in the --since window (HEAD). "
            "Widen the window or omit --since to use full history."
        )
    )


def test_nothing_surfaced_note(source_tree: Path):
    _git(source_tree, "init", "-q")
    _git(source_tree, "add", ".")
    _git(source_tree, "commit", "-qm", "initial")
    churn = git_churn(source_tree)
    assert churn is not None and churn[str(source_tree / "pkg" / "a.py")] == 1
    cli, mcp = _notes(source_tree)
    assert (
        cli
        == mcp
        == (
            "No refactoring priorities surfaced: no file is both complex and frequently changed, "
            "and no module is central and fragile above min_risk=0.15; nothing to prioritize. "
            "Lower min_risk to widen the structural lens."
        )
    )


@pytest.mark.parametrize("git_state", ["non-repo", "git-unavailable", "no-commits"])
def test_structural_only_note(source_tree: Path, monkeypatch: pytest.MonkeyPatch, git_state: str):
    pkg = source_tree / "pkg"
    (pkg / "dep.py").write_text("", encoding="utf-8")
    (pkg / "hub.py").write_text("import pkg.dep\n", encoding="utf-8")
    for name in ("a", "b", "c"):
        (pkg / f"{name}.py").write_text("import pkg.hub\n", encoding="utf-8")
    if git_state != "non-repo":
        _git(source_tree, "init", "-q")
        assert resolve_repo_root(source_tree) == source_tree.resolve()
    if git_state == "git-unavailable":
        monkeypatch.setenv("PATH", str(source_tree / "no-executables"))
    if git_state == "no-commits":
        with pytest.raises(NoCommitsError):
            git_churn(source_tree)
        expected = (
            "No commit history yet; the CC x churn lens was skipped and "
            "this ranking is structural-only (edit-risk)."
        )
        header = "# 1 refactor candidate(s); showing top 1 (structural-only: no commits yet)"
    else:
        assert resolve_repo_root(source_tree) is None
        assert git_churn(source_tree) is None
        expected = (
            "Not a git repository (or git is unavailable); the CC x churn lens was skipped "
            "and this ranking is structural-only (edit-risk)."
        )
        header = (
            "# 1 refactor candidate(s); showing top 1 "
            "(structural-only: not a git repo or git is unavailable)"
        )
    cli, mcp = _notes(source_tree, has_rows=True, text_header=header)
    assert cli == mcp == expected
