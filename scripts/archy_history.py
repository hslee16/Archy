"""Keep archy's score history on an orphan branch, and put it back where archy reads it.

Used by the `history` command of `action.yml` (#605). On a merge to the default
branch it scores the project, appends ONE row to `history/YYYY-MM.jsonl` on a
history branch through the GitHub contents API, and renders the trend. Standard
library only, on purpose: the action runs it under `uv run --no-project`, where
pydantic (which `src/` uses for value objects) is not installed, so `Remote` is a
plain frozen dataclass.

Why these choices (each was weighed against the free GitHub alternatives):

- **An orphan branch, not an artifact or the cache.** Artifacts expire (90 days
  on a public repo) and the cache evicts after 7 idle days; a score series is
  only useful if it survives a quiet month.
- **The contents API, not `git push`.** Updating a file needs the blob `sha` it
  was read at, so two merges racing each other cannot silently overwrite one
  another: the loser gets a 409, re-reads, and re-applies its one row.
- **One file per month.** The contents API serves files up to 1 MB and a row is
  about 450 bytes, so a single file would stop working after roughly 2,300 rows.
- **Dedupe by commit.** A CI re-run of one commit must not add a second row.
- **Never on a pull request.** A PR head is unmerged work and a fork's token is
  read-only, so recording there would fill the series with rows that never landed.

A failure to reach or write the branch is a `::warning::`, not a failed build:
this is observability. Usage errors exit 2.

Usage:
    python archy_history.py --project . [--record] [--branch archy-history]

archy:owns        Client, Conflict, HistoryError, Remote, append_row, commits_in,
                  fetch_all, head_commit, main, month_path, render, run, score_row
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

API_DEFAULT = "https://api.github.com"
HISTORY_DIR = "history"
# The contents API serves a file inline up to 1 MB. Stay well under it so a
# month that grows faster than expected fails loudly instead of truncating.
MAX_FILE_BYTES = 900_000
ATTEMPTS = 5
PULL_REQUEST_EVENTS = frozenset({"pull_request", "pull_request_target"})


class HistoryError(Exception):
    """The history could not be read or written. Reported as a warning."""


class Conflict(HistoryError):
    """Another writer got there first: re-read and re-apply."""


@dataclass(frozen=True)
class Remote:
    content: str
    sha: str


class Client:
    """The few GitHub REST calls this needs, with a token only when writing."""

    def __init__(self, api: str, repo: str, token: str | None) -> None:
        self.api = api.rstrip("/")
        self.repo = repo
        self.token = token

    def request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> tuple[int, object]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "archy-history"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.api}/repos/{self.repo}/{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, _loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _loads(exc.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            raise HistoryError(f"could not reach {self.api}: {exc}") from exc

    def list_months(self, branch: str) -> list[str]:
        status, payload = self.request("GET", f"contents/{HISTORY_DIR}?ref={branch}")
        if status == 404:
            return []
        if status != 200 or not isinstance(payload, list):
            raise HistoryError(f"listing {HISTORY_DIR}/ on {branch} failed: HTTP {status}")
        names = [e["name"] for e in payload if isinstance(e, dict) and "name" in e]
        return sorted(n for n in names if isinstance(n, str) and n.endswith(".jsonl"))

    def get_file(self, branch: str, path: str) -> Remote | None:
        status, payload = self.request("GET", f"contents/{path}?ref={branch}")
        if status == 404:
            return None
        if status != 200 or not isinstance(payload, dict):
            raise HistoryError(f"reading {path} on {branch} failed: HTTP {status}")
        raw = payload.get("content")
        sha = payload.get("sha")
        if not isinstance(raw, str) or not isinstance(sha, str):
            raise HistoryError(f"{path} on {branch} is not a regular file")
        return Remote(base64.b64decode(raw).decode("utf-8"), sha)

    def branch_exists(self, branch: str) -> bool:
        status, _ = self.request("GET", f"git/ref/heads/{branch}")
        return status == 200

    def put_file(self, branch: str, path: str, text: str, sha: str | None, message: str) -> None:
        body: dict[str, object] = {
            "message": message,
            "content": base64.b64encode(text.encode()).decode(),
            "branch": branch,
        }
        if sha is not None:
            body["sha"] = sha
        status, payload = self.request("PUT", f"contents/{path}", body)
        if status in (200, 201):
            return
        # 409 is a stale `sha`; 422 is "sha not supplied" for a file that now
        # exists, i.e. a writer created it between our read and our write.
        if status in (409, 422):
            raise Conflict(f"{path} changed while writing: HTTP {status}")
        raise HistoryError(f"writing {path} failed: HTTP {status} {_message(payload)}")

    def create_branch(self, branch: str, path: str, text: str, message: str) -> None:
        """Create the branch as an ORPHAN root commit holding the first file."""
        status, tree = self.request(
            "POST",
            "git/trees",
            {"tree": [{"path": path, "mode": "100644", "type": "blob", "content": text}]},
        )
        tree_sha = tree.get("sha") if isinstance(tree, dict) else None
        if status != 201 or not isinstance(tree_sha, str):
            raise HistoryError(f"creating the history tree failed: HTTP {status}")
        status, commit = self.request(
            "POST", "git/commits", {"message": message, "tree": tree_sha, "parents": []}
        )
        commit_sha = commit.get("sha") if isinstance(commit, dict) else None
        if status != 201 or not isinstance(commit_sha, str):
            raise HistoryError(f"creating the history commit failed: HTTP {status}")
        status, _ = self.request(
            "POST", "git/refs", {"ref": f"refs/heads/{branch}", "sha": commit_sha}
        )
        if status == 422:
            raise Conflict(f"branch {branch} was created by another writer")
        if status != 201:
            raise HistoryError(f"creating branch {branch} failed: HTTP {status}")


def _loads(raw: bytes) -> object:
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def _message(payload: object) -> str:
    return str(payload.get("message", "")) if isinstance(payload, dict) else ""


def commits_in(text: str) -> set[str]:
    """Every commit a history file already has a row for."""
    seen: set[str] = set()
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        commit = row.get("commit") if isinstance(row, dict) else None
        if isinstance(commit, str):
            seen.add(commit)
    return seen


def month_path(row_line: str) -> str:
    """`history/YYYY-MM.jsonl` from the row's own UTC timestamp."""
    stamp = json.loads(row_line).get("timestamp")
    if not isinstance(stamp, str) or len(stamp) < 7:
        raise HistoryError("the new history row has no timestamp")
    return f"{HISTORY_DIR}/{stamp[:7]}.jsonl"


