"""`archy uninstall --dry-run` previews exactly what the real run does (#703).

It is what a person reads before letting archy delete files from their home
directory, so the two sides are derived independently: the printed lines, and
what a real run then changes on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.install.base import instructions_block
from archy.install.registry import all_adapters


def _tree(*roots: Path) -> dict[Path, bytes | None]:
    """Every file's bytes, and every directory, so creating one counts too."""
    return {p: p.read_bytes() if p.is_file() else None for root in roots for p in root.rglob("*")}


def _files(tree: dict[Path, bytes | None]) -> set[Path]:
    return {path for path, data in tree.items() if data is not None}


def _listed(preview: list[str], verb: str) -> set[Path]:
    return {Path(line.removeprefix(verb)) for line in preview if line.startswith(verb)}


def _run(command: str, project: Path, *extra: str) -> str:
    result = CliRunner().invoke(
        main, [command, "--project-root", str(project), *extra], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    return result.output


def _installed(home: Path, project: Path, location: str) -> None:
    """Install everything, over files of the user's own that hold archy's block.

    Without them every change is to a file archy created. The global ones carry a
    block that only installs before #560 wrote, which uninstall still removes.
    """
    mine = f"# Mine\n\n{instructions_block()}"
    (project / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    _run("install", project, "--target", "all", "--location", location, "--yes")
    if location == "global":
        for name in (".claude/CLAUDE.md", ".codex/AGENTS.md"):
            (home / name).parent.mkdir(parents=True, exist_ok=True)
            (home / name).write_text(mine, encoding="utf-8")


@pytest.mark.parametrize(
    ("location", "flags"),
    [
        ("global", ("--target", "all")),
        ("local", ("--target", "all")),
        ("global", ("--target", "all", "--no-permissions")),
        ("global", ("--target", "claude")),
    ],
    ids=["global", "local", "no-permissions", "one-target"],
)
def test_the_preview_names_what_the_real_run_deletes_and_rewrites(
    location: str, flags: tuple[str, ...], simulate_os, tmp_path: Path
):
    home = simulate_os("linux").home
    project = tmp_path / "project"
    project.mkdir()
    _installed(home, project, location)
    installed = _tree(home, project)
    options = (*flags, "--location", location)

    preview = _run("uninstall", project, *options, "--dry-run").splitlines()

    assert _tree(home, project) == installed, "a dry run changed the disk"
    # Sets: a file two clients share is listed once for each (#1041).
    deleted = _listed(preview, "delete ")
    stripped = _listed(preview, "strip  ")
    assert all(line.startswith(("delete ", "strip  ")) for line in preview), preview

    _run("uninstall", project, *options, "--yes")
    after = _tree(home, project)

    # Something really happens, or the comparison below proves little.
    assert stripped
    assert deleted == _files(installed) - _files(after)
    assert stripped == {
        path for path in _files(installed) & _files(after) if installed[path] != after[path]
    }


def test_the_cases_above_include_a_deletion_and_a_users_own_file(simulate_os, tmp_path: Path):
    """The fixtures reach both verbs, and the legacy global block."""
    home = simulate_os("linux").home
    project = tmp_path / "project"
    project.mkdir()
    _installed(home, project, "global")

    preview = _run(
        "uninstall", project, "--target", "all", "--location", "global", "--dry-run"
    ).splitlines()

    assert _listed(preview, "delete ")
    assert home / ".claude" / "CLAUDE.md" in _listed(preview, "strip  ")


def test_a_dry_run_with_nothing_installed_says_so(simulate_os, tmp_path: Path):
    home = simulate_os("linux").home
    assert all_adapters() and not _tree(home)

    output = _run("uninstall", tmp_path, "--target", "all", "--location", "global", "--dry-run")

    assert output == "Nothing to remove; archy is not installed for these targets.\n"
    assert not _tree(home)
