"""Installer writes preserve symlinks and POSIX modes (#751)."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.install.base import Scope
from archy.install.runner import resolve_targets, run_install, run_uninstall
from archy.install.writer import DryRunWriteSystem, InstallError, RealWriteSystem

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks and modes")


@pytest.mark.parametrize("writer", [RealWriteSystem, DryRunWriteSystem])
def test_install_refuses_unwritable_target_folder_before_any_write(simulate_os, writer):
    project = simulate_os("linux").home / "project"
    store = project / "store"
    store.mkdir(parents=True)
    target = store / "c.md"
    target.write_text("# User rules\n", encoding="utf-8")
    path = project / "CLAUDE.md"
    path.symlink_to("store/c.md")
    store.chmod(0o555)
    ws = writer()
    try:
        if os.access(store, os.W_OK):
            pytest.skip("runner can write a 0555 directory")
        with pytest.raises(InstallError) as exc:
            run_install(
                resolve_targets("claude"),
                Scope.LOCAL,
                project_root=project,
                seed_permissions=False,
                write_system=ws,
            )
        assert str(path) in str(exc.value)
        assert str(target) in str(exc.value)
        assert "file may be open" not in str(exc.value)
        assert not (project / ".mcp.json").exists()
        assert path.is_symlink()
        assert target.read_text(encoding="utf-8") == "# User rules\n"
        assert list(store.iterdir()) == [target]
        assert ws.written == [] if isinstance(ws, RealWriteSystem) else ws.records == []
    finally:
        store.chmod(0o755)


@pytest.mark.parametrize("stage", ["mkdir", "temporary_directory", "write_text"])
@pytest.mark.parametrize("failure", [PermissionError, OSError])
def test_staging_failure_names_path_and_target(tmp_path, monkeypatch, stage, failure):
    target = tmp_path / "store" / "c.md"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    path = tmp_path / "CLAUDE.md"
    path.symlink_to("store/c.md")

    def denied(*args, **kwargs):
        raise failure("staging denied")

    if stage == "temporary_directory":
        monkeypatch.setattr("archy.install.writer.tempfile.TemporaryDirectory", denied)
    else:
        monkeypatch.setattr(Path, stage, denied)
    ws = RealWriteSystem()
    with pytest.raises(InstallError) as exc:
        ws.write_text(path, "new\n")
    assert str(path) in str(exc.value)
    assert str(target) in str(exc.value)
    assert "staging denied" in str(exc.value)
    assert "file may be open" not in str(exc.value)
    assert target.read_text(encoding="utf-8") == "old\n"
    assert path.is_symlink()
    assert ws.written == []
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("target_spec", ["claude,codex", "codex,claude"])
@pytest.mark.parametrize("user_text", ["", "# User rules\n"])
def test_uninstall_cleans_shared_instruction_target(simulate_os, target_spec, user_text):
    project = simulate_os("linux").home / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.write_text(user_text, encoding="utf-8")
    claude = project / "CLAUDE.md"
    claude.symlink_to(agents.name)
    adapters = resolve_targets(target_spec)
    run_install(adapters, Scope.LOCAL, project_root=project, seed_permissions=False)
    assert "archy:begin" in agents.read_text(encoding="utf-8")

    run_uninstall(adapters, Scope.LOCAL, project_root=project, seed_permissions=False)

    if user_text:
        assert agents.read_text(encoding="utf-8") == user_text
        assert claude.is_symlink()
    else:
        assert not agents.exists()
        assert not claude.is_symlink()


def test_remove_unlinks_a_symlink_and_leaves_its_target(tmp_path):
    target = tmp_path / "rules.md"
    target.write_text("my own rules\n", encoding="utf-8")
    path = tmp_path / "archy.mdc"
    path.symlink_to(target.name)
    RealWriteSystem().remove(path)
    assert not path.is_symlink()
    assert target.read_text(encoding="utf-8") == "my own rules\n"


def test_new_file_takes_a_setgid_folders_bit_into_staging(tmp_path, monkeypatch):
    folder = tmp_path / "shared"
    folder.mkdir()
    folder.chmod(0o2775)
    if not folder.stat().st_mode & stat.S_ISGID:
        pytest.skip("filesystem drops setgid")
    # Linux copies setgid to a new child directory; macOS does not, and
    # gives new files the folder's group anyway, so there is nothing to keep.
    probe = Path(tempfile.mkdtemp(dir=folder))
    inherits = probe.stat().st_mode & stat.S_ISGID
    probe.rmdir()
    if not inherits:
        pytest.skip("new directories do not inherit setgid here")
    seen = []
    write = Path.write_text

    def spy(self, *args, **kwargs):
        seen.append(self.parent.stat().st_mode & stat.S_ISGID)
        return write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    RealWriteSystem().write_text(folder / "AGENTS.md", "x\n")
    assert seen == [stat.S_ISGID]


@pytest.mark.parametrize("symlink", [False, True])
@pytest.mark.parametrize("operation", ["write_text", "install"])
def test_refuse_non_regular_target_before_any_write(simulate_os, monkeypatch, symlink, operation):
    project = simulate_os("linux").home / "project"
    project.mkdir()
    target = project / ("muted" if symlink else "CLAUDE.md")
    os.mkfifo(target)
    path = project / "CLAUDE.md"
    if symlink:
        path.symlink_to(target.name)
    # Reading a FIFO would hang, so a read before the refusal fails instead.
    monkeypatch.setattr(RealWriteSystem, "read_text", lambda _, p: p == path and pytest.fail())
    ws = RealWriteSystem()
    with pytest.raises(InstallError, match="not a regular file") as exc:
        if operation == "write_text":
            ws.write_text(path, "new\n")
        else:
            run_install(
                resolve_targets("claude"),
                Scope.LOCAL,
                project_root=project,
                seed_permissions=False,
                write_system=ws,
            )
    assert str(target) in str(exc.value)
    assert stat.S_ISFIFO(path.stat().st_mode)
    assert path.is_symlink() is symlink
    assert ws.written == []


@pytest.mark.parametrize("entry", ["runner", "cli"])
def test_uninstall_after_shared_instruction_target_was_removed(simulate_os, entry):
    project = simulate_os("linux").home / "project"
    project.mkdir()
    agents = project / "AGENTS.md"
    agents.touch()
    claude = project / "CLAUDE.md"
    claude.symlink_to(agents.name)
    run_install(
        resolve_targets("claude,codex"),
        Scope.LOCAL,
        project_root=project,
        seed_permissions=False,
    )
    assert "archy:begin" in agents.read_text(encoding="utf-8")
    assert "archy" in json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]

    _uninstall("codex", project, entry)
    assert not agents.exists()
    assert claude.is_symlink()
    _uninstall("claude", project, entry)
    _uninstall("claude", project, entry)

    assert claude.is_symlink()
    assert claude.readlink() == Path("AGENTS.md")
    assert not agents.exists()
    assert (
        "archy" not in json.loads((project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    )


@pytest.mark.parametrize("entry", ["runner", "cli"])
def test_uninstall_skips_preexisting_dangling_instructions(simulate_os, entry):
    project = simulate_os("linux").home / "project"
    project.mkdir()
    claude = project / "CLAUDE.md"
    claude.symlink_to("gone.md")
    mcp = project / ".mcp.json"
    other = {"command": "other-server"}
    mcp.write_text(
        json.dumps({"mcpServers": {"archy": {"command": "uvx"}, "o": other}}), encoding="utf-8"
    )

    _uninstall("claude", project, entry)

    assert json.loads(mcp.read_text(encoding="utf-8")) == {"mcpServers": {"o": other}}
    assert claude.is_symlink()
    assert claude.readlink() == Path("gone.md")
    assert not (project / "gone.md").exists()


def _uninstall(target, project, entry):
    if entry == "runner":
        run_uninstall(
            resolve_targets(target),
            Scope.LOCAL,
            project_root=project,
            seed_permissions=False,
        )
    else:
        result = CliRunner().invoke(
            main,
            [
                "uninstall",
                "--target",
                target,
                "--location",
                "local",
                "--project-root",
                str(project),
                "-y",
                "--no-permissions",
            ],
        )
        assert result.exit_code == 0, result.output


def test_global_install_skips_a_dangling_retired_instruction_file(simulate_os):
    home = simulate_os("linux").home
    path = home / ".claude" / "CLAUDE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to("gone.md")
    assert path.is_symlink()

    run_install(resolve_targets("claude"), Scope.GLOBAL, seed_permissions=False)

    assert path.is_symlink()
    assert not (path.parent / "gone.md").exists()


def test_print_config_refuses_dangling_symlink(simulate_os):
    project = simulate_os("linux").home / "project"
    project.mkdir()
    path = project / "CLAUDE.md"
    path.symlink_to("gone.md")

    result = CliRunner().invoke(
        main,
        [
            "install",
            "--print-config",
            "claude",
            "--location",
            "local",
            "--project-root",
            str(project),
            "--no-permissions",
        ],
    )

    assert result.exit_code == 1, result.output
    assert str(path) in result.output
    assert list(project.iterdir()) == [path]


@pytest.mark.parametrize("link_kind", ["relative", "absolute", "chain"])
def test_write_through_symlink_preserves_link_and_updates_target(tmp_path, link_kind):
    target = tmp_path / "dotfiles" / "mcp.json"
    target.parent.mkdir()
    target.write_text("old\n", encoding="utf-8")
    link = tmp_path / "mcp.json"
    if link_kind == "chain":
        intermediate = tmp_path / "intermediate.json"
        intermediate.symlink_to("dotfiles/mcp.json")
        assert intermediate.is_symlink()
        link.symlink_to(intermediate.name)
    else:
        link.symlink_to(target if link_kind == "absolute" else "dotfiles/mcp.json")
    assert link.is_symlink()
    original_link = link.readlink()
    ws = RealWriteSystem()

    ws.write_text(link, "new\n")

    assert link.is_symlink()
    assert link.readlink() == original_link
    assert target.read_text(encoding="utf-8") == "new\n"
    assert link.read_text(encoding="utf-8") == "new\n"
    assert ws.written == [link]
    assert sorted(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o755])
@pytest.mark.parametrize("symlink", [False, True])
def test_write_preserves_existing_mode(tmp_path, mode, symlink):
    target = tmp_path / "AGENTS.md"
    target.write_text("old\n", encoding="utf-8")
    target.chmod(mode)
    path = target
    if symlink:
        path = tmp_path / "CLAUDE.md"
        path.symlink_to(target.name)
        assert path.is_symlink()

    RealWriteSystem().write_text(path, "new\n")

    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert target.read_text(encoding="utf-8") == "new\n"
    if symlink:
        assert path.is_symlink()


@pytest.mark.parametrize(
    "mask, expected",
    [(0o022, 0o644), (0o027, 0o640), (0o077, 0o600), (0o222, 0o444), (0o777, 0o000)],
)
def test_new_file_mode_respects_umask(tmp_path, mask, expected):
    path = tmp_path / "mcp.json"
    assert not path.exists()
    previous = os.umask(mask)
    try:
        RealWriteSystem().write_text(path, "new\n")
    finally:
        os.umask(previous)

    assert stat.S_IMODE(path.stat().st_mode) == expected
    path.chmod(0o600)
    assert path.read_text(encoding="utf-8") == "new\n"


def test_write_refuses_dangling_symlink(tmp_path):
    path = tmp_path / "CLAUDE.md"
    target = tmp_path / "missing.md"
    path.symlink_to(target.name)
    assert path.is_symlink()
    ws = RealWriteSystem()

    with pytest.raises(InstallError, match=r"CLAUDE\.md"):
        ws.write_text(path, "new\n")

    assert path.is_symlink()
    assert not target.exists()
    assert ws.written == []
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("writer", [RealWriteSystem, DryRunWriteSystem])
def test_install_refuses_dangling_symlink_before_any_write(simulate_os, writer):
    fake = simulate_os("linux")
    project = fake.home / "project"
    project.mkdir()
    path = project / "AGENTS.md"
    path.symlink_to("missing.md")
    assert path.is_symlink()
    ws = writer()

    with pytest.raises(InstallError, match=r"AGENTS\.md"):
        run_install(
            resolve_targets("claude,codex"),
            Scope.LOCAL,
            project_root=project,
            write_system=ws,
        )

    assert path.is_symlink()
    assert list(project.iterdir()) == [path]
    if isinstance(ws, RealWriteSystem):
        assert ws.written == []
    else:
        assert ws.records == []


@pytest.mark.parametrize("operation", ["install", "write_text"])
def test_symlink_loop_is_an_install_error(simulate_os, operation):
    tmp_path = simulate_os("linux").home
    path = tmp_path / "CLAUDE.md"
    path.symlink_to(path.name)
    assert path.is_symlink()
    ws = RealWriteSystem()

    with pytest.raises(InstallError, match=r"CLAUDE\.md"):
        if operation == "install":
            run_install(
                resolve_targets("claude"), Scope.LOCAL, project_root=tmp_path, write_system=ws
            )
        else:
            ws.write_text(path, "new\n")

    assert path.is_symlink()
    assert ws.written == []
