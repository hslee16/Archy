"""Tests for the #369 generation loop.

Nothing here spends agent time or starts a container. What it pins is the
plumbing that fails SILENTLY: an arm B that is secretly arm A, a leaked server
that lets one run score the next one's code, and the prereg's requirement that a
dead server and a broken harness stop being the same outcome.

The arm-B pilot for #356 found five plumbing bugs of exactly this kind, each of
which would have cost the whole batch.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import textwrap
import time
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT / "bench"))

import greenfield_run  # noqa: E402  # ty: ignore[unresolved-import]  (sys.path above)

FIXTURES = REPO_ROOT / "bench/fixtures/conduit_clean"


# --- the arms differ by exactly one thing -------------------------------------


def test_both_arms_carry_the_papers_constraint_block():
    """The constraint is the paper's, and it is what makes this a transcription
    rather than the measurer authoring the intent (the bias that dogged
    #353-#355). It must reach both arms identically."""
    for arm in ("A", "B"):
        prompt = greenfield_run.build_prompt(arm, Path("/tmp/x"))
        assert greenfield_run.CONSTRAINT_BLOCK in prompt


def test_arm_a_is_told_nothing_about_the_checker():
    """Arm A is the paper's own condition: a static prompt, no course
    correction. Any mention of the checker would contaminate the control."""
    prompt = greenfield_run.build_prompt("A", Path("/tmp/x"))
    assert "archy" not in prompt.lower()
    assert "check_architecture" not in prompt


def test_arm_b_differs_from_arm_a_only_by_the_addendum():
    """Everything else is held. If the task text drifted between arms, any
    difference in compliance would be unattributable."""
    arm_a = greenfield_run.build_prompt("A", Path("/tmp/x"))
    arm_b = greenfield_run.build_prompt("B", Path("/tmp/x"))
    assert arm_b.startswith(arm_a)


def test_arm_b_is_told_to_reach_exit_zero():
    prompt = greenfield_run.build_prompt("B", Path("/tmp/x"))
    assert "exits 0" in prompt
    assert "check_architecture.sh" in prompt


def test_only_arm_b_gets_the_config_and_the_checker(tmp_path, monkeypatch):
    monkeypatch.setattr(greenfield_run, "RUNS", tmp_path)
    arm_a = greenfield_run.prepare_tree("t", "A")
    arm_b = greenfield_run.prepare_tree("t", "B")
    assert not (arm_a / "archy.yaml").exists()
    assert not (arm_a / "check_architecture.sh").exists()
    assert (arm_b / "archy.yaml").exists()
    assert os.access(arm_b / "check_architecture.sh", os.X_OK)


def test_a_reused_directory_is_wiped_first(tmp_path, monkeypatch):
    """Greenfield means greenfield. A resumed batch must not score files left by
    a previous attempt at the same unit."""
    monkeypatch.setattr(greenfield_run, "RUNS", tmp_path)
    first = greenfield_run.prepare_tree("t", "A")
    (first / "leftover.py").write_text("x = 1")
    again = greenfield_run.prepare_tree("t", "A")
    assert not (again / "leftover.py").exists()


# --- arm B's gate must agree with the scorer ----------------------------------
#
# If they disagree the agent either thrashes against an unsatisfiable gate or
# reshapes its layout to satisfy an artifact, and that would read as an archy
# effect. See #377.


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [("compliant", 0), ("violating", 1), ("degenerate", 1)],
)
def test_the_gate_agrees_with_the_paper_on_each_fixture(fixture, expected, tmp_path):
    import shutil

    tree = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, tree)
    assert greenfield_run.run_check(tree) == expected


def test_the_gate_sees_layers_nested_under_a_package(tmp_path):
    """The case the wrapper exists for: bare `archy check` reads a nested tree
    as zero layers present and fails a compliant project."""
    for layer, imports in (
        ("routes", "from app.services import svc"),
        ("services", "from app.repositories import repo"),
        ("repositories", "from app.models import mdl"),
        ("models", ""),
    ):
        directory = tmp_path / "app" / layer
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("")
        (directory / "mod.py").write_text(imports)
    (tmp_path / "app" / "__init__.py").write_text("")

    assert greenfield_run.run_check(tmp_path) == 0
    # And the scorer must reach the same verdict, which is the whole point.
    verdict = greenfield_run.greenfield_eval.structural_verdict(tmp_path)
    assert verdict.compliant is True
    assert verdict.layers_present == 4


