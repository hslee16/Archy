"""PR sampling and checkout for the #548 harness (see `diff_duplicates_precision.py`).

The population, the seed and the per-repo cap are pre-registered in
`docs/research/DIFF_DUPLICATES_PRECISION.md`; this module only implements them.

Pool: AIDev `pull_request` rows that are CLOSED (merged or closed-unmerged, never
open) in repositories whose AIDev `language` is Python. The repository language is a
sampling proxy only: whether a PR is a "Python PR" is decided from its own diff at
analysis time, and a PR with no added `.py` line is dropped (recorded, excluded from
the denominator).

pyarrow is NOT an archy dependency. Run the sampling with
`uv run --with pyarrow python bench/diff_duplicates_precision.py ...`.

archy:owns        PoolPR, Resolved, SampleSpec, Unresolvable, draw_main, draw_pilot,
                  load_pool, resolve
"""

from __future__ import annotations

import json
import random
import subprocess
import urllib.request
from collections.abc import Iterator
from itertools import islice
from pathlib import Path

from pydantic import BaseModel, ConfigDict

SEED = 548
PILOT_N = 20
REPO_CAP = 25
AIDEV_URL = "https://huggingface.co/datasets/hao-li/AIDev/resolve/main/"
REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "bench" / "cache" / "aidev"
CLONES = Path("/tmp/archy_bench/aidev")
GIT_TIMEOUT = 900


class PoolPR(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    number: int
    repo: str
    agent: str
    merged: bool


class SampleSpec(BaseModel):
    """The pre-registered draw parameters, bundled so the draw functions stay narrow."""

    model_config = ConfigDict(frozen=True)

    seed: int = SEED
    pilot_n: int = PILOT_N
    cap: int = REPO_CAP


PREREGISTERED = SampleSpec()


class Resolved(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo_dir: Path
    base: str
    head: str


class Unresolvable(Exception):
    """The PR cannot be reproduced for a reason that will not change on retry
    (deleted repository, pull ref gone). Recorded as a drop. Any OTHER failure is
    transient and the unit is retried, never dropped."""


def _download(name: str) -> Path:
    dest = CACHE / name
    if not dest.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        urllib.request.urlretrieve(AIDEV_URL + name, part)
        part.rename(dest)
    return dest


def load_pool() -> list[PoolPR]:
    """The closed-PR pool in Python-language repositories, sorted by AIDev id."""
    import pyarrow.parquet as pq  # ty: ignore[unresolved-import]  (optional, see module doc)

    repos = pq.read_table(_download("repository.parquet")).to_pylist()
    python_repos = {r["id"]: r["full_name"] for r in repos if r["language"] == "Python"}
    cols = ["id", "number", "agent", "state", "merged_at", "repo_id"]
    rows = pq.read_table(_download("pull_request.parquet"), columns=cols).to_pylist()
    pool = [
        PoolPR(
            id=r["id"],
            number=r["number"],
            repo=python_repos[r["repo_id"]],
            agent=r["agent"],
            merged=bool(r["merged_at"]),
        )
        for r in rows
        if r["repo_id"] in python_repos and r["state"] == "closed"
    ]
    return sorted(pool, key=lambda p: p.id)


def _shuffled(pool: list[PoolPR], seed: int) -> list[PoolPR]:
    ordered = sorted(pool, key=lambda p: p.id)
    random.Random(seed).shuffle(ordered)
    return ordered


def draw_pilot(pool: list[PoolPR], spec: SampleSpec = PREREGISTERED) -> list[PoolPR]:
    """The first `pilot_n` of the seeded shuffle. The main sample never contains these."""
    return _shuffled(pool, spec.seed)[: spec.pilot_n]


def draw_main(pool: list[PoolPR], n: int, spec: SampleSpec = PREREGISTERED) -> list[PoolPR]:
    """`n` PRs from the same shuffle AFTER the pilot, at most `cap` per repository,
    the cap applied in draw order (so the first `cap` PRs a repository reaches win)."""
    return list(islice(_main_stream(pool, spec), n))


def _main_stream(pool: list[PoolPR], spec: SampleSpec) -> Iterator[PoolPR]:
    taken: dict[str, int] = {}
    for pr in _shuffled(pool, spec.seed)[spec.pilot_n :]:
        if taken.get(pr.repo, 0) >= spec.cap:
            continue
        taken[pr.repo] = taken.get(pr.repo, 0) + 1
        yield pr


def _run(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=True, cwd=cwd, timeout=GIT_TIMEOUT
    ).stdout


def _pr_shas(pr: PoolPR) -> tuple[str, str]:
    """`(base_sha, head_sha)` from the GitHub REST API (never GraphQL)."""
    try:
        raw = _run("gh", "api", f"repos/{pr.repo}/pulls/{pr.number}")
    except subprocess.CalledProcessError as exc:
        if "404" in (exc.stderr or ""):
            raise Unresolvable(f"pull request not found: {pr.repo}#{pr.number}") from exc
        raise
    data = json.loads(raw)
    return data["base"]["sha"], data["head"]["sha"]


def _ensure_clone(pr: PoolPR) -> Path:
    """The cached blobless clone of the PR's repository, cloning it if absent."""
    repo_dir = CLONES / pr.repo.replace("/", "__")
    if repo_dir.exists():
        return repo_dir
    CLONES.mkdir(parents=True, exist_ok=True)
    try:
        _run(
            "git",
            "clone",
            "--quiet",
            "--filter=blob:none",
            "--no-checkout",
            f"https://github.com/{pr.repo}.git",
            str(repo_dir),
        )
    except subprocess.CalledProcessError as exc:
        if "not found" in (exc.stderr or "").lower():
            raise Unresolvable(f"repository gone: {pr.repo}") from exc
        raise
    return repo_dir


def _fetch_head(pr: PoolPR, repo_dir: Path) -> str:
    """Fetch the PR's pull ref and return the head sha it points at."""
    try:
        _run("git", "fetch", "--quiet", "origin", f"pull/{pr.number}/head", cwd=repo_dir)
        return _run("git", "rev-parse", "FETCH_HEAD", cwd=repo_dir).strip()
    except subprocess.CalledProcessError as exc:
        if "couldn't find remote ref" in (exc.stderr or ""):
            raise Unresolvable(f"pull ref gone: {pr.repo}#{pr.number}") from exc
        raise


def resolve(pr: PoolPR) -> Resolved:
    """Blobless-clone the repository (cached), fetch the PR's pull ref, and return
    `merge-base(base, head)` as the diff base, i.e. the three-dot diff GitHub shows."""
    base_sha, _ = _pr_shas(pr)
    repo_dir = _ensure_clone(pr)
    head = _fetch_head(pr, repo_dir)
    base = _run("git", "merge-base", base_sha, head, cwd=repo_dir).strip()
    return Resolved(repo_dir=repo_dir, base=base, head=head)
