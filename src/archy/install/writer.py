"""Write systems: the only place the installer touches the filesystem.

Two implementations behind one Protocol, mirroring chezmoi's dry-run
architecture (`docs/SPEC_INSTALL_TESTING.md`, "Dry-run is non-negotiable"):

- :class:`RealWriteSystem` performs atomic writes (temp file + ``os.replace``)
  and is used by layers 3-5 of the test strategy and by the live CLI.
- :class:`DryRunWriteSystem` *reads* the real filesystem (so merge logic is
  exercised against real existing configs) but *discards* writes, recording
  them instead. Layers 1-2 and ``archy install --print-config`` use it.

Keeping every write behind this seam is what lets the slow, OS-specific tests
stay rare: the bulk of the suite drives the pure render functions through the
dry-run system on a single Linux runner.

archy:owns        DryRunWriteSystem, InstallError, RealWriteSystem, WriteRecord,
                  WriteSystem, check_writable, write_target
archy:mirrored-by DryRunWriteSystem -> archy.cli_install, archy.install.runner,
                  InstallError -> archy.cli_install, archy.install, archy.install.base,
                  archy.install.merge, archy.install.runner,
                  WriteSystem -> archy.install.base, archy.install.runner
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class InstallError(Exception):
    """User-facing installer failure. Carries a message safe to print as-is."""


class WriteRecord(BaseModel):
    """A write that a :class:`DryRunWriteSystem` captured instead of performing."""

    model_config = ConfigDict(frozen=True)

    path: Path
    content: str


@runtime_checkable
class WriteSystem(Protocol):
    """Filesystem seam. Reads may hit disk; writes route through the impl."""

    def read_text(self, path: Path) -> str | None:
        """Return the file's text, or ``None`` if it does not exist."""
        ...

    def exists(self, path: Path) -> bool: ...

    def write_text(self, path: Path, content: str) -> None: ...

    def remove(self, path: Path) -> None:
        """Delete the file. A missing file is a no-op (uninstall is idempotent)."""
        ...


def write_target(path: Path) -> Path:
    """Return where a write to ``path`` lands: a symlink's target, else ``path``.

    Refuses a dangling link and a target that is not a regular file.
    """
    try:
        target = path.resolve(strict=True) if path.is_symlink() else path
    except (OSError, RuntimeError) as error:
        raise InstallError(f"{path}: cannot resolve write target ({error})") from error
    try:
        mode = target.stat().st_mode
    except FileNotFoundError:
        pass
    except OSError as error:
        raise InstallError(f"{path}: cannot inspect write target {target} ({error})") from error
    else:
        if not stat.S_ISREG(mode):
            kind = "is a directory; " if stat.S_ISDIR(mode) else ""
            raise InstallError(
                f"{path}: target {target} {kind}not a regular file; archy will not edit it"
            )
    return target


def check_writable(path: Path) -> None:
    """Refuse ``path`` when its write target is refused or sits in an unwritable folder."""
    target = write_target(path)
    if target.parent.exists() and not os.access(target.parent, os.W_OK):
        raise InstallError(
            f"{path}: cannot write target {target}: folder {target.parent} is not writable"
        )


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except IsADirectoryError as error:
        raise InstallError(f"{path}: is a directory; archy will not edit it") from error
    except UnicodeDecodeError as error:
        raise InstallError(
            f"{path}: not valid UTF-8 ({error.reason} at byte {error.start}); "
            "archy will not edit it"
        ) from error
    except PermissionError as error:
        if path.is_dir():
            raise InstallError(f"{path}: is a directory; archy will not edit it") from error
        raise InstallError(f"{path}: cannot read ({error})") from error
    if text.startswith("\ufeff"):
        raise InstallError(f"{path}: starts with a UTF-8 BOM; archy will not edit it")
    return text


@dataclass
class RealWriteSystem:
    """Atomic, real-filesystem writes. Tracks written and removed paths."""

    written: list[Path] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)

    def read_text(self, path: Path) -> str | None:
        return _read_text(path)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def remove(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            return
        self.removed.append(path)

    def write_text(self, path: Path, content: str) -> None:
        target = write_target(path)
        # A private directory keeps the temp on the target's filesystem and lets
        # new files inherit the umask without changing process-wide state.
        # Text mode uses the platform's default newline translation, per spec.
        try:
            mode = None
            if sys.platform != "win32":
                with suppress(FileNotFoundError):
                    mode = stat.S_IMODE(target.stat().st_mode)
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = tempfile.TemporaryDirectory(dir=target.parent, prefix=f".{target.name}.")
        except OSError as error:
            raise InstallError(
                f"{path}: cannot stage write to target {target} ({error})"
            ) from error
        with staging as tmp_dir:
            try:
                # An umask can remove owner access from the staging directory too.
                if sys.platform != "win32":
                    # Keep setgid, so a new file takes a shared folder's group.
                    Path(tmp_dir).chmod(stat.S_IMODE(Path(tmp_dir).stat().st_mode) | 0o700)
                tmp = Path(tmp_dir) / target.name
                tmp.write_text(content, encoding="utf-8")
                if mode is not None:
                    tmp.chmod(mode)
            except OSError as error:
                raise InstallError(
                    f"{path}: cannot stage write to target {target} ({error})"
                ) from error
            try:
                os.replace(tmp, target)
            except PermissionError as exc:
                # Windows holds an exclusive handle while an Electron client is
                # running, so the rename onto an open target fails. Surface a
                # clear instruction instead of retrying indefinitely.
                raise InstallError(
                    f"Could not write {path}: the file may be open in a running "
                    "client. Close the client and re-run `archy install`."
                ) from exc
        self.written.append(path)


@dataclass
class DryRunWriteSystem:
    """Reads the real filesystem; captures writes and removals, performs neither."""

    records: list[WriteRecord] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)

    def read_text(self, path: Path) -> str | None:
        return _read_text(path)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def write_text(self, path: Path, content: str) -> None:
        self.records.append(WriteRecord(path=path, content=content))

    def remove(self, path: Path) -> None:
        self.removed.append(path)
