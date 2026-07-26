#!/usr/bin/env python
"""Generate Conduit backends under the paper's architecture constraint (#369).

    uv run python bench/greenfield_run.py --dry-run
    uv run python bench/greenfield_run.py --limit 1 --arms A       # smoke, 1 run
    uv run python bench/greenfield_run.py --limit 5                # a 10-run chunk
    uv run python bench/greenfield_run.py                          # the pre-registered N
    uv run python bench/greenfield_run.py --progress               # safe at any time
    uv run python bench/greenfield_run.py --report                 # only once N is reached

THE GENERATION HALF. The measurement half is `bench/greenfield_eval.py` and the
thresholds are `bench/greenfield_prereg.py`, both of which exist and are
validated first, per `CLAUDE.md`: every harness bug found during a paid run is a
run wasted. Running the evaluator against a live server found three defects that
would each have invalidated the whole batch (PR #376), which is the argument for
this ordering, not a hypothetical.

## The two arms

**Arm A** gets the task and the constraint block. Nothing else. This is the
paper's own condition: a static prompt describing the architecture, with no
course-correction during generation.

**Arm B** gets the identical prompt plus an `archy.yaml` transcribed from that
same constraint block, plus a requirement to run the checker and reach exit 0
before declaring done.

Everything else is held: same model, same wall clock, same tool allowlist apart
from the checker, same harness contract, same task text.

## Why arm B is checked through a wrapper rather than bare `archy check`

The paper's verifier looks for a DIRECTORY; archy matches a DOTTED NAME, so a
tree nested under a package (`app/routes/...`) does not match `routes.**` and
reads as zero layers present. `greenfield_eval` compensates by discovering the
real prefixes, but the archy CLI cannot express that (#377).

Left alone, arm B's gate would report FAIL on a tree the scorer calls compliant.
The agent would then either thrash against an unsatisfiable gate or flatten its
package layout to satisfy it, and THAT WOULD LOOK LIKE AN ARCHY EFFECT while
being an artifact of a pattern limitation. So the wrapper runs real archy over a
config expanded for the tree in front of it, and gate and scorer agree by
construction. The agent still sees archy's own output: named violations, layers
present, exit code.

## What the prompt is, and is not

The constraint block is transcribed verbatim from the paper (section 3.2,
Appendix E.2), the same text `bench/fixtures/conduit_clean/archy.yaml` was
written from. **The surrounding task text is this harness's own**, because the
paper's full Appendix E.3 prompt is not published; it states the API contract
and the runnability requirements the serve step needs. It is byte-identical
across arms, so it cannot produce a difference between them, but it does mean
absolute rates are not comparable to the paper's table. That limit is already
recorded in the prereg.

## Serving, and the distinction the prereg requires

The prereg demands that two things stop being merged:

- the generated server did not come up -> **the generation's failure**, and a
  behavioral zero. Scoring it unevaluable would drop exactly the runs that
  failed worst and flatter both arms.
- docker missing, port taken, the harness fell over -> **unevaluable**, dropped
  from the denominator and counted in the write-up.

`greenfield_eval.behavioral_verdict` cannot tell these apart on its own (it sees
only "nothing listening"), so the runner classifies before calling it.

## Resumability, which is a rule here and not a nicety

Copied from `bench/q1b_run.py`, whose shape `tests/test_q1b_run.py` pins and
whose every rule was paid for: one ledger row per completed unit written in a
single append, only `status="ok"` counts as done, stalls retried and results
never, one crashing unit does not end the loop, a usage limit is not a result,
interrupts exit cleanly saying how to resume.

Two failure modes #356 did not have, both handled per task: a generated server
that will not start, and a port or process leaked into the next task.

## The batch sweeps until it is done, rather than once

A single pass is not enough for a run measured in hours. The failures are
ordinary and mostly transient (a stalled CLI, a port that lost a race, a suite
timeout), so the batch re-sweeps, and each pass skips what is already `ok` and
retries what is not. That is only safe BECAUSE the ledger checkpoints per unit;
the loop is the payoff for the checkpointing, not a substitute for it.

Three ways it stops, and all three are needed or it either spins or quits early:

- **nothing pending**: complete.
- **a wipeout**: two or more units were tried in a pass and not one advanced.
  That points at the environment, not at any single generation, and retrying to
  the per-unit cap would spend `pending * max_attempts` agent runs reproducing
  it. Stops so a human can read the rows and fix.
- **per-unit attempt cap** (`--max-attempts`): one poisoned unit cannot hold the
  batch open, and is NOT treated as a wipeout, because a single stubborn unit is
  not evidence about the environment. It is recorded, reported, and **dropped,
  never scored**, because an unmeasurable run counted clean biases the rate by
  exactly the runs that broke.

`--passes` bounds the whole thing. Re-running always resumes, so an operator
who fixes a bug mid-batch just starts it again and keeps everything already
recorded.

## Running it in chunks is fine; scoring it in chunks is not

`--limit 5` runs 10 units, `--limit 10` then runs the next 10, and so on to the
pre-registered N. The unit ids are stable, so raising `--limit` only ever ADDS
work, and the arms are interleaved within every chunk. That interleaving is what
makes chunking safe: running all of arm A on Monday and all of arm B on
Wednesday would put any server-side model change entirely on one arm, where it
would read as an effect.

**Scoring between chunks is optional stopping**, which is what the fixed N in
the prereg exists to prevent: a partial batch that happens to look good is how a
null becomes a finding. So `--report` refuses until the pre-registered N is
complete, and `--progress` exists to watch the batch WITHOUT showing outcomes.
`--limit` above the pre-registered N is refused for the same reason; the
pre-registered way to get more data is EXPAND, not a bigger N here.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.append(str(REPO_ROOT / "bench"))
import greenfield_eval  # noqa: E402
import greenfield_prereg  # noqa: E402
from _supervise import Ledger, run_supervised, with_retries  # noqa: E402
from q1b_run import (  # noqa: E402
    RateLimited,
    agent_env,
    hit_rate_limit,
    parse_reset_at,
    project_slug,
)

RUNS = REPO_ROOT / "bench/cache/greenfield_runs"
TRANSCRIPTS = REPO_ROOT / "bench/greenfield_transcripts"
LEDGER_PATH = REPO_ROOT / "bench/greenfield_results.jsonl"
CONDUIT_CONFIG = REPO_ROOT / "bench/fixtures/conduit_clean/archy.yaml"

# The checker arm B runs. Deliberately this repo's archy and NOT `uvx archy`:
# `min_layers_present` is half the paper's verifier and is newer than the last
# release, so uvx would hand arm B a checker that silently tests one of the two
# checks. An arm B gated on half the definition would produce a clean-looking
# null.
ARCHY_BIN = REPO_ROOT / ".venv/bin/archy"

ALLOWED_TOOLS = ("Read", "Write", "Edit", "Bash", "Grep", "Glob")

#: Arm B stops after this many checker cycles; pre-registered, and exceeding it
#: is recorded on the row rather than retried, because re-rolling a result
#: selects for the runs that happened to converge.
MAX_CORRECTION_ITERATIONS = 10

#: The pre-registered N per arm. Read from the prereg rather than restated, so
#: the runner cannot drift from the document that fixes it.
TARGET_N_PER_ARM = greenfield_prereg.N_PER_ARM

# Verbatim from the paper (section 3.2 / Appendix E.2). This is the ONLY part of
# the prompt taken from the paper, and it is the part both arms share.
CONSTRAINT_BLOCK = """\
Split the codebase into four domain layers with strict top-down dependency \
direction: routes/handlers, services/use cases, models/entities, and \
repository/data access. Each layer must reside in its own directory."""

# This harness's own, and IDENTICAL IN BOTH ARMS. It exists so the serve step
# has a contract to rely on; it says nothing about structure.
HARNESS_CONTRACT = """\
Build a RealWorld "Conduit" backend API in Python using FastAPI, in the current
directory.

