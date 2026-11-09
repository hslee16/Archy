"""A global install writes no instruction block, and clears an old one (#560).

A client-wide instruction file (``~/.claude/CLAUDE.md``, ``~/.codex/AGENTS.md``)
is loaded in every project, Python or not, so archy's "call it before you edit"
block does not belong there. The block still goes into a project's own file on a
local install, so the guidance appears only where the user opted archy in.
"""

from __future__ import annotations

import pytest

from archy.install.base import Scope, instructions_block
from archy.install.runner import resolve_targets, run_install, run_uninstall
from archy.install.writer import RealWriteSystem

ADAPTERS = ["claude", "codex", "cursor", "opencode", "continue"]
# Where each adapter keeps its instruction file, relative to the (faked) home on
# linux, and whether archy owns the file outright (so retiring it deletes it).
GLOBAL_FILES = {
    "claude": (".claude/CLAUDE.md", False),
    "codex": (".codex/AGENTS.md", False),
    "opencode": (".config/opencode/AGENTS.md", False),
    "cursor": (".cursor/rules/archy.mdc", True),
    "continue": (".continue/rules/archy.md", True),
}
LOCAL_FILES = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "opencode": "AGENTS.md",
    "cursor": ".cursor/rules/archy.mdc",
    "continue": ".continue/rules/archy.md",
}
USER_TEXT = b"# my notes\r\nkeep this exactly\r\n"  # CRLF: a rewrite would normalise it
OLD_BLOCK = instructions_block()


def _install(adapter_id: str, scope: Scope, project=None) -> None:
    run_install(
        resolve_targets(adapter_id),
        scope,
        project_root=project,
        write_system=RealWriteSystem(),
    )


@pytest.mark.parametrize("adapter_id", ADAPTERS)
def test_global_install_creates_no_instruction_file(adapter_id, simulate_os):
    fake = simulate_os("linux")
    rel, _owned = GLOBAL_FILES[adapter_id]

    _install(adapter_id, Scope.GLOBAL)

    assert not (fake.home / rel).exists()


@pytest.mark.parametrize("adapter_id", ["claude", "codex", "opencode"])
def test_global_install_leaves_a_users_own_file_byte_identical(adapter_id, simulate_os):
    fake = simulate_os("linux")
    path = fake.home / GLOBAL_FILES[adapter_id][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(USER_TEXT)

    _install(adapter_id, Scope.GLOBAL)

    assert path.read_bytes() == USER_TEXT


@pytest.mark.parametrize("adapter_id", ["claude", "codex", "opencode"])
def test_global_install_removes_an_old_block_and_keeps_the_users_text(adapter_id, simulate_os):
    """Migration: a pre-#560 global install left the block next to the user's text."""
    fake = simulate_os("linux")
    path = fake.home / GLOBAL_FILES[adapter_id][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    user = "# my notes\nkeep this exactly\n"
    path.write_text(f"{user}\n{OLD_BLOCK}", encoding="utf-8", newline="")
    assert "archy:begin" in path.read_text(encoding="utf-8")  # the fixture has the block

    _install(adapter_id, Scope.GLOBAL)

    assert path.read_bytes() == user.encode("utf-8")


@pytest.mark.parametrize("adapter_id", ["claude", "codex", "opencode"])
def test_global_install_deletes_a_file_that_held_only_the_old_block(adapter_id, simulate_os):
    fake = simulate_os("linux")
    path = fake.home / GLOBAL_FILES[adapter_id][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OLD_BLOCK, encoding="utf-8", newline="")

    _install(adapter_id, Scope.GLOBAL)

    assert not path.exists()


@pytest.mark.parametrize("adapter_id", ["cursor", "continue"])
def test_global_install_deletes_a_rules_file_archy_owns(adapter_id, simulate_os):
    fake = simulate_os("linux")
    path = fake.home / GLOBAL_FILES[adapter_id][0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(OLD_BLOCK, encoding="utf-8", newline="")

    _install(adapter_id, Scope.GLOBAL)

    assert not path.exists()


def test_global_install_clears_the_old_block_even_when_the_claude_plugin_is_installed(
    simulate_os,
):
    fake = simulate_os("linux")
    manifest = fake.home / ".claude" / "plugins" / "archy" / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name": "archy"}', encoding="utf-8")
    path = fake.home / ".claude" / "CLAUDE.md"
    path.write_text(f"mine\n\n{OLD_BLOCK}", encoding="utf-8", newline="")

    _install("claude", Scope.GLOBAL)

    assert path.read_bytes() == b"mine\n"


@pytest.mark.parametrize("adapter_id", ADAPTERS)
def test_local_install_still_writes_the_block_into_the_project(adapter_id, simulate_os):
    """Guard: the fix must not pass by deleting the block everywhere."""
    fake = simulate_os("linux")
    project = fake.home / "proj"
    project.mkdir()

    _install(adapter_id, Scope.LOCAL, project)

    written = (project / LOCAL_FILES[adapter_id]).read_text(encoding="utf-8")
    assert OLD_BLOCK in written


@pytest.mark.parametrize("adapter_id", ADAPTERS)
def test_a_local_install_and_uninstall_leaves_the_project_file_as_it_was(adapter_id, simulate_os):
    fake = simulate_os("linux")
    project = fake.home / "proj"
    project.mkdir()
    path = project / LOCAL_FILES[adapter_id]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("mine\n", encoding="utf-8", newline="")
    owned = adapter_id in {"cursor", "continue"}

    _install(adapter_id, Scope.LOCAL, project)
    run_uninstall(
        resolve_targets(adapter_id),
        Scope.LOCAL,
        project_root=project,
        write_system=RealWriteSystem(),
    )

    # An archy-owned rules file is deleted on uninstall, user text and all (existing behaviour).
    assert path.exists() is not owned
    if not owned:
        assert path.read_bytes() == b"mine\n"
