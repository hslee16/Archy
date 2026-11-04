"""Shared helpers for the bench sweep scripts.

Extracted so `hotspots_sweep.py` and `duplicates_sweep.py` (and any future
sweep) share one manifest loader and one clone/checkout routine instead of
copy-pasting them. Sweep scripts are run as `python bench/<name>.py`, so
`bench/` is on `sys.path[0]` and this module imports as `from _common import ...`.

archy:owns        clone_or_update, load_manifest
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


def clone_or_update(proj: dict) -> Path:
    """Clone or update a project repo to the specified ref.

    Returns the path to the local checkout.
    """
    name = proj["name"]
    url = proj["url"]
    ref = proj.get("ref", "HEAD")
    dest = WORKDIR / name

    if dest.exists():
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--all", "--prune"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout", ref],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(dest), "reset", "--hard", "FETCH_HEAD"],
            check=True,
            capture_output=True,
        )
    else:
        WORKDIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", ref, url, str(dest)],
            check=True,
            capture_output=True,
        )

    return dest