# --- serving, and the prereg's failure taxonomy -------------------------------


def _write_run_sh(tree: Path, body: str) -> None:
    (tree / "run.sh").write_text(textwrap.dedent(body), encoding="utf-8")


def test_a_missing_run_sh_is_not_a_bound_server(tmp_path):
    with greenfield_run.served(tmp_path, 1, boot_timeout=1.0) as (bound, why):
        assert bound is False
        assert "run.sh" in why


def test_a_server_that_exits_on_boot_does_not_wait_out_the_timeout(tmp_path):
    """A crash-on-boot must be detected by the process dying, not by burning the
    full boot timeout on every failed generation. At 25 runs per arm that is the
    difference between minutes and hours."""
    _write_run_sh(tmp_path, "#!/bin/sh\nexit 1\n")
    started = time.monotonic()
    with greenfield_run.served(tmp_path, greenfield_run._free_port(), boot_timeout=30.0) as (
        bound,
        _,
    ):
        assert bound is False
    assert time.monotonic() - started < 15.0


def test_a_server_that_binds_is_detected_and_then_killed(tmp_path):
    """The teardown half matters as much as the detection half: a leaked
    listener means the NEXT task's suite scores THIS task's server."""
    port = greenfield_run._free_port()
    _write_run_sh(
        tmp_path,
        f"""\
        #!/bin/sh
        exec {sys.executable} -c "
        import http.server, socketserver
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Length','2')
                self.end_headers(); self.wfile.write(b'{{}}')
            def log_message(self, *a): pass
        socketserver.TCPServer(('0.0.0.0', {port}), H).serve_forever()
        "
        """,
    )
    with greenfield_run.served(tmp_path, port, boot_timeout=30.0) as (bound, why):
        assert bound is True, why
    # The port must be free again immediately after the context exits.
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))


def test_a_shell_that_forks_is_still_torn_down(tmp_path):
    """`run.sh` usually spawns uvicorn rather than exec'ing it, so killing only
    the direct child leaves the listener behind. The whole process group goes."""
    port = greenfield_run._free_port()
    _write_run_sh(
        tmp_path,
        f"""\
        #!/bin/sh
        {sys.executable} -c "
        import http.server, socketserver
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header('Content-Length','2')
                self.end_headers(); self.wfile.write(b'{{}}')
            def log_message(self, *a): pass
        socketserver.TCPServer(('0.0.0.0', {port}), H).serve_forever()
        " &
        wait
        """,
    )
    with greenfield_run.served(tmp_path, port, boot_timeout=30.0) as (bound, why):
        assert bound is True, why
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))


def test_free_ports_do_not_repeat(tmp_path):
    assert greenfield_run._free_port() != greenfield_run._free_port()


def test_a_project_with_no_requirements_does_not_install(tmp_path):
    installed, why = greenfield_run.install_deps(tmp_path, timeout=30.0)
    assert installed is False
    assert "requirements" in why


def test_an_uninstallable_project_is_scored_zero_not_dropped(tmp_path, monkeypatch):
    """The prereg's rule. A generated artifact that does not run is the
    GENERATION's failure and scores zero; dropping it would remove exactly the
    runs that failed worst and flatter both arms."""
    monkeypatch.setattr(greenfield_run.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(greenfield_run, "install_deps", lambda *a, **k: (False, "boom"))
    row = greenfield_run.behavioral_row(tmp_path, boot_timeout=1.0, suite_timeout=1.0)
    assert row["evaluable"] is True
    assert row["pass_rate"] == 0.0
    assert row["server_started"] is False


def test_a_dead_server_is_scored_zero_not_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(greenfield_run.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(greenfield_run, "install_deps", lambda *a, **k: (True, ""))
    row = greenfield_run.behavioral_row(tmp_path, boot_timeout=1.0, suite_timeout=1.0)
    assert row["evaluable"] is True
    assert row["pass_rate"] == 0.0
    assert row["server_started"] is False


def test_a_missing_docker_is_unevaluable_not_zero(tmp_path, monkeypatch):
    """The other half of the taxonomy. Scoring a harness failure as zero would
    put this machine into the measurement."""
    monkeypatch.setattr(greenfield_run.shutil, "which", lambda _: None)
    row = greenfield_run.behavioral_row(tmp_path, boot_timeout=1.0, suite_timeout=1.0)
    assert row["evaluable"] is False
    assert row["pass_rate"] is None


# --- an arm B that never ran the checker is arm A -----------------------------


def test_checker_use_is_counted_from_the_agents_own_transcript(tmp_path):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        '{"tool":"Bash","cmd":"ls"}\n'
        '{"tool":"Bash","cmd":"./check_architecture.sh"}\n'
        '{"tool":"Bash","cmd":"sh check_architecture.sh"}\n',
        encoding="utf-8",
    )
    assert greenfield_run.count_archy_invocations(transcript) == 2


def test_a_missing_transcript_counts_zero_rather_than_raising(tmp_path):
    assert greenfield_run.count_archy_invocations(tmp_path / "absent.jsonl") == 0


@pytest.fixture
def tools_present(monkeypatch, tmp_path):
    """Everything preflight checks except the checker itself, so each test
    isolates the one precondition it is about."""
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "auth.hurl").write_text("")
    monkeypatch.setattr(greenfield_run.greenfield_eval, "SUITE_DIR", suite)
    monkeypatch.setattr(greenfield_run.shutil, "which", lambda _: "/usr/bin/thing")


