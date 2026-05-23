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
"""

from __future__ import annotations

import os
import tempfile
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


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


@dataclass
class RealWriteSystem:
    """Atomic, real-filesystem writes. Tracks written paths for reporting."""

    written: list[Path] = field(default_factory=list)

    def read_text(self, path: Path) -> str | None:
        return _read_text(path)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory, then atomically rename.
        # Same-directory keeps the rename on one filesystem (cross-device
        # os.replace raises). Text mode with the default newline translation,
        # per the spec: we never hand-format line endings.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            try:
                os.replace(tmp, path)
            except PermissionError as exc:
                # Windows holds an exclusive handle while an Electron client is
                # running, so the rename onto an open target fails. Surface a
                # clear instruction instead of retrying indefinitely.
                raise InstallError(
                    f"Could not write {path}: the file may be open in a running "
                    "client. Close the client and re-run `archy install`."
                ) from exc
        finally:
            if tmp.exists():
                tmp.unlink()
        self.written.append(path)


@dataclass
class DryRunWriteSystem:
    """Reads the real filesystem; captures writes instead of performing them."""

    records: list[WriteRecord] = field(default_factory=list)

    def read_text(self, path: Path) -> str | None:
        return _read_text(path)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def write_text(self, path: Path, content: str) -> None:
        self.records.append(WriteRecord(path=path, content=content))
