"""The gated E2E job installs vendor CLIs next to API keys, so both are fenced (#607)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
E2E = yaml.safe_load((WORKFLOWS / "install-e2e.yml").read_text(encoding="utf-8"))
JOB = E2E["jobs"]["e2e"]


STEPS = {step["name"]: step for step in JOB["steps"] if "name" in step}


def _runs() -> list[str]:
    paths = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
    return [
        step["run"]
        for path in paths
        for job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def test_no_workflow_pipes_a_download_into_a_shell():
    """A guard against the likely regression, not against every way to spell it."""
    piped = re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z)?sh\b")

    assert [run for run in _runs() if piped.search(run)] == []


def test_global_npm_installs_name_an_exact_version():
    packages = [
        word
        for run in _runs()
        for args in re.findall(r"\bnpm (?:install|i)\b(.*)", run)
        if {"-g", "--global"} & set(args.split())
        for word in args.split()
        if not word.startswith("-")
    ]

    assert packages, "fixture drifted: no global npm install left to check"
    assert [p for p in packages if not re.search(r".@\d+\.\d+\.\d+$", p)] == []


def test_the_cursor_download_is_verified_before_it_is_unpacked():
    run = STEPS["Install Cursor CLI"]["run"]

    assert 'echo "$sha  cursor.tar.gz" | shasum -a 256 -c -' in run
    assert run.index("shasum -a 256 -c") < run.index("tar --strip-components")


def test_neither_client_updates_itself_past_the_pin():
    assert "cursor-agent --disable-auto-update -p" in STEPS["Headless reachability (Cursor)"]["run"]
    assert STEPS["Headless reachability (Claude Code)"]["env"]["DISABLE_AUTOUPDATER"] == "1"


def test_api_keys_reach_only_the_step_that_calls_the_client():
    # The whole serialized form, so `with:`, `run:`, another job and the
    # `secrets['X']` spelling all count, not just a step's `env:`.
    assert "secrets" not in str({k: v for k, v in E2E.items() if k != "jobs"})
    assert list(E2E["jobs"]) == ["e2e"]
    assert "secrets" not in str({k: v for k, v in JOB.items() if k not in ("env", "steps")})
    assert JOB["env"] == {
        "HAS_ANTHROPIC_KEY": "${{ secrets.ANTHROPIC_API_KEY != '' }}",
        "HAS_CURSOR_KEY": "${{ secrets.CURSOR_API_KEY != '' }}",
    }
    holders = {
        step.get("name"): sorted(re.findall(r"secrets\W+(\w+)", str(step)))
        for step in JOB["steps"]
        if "secrets" in str(step)
    }
    assert holders == {
        "Headless reachability (Claude Code)": ["ANTHROPIC_API_KEY"],
        "Headless reachability (Cursor)": ["CURSOR_API_KEY"],
    }


def test_headless_steps_are_gated_on_the_booleans():
    """A step `if` reading a key no longer in the job env would skip forever."""
    assert STEPS["Headless reachability (Claude Code)"]["if"] == (
        "matrix.client == 'claude-code' && env.HAS_ANTHROPIC_KEY == 'true'"
    )
    assert STEPS["Headless reachability (Cursor)"]["if"] == (
        "matrix.client == 'cursor' && env.HAS_CURSOR_KEY == 'true'"
    )


@pytest.mark.parametrize(
    "name", ["Headless reachability (Claude Code)", "Headless reachability (Cursor)"]
)
def test_a_failing_client_fails_the_headless_step(name: str):
    """#1018: without pipefail the `tee` swallowed the exit code, and almost any
    error text satisfied the grep."""
    step = STEPS[name]

    assert step["shell"] == "bash"
    assert "grep" not in step["run"]
    assert (
        step["run"]
        .rstrip()
        .endswith("uv run python tests/install/e2e_assert_score.py expected.json out.txt")
    )


def test_the_key_holding_job_restores_no_cache_and_keeps_no_token():
    uses = {
        step["uses"].split("@")[0]: step.get("with", {}) for step in JOB["steps"] if "uses" in step
    }

    assert uses["actions/checkout"]["persist-credentials"] is False
    assert uses["astral-sh/setup-uv"]["enable-cache"] is False


@pytest.mark.parametrize(
    ("reply", "passes"),
    [
        ("0.600", True),
        ("The overall score is **0.60**.", True),
        ("overall: 0.5999435989246736", True),
        ("0.612", False),
        ("0.6", False),
        ("Error 401: invalid API key", False),
        ("archy_score failed after 0 tools", False),
        ("Windows 10.0.19041 overall", False),
        ("claude 2.1.323", False),
        ("", False),
    ],
)
def test_only_the_real_score_counts_as_reachability(tmp_path: Path, reply: str, passes: bool):
    expected, out = tmp_path / "expected.json", tmp_path / "out.txt"
    expected.write_text('{"overall": 0.5999435989246736}', encoding="utf-8")
    out.write_text(reply, encoding="utf-8")
    script = ROOT / "tests" / "install" / "e2e_assert_score.py"

    result = subprocess.run(
        [sys.executable, str(script), str(expected), str(out)], capture_output=True, text=True
    )

    assert (result.returncode == 0) is passes, result.stdout + result.stderr
