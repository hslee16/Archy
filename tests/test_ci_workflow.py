"""The `score-delta` job in `ci.yml` (#604) holds a write token, so its shape is pinned."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

CI = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text())
JOB = CI["jobs"]["score-delta"]


def test_only_this_job_can_write_to_pull_requests():
    # The workflow default stays read-only; the comment job alone gets the write scope.
    assert CI["permissions"] == {"contents": "read"}
    assert JOB["permissions"] == {"contents": "read", "pull-requests": "write"}
    others = {n: j for n, j in CI["jobs"].items() if n != "score-delta"}
    assert not [n for n, j in others.items() if "permissions" in j]


def test_it_runs_only_on_pull_requests():
    assert JOB["if"] == "github.event_name == 'pull_request'"


def test_no_event_expression_is_interpolated_into_a_shell_script():
    # `${{ github.event.* }}` in `run:` is expanded before bash parses it. The values
    # reach the scripts through `env:` instead.
    runs = [s["run"] for s in JOB["steps"] if "run" in s]
    assert runs
    assert not [r for r in runs if "${{" in r]


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = [s["uses"] for s in JOB["steps"] if "uses" in s]
    assert uses
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses), uses


def test_both_commits_are_scored_by_the_checkouts_own_archy():
    # Scoring the base with a released archy would make the delta measure the scorer.
    script = next(s["run"] for s in JOB["steps"] if "run" in s and "BASE_SHA" in s.get("env", {}))
    assert script.count("uv run archy score") == 2
