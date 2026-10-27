from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def test_git_subprocess_uses_isolated_global_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")

    assert _git(repo, "config", "--get", "commit.gpgsign") == "false"
    assert _git(repo, "config", "--get", "tag.gpgsign") == "false"
    assert _git(repo, "config", "--get", "user.email") == "tests@example.invalid"

    (repo / "test.txt").write_text("test\n", encoding="utf-8")
    _git(repo, "add", "test.txt")
    _git(repo, "commit", "-qm", "test commit")
    assert len(_git(repo, "rev-parse", "HEAD")) == 40
