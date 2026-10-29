"""The score-comment workflows (#604) split scoring from posting, so their shape is pinned.

The scoring half runs a PR's code and so holds no write token. The posting half holds
one and so must never run that code. Both properties live in YAML, where nothing else
would notice a regression.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


CI = load("ci.yml")
SCORE = load("score-delta.yml")
COMMENT = load("score-comment.yml")
SCORE_JOB = SCORE["jobs"]["score-delta"]
COMMENT_JOB = COMMENT["jobs"]["comment"]


def steps(job: dict) -> list[dict]:
    return job["steps"]


def test_no_job_that_runs_pr_code_can_write():
    # PyYAML reads the `on:` key as the boolean True.
    for wf in (CI, SCORE):
        assert wf["permissions"] == {"contents": "read"}
        assert not [n for n, j in wf["jobs"].items() if "permissions" in j]


def test_the_scoring_workflow_runs_on_pull_requests_and_never_on_pull_request_target():
    triggers = SCORE[True]
    assert "pull_request" in triggers
    assert "pull_request_target" not in triggers


def test_only_the_comment_job_can_write_to_pull_requests():
    assert COMMENT["permissions"] == {}
    assert COMMENT_JOB["permissions"] == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "write",
    }


def test_the_comment_workflow_is_triggered_by_the_scoring_workflow_only():
    trigger = COMMENT[True]["workflow_run"]
    assert trigger["workflows"] == [SCORE["name"]]
    assert "pull_request" not in COMMENT[True]
    assert "pull_request_target" not in COMMENT[True]
    assert "workflow_run.event == 'pull_request'" in COMMENT_JOB["if"]
    assert "conclusion == 'success'" in COMMENT_JOB["if"]


def test_the_comment_job_never_checks_out_or_runs_the_prs_code():
    # A `ref:` or `repository:` on checkout would fetch the fork's tree into a job
    # that holds a write token. `uv run` / `pip` would build the fork's project.
    for step in steps(COMMENT_JOB):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert not set(step.get("with", {})) & {"ref", "repository"}
            assert step["with"]["persist-credentials"] is False
    runs = " ".join(s["run"] for s in steps(COMMENT_JOB) if "run" in s)
    assert "uv " not in runs and "pip" not in runs and "setup-" not in str(COMMENT_JOB)


def test_the_comment_job_hands_the_prs_identity_from_the_event_not_the_artifact():
    run = next(s for s in steps(COMMENT_JOB) if "run" in s)
    assert "--head" in run["run"] and "--head-sha" in run["run"]
    assert "--pr" not in run["run"]
    assert "workflow_run.head_sha" in run["env"]["HEAD_SHA"]


def test_the_two_workflows_agree_on_the_artifact_name():
    uploaded = next(s for s in steps(SCORE_JOB) if "upload-artifact" in s.get("uses", ""))
    downloaded = next(s for s in steps(COMMENT_JOB) if "download-artifact" in s.get("uses", ""))
    assert uploaded["with"]["name"] == downloaded["with"]["name"] == "score-delta"
    assert downloaded["with"]["run-id"] == "${{ github.event.workflow_run.id }}"


def test_no_event_expression_is_interpolated_into_a_shell_script():
    # `${{ github.event.* }}` in `run:` is expanded before bash parses it. The values
    # reach the scripts through `env:` instead.
    for job in (SCORE_JOB, COMMENT_JOB):
        runs = [s["run"] for s in steps(job) if "run" in s]
        assert runs
        assert not [r for r in runs if "${{" in r]


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = [s["uses"] for job in (SCORE_JOB, COMMENT_JOB) for s in steps(job) if "uses" in s]
    assert len(uses) >= 5
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses


def test_both_commits_are_scored_by_the_checkouts_own_archy():
    # Scoring the base with a released archy would make the delta measure the scorer.
    script = next(
        s["run"] for s in steps(SCORE_JOB) if "run" in s and "BASE_SHA" in s.get("env", {})
    )
    assert script.count("uv run archy score") == 2
