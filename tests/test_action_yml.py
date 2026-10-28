"""`action.yml` must treat its inputs as data, never as shell source (#607).

GitHub expands `${{ inputs.* }}` before bash parses a `run:` script, so an input
interpolated there is command execution for whoever controls the value. The
steps are exercised for real: the script is lifted out of the YAML and run under
bash with a fake `archy` on PATH that records its argv.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="runs the action's bash scripts under a POSIX shell"
)

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / "action.yml").read_text())
STEPS = {step["name"]: step for step in ACTION["runs"]["steps"]}
RUN_STEP = next(s for n, s in STEPS.items() if n.startswith("Run archy"))
PYTHON_STEP = STEPS["Set up Python"]

# Every input the run step reads, with the defaults the action declares.
DEFAULTS = {
    "INPUT_COMMAND": "score",
    "INPUT_PATH": ".",
    "INPUT_STRICT": "true",
    "INPUT_STRICT_TOLERANCE": "0.02",
    "INPUT_RECORD": "false",
    "INPUT_CONFIG": "",
    "INPUT_BASELINE": "archy-baseline.json",
    "INPUT_HISTORY_BRANCH": "archy-history",
    "INPUT_HISTORY_LAST": "10",
    "INPUT_PYTHON_VERSION": "3.12",
    "ACTION_PATH": "/action",
    "RUNNER_TEMP_DIR": "/tmp/runner",
}


def _fake_bin(tmp_path: Path) -> Path:
    """A `bin/` with an `archy` and a `uv` that record their argv, one arg per line."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("archy", "uv"):
        script = bin_dir / tool
        script.write_text(f'#!/bin/bash\nprintf \'%s\\n\' "$@" >> "{tmp_path}/{tool}.argv"\n')
        script.chmod(0o755)
    return bin_dir


def _run(step: dict, tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    full_env = {"PATH": f"{_fake_bin(tmp_path)}:/usr/bin:/bin", "HOME": str(tmp_path), **env}
    return subprocess.run(
        ["bash", "-c", step["run"]],
        env=full_env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def _argv(tmp_path: Path, tool: str) -> list[str]:
    path = tmp_path / f"{tool}.argv"
    return path.read_text().splitlines() if path.exists() else []


def test_no_input_expression_reaches_a_shell_script():
    # The structural invariant. Behaviour below proves the current script is
    # safe; this stops the next edit reintroducing `${{ inputs.x }}` in a `run:`.
    offenders = [
        name
        for name, step in STEPS.items()
        if "run" in step and re.search(r"\$\{\{\s*inputs\.", step["run"])
    ]
    assert offenders == []


def test_the_run_step_actually_reads_inputs_from_the_environment():
    # Guard the guard: the previous test passes trivially if the run step stops
    # taking inputs at all.
    assert set(DEFAULTS) <= set(RUN_STEP["env"])
    assert 'archy score "${args[@]}"' in RUN_STEP["run"]


@pytest.mark.parametrize("field", ["INPUT_PATH", "INPUT_CONFIG", "INPUT_BASELINE"])
def test_a_hostile_value_is_passed_as_one_argument_and_never_executed(tmp_path: Path, field: str):
    marker = tmp_path / "pwned"
    payload = f'x"; touch {marker}; echo "$(touch {marker})`touch {marker}`'
    env = {**DEFAULTS, field: payload}
    # Pick the command whose argv carries the field.
    env["INPUT_COMMAND"] = {"INPUT_PATH": "score", "INPUT_CONFIG": "check"}.get(field, "diff")
    if field == "INPUT_CONFIG":
        env["INPUT_PATH"] = "."
    result = _run(RUN_STEP, tmp_path, env)
    assert result.returncode == 0, result.stderr
    assert not marker.exists(), "the payload was executed by the shell"
    # The whole payload, metacharacters and all, is a single argv entry.
    assert payload in _argv(tmp_path, "archy")


@pytest.mark.parametrize("field", ["INPUT_PATH", "INPUT_CONFIG", "INPUT_BASELINE"])
def test_a_value_starting_with_a_dash_is_refused_as_option_injection(tmp_path: Path, field: str):
    result = _run(RUN_STEP, tmp_path, {**DEFAULTS, field: "--out=/etc/passwd"})
    assert result.returncode == 2
    assert _argv(tmp_path, "archy") == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("INPUT_COMMAND", "score; id"),
        ("INPUT_STRICT", "yes"),
        ("INPUT_RECORD", "1"),
        ("INPUT_STRICT_TOLERANCE", "0.02; id"),
        ("INPUT_STRICT_TOLERANCE", "-1"),
        ("INPUT_HISTORY_BRANCH", "-delete"),
        ("INPUT_HISTORY_BRANCH", "main; id"),
        ("INPUT_HISTORY_LAST", "0"),
        ("INPUT_HISTORY_LAST", "5; id"),
    ],
)
def test_an_invalid_enumerated_input_is_refused_before_archy_runs(
    tmp_path: Path, field: str, value: str
):
    result = _run(RUN_STEP, tmp_path, {**DEFAULTS, field: value})
    assert result.returncode == 2
    assert _argv(tmp_path, "archy") == []


