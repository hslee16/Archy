"""`CODEX_HOME` and `XDG_CONFIG_HOME` move the files the clients read (#797)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.install import paths
from archy.install.adapters.codex import CodexAdapter
from archy.install.adapters.opencode import OpencodeAdapter


def _files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


@pytest.fixture
def relocated(simulate_os, monkeypatch, tmp_path):
    fake = simulate_os("linux")
    codex_home = tmp_path / "codexhome"
    xdg = tmp_path / "xdg"
    assert codex_home != fake.home / ".codex"
    assert xdg != fake.home / ".config"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return fake, codex_home, xdg


def test_install_and_uninstall_use_the_variables_dirs(relocated):
    fake, codex_home, xdg = relocated

    result = CliRunner().invoke(main, ["install", "--target", "codex,opencode", "-y"])

    assert result.exit_code == 0, result.output
    assert _files(fake.home) == []
    assert _files(codex_home) == [codex_home / "AGENTS.md", codex_home / "config.toml"]
    assert _files(xdg) == [xdg / "opencode" / "AGENTS.md", xdg / "opencode" / "opencode.json"]

    result = CliRunner().invoke(main, ["uninstall", "--target", "codex,opencode", "-y"])

    assert result.exit_code == 0, result.output
    assert not (codex_home / "AGENTS.md").exists()
    assert "archy" not in (codex_home / "config.toml").read_text(encoding="utf-8")
    assert not (xdg / "opencode" / "AGENTS.md").exists()
    assert "archy" not in (xdg / "opencode" / "opencode.json").read_text(encoding="utf-8")


def test_print_config_names_the_variables_dirs(relocated):
    _, codex_home, xdg = relocated

    assert str(codex_home / "config.toml") in (
        CliRunner().invoke(main, ["install", "--print-config", "codex"]).output
    )
    assert str(xdg / "opencode" / "opencode.json") in (
        CliRunner().invoke(main, ["install", "--print-config", "opencode"]).output
    )


def test_detection_looks_in_the_variables_dirs(relocated):
    fake, codex_home, xdg = relocated

    assert CodexAdapter().detection_paths() == [codex_home]
    assert OpencodeAdapter().detection_paths() == [xdg / "opencode"]
    assert fake.home / ".codex" not in CodexAdapter().detection_paths()


def test_defaults_stay_when_the_variables_are_unset(simulate_os):
    fake = simulate_os("linux")

    assert paths.codex_home() == fake.home / ".codex"
    assert paths.xdg_config_home() == fake.home / ".config"


def test_a_relative_xdg_config_home_is_ignored(simulate_os, monkeypatch):
    fake = simulate_os("linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")

    assert paths.xdg_config_home() == fake.home / ".config"


def test_windows_keeps_appdata_for_opencode(simulate_os, monkeypatch, tmp_path):
    fake = simulate_os("win32")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert OpencodeAdapter().detection_paths() == [fake.appdata / "opencode"]


def test_simulate_os_clears_the_variables_a_runner_may_set(simulate_os, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "from-the-runner"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "from-the-runner"))
    fake = simulate_os("linux")

    assert CodexAdapter().detection_paths() == [fake.home / ".codex"]
    assert OpencodeAdapter().detection_paths() == [fake.home / ".config" / "opencode"]