def fetch_all(client: Client, branch: str) -> str:
    """Every month's rows, oldest first, as one history file."""
    parts = []
    for name in client.list_months(branch):
        remote = client.get_file(branch, f"{HISTORY_DIR}/{name}")
        if remote is not None:
            parts.append(remote.content if remote.content.endswith("\n") else remote.content + "\n")
    return "".join(parts)


def append_row(
    client: Client, branch: str, row_line: str, commit: str | None, *, sleep: bool = True
) -> str:
    """Add `row_line` to its month's file, safely against a concurrent writer.

    Returns `"recorded"` or `"skipped"` (the commit already has a row). On a
    conflict only this one row is re-applied to a fresh read, so a lost race
    never drops the other writer's row or duplicates ours.
    """
    path = month_path(row_line)
    for attempt in range(ATTEMPTS):
        remote = client.get_file(branch, path)
        text = remote.content if remote is not None else ""
        if commit is not None and commit in commits_in(text):
            return "skipped"
        if text and not text.endswith("\n"):
            text += "\n"
        text += row_line + "\n"
        if len(text.encode()) > MAX_FILE_BYTES:
            raise HistoryError(f"{path} would exceed {MAX_FILE_BYTES} bytes; not writing it")
        message = f"archy: record score for {(commit or 'unknown')[:7]}"
        try:
            if remote is not None:
                client.put_file(branch, path, text, remote.sha, message)
            elif client.branch_exists(branch):
                client.put_file(branch, path, text, None, message)
            else:
                client.create_branch(branch, path, text, message)
            return "recorded"
        except Conflict:
            if attempt == ATTEMPTS - 1:
                break
            print(f"::notice::{path} changed under us; retrying ({attempt + 1}/{ATTEMPTS})")
            if sleep:
                time.sleep(random.uniform(0.2, 1.0) * (attempt + 1))
    raise HistoryError(f"gave up after {ATTEMPTS} conflicting writes to {path}")


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def head_commit(project: Path) -> str | None:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    out = run("git", "-C", str(project), "rev-parse", "HEAD")
    return out.stdout.strip() or None if out.returncode == 0 else None


def score_row(project: Path) -> str:
    """Score the project with `--record` and return the one row that added."""
    history = project / ".archy" / "history.jsonl"
    before = history.read_text().count("\n") if history.exists() else 0
    out = run("archy", "score", str(project), "--record")
    if out.returncode not in (0, 1):  # 1 is a failed --strict gate, not a failed run
        raise HistoryError(f"archy score failed: {out.stderr.strip() or out.stdout.strip()}")
    lines = history.read_text().splitlines() if history.exists() else []
    if len(lines) <= before:
        raise HistoryError("archy score --record did not append a row")
    return lines[-1]


def render(project: Path, html_out: Path, summary: Path | None, last: int) -> None:
    """The trend as a self-contained HTML file plus a short job-summary table."""
    table = run("archy", "trend", str(project), "--last", str(last), "--format", "text")
    page = run("archy", "render", str(project), "--view", "trend", "--out", str(html_out))
    if page.returncode != 0:
        print(f"::warning::archy render failed: {page.stderr.strip()}")
    if summary is not None:
        with summary.open("a", encoding="utf-8") as fh:
            fh.write(f"### archy score trend (last {last})\n\n```\n{table.stdout.rstrip()}\n```\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--branch", default="archy-history")
    parser.add_argument("--record", action="store_true", help="append this commit's score")
    parser.add_argument("--last", type=int, default=10)
    parser.add_argument("--html-out", type=Path, default=Path("archy-trend.html"))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required")
    if args.last < 1:
        parser.error("--last must be >= 1")

    token = os.environ.get("ARCHY_HISTORY_TOKEN") or None
    client = Client(os.environ.get("GITHUB_API_URL", API_DEFAULT), args.repo, token)
    summary_env = os.environ.get("GITHUB_STEP_SUMMARY")
    summary = Path(summary_env) if summary_env else None
    project: Path = args.project

    record = args.record
    if record and os.environ.get("GITHUB_EVENT_NAME") in PULL_REQUEST_EVENTS:
        print("::warning::not recording history on a pull request; it only records merges")
        record = False
    if record and not token:
        print("::warning::no ARCHY_HISTORY_TOKEN; reading history without recording")
        record = False

    try:
        text = fetch_all(client, args.branch)
        history = project / ".archy" / "history.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(text, encoding="utf-8")
        if record:
            commit = head_commit(project)
            if commit is not None and commit in commits_in(text):
                print(f"history already has a row for {commit[:7]}")
            else:
                row_line = score_row(project)
                print(append_row(client, args.branch, row_line, commit), "history row")
    except HistoryError as exc:
        print(f"::warning::archy history: {exc}")
    render(project, args.html_out, summary, args.last)
    return 0


if __name__ == "__main__":
    sys.exit(main())
