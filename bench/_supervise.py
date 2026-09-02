"""Supervised subprocess execution and resumable ledgers for the agent benches.

Extracted from the pain of #282 and #289, where a single bench run is hours of
live agent time and two failure modes made that expensive:

1. **Headless `claude` stalls.** Observed in #282: a run alive for 19 minutes
   having consumed 2 seconds of CPU. `bench/agent_footprint.py` calls
   `subprocess.run` with no timeout, so a stalled run hangs until a human
   notices and kills it.
2. **No resume.** Records append to `records.jsonl` as they complete, so
   finished work survives a crash, but nothing reads that back. Re-running
   after a failure re-does every completed run at full agent cost.

## What "stalled" means here, precisely

A stall is **not** "taking a long time". A model generating a long response, or
a test suite running for eight minutes, are both healthy and must not be
killed. The distinguishing property of the #282 pathology is that the process
was doing *nothing*: no output, no file writes, no CPU.

So a run is declared stalled when, for `stall_after` seconds continuously:

- **no watched path advanced its mtime** (the agent's transcript directory and
  the repo working tree, so any streamed token or any file edit counts as
  progress), **and**
- **the child consumed less than `cpu_epsilon` seconds of CPU** over that same
  window.

Both conditions are required. Either alone produces false kills: a long quiet
`Bash` tool call can leave mtimes untouched while CPU burns, and a process
blocked on a slow network read can tick a log file while using no CPU.

## Why CPU comes from `ps` and not from stdlib

The obvious stdlib answer, `resource.getrusage(RUSAGE_CHILDREN)`, does not work
for this. It only accounts for children that have already been **reaped**, so a
*running* child contributes exactly zero: the CPU signal reads as "idle" for
every live process, which is precisely when it is needed.
`test_cpu_burning_process_is_not_killed` fails against that implementation.

So CPU is read live from `ps -o time= -g <pgid>`, queried by process *group* so
it covers the children `claude` spawns rather than just the parent.

Deeper OS introspection was considered and rejected. `strace` is Linux-only and
has no macOS equivalent that works under SIP; `/proc/<pid>/syscall` and
`wchan` (Linux) would name the exact blocking syscall. None of it discriminates,
because a *healthy* agent waiting on the API is also blocked in a socket read.
"Blocked on I/O" is the normal state for most of a run. The question is whether
that I/O will ever complete, which is not visible in the syscall and is only
observable as absence of progress over time.

archy:owns        Ledger, StallTimeout, SupervisedResult, WallTimeout, run_supervised,
                  with_retries
archy:mirrored-by Ledger -> bench.contract_decay, bench.contract_prevalence,
                  bench.greenfield_run, bench.q1b_run,
                  run_supervised -> bench.greenfield_run, bench.q1b_run,
                  with_retries -> bench.greenfield_run, bench.q1b_run
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# Defaults tuned against the #282 observation (2s CPU over 19 minutes) with
# generous headroom so healthy-but-slow work is never killed. A legitimate long
# tool call (a full test suite) can exceed 5 minutes of quiet, so the window is
# deliberately wider than any tool call seen in the prior benches.
DEFAULT_STALL_AFTER = 600.0  # 10 min with no mtime advance AND no CPU
DEFAULT_MAX_WALL = 3600.0  # hard ceiling regardless of liveness
DEFAULT_POLL = 5.0
DEFAULT_CPU_EPSILON = 2.0  # CPU-seconds within the window that still counts as idle


class StallTimeout(RuntimeError):
    """The child made no observable progress and burned no CPU. Killed."""


class WallTimeout(RuntimeError):
    """The child exceeded the hard wall-clock ceiling while still alive."""


class SupervisedResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    returncode: int
    stdout: str
    stderr: str
    wall_seconds: float
    cpu_seconds: float

    @property
    def cpu_ratio(self) -> float:
        """Worth recording even on runs that finish: a run whose CPU is a tiny
        fraction of its wall time was degraded, which is the pattern that
        justified this module existing. Kept as a property so `model_dump()`
        excludes it."""
        return self.cpu_seconds / self.wall_seconds if self.wall_seconds else 0.0


def _newest_mtime(paths: list[Path]) -> float:
    """Most recent mtime under any watched path, 0.0 if none exist yet.

    Walks directories because the agent transcript is a file *inside* a
    directory whose name is not known until the session starts, and because a
    repo edit lands at an arbitrary depth.
    """
    newest = 0.0
    for p in paths:
        if not p.exists():
            continue
        if p.is_file():
            newest = max(newest, p.stat().st_mtime)
            continue
        for root, dirs, files in os.walk(p):
            # .git churns on its own during checkouts and would mask a stall.
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                try:
                    newest = max(newest, (Path(root) / name).stat().st_mtime)
                except OSError:
                    continue  # file vanished mid-walk; not a progress signal
    return newest


def _parse_ps_time(raw: str) -> float:
    """Parse `ps -o time=` output into seconds.

    Formats differ by platform and neither is documented as stable:
    macOS/BSD emits `M:SS.ff`, GNU/Linux emits `HH:MM:SS`. Parse right-to-left
    so both fall out of the same code.
    """
    parts = raw.strip().split(":")
    if not parts or not parts[0]:
        return 0.0
    try:
        seconds = float(parts[-1])
        for i, chunk in enumerate(reversed(parts[:-1]), start=1):
            seconds += float(chunk) * (60**i)
    except ValueError:
        return 0.0
    return seconds


def _group_cpu_seconds(pgid: int) -> float:
    """Live cumulative CPU for every process in `pgid`.

    `resource.getrusage(RUSAGE_CHILDREN)` cannot do this: it only accounts for
    children that have been *reaped*, so a running child contributes exactly
    zero and the CPU signal is dead precisely when it is needed. Verified by
    `test_cpu_burning_process_is_not_killed`, which fails against the rusage
    implementation.

    `ps` reads live state and, queried by process *group*, covers the children
    `claude` spawns rather than just the parent. Returns 0.0 on any failure,
    which degrades to relying on the mtime signal alone rather than crashing a
    multi-hour bench over a diagnostic.
    """
    try:
        proc = subprocess.run(
            ["ps", "-o", "time=", "-g", str(pgid)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if proc.returncode != 0:
        return 0.0
    return sum(_parse_ps_time(line) for line in proc.stdout.splitlines() if line.strip())


def _kill_tree(proc: subprocess.Popen) -> None:
    """SIGTERM then SIGKILL the child's whole process group.

    The group matters: `claude` spawns children, and killing only the parent
    leaves them holding the terminal and the API connection.
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=15)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait(timeout=15)


