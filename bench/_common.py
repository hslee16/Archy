"""Shared helpers for the bench sweep scripts.

Extracted so `hotspots_sweep.py` and `duplicates_sweep.py` (and any future
sweep) share one manifest loader and one clone/checkout routine instead of
copy-pasting them. Sweep scripts are run as `python bench/<name>.py`, so
`bench/` is on `sys.path[0]` and this module imports as `from _common import ...`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "bench" / "projects.yaml"
WORKDIR = Path("/tmp/archy_bench")


def load_manifest() -> list[dict]:
    return yaml.safe_load(MANIFEST.read_text())["projects"]


def clone_or_update(proj: dict) -> Path | None:
    """Clone/checkout a project at its pinned SHA; never raises -- on any failure
    return None so the caller can skip the project and keep the sweep going. The
    archy self-entry uses REPO_ROOT directly (its pin may name a tag that does
    not exist yet during a release PR)."""
    name = proj["name"]
    sha = proj["sha"]
    if name == "archy":
        return REPO_ROOT
    target = WORKDIR / name
    if not target.exists():
        WORKDIR.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", "clone", "--quiet", f"https://github.com/{proj['repo']}.git", str(target)],
        )
        if res.returncode != 0:
            return None
    has_sha = subprocess.run(
        ["git", "-C", str(target), "cat-file", "-e", sha],
        capture_output=True,
    )
    if has_sha.returncode != 0:
        subprocess.run(["git", "-C", str(target), "fetch", "--quiet", "origin"], check=False)
    if (
        subprocess.run(
            ["git", "-C", str(target), "checkout", "--quiet", sha],
        ).returncode
        != 0
    ):
        return None
    subprocess.run(["git", "-C", str(target), "reset", "--hard", "--quiet", sha], check=False)
    subprocess.run(["git", "-C", str(target), "clean", "-fdx", "--quiet"], check=False)
    return target