Implement the RealWorld API specification: user registration and login with JWT
authentication, user profiles with follow and unfollow, CRUD for articles with
slugs and tags, favouriting articles, comments on articles, an article feed with
pagination and filtering, and a tags endpoint. All endpoints are served under
the /api prefix, exactly as the RealWorld spec defines them.

Requirements this project is built and tested with:

- Use SQLite for storage. No external database or service may be required.
- Write `requirements.txt` listing every dependency needed to run the server.
- Write `run.sh` which starts the API server listening on the port given by the
  PORT environment variable, on 0.0.0.0. It may assume its dependencies are
  already installed and that it is run from the project root.
- The server must create its schema on startup if it does not exist.

%s"""

ARM_B_ADDENDUM = """\

An architecture checker is available at:

    %s

`archy.yaml` in this directory declares the four layers and the forbidden
dependency directions. Before you declare the task done, run the checker and fix
what it reports, until it exits 0:

    %s

Do not edit or delete archy.yaml. It is the specification you are being held to.
"""


def _free_port() -> int:
    """A port the OS says is free right now.

    Fixed ports are how one leaked server poisons the next task: the suite
    connects to the previous run's process and scores this run's code from it.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def expanded_config(tree: Path) -> dict:
    """The Conduit layer config, with aliases expanded for this tree's prefixes.

    So arm B's gate and the scorer cannot disagree; see the module docstring.
    """
    base = greenfield_eval.load_config(CONDUIT_CONFIG)
    graph = greenfield_eval.build_graph(
        tree, ignored_dirs=greenfield_eval.DEFAULT_IGNORED_DIRS | set(base.exclude)
    )
    config = greenfield_eval.nesting_tolerant_config(base, sorted(graph.nodes))
    return {
        "min_layers_present": config.min_layers_present,
        "layers": {layer.name: {"modules": list(layer.patterns)} for layer in config.layers},
        "forbid": [{"from": rule.from_layer, "to": rule.to_layer} for rule in config.forbid],
        "exclude": list(config.exclude),
    }