def run_supervised(
    cmd: list[str],
    *,
    cwd: Path,
    progress_paths: list[Path],
    stall_after: float = DEFAULT_STALL_AFTER,
    max_wall: float = DEFAULT_MAX_WALL,
    poll: float = DEFAULT_POLL,
    cpu_epsilon: float = DEFAULT_CPU_EPSILON,
    env: dict[str, str] | None = None,
) -> SupervisedResult:
    """Run `cmd`, killing it if it stalls or exceeds the wall ceiling.

    CPU is scoped to the child's own process group, so concurrent supervised
    runs do not contaminate each other's readings. The benches still run one
    child at a time for unrelated reasons (a shared repo working tree).

    Raises StallTimeout or WallTimeout after killing the process group. The
    caller decides whether to retry; see `with_retries`.
    """
    start = time.monotonic()

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        env=env,
        # Own process group so _kill_tree can take the children with it.
        start_new_session=True,
    )

    pgid = os.getpgid(proc.pid)
    cpu_start = _group_cpu_seconds(pgid)
    # Track the peak seen while the group is ALIVE. Reading CPU after the child
    # exits returns nothing (the group is gone) and would report 0.0 for every
    # completed run, which is what the first live smoke test did.
    cpu_peak = cpu_start
    last_progress_at = time.monotonic()
    last_mtime = _newest_mtime(progress_paths)
    cpu_at_last_progress = cpu_start

    while True:
        try:
            proc.wait(timeout=poll)
            break  # exited on its own
        except subprocess.TimeoutExpired:
            pass

        now = time.monotonic()
        if now - start > max_wall:
            _kill_tree(proc)
            raise WallTimeout(f"exceeded max_wall={max_wall}s (still alive)")

        mtime = _newest_mtime(progress_paths)
        cpu_now = _group_cpu_seconds(pgid)
        cpu_peak = max(cpu_peak, cpu_now)
        made_progress = mtime > last_mtime or (cpu_now - cpu_at_last_progress) > cpu_epsilon
        if made_progress:
            last_mtime = max(mtime, last_mtime)
            last_progress_at = now
            cpu_at_last_progress = cpu_now
            continue

        if now - last_progress_at > stall_after:
            _kill_tree(proc)
            raise StallTimeout(
                f"no mtime advance and <{cpu_epsilon}s CPU for {stall_after}s "
                f"(alive {now - start:.0f}s)"
            )

    stdout, stderr = proc.communicate()
    wall = time.monotonic() - start
    return SupervisedResult(
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
        wall_seconds=wall,
        cpu_seconds=max(0.0, cpu_peak - cpu_start),
    )


def with_retries(fn, *, attempts: int = 3, on_error=None):
    """Call `fn()`, retrying on StallTimeout/WallTimeout up to `attempts`.

    Deliberately does NOT retry other exceptions. A non-zero exit from the
    agent, or a parse failure, is a real result and re-rolling it would quietly
    select for runs that happened to succeed, biasing the sample.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except (StallTimeout, WallTimeout) as exc:
            last = exc
            if on_error:
                on_error(i + 1, exc)
    raise last if last else RuntimeError("unreachable")


# --------------------------------------------------------------------------
# Resumable ledger
# --------------------------------------------------------------------------


@dataclass
class Ledger:
    """Append-only JSONL record of completed units of work, keyed for resume.

    A bench run is hours of paid agent time. Re-running after any failure must
    skip what already completed, so every unit gets a stable key and the ledger
    is consulted before spending anything.

    Append-only, and each row is written with its newline in a single call, the
    same discipline as `src/archy/history.py`: two writes leave a window where a
    crash lands a row with no trailing newline, which silently merges the next
    row into it and loses both.
    """

    path: Path
    _done: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.path.exists():
            return
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A torn final row from a hard kill. Skip it; the unit will be
                # re-run, which is the safe direction.
                continue
            if row.get("status") == "ok" and "key" in row:
                self._done[row["key"]] = row

    def is_done(self, key: str) -> bool:
        return key in self._done

    def get(self, key: str) -> dict | None:
        return self._done.get(key)

    def _ends_without_newline(self) -> bool:
        """Did a previous process die mid-write, leaving a partial last line?"""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return False
        with self.path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"

    def record(self, key: str, payload: dict, *, status: str = "ok") -> None:
        row = {"key": key, "status": status, **payload}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # HEAL A TORN TAIL BEFORE APPENDING. Reading already tolerates a partial
        # last line, but appending onto one concatenates the two and destroys
        # THIS row as well as the torn one, so a single hard kill cost two units
        # instead of the one it should. Found by the resume test, 2026-07-26.
        prefix = "\n" if self._ends_without_newline() else ""
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + json.dumps(row, sort_keys=True) + "\n")
        if status == "ok":
            self._done[key] = row

    @property
    def completed(self) -> int:
        return len(self._done)
