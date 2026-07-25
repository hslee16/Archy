"""Tests for the supervised runner and the resumable ledger.

These matter more than most bench tests: the code under test exists to kill
long-running agent processes, and a false kill costs a paid run while a missed
kill costs a human noticing an hour later. Both directions are tested with real
subprocesses rather than mocks, because the failure being guarded against
(#282's 19-minutes-at-2s-CPU) is a property of real process behavior.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module
# names cannot shadow stdlib/installed imports, mirroring tests/test_agent_footprint.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from _supervise import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    Ledger,
    StallTimeout,
    WallTimeout,
    run_supervised,
    with_retries,
)

PY = sys.executable


def test_normal_process_completes_and_reports_cpu(tmp_path: Path):
    res = run_supervised(
        [PY, "-c", "print('done')"],
        cwd=tmp_path,
        progress_paths=[tmp_path],
        stall_after=30,
        poll=0.2,
    )
    assert res.returncode == 0
    assert "done" in res.stdout
    assert res.wall_seconds > 0


def test_stalled_process_is_killed(tmp_path: Path):
    """The #282 pathology: alive, silent, no CPU, no file writes."""
    start = time.monotonic()
    with pytest.raises(StallTimeout):
        run_supervised(
            [PY, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            progress_paths=[tmp_path],
            stall_after=1.5,
            poll=0.2,
            cpu_epsilon=2.0,
        )
    # Killed promptly rather than run to completion.
    assert time.monotonic() - start < 20


def test_process_writing_files_is_not_killed(tmp_path: Path):
    """A slow-but-working run must survive: mtime advances count as progress.

    This is the false-kill direction, and it is the one that costs paid agent
    time when it regresses.
    """
    watch = tmp_path / "work"
    watch.mkdir()
    script = (
        "import time, pathlib\n"
        "d = pathlib.Path('work')\n"
        "for i in range(8):\n"
        "    (d / f'f{i}').write_text(str(i))\n"
        "    time.sleep(0.3)\n"
        "print('ok')\n"
    )
    res = run_supervised(
        [PY, "-c", script],
        cwd=tmp_path,
        progress_paths=[watch],
        stall_after=1.0,  # shorter than total runtime; only mtime saves it
        poll=0.2,
    )
    assert res.returncode == 0
    assert "ok" in res.stdout


def test_cpu_burning_process_is_not_killed(tmp_path: Path):
    """Quiet but computing (a long tool call) must survive on the CPU signal."""
    script = "x = 0\nfor i in range(40_000_000):\n    x += i\nprint(x)\n"
    res = run_supervised(
        [PY, "-c", script],
        cwd=tmp_path,
        progress_paths=[tmp_path / "nonexistent"],
        stall_after=1.0,
        poll=0.2,
        cpu_epsilon=0.05,
    )
    assert res.returncode == 0


def test_completed_run_reports_nonzero_cpu(tmp_path: Path):
    """Regression: `cpu_seconds` must survive the child exiting.

    Caught by a live smoke run against `claude -p`, not by the tests above. The
    first implementation read CPU once more *after* `proc.wait()` returned, by
    which point the process group no longer exists and `ps` reports nothing, so
    every completed run logged 0.0s CPU and a `cpu_ratio` of 0.00. The value is
    now the peak observed while the group was alive.
    """
    script = "x = 0\nfor i in range(30_000_000):\n    x += i\nprint(x)\n"
    res = run_supervised(
        [PY, "-c", script],
        cwd=tmp_path,
        progress_paths=[tmp_path],
        stall_after=60,
        poll=0.2,
    )
    assert res.returncode == 0
    assert res.cpu_seconds > 0.0, "CPU must be sampled while the child is alive"
    assert res.cpu_ratio > 0.0


def test_wall_ceiling_kills_even_a_busy_process(tmp_path: Path):
    script = "import time\nwhile True:\n    _ = sum(range(10000))\n"
    with pytest.raises(WallTimeout):
        run_supervised(
            [PY, "-c", script],
            cwd=tmp_path,
            progress_paths=[tmp_path],
            stall_after=100,
            max_wall=1.5,
            poll=0.2,
        )


def test_retries_only_on_stall_not_on_real_failure(tmp_path: Path):
    calls = {"n": 0}

    def stalls_then_succeeds():
        calls["n"] += 1
        if calls["n"] < 3:
            raise StallTimeout("simulated")
        return "ok"

    assert with_retries(stalls_then_succeeds, attempts=3) == "ok"
    assert calls["n"] == 3

    # A genuine failure must propagate on the first attempt. Retrying it would
    # silently select for runs that happened to succeed and bias the sample.
    def real_failure():
        calls["n"] += 1
        raise RuntimeError("agent exited 1")

    calls["n"] = 0
    with pytest.raises(RuntimeError, match="agent exited 1"):
        with_retries(real_failure, attempts=3)
    assert calls["n"] == 1


def test_retries_exhaust_and_reraise(tmp_path: Path):
    seen: list[int] = []

    def always_stalls():
        raise StallTimeout("nope")

    with pytest.raises(StallTimeout):
        with_retries(always_stalls, attempts=2, on_error=lambda i, e: seen.append(i))
    assert seen == [1, 2]


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------


def test_ledger_roundtrip_and_resume(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    assert not led.is_done("task1:B")

    led.record("task1:B", {"bad_diff": True})
    assert led.is_done("task1:B")
    assert led.get("task1:B")["bad_diff"] is True

    # A fresh Ledger over the same file is what a resumed run constructs.
    reopened = Ledger(path)
    assert reopened.is_done("task1:B")
    assert reopened.completed == 1
    assert not reopened.is_done("task2:B")


def test_ledger_ignores_failed_rows_so_they_rerun(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    led.record("task1:B", {"error": "stalled"}, status="failed")
    assert not led.is_done("task1:B")
    assert Ledger(path).is_done("task1:B") is False


def test_ledger_survives_a_torn_final_row(tmp_path: Path):
    """A hard kill mid-write must not poison the whole ledger."""
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    led.record("task1:B", {"ok": 1})
    led.record("task2:B", {"ok": 1})
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"key": "task3:B", "status": "o')  # torn, no newline

    reopened = Ledger(path)
    assert reopened.is_done("task1:B")
    assert reopened.is_done("task2:B")
    # The torn unit is simply re-run, which is the safe direction.
    assert not reopened.is_done("task3:B")
