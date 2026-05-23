"""Unit tests for the single path-resolution helper."""

from __future__ import annotations

from pathlib import Path

from archy.install import paths


def test_platform_predicates(monkeypatch):
    for plat, win, mac in [
        ("linux", False, False),
        ("darwin", False, True),
        ("win32", True, False),
    ]:
        monkeypatch.setattr(paths, "current_platform", lambda p=plat: p)
        assert paths.is_windows() is win
        assert paths.is_macos() is mac


def test_env_roots_read_from_environ(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert paths.appdata() == tmp_path / "roaming"
    assert paths.local_appdata() == tmp_path / "local"


def test_env_roots_none_when_unset(monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert paths.appdata() is None
    assert paths.local_appdata() is None


def test_user_profile_falls_back_to_home(monkeypatch, tmp_path):
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setattr(paths, "home", lambda: tmp_path / "h")
    assert paths.user_profile() == tmp_path / "h"
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))
    assert paths.user_profile() == tmp_path / "profile"


def test_home_is_a_path():
    assert isinstance(paths.home(), Path)
