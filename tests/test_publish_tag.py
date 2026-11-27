"""The publish workflow refuses a release tag that disagrees with the version (#772).

The step's script is lifted out of the YAML and run under bash, as GitHub would.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILD = yaml.safe_load((ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8"))[
    "jobs"
]["build"]["steps"]
NAMES = [step.get("name") for step in BUILD]
STEP = BUILD[NAMES.index("Check the tag matches the project version")]

needs_bash = pytest.mark.skipif(sys.platform == "win32", reason="runs the step under a POSIX shell")


def _run(tag: str, bin_dir: Path | None) -> subprocess.CompletedProcess[str]:
    path = os.environ["PATH"] if bin_dir is None else f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
    return subprocess.run(
        ["bash", "-e", "-c", STEP["run"]],  # a step with no `shell:` gets `bash -e`
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PATH": path, "GITHUB_REF_NAME": tag},
    )


def test_the_check_runs_for_tags_and_before_anything_is_built():
    assert STEP["if"] == "github.ref_type == 'tag'"
    assert NAMES.index("Check the tag matches the project version") < NAMES.index(
        "Build sdist and wheel"
    )


@needs_bash
@pytest.mark.parametrize(
    ("tag", "passes"),
    [
        ("v1.2.3", True),
        ("1.2.3", True),
        ("v1.2.4", False),
        ("v1.2.30", False),
        ("v1.2", False),
        ("release-1.2.3", False),
    ],
)
def test_only_the_projects_version_passes(tmp_path: Path, tag: str, passes: bool):
    uv = tmp_path / "uv"
    uv.write_text('#!/bin/sh\n[ "$*" = "version --short" ] && echo 1.2.3\n', encoding="utf-8")
    uv.chmod(0o755)

    result = _run(tag, tmp_path)

    assert (result.returncode == 0) is passes, result.stdout + result.stderr
    if not passes:
        assert f"tag {tag} does not match project version 1.2.3" in result.stderr


@needs_bash
@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv, as the workflow has")
def test_the_real_uv_reports_the_version_in_pyproject():
    """The fake above is only as good as this: what `uv version --short` prints."""
    declared = next(
        line.split('"')[1]
        for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )

    assert _run(f"v{declared}", None).returncode == 0
