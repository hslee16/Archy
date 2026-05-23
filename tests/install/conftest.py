"""Shared fixtures for the installer test layers.

The single monkeypatch surface for cross-OS simulation is :mod:`archy.install.paths`
(see docs/SPEC_INSTALL_TESTING.md, "Single path-resolution helper"). `simulate_os`
fakes the platform and every path root from one Linux runner, and `tokenize_path`
turns an absolute emitted path back into a `<HOME>` / `<APPDATA>` token string so
snapshots are stable regardless of tmp_path or the runner's real OS.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from archy.install import base, paths

# The three OSes every adapter must pass detection/snapshot tests on.
PLATFORMS = ["linux", "darwin", "win32"]


@dataclass
class FakeOS:
    platform: str
    home: Path
    appdata: Path | None
    local_appdata: Path | None

    def tokenize(self, path: Path) -> str:
        """Replace volatile roots with stable tokens, normalize to POSIX."""
        text = str(path)
        roots = [
            (self.local_appdata, "<LOCALAPPDATA>"),
            (self.appdata, "<APPDATA>"),
            (self.home, "<HOME>"),
        ]
        for root, token in roots:
            if root is not None:
                text = text.replace(str(root), token)
        return text.replace("\\", "/")


@pytest.fixture
def simulate_os(monkeypatch, tmp_path):
    """Factory: fake an OS and its path roots under a tmp directory.

    All four `paths` accessors are patched here and nowhere else, so adapters
    that resolve via `paths.*` behave as if running on the requested platform.
    `shutil.which` is forced to miss so detection exercises the config probes.
    """

    def _simulate(platform: str) -> FakeOS:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        appdata: Path | None = None
        local_appdata: Path | None = None
        if platform == "win32":
            appdata = tmp_path / "AppData" / "Roaming"
            local_appdata = tmp_path / "AppData" / "Local"
            appdata.mkdir(parents=True, exist_ok=True)
            local_appdata.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(paths, "current_platform", lambda: platform)
        monkeypatch.setattr(paths, "home", lambda: home)
        monkeypatch.setattr(paths, "appdata", lambda: appdata)
        monkeypatch.setattr(paths, "local_appdata", lambda: local_appdata)
        monkeypatch.setattr(base.shutil, "which", lambda _name: None)
        return FakeOS(platform, home, appdata, local_appdata)

    return _simulate
