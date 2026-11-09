"""A reader that closes early must not read as a failed gate.

`archy graph SRC --format json | head -1` used to exit 1 with an empty
stderr, because Click's standalone_mode turns the EPIPE into a plain exit 1.
Exit 1 is a finding code for `check`, `cycles --strict` and `diff --strict`,
so a shell pipeline reported a problem that archy never found. The handler in
`_ArchyGroup.invoke` redirects stdout to devnull and exits 141, the status a
process dies with by SIGPIPE.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

SIGPIPE_EXIT = 128 + signal.SIGPIPE


def _large_project(root: Path) -> Path:
    """Enough modules that a full JSON graph exceeds a pipe buffer."""
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(900):
        (pkg / f"m{i}.py").write_text(f"X{i} = {i}\n")
    return root


def _archy() -> str:
    return str(Path(sys.executable).parent / "archy")


def test_piping_a_large_graph_into_a_reader_that_closes_early(tmp_path: Path):
    project = _large_project(tmp_path)
    archy = _archy()

    full = subprocess.run(
        [archy, "graph", str(project), "--format", "json"],
        capture_output=True,
        check=False,
    )
    assert full.returncode == 0, full.stderr
    # A small output fits the pipe buffer, so head never closes early. Assert
    # the fixture really is large before asserting anything about the status.
    assert len(full.stdout) > 64 * 1024

    # POSIX shell: close the read end after the first line, then report archy's
    # status through PIPESTATUS. zsh has no PIPESTATUS, so use bash -c.
    script = (
        f'"{archy}" graph "{project}" --format json 2>"{tmp_path}/stderr" '
        "| head -1 > /dev/null; echo ${PIPESTATUS[0]}"
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(SIGPIPE_EXIT), proc.stdout
    assert (tmp_path / "stderr").read_bytes() == b""


def test_full_output_and_finding_codes_are_unchanged(tmp_path: Path):
    project = _large_project(tmp_path)
    archy = _archy()

    graph = subprocess.run(
        [archy, "graph", str(project), "--format", "json"],
        capture_output=True,
        check=False,
    )
    assert graph.returncode == 0
    assert b'"nodes"' in graph.stdout

    # `check` still uses 1 for a real finding; the pipe handler must not touch it.
    check = subprocess.run(
        [archy, "check", str(project)],
        capture_output=True,
        check=False,
    )
    assert check.returncode == 1
    assert b"Traceback" not in check.stdout + check.stderr
