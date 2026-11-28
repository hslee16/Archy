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
from archy.install.registry import all_adapters


def _tree(*roots: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for root in roots for p in root.rglob("*") if p.is_file()}


def _listed(preview: list[str], verb: str) -> set[Path]:
    return {Path(line.removeprefix(verb)) for line in preview if line.startswith(verb)}


def _run(command: str, project: Path, *extra: str) -> str:
    result = CliRunner().invoke(
        main,
        [command, "--target", "all", "--project-root", str(project), *extra],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    return result.output


@pytest.mark.parametrize("location", ["global", "local"])
def test_the_preview_names_what_the_real_run_deletes_and_rewrites(
    location: str, simulate_os, tmp_path: Path
):
    home = simulate_os("linux").home
    project = tmp_path / "project"
    project.mkdir()
    # A file of the user's own that install adds to, so something is stripped
    # rather than deleted.
    (project / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    _run("install", project, "--location", location, "--yes")
    installed = _tree(home, project)

    preview = _run("uninstall", project, "--location", location, "--dry-run").splitlines()

    assert _tree(home, project) == installed, "a dry run changed the disk"
    # Sets: a file two clients share is listed once for each (#1041).
    deleted = _listed(preview, "delete ")
    stripped = _listed(preview, "strip  ")
    assert all(line.startswith(("delete ", "strip  ")) for line in preview), preview

    _run("uninstall", project, "--location", location, "--yes")
    after = _tree(home, project)

    # Both kinds really occur, or the comparison below proves little.
    assert deleted and stripped
    assert deleted == set(installed) - set(after)
    assert stripped == {p for p in set(installed) & set(after) if installed[p] != after[p]}


def test_a_dry_run_with_nothing_installed_says_so(simulate_os, tmp_path: Path):
    home = simulate_os("linux").home
    assert all_adapters() and not _tree(home)

    output = _run("uninstall", tmp_path, "--location", "global", "--dry-run")

    assert output == "Nothing to remove; archy is not installed for these targets.\n"
    assert not _tree(home)
