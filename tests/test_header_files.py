"""`--emit-headers --write` changes the docstring and nothing else (#783)."""

from __future__ import annotations

import ast
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from archy.cli import main
from archy.header_files import read_source, rewritten, write_atomic
from archy.mcp_runs import run_conventions

FUNCTION = b"def public_fn():\n    return 1\n"
LATIN = b'# -*- coding: latin-1 -*-\n"""Caf\xe9."""\n\n' + FUNCTION


def _project(root: Path, files: dict[str, bytes]) -> Path:
    (root / "pkg").mkdir()
    for name, data in {"__init__.py": b"", **files}.items():
        (root / "pkg" / name).write_bytes(data)
    return root


def _emit(root: Path, flag: str) -> Result:
    result = CliRunner().invoke(main, ["conventions", str(root), "--emit-headers", flag])
    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    return result


def _code(data: bytes) -> str:
    tree = ast.parse(data)
    body = tree.body[1:] if ast.get_docstring(tree) is not None else tree.body
    return ast.dump(ast.Module(body=body, type_ignores=[]))


@pytest.mark.parametrize(
    "original",
    [
        b'\xef\xbb\xbf"""Bom doc."""\n\n' + FUNCTION,
        LATIN,
        b'"""Crlf doc."""\r\n\r\ndef public_fn():\r\n    return 1\r\n',
        b"import os\n\n" + FUNCTION,
        b"import os\r\n\r\ndef public_fn():\r\n    return 1\r\n",
    ],
    ids=["bom", "latin-1", "crlf", "no-docstring", "crlf-no-docstring"],
)
def test_write_keeps_encoding_endings_and_code_and_converges(tmp_path: Path, original: bytes):
    root = _project(tmp_path, {"a.py": original})
    target = root / "pkg" / "a.py"
    ast.parse(original)  # the fixture is valid Python before anything is done to it

    assert _emit(root, "--write").exit_code == 0
    once = target.read_bytes()

    assert b"archy:owns" in once
    assert once.startswith(b"\xef\xbb\xbf") == original.startswith(b"\xef\xbb\xbf")
    assert once.count(b"\xe9") == original.count(b"\xe9")
    assert _code(once) == _code(original)
    # One kind of line ending, the one the file already had.
    if b"\r\n" in original:
        assert once.count(b"\n") == once.count(b"\r\n")
    else:
        assert b"\r" not in once
    assert "wrote 0 header(s)" in _emit(root, "--write").output
    assert target.read_bytes() == once
    assert _emit(root, "--check").exit_code == 0


@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        (b'"""Mixed."""\r\n\ndef public_fn():\n    return 1\n', "mixed line endings"),
        (b'# -*- coding: ascii -*-\n"""Caf\xe9."""\n' + FUNCTION, "pkg.b"),
    ],
    ids=["mixed-endings", "bytes-not-matching-cookie"],
)
def test_a_file_that_cannot_be_rewritten_is_named_and_left_alone(
    tmp_path: Path, broken: bytes, reason: str
):
    root = _project(tmp_path, {"a.py": FUNCTION, "b.py": broken, "c.py": FUNCTION})

    result = _emit(root, "--write")

    assert result.exit_code == 1
    assert reason in result.output
    assert (root / "pkg" / "b.py").read_bytes() == broken
    for neighbour in ("a.py", "c.py"):
        assert b"archy:owns" in (root / "pkg" / neighbour).read_bytes()


def test_check_names_an_undecodable_module_instead_of_calling_the_tree_clean(tmp_path: Path):
    root = _project(tmp_path, {"b.py": b'# -*- coding: ascii -*-\n"""Caf\xe9."""\n' + FUNCTION})

    result = _emit(root, "--check")

    assert result.exit_code == 1
    assert "pkg.b" in result.output and "match the tree" not in result.output


def test_a_block_that_would_change_the_code_is_refused(tmp_path: Path):
    path = tmp_path / "a.py"
    path.write_bytes(b'"""Doc."""\n' + FUNCTION)

    with pytest.raises(ValueError, match=r"change the module's code|does not parse"):
        rewritten(read_source(path), 'archy:owns        x\n"""\nimport os\n"""')


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes and symlinks")
def test_write_atomic_goes_through_a_symlink_and_keeps_the_mode(tmp_path: Path):
    real = tmp_path / "real.py"
    real.write_bytes(b"old\n")
    real.chmod(0o755)
    link = tmp_path / "a.py"
    link.symlink_to(real)

    write_atomic(link, b"new\n", expected=b"old\n")

    assert link.is_symlink()
    assert real.read_bytes() == b"new\n"
    assert real.stat().st_mode & 0o777 == 0o755
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.py", "real.py"]


