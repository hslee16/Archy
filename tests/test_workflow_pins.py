"""The gated E2E job installs vendor CLIs next to API keys, so both are fenced (#607)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
E2E = yaml.safe_load((WORKFLOWS / "install-e2e.yml").read_text(encoding="utf-8"))
JOB = E2E["jobs"]["e2e"]


def _runs() -> list[str]:
    return [
        step["run"]
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def test_no_workflow_pipes_a_download_into_a_shell():
    piped = re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b")

    assert [run for run in _runs() if piped.search(run)] == []


def test_global_npm_installs_name_an_exact_version():
    installs = [
        package
        for run in _runs()
        for args in re.findall(r"npm install -g (.+)", run)
        for package in args.split()
    ]

    assert installs, "fixture drifted: no global npm install left to check"
    assert [p for p in installs if not re.search(r".@\d+\.\d+\.\d+$", p)] == []


def test_api_keys_reach_only_the_step_that_calls_the_client():
    assert "secrets." not in str({k: v for k, v in E2E.items() if k != "jobs"})
    assert all(value.endswith("!= '' }}") for value in JOB["env"].values())
    holders = {
        step["name"]: sorted(step["env"])
        for step in JOB["steps"]
        if "secrets." in str(step.get("env", {}))
    }

    assert holders == {
        "Headless reachability (Claude Code)": ["ANTHROPIC_API_KEY"],
        "Headless reachability (Cursor)": ["CURSOR_API_KEY"],
    }
