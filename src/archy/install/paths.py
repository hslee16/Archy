"""Single source of truth for platform detection and OS-specific path roots.

Every `os.environ` lookup (`APPDATA`, `LOCALAPPDATA`, `USERPROFILE`) and every
`Path.home()` / `sys.platform` read in the installer flows through this module.
That is deliberate: the install path is the kind of code that silently breaks on
the OS the maintainer does not develop on, so unit tests simulate Linux, macOS,
and Windows from a single CI runner by monkeypatching *only the functions here*
(see `docs/SPEC_INSTALL_TESTING.md`, "Single path-resolution helper"). Adapters
must never call `sys.platform` or `os.environ` directly.

Homedir-anchored paths (`~/.claude.json`, `~/.cursor/mcp.json`, ...) use
`home()` on all three OSes: on Windows `Path.home()` already resolves to
`%USERPROFILE%`, so the spec's "Windows column" of `%USERPROFILE%\\.claude.json`
is just `home() / ".claude.json"`. Only the roaming/local AppData roots and the
Windows install-dir fallbacks need explicit branching.
"""

from __future__ import annotations

import os
from pathlib import Path


def current_platform() -> str:
    """Return the platform string (``linux``, ``darwin``, ``win32``, ...).

    Reads ``sys.platform`` lazily through ``os`` so a test can monkeypatch this
    function to fake any OS. Everything else in the installer branches on the
    helpers below rather than on ``sys.platform`` directly.
    """
    import sys

    return sys.platform


def is_windows() -> bool:
    return current_platform().startswith("win")


def is_macos() -> bool:
    return current_platform() == "darwin"


def home() -> Path:
    """User home directory (``%USERPROFILE%`` on Windows, ``$HOME`` elsewhere)."""
    return Path.home()


def _env_path(var: str) -> Path | None:
    raw = os.environ.get(var)
    return Path(raw) if raw else None


def appdata() -> Path | None:
    """``%APPDATA%`` (roaming). Windows-only in practice; ``None`` if unset."""
    return _env_path("APPDATA")


def local_appdata() -> Path | None:
    """``%LOCALAPPDATA%``. Windows-only in practice; ``None`` if unset."""
    return _env_path("LOCALAPPDATA")


def user_profile() -> Path:
    """``%USERPROFILE%`` on Windows, falling back to :func:`home` everywhere."""
    return _env_path("USERPROFILE") or home()
