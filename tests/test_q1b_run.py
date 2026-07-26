"""Tests for the Q1b arm-B pilot runner.

The pilot spends hours of agent time against a subscription's usage limits, so
the properties tested here are the ones that decide whether an interrupted run
costs one task or all of them: per-task checkpointing, resume, and the fact that
a crash in one task does not end the loop.

Nothing here invokes an agent. `run_task` is monkeypatched, because what needs
testing is the LOOP's failure handling, not the agent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_supervise.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import q1b_run  # ty: ignore[unresolved-import]  (added to sys.path above)
from _supervise import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    Ledger,
    StallTimeout,
)

TASKS = [
    {
        "instance_id": "a__a-1",
        "repo": "django/django",
        "base_commit": "aaa",
        "problem_statement": "x",
    },
    {
        "instance_id": "b__b-2",
        "repo": "sympy/sympy",
        "base_commit": "bbb",
        "problem_statement": "y",
    },
    {
        "instance_id": "c__c-3",
        "repo": "pydata/xarray",
        "base_commit": "ccc",
        "problem_statement": "z",
    },
]


def _good_row(task: dict, **over) -> dict:
    row = {
        "instance_id": task["instance_id"],
        "repo": task["repo"],
        "arm": "B",
        "measurable": True,
        "structurally_bad": False,
        "cycle_regression": False,
        "layer_violations_introduced": {},
        "made_edit": True,
        "files_changed": 2,
        "wall_seconds": 1.0,
    }
    row.update(over)
    return row


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    """Point the runner's manifest and ledger at a tmp dir, agent stubbed out."""
    manifest = tmp_path / "bench" / "q1b_tasks.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"dataset": "test/ds", "tasks": TASKS}))
    monkeypatch.setattr(q1b_run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(q1b_run, "LEDGER_PATH", tmp_path / "results.jsonl")
    # --pause 0: the real default paces usage limits, which would make this
    # suite sleep for minutes.
    monkeypatch.setattr(sys, "argv", ["q1b_run.py", "--limit", "3", "--pause", "0"])
    # The prompts live in the dataset, not the manifest; stub the fetch so no
    # test touches the network.
    monkeypatch.setattr(
        q1b_run,
        "problem_statements",
        lambda dataset: {t["instance_id"]: t["problem_statement"] for t in TASKS},
    )
    return tmp_path


def _read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_each_task_is_checkpointed_as_it_completes(pilot, monkeypatch):
    """A row lands per task, not batched at the end: a kill loses one run."""
    seen: list[str] = []

    def fake_run_task(task, statement, **_):
        # The ledger for previously-finished tasks must already be on DISK by
        # the time the next task starts, which is what makes a kill cheap.
        if seen:
            written = _read_rows(pilot / "results.jsonl")
            assert [r["key"] for r in written] == [f"{i}:B" for i in seen]
        seen.append(task["instance_id"])
        return _good_row(task)

    monkeypatch.setattr(q1b_run, "run_task", fake_run_task)
    assert q1b_run.main() == 0
    assert len(_read_rows(pilot / "results.jsonl")) == 3


def test_resume_skips_completed_and_reruns_failures(pilot, monkeypatch):
    """Only status="ok" counts as done; crashed and stalled tasks are retried."""
    ledger = Ledger(pilot / "results.jsonl")
    ledger.record("a__a-1:B", _good_row(TASKS[0]))
    ledger.record("b__b-2:B", {"instance_id": "b__b-2", "error": "boom"}, status="error")
    ledger.record("c__c-3:B", {"instance_id": "c__c-3", "error": "stall"}, status="stalled")

    attempted: list[str] = []

    def fake_run_task(task, statement, **_):
        attempted.append(task["instance_id"])
        return _good_row(task)

    monkeypatch.setattr(q1b_run, "run_task", fake_run_task)
    assert q1b_run.main() == 0
    assert attempted == ["b__b-2", "c__c-3"]


def test_one_crashing_task_does_not_end_the_pilot(pilot, monkeypatch):
    """A git/scorer failure costs its own task only, and is recorded for retry."""

    def fake_run_task(task, statement, **_):
        if task["instance_id"] == "b__b-2":
            raise RuntimeError("git exploded")
        return _good_row(task)

    monkeypatch.setattr(q1b_run, "run_task", fake_run_task)
    assert q1b_run.main() == 0

    rows = {r["key"]: r for r in _read_rows(pilot / "results.jsonl")}
    assert rows["a__a-1:B"]["status"] == "ok"
    assert rows["b__b-2:B"]["status"] == "error"
    assert "git exploded" in rows["b__b-2:B"]["error"]
    assert rows["c__c-3:B"]["status"] == "ok"  # the loop kept going


def test_exhausted_stall_retries_are_recorded_not_raised(pilot, monkeypatch):
    def fake_run_task(task, statement, **_):
        if task["instance_id"] == "a__a-1":
            raise StallTimeout("no progress for 900s")
        return _good_row(task)

    monkeypatch.setattr(q1b_run, "run_task", fake_run_task)
    assert q1b_run.main() == 0
    rows = {r["key"]: r for r in _read_rows(pilot / "results.jsonl")}
    assert rows["a__a-1:B"]["status"] == "stalled"
    assert len([r for r in rows.values() if r["status"] == "ok"]) == 2


def test_interrupt_returns_130_and_keeps_finished_rows(pilot, monkeypatch):
    def fake_run_task(task, statement, **_):
        if task["instance_id"] == "b__b-2":
            raise KeyboardInterrupt
        return _good_row(task)

    monkeypatch.setattr(q1b_run, "run_task", fake_run_task)
    assert q1b_run.main() == 130
    rows = _read_rows(pilot / "results.jsonl")
    assert [r["key"] for r in rows] == ["a__a-1:B"]


def test_missing_prompt_aborts_before_spending_anything(pilot, monkeypatch):
    """The bug the first smoke run found: the manifest carries no prompts.

    A missing statement must fail the whole pilot up front, not one task at a
    time after the others have already been paid for.
    """
    monkeypatch.setattr(q1b_run, "problem_statements", lambda dataset: {"a__a-1": "x"})
    called: list[str] = []
    monkeypatch.setattr(
        q1b_run, "run_task", lambda task, statement, **_: called.append(task["instance_id"])
    )
    assert q1b_run.main() == 1
    assert called == []


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def two_commit_repo(tmp_path):
    """A real clone with two commits, for the worktree/diff regressions below."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("config", "user.email", "t@t.t", cwd=origin)
    _git("config", "user.name", "t", cwd=origin)
    (origin / "pkg").mkdir()
    (origin / "pkg" / "__init__.py").write_text("")
    (origin / "pkg" / "a.py").write_text("A = 1\n")
    _git("add", "-A", cwd=origin)
    _git("commit", "-qm", "first", cwd=origin)
    first = _git("rev-parse", "HEAD", cwd=origin)
    (origin / "pkg" / "a.py").write_text("A = 2\n")
    _git("commit", "-qam", "second", cwd=origin)
    second = _git("rev-parse", "HEAD", cwd=origin)
    return origin, first, second


def test_worktree_survives_a_previous_runs_staged_edits(two_commit_repo, tmp_path, monkeypatch):
    """The bug that killed 11 runs: `git checkout` refuses to clobber local edits.

    Agents routinely `git add` their work, so the next task's checkout died with
    "Your local changes would be overwritten by checkout" for every repo after
    its first task.
    """
    origin, first, second = two_commit_repo
    monkeypatch.setattr(q1b_run, "CACHE", origin.parent)
    monkeypatch.setattr(q1b_run, "WORKTREES", tmp_path / "runs")

    tree = q1b_run.worktree_at("origin", second)
    # Simulate the agent: edit AND stage, which is what plain `git diff` misses
    # and what plain `git checkout` refuses to overwrite.
    (tree / "pkg" / "a.py").write_text("A = 999\n")
    (tree / "pkg" / "new.py").write_text("B = 1\n")
    _git("add", "-A", cwd=tree)

    tree = q1b_run.worktree_at("origin", first)  # must not raise
    assert (tree / "pkg" / "a.py").read_text() == "A = 1\n"
    assert not (tree / "pkg" / "new.py").exists()
    assert _git("status", "--porcelain", cwd=tree) == ""


def test_staged_and_untracked_edits_both_count_as_changed(two_commit_repo, tmp_path, monkeypatch):
    """`git diff` alone reported 0 files for runs that had clearly edited."""
    origin, _first, second = two_commit_repo
    monkeypatch.setattr(q1b_run, "CACHE", origin.parent)
    monkeypatch.setattr(q1b_run, "WORKTREES", tmp_path / "runs")
    monkeypatch.setattr(q1b_run, "REPOS", {"o/o": ("origin", "pkg")})
    monkeypatch.setattr(q1b_run, "layer_violations", lambda tree, name: None)

    def fake_run_agent(tree, prompt, **_):
        (tree / "pkg" / "a.py").write_text("A = 3\n")
        (tree / "pkg" / "new.py").write_text("B = 1\n")
        _git("add", "pkg/a.py", cwd=tree)  # staged: invisible to plain `git diff`
        return {"ok": True, "session_id": "", "wall_seconds": 1.0}

    monkeypatch.setattr(q1b_run, "run_agent", fake_run_agent)
    row = q1b_run.run_task(
        {"instance_id": "o__o-1", "repo": "o/o", "base_commit": second},
        "do it",
        model="m",
        max_wall=10.0,
    )
    assert row["made_edit"] is True
    assert row["files_changed"] == 2  # the staged one AND the untracked one


def test_agent_output_mentioning_rate_limits_is_not_a_rate_limit():
    """A successful run whose AGENT text says "429" slept 5m, then 10m, live.

    Fixing an HTTP library, or quoting a traceback, routinely puts that prose in
    the result. Only the CLI's own signal counts.
    """
    ok_envelope = json.dumps(
        {"is_error": False, "result": "Handled HTTP 429 rate limit responses.", "session_id": "s"}
    )
    assert q1b_run.hit_rate_limit(ok_envelope, "", 0) is False


def test_a_real_limit_is_still_caught_on_every_channel():
    limited = json.dumps({"is_error": True, "result": "Claude AI usage limit reached|1753500000"})
    assert q1b_run.hit_rate_limit(limited, "", 0) is True  # error envelope
    assert q1b_run.hit_rate_limit("", "429 Too Many Requests", 0) is True  # stderr
    assert q1b_run.hit_rate_limit("rate limit exceeded", "", 1) is True  # non-zero exit


def test_reset_stamp_is_parsed_so_the_wait_matches_the_limit():
    assert q1b_run.parse_reset_at("Claude AI usage limit reached|1753500000") == 1753500000.0
    assert q1b_run.parse_reset_at("some other failure") is None


def test_agent_env_hides_this_repos_virtualenv(monkeypatch):
    """The first pilot attempt let an agent uninstall scipy from archy's venv.

    `uv run` exports VIRTUAL_ENV, the agent has Bash, and a scikit-learn task
    reasonably runs `pip install`. That wrote into the environment the
    MEASUREMENT itself runs in.
    """
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/archy/.venv")
    monkeypatch.setenv("PYTHONPATH", "/somewhere/archy/src")
    monkeypatch.setenv("HOME", "/home/tester")

    env = q1b_run.agent_env()

    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert env["PIP_REQUIRE_VIRTUALENV"] == "1"
    assert env["HOME"] == "/home/tester"  # the agent still needs its own auth


def test_unmeasurable_runs_are_dropped_never_counted_as_passes():
    """The bias this guards: an unmeasurable run is not a clean run."""
    rows = [
        _good_row(TASKS[0], measurable=False, structurally_bad=False),
        _good_row(TASKS[1], structurally_bad=True),
        _good_row(TASKS[2], structurally_bad=False),
    ]
    out = q1b_run.summarize(rows)
    assert "dropped (unmeasurable): 1" in out
    assert "p_B (pooled) = 1/2 = 50.0%" in out


def test_no_edit_runs_get_their_own_rate_not_a_silent_denominator():
    """django-11281 finished in 70s having changed nothing, live.

    Such a run cannot break structure, so counting it as a clean trial drags
    p_B down for a reason unrelated to structural damage.
    """
    rows = [
        _good_row(TASKS[0], made_edit=False, files_changed=0),
        _good_row(TASKS[1], structurally_bad=True),
        _good_row(TASKS[2]),
    ]
    out = q1b_run.summarize(rows)
    assert "p_B (pooled) = 1/3 = 33.3%" in out
    assert "p_B (edited runs only) = 1/2 = 50.0%" in out
    assert "1 no-edit run(s) excluded" in out


def test_summarize_reports_per_repo_because_ruleset_strength_differs():
    rows = [_good_row(TASKS[0], structurally_bad=True), _good_row(TASKS[1])]
    out = q1b_run.summarize(rows)
    assert "django/django" in out and "sympy/sympy" in out


def test_small_samples_get_no_verdict():
    """A 1-run smoke printed "THE CORPUS IS WRONG" before this guard existed."""
    out = q1b_run.summarize([_good_row(TASKS[0])])
    assert "No verdict" in out
    assert "CORPUS IS WRONG" not in out


def test_verdict_appears_once_the_sample_is_big_enough():
    rows = [_good_row(TASKS[0]) for _ in range(q1b_run.MIN_N_FOR_VERDICT)]
    out = q1b_run.summarize(rows)
    assert "CORPUS IS WRONG" in out


def test_no_measurable_runs_reports_undefined_not_zero():
    out = q1b_run.summarize([_good_row(TASKS[0], measurable=False)])
    assert "undefined, not zero" in out
    assert "p_B (pooled)" not in out