def test_valid_inputs_still_build_the_same_command(tmp_path: Path):
    # The fix must not change what a normal caller gets.
    env = {
        **DEFAULTS,
        "INPUT_PATH": "src",
        "INPUT_STRICT_TOLERANCE": "0.05",
        "INPUT_RECORD": "true",
    }
    assert _run(RUN_STEP, tmp_path, env).returncode == 0
    assert _argv(tmp_path, "archy") == [
        "score",
        "src",
        "--strict",
        "--strict-tolerance",
        "0.05",
        "--record",
    ]


@pytest.mark.parametrize("value", ["3.12; touch pwned", "--help", "3.12 && id", "$(id)"])
def test_a_hostile_python_version_is_refused_before_uv_runs(tmp_path: Path, value: str):
    result = _run(PYTHON_STEP, tmp_path, {"INPUT_PYTHON_VERSION": value})
    assert result.returncode == 2
    assert _argv(tmp_path, "uv") == []
    assert not (tmp_path / "pwned").exists()


def test_a_normal_python_version_reaches_uv(tmp_path: Path):
    assert _run(PYTHON_STEP, tmp_path, {"INPUT_PYTHON_VERSION": "3.12"}).returncode == 0
    assert _argv(tmp_path, "uv") == ["python", "install", "3.12"]


def test_setup_uv_is_pinned_to_a_full_commit_sha():
    # A mutable tag runs whatever its owner moves it to, in every consumer's job.
    uses = STEPS["Install uv"]["uses"]
    assert re.fullmatch(r"astral-sh/setup-uv@[0-9a-f]{40}", uses), uses


def test_the_env_names_cover_every_input_the_script_reads():
    # A name the script reads but the step never sets would be empty at run time.
    read = set(re.findall(r"\bINPUT_[A-Z_]+\b", RUN_STEP["run"]))
    assert read <= set(RUN_STEP["env"])


def test_the_history_command_runs_the_script_with_validated_arguments(tmp_path: Path):
    env = {**DEFAULTS, "INPUT_COMMAND": "history", "INPUT_RECORD": "true", "INPUT_PATH": "src"}
    assert _run(RUN_STEP, tmp_path, env).returncode == 0
    assert _argv(tmp_path, "uv") == [
        "run",
        "--no-project",
        "--python",
        "3.12",
        "python",
        "/action/scripts/archy_history.py",
        "--project",
        "src",
        "--branch",
        "archy-history",
        "--last",
        "10",
        "--html-out",
        "/tmp/runner/archy-trend.html",
        "--record",
    ]


def test_the_history_command_does_not_record_unless_asked(tmp_path: Path):
    env = {**DEFAULTS, "INPUT_COMMAND": "history", "INPUT_RECORD": "false"}
    assert _run(RUN_STEP, tmp_path, env).returncode == 0
    assert "--record" not in _argv(tmp_path, "uv")


def test_the_token_reaches_only_the_history_command():
    # An expression, not a literal: any other command gets an empty token.
    token = RUN_STEP["env"]["ARCHY_HISTORY_TOKEN"]
    assert "inputs.command == 'history'" in token
    assert "inputs.token" in token


def test_the_trend_artifact_step_is_pinned_and_history_only():
    step = STEPS["Upload the score trend"]
    assert step["if"] == "inputs.command == 'history'"
    assert re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", step["uses"]), step["uses"]
