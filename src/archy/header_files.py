"""Rewrite a module's header without changing anything else about the file (#783).

`headers.apply_header` works on text. This is the part that gets the text out of
a file and back: same encoding, BOM and line endings, and never a rewrite whose
code differs from what was there.

archy:owns        SourceFile, header_is_current, read_source, rewritten, write_atomic
"""

from __future__ import annotations

import ast
import io
import os
import shutil
import tempfile
import tokenize
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archy.headers import apply_header, existing_block


class SourceFile(BaseModel):
    """A module as read: its text with `\\n` endings, and how to write it back."""

    model_config = ConfigDict(frozen=True)

    path: Path
    raw: bytes
    # `utf-8-sig` when there is a BOM, so encoding puts it back.
    encoding: str
    text: str

    def newline(self) -> str:
        """The file's one line ending; a mixed file cannot be rewritten faithfully."""
        crlf = self.raw.count(b"\r\n")
        found = {
            "\r\n": crlf,
            "\n": self.raw.count(b"\n") - crlf,
            "\r": self.raw.count(b"\r") - crlf,
        }
        used = [ending for ending, count in found.items() if count]
        if len(used) > 1:
            raise ValueError("mixed line endings; not rewritten")
        return used[0] if used else "\n"


def read_source(path: Path) -> SourceFile:
    """Decode by the file's own cookie or BOM. Raises ValueError, never guesses."""
    raw = path.read_bytes()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
        decoded = raw.decode(encoding)
    except (SyntaxError, ValueError, LookupError) as exc:
        raise ValueError(f"cannot decode: {exc}") from exc
    text = decoded.replace("\r\n", "\n").replace("\r", "\n")
    return SourceFile(path=path, raw=raw, encoding=encoding, text=text)


def header_is_current(source: SourceFile, block: str) -> bool:
    return existing_block(source.text) == block


def rewritten(source: SourceFile, block: str) -> bytes | None:
    """The file's new bytes, or None when it already says this.

    Raises ValueError rather than return bytes that change the module's code.
    """
    updated = apply_header(source.text, block)
    if updated == source.text:
        return None
    _require_round_trip(source)
    newline = source.newline()
    try:
        data = updated.replace("\n", newline).encode(source.encoding)
    except UnicodeEncodeError as exc:
        raise ValueError(f"header does not fit the file's {source.encoding} encoding") from exc
    # One guard for every way the splice could go wrong: whatever the block
    # holds, nothing but the docstring may differ.
    if _code(data) != _code(source.raw):
        raise ValueError("rewriting the header would change the module's code")
    return data


def _require_replaceable(target: Path, expected: bytes) -> None:
    if not os.access(target, os.W_OK):
        raise ValueError("file is not writable")
    if target.stat().st_nlink > 1:
        raise ValueError("file has other hard links; replacing it would detach them")
    if target.read_bytes() != expected:
        raise ValueError("file changed while archy was running; not rewritten")


def _require_round_trip(source: SourceFile) -> None:
    """Some codecs decode two byte spellings to one character (cp932, utf-7), so
    re-encoding would change bytes far from the docstring."""
    try:
        same = source.raw.decode(source.encoding).encode(source.encoding) == source.raw
    except UnicodeError:
        same = False
    if not same:
        raise ValueError(f"{source.encoding} does not round-trip this file; not rewritten")


def _code(data: bytes) -> str:
    """The module's statements, docstring aside. Raises ValueError if it cannot tell."""
    try:
        tree = ast.parse(data)
        body = tree.body[1:] if ast.get_docstring(tree) is not None else tree.body
        return ast.dump(ast.Module(body=body, type_ignores=[]))
    except SyntaxError as exc:
        raise ValueError(f"does not parse: {exc.msg}") from exc
    except RecursionError as exc:
        # `ast.dump` recurses; a very long expression exhausts it.
        raise ValueError("too deeply nested to verify; not rewritten") from exc


def write_atomic(path: Path, data: bytes, *, expected: bytes) -> None:
    """Replace the file in one step, through a symlink and keeping its mode.

    Refuses a file that no longer holds `expected` (someone saved it since it
    was read), and one a replacement would treat differently from a write: not
    writable, or hard-linked elsewhere.
    """
    target = path.resolve()
    _require_replaceable(target, expected)
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        shutil.copymode(target, staged)
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