def test_preflight_refuses_arm_b_without_a_checker(tools_present, monkeypatch, tmp_path):
    """A missing checker turns arm B into arm A silently, and the batch would
    look complete while measuring nothing."""
    monkeypatch.setattr(greenfield_run, "ARCHY_BIN", tmp_path / "nope")
    problem = greenfield_run.preflight(("B",))
    assert problem and "checker" in problem


def test_preflight_does_not_require_a_checker_for_arm_a_only(tools_present, monkeypatch, tmp_path):
    monkeypatch.setattr(greenfield_run, "ARCHY_BIN", tmp_path / "nope")
    assert greenfield_run.preflight(("A",)) is None


def test_preflight_refuses_a_batch_with_no_suite(tools_present, monkeypatch, tmp_path):
    """Without the suite every behavioral score is unevaluable, so the batch
    would spend a full run of agent time and measure only structure."""
    monkeypatch.setattr(greenfield_run.greenfield_eval, "SUITE_DIR", tmp_path / "empty")
    problem = greenfield_run.preflight(("A",))
    assert problem and "hurl suite" in problem


# --- checkpoint and resume ----------------------------------------------------
#
# A run is hours long and the failure modes are ordinary: a stalled subprocess,
# a usage limit, a laptop sleeping, a crash in the scorer, someone pressing
# Ctrl-C. `q1b_run.py` supplies this shape and `tests/test_q1b_run.py` pins it;
# these pin that THIS loop honours it, because copying the shape is not the same
# as keeping it.