def test_option_text_never_reaches_a_header_or_a_payload(tmp_path: Path):
    cli = (
        b'"""CLI."""\nimport sys\n\nimport click\n\n\n@click.command()\n'
        b'@click.option("--quiet \\"\\"\\"\\nprint(1)\\n\\"\\"\\"", "verbose")\n'
        b"def main(verbose):\n    if verbose:\n        sys.exit(1)\n"
    )
    root = _project(tmp_path, {"tool.py": cli})

    controls = [
        g["control"]
        for g in run_conventions(root, min_family=3, include_tests=False).model_dump()["gates"]
    ]
    result = _emit(root, "--write")

    assert controls == ["param:verbose"]
    assert result.exit_code == 0, result.output
    written = (root / "pkg" / "tool.py").read_bytes()
    assert b"(param:verbose)" in written
    assert _code(written) == _code(cli)


@pytest.mark.parametrize(
    ("original", "reason"),
    [
        (b'# -*- coding: cp932 -*-\n"""Doc."""\nX = "\x87\x90"\n', "round-trip"),
        (b'"""Doc."""\nX = ' + b" + ".join([b"1"] * 1500) + b"\n", "too deeply nested"),
        (b"# -*- coding: rot13 -*-\nkey = 1\n", "cannot decode"),
    ],
    ids=["lossy-codec", "deep-expression", "not-a-text-codec"],
)
def test_a_file_archy_cannot_verify_is_left_alone(tmp_path: Path, original: bytes, reason: str):
    path = tmp_path / "a.py"
    path.write_bytes(original)

    with pytest.raises(ValueError, match=reason):
        rewritten(read_source(path), "archy:owns        key")


def test_a_current_mixed_endings_file_is_not_an_error(tmp_path: Path):
    path = tmp_path / "a.py"
    path.write_bytes(b'"""archy:owns        public_fn\n"""\r\n' + FUNCTION)

    assert rewritten(read_source(path), "archy:owns        public_fn") is None


def test_an_edit_made_during_the_run_is_not_overwritten(tmp_path: Path):
    path = tmp_path / "a.py"
    path.write_bytes(b"USER_EDIT = 1\n")

    with pytest.raises(ValueError, match="changed while archy was running"):
        write_atomic(path, b"new\n", expected=b"old\n")

    assert path.read_bytes() == b"USER_EDIT = 1\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and links")
@pytest.mark.parametrize(
    ("protect", "reason"),
    [
        (lambda p: p.chmod(0o444), "not writable"),
        (lambda p: os.link(p, p.with_name("other")), "hard links"),
    ],
    ids=["read-only", "hard-linked"],
)
def test_a_protected_file_is_not_replaced(
    tmp_path: Path, protect: Callable[[Path], object], reason: str
):
    if os.geteuid() == 0:
        pytest.skip("root may write anything")
    path = tmp_path / "a.py"
    path.write_bytes(b"old\n")
    protect(path)

    with pytest.raises(ValueError, match=reason):
        write_atomic(path, b"new\n", expected=b"old\n")

    assert path.read_bytes() == b"old\n"


def test_a_failed_replace_reports_the_module_and_leaves_no_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _project(tmp_path, {"a.py": FUNCTION})

    def refuse(source: str, target: str) -> None:
        raise OSError("disk says no")

    monkeypatch.setattr(os, "replace", refuse)
    result = _emit(root, "--write")

    assert result.exit_code == 1
    assert "pkg.a: disk says no" in result.output
    assert (root / "pkg" / "a.py").read_bytes() == FUNCTION
    assert sorted(p.name for p in (root / "pkg").iterdir()) == ["__init__.py", "a.py"]


@pytest.mark.parametrize("flag", ["--on/--no-on", "--on / --no-on", "--on/-n"])
def test_slashed_flags_are_still_named(tmp_path: Path, flag: str):
    cli = (
        '"""CLI."""\nimport sys\n\nimport click\n\n\n@click.command()\n'
        f'@click.option("{flag}", "on")\ndef main(on):\n    if on:\n        sys.exit(1)\n'
    )
    root = _project(tmp_path, {"tool.py": cli.encode()})

    gates = run_conventions(root, min_family=3, include_tests=False).model_dump()["gates"]

    assert [g["control"] for g in gates] == [f"flag:{flag}"]