def check_script(tree: Path) -> Path:
    """Write the wrapper arm B runs, and return its path.

    Regenerates the expanded config on every invocation, because the tree the
    agent is checking is the tree it is still writing: a config expanded once at
    setup would go stale the moment the agent introduced a package.
    """
    script = tree / "check_architecture.sh"
    script.write_text(
        "#!/bin/sh\n"
        "# Runs archy against this project's declared layers.\n"
        f'exec "{sys.executable}" "{Path(__file__).resolve()}" --check-tree "{tree}"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def run_check(tree: Path) -> int:
    """`archy check` over the expanded config. Returns archy's exit code."""
    config_path = tree / ".archy-expanded.yaml"
    config_path.write_text(yaml.safe_dump(expanded_config(tree), sort_keys=False), encoding="utf-8")
    proc = subprocess.run(
        [str(ARCHY_BIN), "check", str(tree), "--config", str(config_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def build_prompt(arm: str, tree: Path) -> str:
    body = HARNESS_CONTRACT % CONSTRAINT_BLOCK
    if arm == "A":
        return body
    return body + ARM_B_ADDENDUM % (ARCHY_BIN, tree / "check_architecture.sh")


def prepare_tree(task_id: str, arm: str) -> Path:
    """A fresh directory per run. Greenfield means greenfield.

    Removed and recreated rather than reused, so a resumed batch cannot score a
    previous attempt's files.
    """
    tree = RUNS / f"{task_id}_{arm}"
    if tree.exists():
        shutil.rmtree(tree)
    tree.mkdir(parents=True)
    if arm == "B":
        shutil.copy(CONDUIT_CONFIG, tree / "archy.yaml")
        check_script(tree)
    return tree


def run_agent(tree: Path, prompt: str, *, model: str, max_wall: float) -> dict:
    """One supervised headless `claude` run in `tree`.

    Same shape as `q1b_run.run_agent`, including that stalls are the only
    retried failure: a non-zero exit is a real outcome and re-rolling it selects
    for runs that happened to succeed.
    """
    transcript_dir = Path.home() / ".claude" / "projects" / project_slug(tree)
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--dangerously-skip-permissions",
        "--allowedTools",
        ",".join(ALLOWED_TOOLS),
        "--setting-sources",
        "local",
    ]

    def once():
        return run_supervised(
            cmd,
            cwd=tree,
            progress_paths=[transcript_dir, tree],
            max_wall=max_wall,
            env=agent_env(),
        )

    def on_stall(attempt: int, exc: Exception) -> None:
        print(f"      stall on attempt {attempt}: {exc}", flush=True)

    result = with_retries(once, attempts=3, on_error=on_stall)
    blob = f"{result.stdout}\n{result.stderr}"
    if hit_rate_limit(result.stdout, result.stderr, result.returncode):
        # A fact about the subscription, not about the agent. Raised so the
        # caller waits and retries the same task, the one retry that cannot
        # select for luck.
        raise RateLimited(blob.strip()[:200], reset_at=parse_reset_at(blob))
    if result.returncode != 0:
        return {
            "ok": False,
            "returncode": result.returncode,
            "stderr": result.stderr[-1000:],
            "stdout": result.stdout[-1000:],
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "returncode": 0, "stderr": "unparseable claude JSON"}
    payload["ok"] = True
    payload["wall_seconds"] = result.wall_seconds
    return payload


def install_deps(tree: Path, timeout: float) -> tuple[bool, str]:
    """Create a venv and install `requirements.txt`, if the agent wrote one.

    A missing or broken requirements.txt is the GENERATION's failure, not the
    harness's: the artifact does not run. It is reported as such so the caller
    scores a behavioral zero rather than dropping the run.
    """
    requirements = tree / "requirements.txt"
    if not requirements.exists():
        return False, "no requirements.txt"
    venv = tree / ".venv"
    for cmd in (
        ["uv", "venv", str(venv)],
        ["uv", "pip", "install", "--python", str(venv / "bin/python"), "-r", str(requirements)],
    ):
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        if proc.returncode != 0:
            return False, f"{cmd[1]} failed: {proc.stderr[-300:]}"
    return True, ""


@contextlib.contextmanager
def served(tree: Path, port: int, *, boot_timeout: float):
    """Start `run.sh` on `port`, yield whether it bound, and always tear down.

    `start_new_session` puts the server in its own process group so teardown
    kills the whole tree. `run.sh` typically execs a reloader or a shell that
    spawns uvicorn, so killing the direct child alone leaks a listener onto the
    port, and the NEXT task then measures this task's server.
    """
    script = tree / "run.sh"
    if not script.exists():
        yield False, "no run.sh"
        return
    env = {
        **{k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "PYTHONHOME"}},
        "PORT": str(port),
        "PATH": f"{tree / '.venv/bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "VIRTUAL_ENV": str(tree / ".venv"),
    }
    log = (tree / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        ["sh", "run.sh"],
        cwd=tree,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    try:
        host = f"http://127.0.0.1:{port}"
        deadline = time.time() + boot_timeout
        bound = False
        while time.time() < deadline:
            if proc.poll() is not None:
                break  # exited on boot; no point waiting out the timeout
            if greenfield_eval._server_responds(host, timeout=2.0):
                bound = True
                break
            time.sleep(1.0)
        yield bound, "" if bound else "run.sh never bound the port"
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        with contextlib.suppress(Exception):
            proc.wait(timeout=30)
        log.close()


def behavioral_row(tree: Path, *, boot_timeout: float, suite_timeout: float) -> dict:
    """Score behaviour, splitting generation failure from harness failure.

    The prereg's rule, and the reason `behavioral_verdict` is not called
    directly: it sees only "nothing listening" and cannot tell a server that
    crashed on boot from a docker problem. The first is a behavioral zero, the
    second is unevaluable, and merging them either drops the worst runs or puts
    the harness into the measurement.
    """
    if shutil.which("docker") is None:
        return {"evaluable": False, "reason": "docker not available", "pass_rate": None}
    installed, why = install_deps(tree, timeout=suite_timeout)
    if not installed:
        return {
            "evaluable": True,
            "pass_rate": 0.0,
            "reason": f"generated project does not install: {why}",
            "server_started": False,
        }
    port = _free_port()
    with served(tree, port, boot_timeout=boot_timeout) as (bound, why):
        if not bound:
            return {
                "evaluable": True,
                "pass_rate": 0.0,
                "reason": f"generated server did not come up: {why}",
                "server_started": False,
            }
        verdict = greenfield_eval.behavioral_verdict(
            f"http://127.0.0.1:{port}", timeout=suite_timeout
        )
    return {**verdict.model_dump(), "server_started": True}


def count_archy_invocations(transcript: Path) -> int:
    """How often the agent actually ran the checker, from its own transcript.

    An arm B that never invoked it is arm A with extra steps, and would produce
    a null that looks like evidence. This must be visible per run rather than
    assumed from the prompt.
    """
    if not transcript.exists():
        return 0
    return sum(
        1
        for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines()
        if "check_architecture.sh" in line or str(ARCHY_BIN) in line
    )


def save_transcript(tree: Path, session_id: str, key: str) -> Path | None:
    src = Path.home() / ".claude" / "projects" / project_slug(tree) / f"{session_id}.jsonl"
    if not session_id or not src.exists():
        return None
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    dst = TRANSCRIPTS / f"{key.replace(':', '_')}_{session_id}.jsonl"
    shutil.copy(src, dst)
    return dst


def run_task(
    task_id: str,
    arm: str,
    *,
    model: str,
    max_wall: float,
    boot_timeout: float,
    suite_timeout: float,
) -> dict:
    started_at = time.time()
    tree = prepare_tree(task_id, arm)
    result = run_agent(tree, build_prompt(arm, tree), model=model, max_wall=max_wall)
    transcript = save_transcript(tree, result.get("session_id", ""), f"{task_id}:{arm}")

    row = {
        "task_id": task_id,
        "arm": arm,
        "model": model,
        "framework": "fastapi",
        "started_at": round(started_at, 1),
        "agent_ok": bool(result.get("ok")),
        "agent_turns": result.get("num_turns"),
        "agent_wall_seconds": result.get("wall_seconds"),
        "transcript": transcript.name if transcript else None,
        "archy_invocations": count_archy_invocations(transcript) if transcript else 0,
    }
    if not result.get("ok"):
        # A non-zero agent exit is a real outcome, so this is an `error` row that
        # resume will retry rather than a scored one. Both streams are kept: the
        # CLI puts its error text on stdout, and a failure row that cannot be
        # diagnosed later is worth nothing.
        return {
            **row,
            "error": f"agent exited {result.get('returncode')}",
            "stderr": result.get("stderr", ""),
            "stdout": result.get("stdout", ""),
        }

    structural = greenfield_eval.structural_verdict(tree)
    row["structural"] = structural.model_dump()
    row["behavioral"] = behavioral_row(tree, boot_timeout=boot_timeout, suite_timeout=suite_timeout)
    row["finished_at"] = round(time.time(), 1)
    return row


def run_with_limit_backoff(
    task_id: str, arm: str, *, max_waits: int, max_backoff: float, **kwargs
) -> dict:
    """`run_task`, waiting out subscription limits rather than burning tasks.

    Without it one exhausted limit marches through every remaining task in
    seconds, recording each as an error, and the batch looks finished having
    measured nothing.
    """
    for attempt in range(max_waits + 1):
        try:
            return run_task(task_id, arm, **kwargs)
        except RateLimited as exc:
            if attempt == max_waits:
                raise
            if exc.reset_at:
                wait = min(max(exc.reset_at - time.time(), 60.0), max_backoff)
                why = f"limit resets in {wait / 60:.0f}m"
            else:
                wait = min(300.0 * (2**attempt), max_backoff)
                why = "no reset time given, backing off"
            print(
                f"      rate limited ({why}); sleeping {wait / 60:.0f}m "
                f"[wait {attempt + 1}/{max_waits}]",
                flush=True,
            )
            time.sleep(wait)
    raise RuntimeError("unreachable")


def preflight(arms: tuple[str, ...]) -> str | None:
    """Refuse to start a batch that cannot measure what it claims to.

    Every check here is one that fails SILENTLY at run time: a missing checker
    makes arm B into arm A, a missing suite makes every behavioral score
    unevaluable, and both produce a batch that looks complete.
    """
    if shutil.which("claude") is None:
        return "no `claude` on PATH"
    if not any(greenfield_eval.SUITE_DIR.glob("*.hurl")):
        return f"no hurl suite at {greenfield_eval.SUITE_DIR}; run greenfield_eval.py --fetch-suite"
    if shutil.which("docker") is None:
        return "no `docker` on PATH; the behavioral half cannot run"
    if shutil.which("uv") is None:
        return "no `uv` on PATH; generated projects cannot be installed"
    if "B" not in arms:
        return None
    if not ARCHY_BIN.exists():
        return f"arm B needs a checker at {ARCHY_BIN}; run `uv sync`"
    probe = subprocess.run(
        [str(ARCHY_BIN), "check", str(REPO_ROOT / "bench/fixtures/conduit_clean/degenerate")],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT / "bench/fixtures/conduit_clean/degenerate",
    )
    if "min_layers_present" not in f"{probe.stdout}{probe.stderr}":
        # Half the paper's verifier. An arm B gated on the other half only would
        # produce a clean-looking null.
        return f"{ARCHY_BIN} does not support min_layers_present; arm B would check half the rule"
    return None


def unit_id(index: int) -> str:
    """The stable id for run `index`.

    One definition, because chunking depends on these ids not moving: a later
    `--limit` must name the SAME units the earlier one did, or raising it would
    re-run work the ledger thinks is done.
    """
    return f"conduit-{index:02d}"


def ledger_key(index: int, arm: str) -> str:
    return f"{unit_id(index)}:{arm}"


def missing_for_verdict(ledger: Ledger) -> int:
    """Runs still owed before the pre-registered reading may be taken."""
    done = sum(
        1
        for arm in ("A", "B")
        for i in range(1, TARGET_N_PER_ARM + 1)
        if ledger.is_done(ledger_key(i, arm))
    )
    return max(0, TARGET_N_PER_ARM * 2 - done)


def render_progress(ledger: Ledger) -> str:
    """Where the batch is, WITHOUT saying anything about how it is going.

    Deliberately counts only. Compliance and pass rates are the things the
    thresholds read, so showing them mid-batch would be the peek the fixed N
    exists to prevent, dressed up as monitoring.
    """
    failures = attempts_per_key(LEDGER_PATH)
    lines = ["batch progress (counts only; outcomes are not shown before the batch completes)", ""]
    for arm in ("A", "B"):
        keys = [ledger_key(i, arm) for i in range(1, TARGET_N_PER_ARM + 1)]
        done = sum(1 for key in keys if ledger.is_done(key))
        retrying = sum(1 for key in keys if not ledger.is_done(key) and failures[key])
        label = "arm A (static)" if arm == "A" else "arm B (archy)"
        lines.append(
            f"  {label:16} {done:2}/{TARGET_N_PER_ARM} done, "
            f"{TARGET_N_PER_ARM - done:2} pending ({retrying} of them previously failed)"
        )
    short = missing_for_verdict(ledger)
    lines += [
        "",
        "complete. `--report` will score it."
        if not short
        else f"{short} run(s) to go before `--report` will score it.",
    ]
    return "\n".join(lines)


def summarize(rows: list[dict]) -> str:
    """Score the ledger through the pre-registered reading, never by eye."""
    arm_a = greenfield_prereg.summarize_rows(rows, "A")
    arm_b = greenfield_prereg.summarize_rows(rows, "B")
    return greenfield_prereg.render(arm_a, arm_b, greenfield_prereg.verdict(arm_a, arm_b))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=25, help="runs per arm")
    ap.add_argument("--arms", default="AB", help="which arms to run, e.g. A, B, AB")
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--max-wall", type=float, default=3600.0, help="seconds per agent run")
    ap.add_argument("--boot-timeout", type=float, default=90.0, help="seconds to bind the port")
    ap.add_argument("--suite-timeout", type=float, default=600.0)
    ap.add_argument("--pause", type=float, default=30.0, help="seconds between runs that spent")
    ap.add_argument("--limit-waits", type=int, default=6)
    ap.add_argument("--max-backoff", type=float, default=3600.0)
    ap.add_argument(
        "--passes",
        type=int,
        default=8,
        help="how many times to sweep the batch, retrying units that failed",
    )
    ap.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="per-unit failures before it is dropped, so one bad unit cannot spin the loop",
    )
    ap.add_argument("--dry-run", action="store_true", help="plan only; spends nothing")
    ap.add_argument("--report", action="store_true", help="score the ledger and exit")
    ap.add_argument(
        "--progress",
        action="store_true",
        help="how far along the batch is, without showing any outcome",
    )
    ap.add_argument(
        "--force-partial",
        action="store_true",
        help="score before the pre-registered N is reached. Not the pre-registered reading.",
    )
    ap.add_argument("--check-tree", type=Path, help=argparse.SUPPRESS)  # used by the arm B wrapper
    args = ap.parse_args()

    if args.check_tree:
        return run_check(args.check_tree)

    ledger = Ledger(LEDGER_PATH)
    if args.progress:
        print(render_progress(ledger))
        return 0
    if args.report:
        rows = [r for r in (ledger.get(k) for k in ledger._done) if r]
        short = missing_for_verdict(ledger)
        if short and not args.force_partial:
            print(
                f"refusing to score: {short} of the pre-registered "
                f"{TARGET_N_PER_ARM * 2} run(s) are not complete.\n"
                "The reading is pre-registered at a FIXED N and scoring early is optional\n"
                "stopping: a partial batch that happens to look good is exactly how a null\n"
                "becomes a finding. Use --progress to watch the batch without seeing outcomes,\n"
                "or --force-partial if you have a reason that is not 'I want to know yet'."
            )
            return 1
        if short:
            print(
                f"# WARNING: PARTIAL, {short} run(s) short of the pre-registered N.\n"
                "# This is NOT the pre-registered reading and must not be reported as one.\n"
            )
        print(summarize(rows))
        return 0

    arms = tuple(a for a in args.arms.upper() if a in "AB")
    if not arms:
        ap.error("--arms must name at least one of A, B")
    if args.limit > TARGET_N_PER_ARM:
        # Adding runs beyond the pre-registered N is optional stopping wearing a
        # different hat. The pre-registered way to get more data is EXPAND
        # (flask and django at the same N), not more conduit runs on fastapi.
        ap.error(
            f"--limit {args.limit} exceeds the pre-registered {TARGET_N_PER_ARM} per arm. "
            "More data is EXPAND (see bench/greenfield_prereg.py), not a bigger N here."
        )
    # Interleaved, so a batch stopped halfway is balanced rather than being all
    # of one arm. An unbalanced partial batch cannot be read at all.
    units = [(unit_id(i), arm) for i in range(1, args.limit + 1) for arm in arms]

    if args.dry_run:
        pending = [u for u in units if not ledger.is_done(f"{u[0]}:{u[1]}")]
        print(f"# plan: {len(units)} runs ({len(arms)} arm(s) x {args.limit}), model {args.model}")
        print(f"# {len(units) - len(pending)} already done, {len(pending)} to run")
        print(f"# preflight: {preflight(arms) or 'ok'}")
        return 0

    problem = preflight(arms)
    if problem:
        print(f"preflight failed: {problem}")
        return 1

    RUNS.mkdir(parents=True, exist_ok=True)
    return loop(units, arms, args)


def attempts_per_key(path: Path) -> Counter[str]:
    """How many times each unit has already failed, across every pass.

    Read from the file rather than held in memory, because the whole point is
    that a later process can pick up where an earlier one died.
    """
    counts: Counter[str] = Counter()
    if not path.exists():
        return counts
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn row; the unit is simply re-run
        if row.get("status") in {"error", "limited"} and "key" in row:
            counts[row["key"]] += 1
    return counts


def loop(units: list[tuple[str, str]], arms: tuple[str, ...], args) -> int:
    """Sweep the batch repeatedly until every unit is `ok` or given up on.

    A single pass is not enough. Failures here are ordinary and mostly
    transient (a stalled CLI, a port that lost a race, a suite timeout), and
    they are spread across a run measured in hours, so the batch has to be able
    to heal itself while a human watches and fixes the ones that do not.

    Two stopping conditions, and BOTH are needed:

    - nothing pending: the batch is complete.
    - no unit advanced this pass: everything still failing is failing
      deterministically, so another identical pass would only burn agent time.

    Per-unit attempts are capped as well, so one poisoned unit cannot hold the
    batch open forever. An abandoned unit is recorded and reported, never
    scored: an unmeasurable run is dropped, not counted clean.
    """
    for sweep_index in range(1, args.passes + 1):
        ledger = Ledger(LEDGER_PATH)
        failures = attempts_per_key(LEDGER_PATH)
        pending = [
            (task_id, arm)
            for task_id, arm in units
            if not ledger.is_done(f"{task_id}:{arm}")
            and failures[f"{task_id}:{arm}"] < args.max_attempts
        ]
        abandoned = [
            (task_id, arm)
            for task_id, arm in units
            if not ledger.is_done(f"{task_id}:{arm}")
            and failures[f"{task_id}:{arm}"] >= args.max_attempts
        ]
        if not pending:
            if abandoned:
                print(
                    f"\n{len(abandoned)} unit(s) gave up after {args.max_attempts} attempts "
                    f"and are DROPPED, not scored: {[f'{t}:{a}' for t, a in abandoned][:6]}"
                )
            print(f"\nbatch complete: {ledger.completed} of {len(units)} unit(s) ok")
            return 0

        print(
            f"\n=== pass {sweep_index}/{args.passes}: {len(pending)} pending, "
            f"{ledger.completed} done, {len(abandoned)} abandoned ===",
            flush=True,
        )
        before = ledger.completed
        code = sweep(pending, ledger, args)
        if code:
            return code
        if Ledger(LEDGER_PATH).completed == before and len(pending) >= 2:
            # A WIPEOUT, not a bad unit. Several units were tried and not one
            # advanced, which points at the environment (docker down, the CLI
            # unauthenticated, the disk full) rather than at any single
            # generation. Retrying to the per-unit cap would spend
            # len(pending) * max_attempts agent runs reproducing it.
            #
            # Deliberately NOT triggered when a single unit is left: that is
            # just a stubborn unit, the attempt cap is the right bound for it,
            # and stopping there would leave the batch one row short forever.
            print(
                f"\nno unit advanced in pass {sweep_index} across {len(pending)} units; "
                "this looks environmental rather than per-unit. Stopping rather than "
                "re-spending. Fix what the rows report and re-run to resume."
            )
            return 1
    print(f"\nout of passes ({args.passes}). Re-run to resume.")
    return 1


def sweep(units: list[tuple[str, str]], ledger: Ledger, args) -> int:
    """One pass over the pending units. Returns non-zero only to stop the loop."""
    spent = 0
    for index, (task_id, arm) in enumerate(units, 1):
        key = f"{task_id}:{arm}"
        if spent and args.pause:
            time.sleep(args.pause)
        print(f"[{index}/{len(units)}] {key} ...", flush=True)
        spent += 1
        try:
            row = run_with_limit_backoff(
                task_id,
                arm,
                max_waits=args.limit_waits,
                max_backoff=args.max_backoff,
                model=args.model,
                max_wall=args.max_wall,
                boot_timeout=args.boot_timeout,
                suite_timeout=args.suite_timeout,
            )
        except RateLimited as exc:
            # Out of budgeted waits. `limited` is not `ok`, so a resume picks
            # this unit up untouched.
            ledger.record(key, {"task_id": task_id, "arm": arm, "note": str(exc)}, status="limited")
            print("stopping: usage limit outlasted the budgeted waits. Re-run to resume.")
            return 1
        except KeyboardInterrupt:
            print(
                f"\ninterrupted. {ledger.completed} run(s) recorded. Re-run to resume from {key}."
            )
            return 130
        # One bad unit must not end a batch that has already run for hours.
        except Exception as exc:
            ledger.record(
                key,
                {"task_id": task_id, "arm": arm, "error": f"{type(exc).__name__}: {exc}"},
                status="error",
            )
            print(f"      error: {type(exc).__name__}: {exc}", flush=True)
            continue

        status = "ok" if "error" not in row else "error"
        ledger.record(key, row, status=status)
        if status == "ok":
            structural = row["structural"]
            behavioral = row["behavioral"]
            print(
                f"      compliant={structural['compliant']} "
                f"layers={structural['layers_present']} "
                f"behavioral={behavioral.get('pass_rate')} "
                f"archy_calls={row['archy_invocations']}",
                flush=True,
            )
        else:
            print(f"      {row['error']}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