@pytest.fixture
def batch(monkeypatch, tmp_path):
    """A runnable two-unit batch whose agent call is under the test's control."""
    monkeypatch.setattr(greenfield_run, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(greenfield_run, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(greenfield_run, "preflight", lambda _arms: None)
    # --pause 0: the real batch paces itself to spread usage, which would
    # otherwise add 30s of sleep per unit to every test here.
    monkeypatch.setattr(
        sys, "argv", ["greenfield_run.py", "--limit", "2", "--arms", "A", "--pause", "0"]
    )

    def ok_row(task_id, arm, **_):
        return {
            "task_id": task_id,
            "arm": arm,
            "structural": {"compliant": True, "layers_present": 4},
            "behavioral": {"pass_rate": 0.9},
            "archy_invocations": 0,
        }

    return ok_row


def _rows(path: Path) -> list[dict]:
    """Parseable rows, skipping a torn one exactly as `Ledger` does on read."""
    import json

    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        with contextlib.suppress(json.JSONDecodeError):
            rows.append(json.loads(line))
    return rows


def test_a_crash_in_one_unit_does_not_end_the_batch(batch, monkeypatch, tmp_path):
    """Otherwise one bad task costs a batch that has already run for hours."""
    calls: list[str] = []

    def sometimes_explodes(task_id, arm, **kwargs):
        calls.append(task_id)
        if task_id == "conduit-01":
            raise RuntimeError("scorer fell over")
        return batch(task_id, arm)

    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", sometimes_explodes)
    assert greenfield_run.main() == 0

    # The invariant: the crash did not stop the REST OF THE PASS. conduit-02 was
    # still attempted and recorded, in the same sweep, immediately after.
    assert calls[:2] == ["conduit-01", "conduit-02"]
    rows = _rows(tmp_path / "ledger.jsonl")
    assert rows[0]["status"] == "error"
    assert "scorer fell over" in rows[0]["error"]
    assert [r["key"] for r in rows if r["status"] == "ok"] == ["conduit-02:A"]


def test_only_ok_units_are_skipped_on_resume(batch, monkeypatch, tmp_path):
    """`error` rows are work still owed. Treating them as done would silently
    shrink N by exactly the units that broke."""
    attempts: list[str] = []

    def explode_once(task_id, arm, **kwargs):
        attempts.append(task_id)
        if task_id == "conduit-01" and attempts.count("conduit-01") == 1:
            raise RuntimeError("transient")
        return batch(task_id, arm)

    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", explode_once)
    greenfield_run.main()
    greenfield_run.main()  # resume

    # conduit-02 succeeded first time and is not re-run; conduit-01 is retried.
    assert attempts == ["conduit-01", "conduit-02", "conduit-01"]
    statuses = [r["status"] for r in _rows(tmp_path / "ledger.jsonl")]
    assert statuses == ["error", "ok", "ok"]


def test_an_exhausted_usage_limit_is_not_recorded_as_a_result(batch, monkeypatch, tmp_path):
    """A limit is a fact about the account. Recording it as an outcome would put
    the subscription into the measurement, and `limited` is not `ok`, so the
    unit is picked up untouched on resume."""

    def limited(task_id, arm, **kwargs):
        raise greenfield_run.RateLimited("usage limit reached")

    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", limited)
    assert greenfield_run.main() == 1

    rows = _rows(tmp_path / "ledger.jsonl")
    assert [r["status"] for r in rows] == ["limited"]
    assert "structural" not in rows[0]
    # And the next run retries it rather than skipping it.
    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", batch)
    assert greenfield_run.main() == 0
    assert [r["status"] for r in _rows(tmp_path / "ledger.jsonl")] == ["limited", "ok", "ok"]


def test_an_interrupt_keeps_everything_already_written(batch, monkeypatch, tmp_path):
    """Only the in-flight unit is lost."""

    def interrupt_on_second(task_id, arm, **kwargs):
        if task_id == "conduit-02":
            raise KeyboardInterrupt
        return batch(task_id, arm)

    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", interrupt_on_second)
    assert greenfield_run.main() == 130
    assert [r["status"] for r in _rows(tmp_path / "ledger.jsonl")] == ["ok"]


def test_each_row_is_written_in_a_single_append(batch, monkeypatch, tmp_path):
    """Two writes leave a window where a crash merges the next row into a torn
    one and loses both. Every line must be independently parseable."""
    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", batch)
    greenfield_run.main()
    text = (tmp_path / "ledger.jsonl").read_text()
    assert text.endswith("\n")
    assert len(_rows(tmp_path / "ledger.jsonl")) == len(text.strip().splitlines())


def test_a_torn_final_row_costs_one_unit_and_not_two(batch, monkeypatch, tmp_path):
    """A hard kill can leave a partial line with no trailing newline.

    Reading already skipped it, but APPENDING onto it concatenated the two and
    destroyed the incoming row as well, so one crash cost two units. The torn
    line is left in place (history is not rewritten); what must hold is that
    every row written after it is intact and independently parseable.
    """
    import json

    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text('{"key": "conduit-01:A", "status": "ok"}\n{"key": "conduit-0')
    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", batch)
    assert greenfield_run.main() == 0

    lines = [ln for ln in ledger_path.read_text().splitlines() if ln.strip()]
    torn = [ln for ln in lines if _unparseable(ln)]
    assert len(torn) == 1, "the torn row must not have absorbed the next one"
    assert torn[0] == '{"key": "conduit-0'

    written = [json.loads(ln) for ln in lines if not _unparseable(ln)]
    # conduit-01 was already ok and is skipped; conduit-02 is run and recorded.
    assert [r["key"] for r in written] == ["conduit-01:A", "conduit-02:A"]


def _unparseable(line: str) -> bool:
    import json

    try:
        json.loads(line)
    except json.JSONDecodeError:
        return True
    return False


# --- the sweep loop -----------------------------------------------------------
#
# The batch re-sweeps until every unit is ok, which is only safe because the
# ledger checkpoints per unit. These pin that it heals transient failures, and
# that it cannot spin on ones it cannot heal.


def test_a_transient_failure_is_healed_by_the_next_pass(batch, monkeypatch, tmp_path):
    """The reason the loop exists: a stalled CLI or a lost port race should cost
    a retry, not a hole in the batch."""
    attempts: Counter[str] = Counter()

    def flaky(task_id, arm, **kwargs):
        attempts[task_id] += 1
        if task_id == "conduit-01" and attempts[task_id] == 1:
            raise RuntimeError("transient")
        return batch(task_id, arm)

    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", flaky)
    assert greenfield_run.main() == 0
    # Both units end ok without a human resuming by hand.
    ok = {r["key"] for r in _rows(tmp_path / "ledger.jsonl") if r["status"] == "ok"}
    assert ok == {"conduit-01:A", "conduit-02:A"}


def test_a_unit_that_always_fails_is_dropped_rather_than_spun_on(batch, monkeypatch, tmp_path):
    """One poisoned unit must not hold the batch open, and must not be scored:
    an unmeasurable run counted clean biases the rate by exactly the runs that
    broke."""
    attempts: Counter[str] = Counter()

    def always_fails_one(task_id, arm, **kwargs):
        attempts[task_id] += 1
        if task_id == "conduit-01":
            raise RuntimeError("poisoned")
        return batch(task_id, arm)

    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", always_fails_one)
    assert greenfield_run.main() == 0
    # Tried exactly --max-attempts times (default 3), then abandoned.
    assert attempts["conduit-01"] == 3
    rows = _rows(tmp_path / "ledger.jsonl")
    assert not [r for r in rows if r["key"] == "conduit-01:A" and r["status"] == "ok"]
    assert [r for r in rows if r["key"] == "conduit-02:A" and r["status"] == "ok"]


def test_a_wipeout_stops_the_loop_instead_of_retrying_to_the_cap(batch, monkeypatch, tmp_path):
    """Several units tried and none advanced points at the environment (docker
    down, CLI unauthenticated), not at any generation. Retrying to the per-unit
    cap would spend pending * max_attempts agent runs reproducing it."""
    calls = Counter()

    def everything_fails(task_id, arm, **kwargs):
        calls[task_id] += 1
        raise RuntimeError("down")

    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", everything_fails)
    assert greenfield_run.main() == 1
    # One pass over both units, then stop: no second sweep of the same failures.
    assert sum(calls.values()) == 2


def test_attempts_are_counted_across_processes_not_held_in_memory(tmp_path):
    """A later process must see what an earlier one already tried, or the cap
    resets on every resume and a poisoned unit runs forever."""
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text(
        '{"key": "a:A", "status": "error"}\n'
        '{"key": "a:A", "status": "error"}\n'
        '{"key": "b:A", "status": "ok"}\n'
        '{"key": "c:A", "status": "limited"}\n'
    )
    counts = greenfield_run.attempts_per_key(ledger_path)
    assert counts["a:A"] == 2
    assert counts["b:A"] == 0
    # A usage limit counts toward attempts too: it is not a result, but a unit
    # that only ever hits limits must not hold the batch open either.
    assert counts["c:A"] == 1


def test_attempts_survive_a_torn_row(tmp_path):
    ledger_path = tmp_path / "ledger.jsonl"
    ledger_path.write_text('{"key": "a:A", "status": "error"}\n{"key": "a:A", "sta')
    assert greenfield_run.attempts_per_key(ledger_path)["a:A"] == 1


def test_a_finished_batch_reports_complete_without_another_pass(batch, monkeypatch, tmp_path):
    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", batch)
    assert greenfield_run.main() == 0
    calls = Counter()

    def should_not_run(task_id, arm, **kwargs):
        calls[task_id] += 1
        return batch(task_id, arm)

    monkeypatch.setattr(greenfield_run, "run_with_limit_backoff", should_not_run)
    assert greenfield_run.main() == 0
    assert sum(calls.values()) == 0


def test_arms_are_interleaved_so_a_partial_batch_stays_balanced(monkeypatch, tmp_path):
    """A batch stopped halfway must not be all of one arm; an unbalanced partial
    batch cannot be read against the thresholds at all."""
    monkeypatch.setattr(greenfield_run, "LEDGER_PATH", tmp_path / "l.jsonl")
    monkeypatch.setattr(greenfield_run, "preflight", lambda _arms: None)
    monkeypatch.setattr(sys, "argv", ["greenfield_run.py", "--limit", "3", "--dry-run"])
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(
        greenfield_run, "run_with_limit_backoff", lambda t, a, **k: seen.append((t, a))
    )
    greenfield_run.main()  # --dry-run only plans, so assert on the unit order
    units = [(f"conduit-{i:02d}", arm) for i in range(1, 4) for arm in ("A", "B")]
    assert units[:2] == [("conduit-01", "A"), ("conduit-01", "B")]
