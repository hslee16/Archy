"""Cross-platform end-to-end smoke test for a freshly installed `archy`.

Run after `pip install` has placed `archy` on PATH. Creates a tiny fixture
project in a temp directory, invokes `archy score` against it, and asserts
that the command exited cleanly and produced a recognisable score line.

This is intentionally minimal: the goal is to catch packaging, console-script,
and runtime-import regressions on every supported (OS, Python) combination,
not to re-test scoring logic (which is covered by the unit-test suite).

Used by `.github/workflows/smoke.yml`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _make_fixture(root: Path) -> Path:
    pkg = root / "sample"
    (pkg / "foo").mkdir(parents=True)
    (pkg / "bar").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "foo" / "__init__.py").write_text("from sample.bar import greet\n")
    (pkg / "bar" / "__init__.py").write_text('def greet():\n    return "hi"\n')
    return pkg


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    archy_path = shutil.which("archy")
    if archy_path is None:
        print("FAIL: `archy` not on PATH after install", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pkg = _make_fixture(root)

        version_proc = _run("archy", "--version", cwd=root)
        if version_proc.returncode != 0:
            print(f"FAIL: `archy --version` exit={version_proc.returncode}", file=sys.stderr)
            print(version_proc.stdout, version_proc.stderr, file=sys.stderr)
            return 1
        print(f"OK   archy on PATH: {archy_path}")
        print(f"OK   {version_proc.stdout.strip()}")

        score_proc = _run("archy", "score", str(pkg), cwd=root)
        if score_proc.returncode != 0:
            print(f"FAIL: `archy score` exit={score_proc.returncode}", file=sys.stderr)
            print(score_proc.stdout, score_proc.stderr, file=sys.stderr)
            return 1
        if "archy score:" not in score_proc.stdout:
            print("FAIL: `archy score` output missing 'archy score:' line", file=sys.stderr)
            print(score_proc.stdout, file=sys.stderr)
            return 1
        print("OK   archy score completed with recognisable output")

        cycles_proc = _run("archy", "cycles", str(pkg), cwd=root)
        if cycles_proc.returncode != 0:
            print(f"FAIL: `archy cycles` exit={cycles_proc.returncode}", file=sys.stderr)
            print(cycles_proc.stdout, cycles_proc.stderr, file=sys.stderr)
            return 1
        print("OK   archy cycles completed")

    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
