"""Post archy's score change for a pull request as ONE sticky comment (#604).

Compares two `archy score --format json` runs, the base commit and the PR head,
renders the overall and per-axis change as Markdown, and upserts it as a single
comment keyed by a hidden marker, so a PR carries one comment that is updated in
place rather than one per push. Standard library only, so it runs under
`uv run --no-project`.

It reports and never gates: a drop past the tolerance is flagged in the text, not
turned into a failure. Where a comment cannot be written (a fork PR's token is
read-only) the same Markdown goes to the job summary instead, so the number is
still visible. Both runs must be scored by the SAME archy, or the delta measures
the scorer and not the code; the workflow scores both with the checkout's own.

Usage:
    python archy_score_delta.py BASE.json HEAD.json BASE_SHA --pr 123

archy:owns        Client, Unavailable, find_comment, load, main, render, upsert
archy:mirrored-by Client also in scripts.archy_history
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "<!-- archy-score-delta -->"
BOT = "github-actions[bot]"
AXES = ("modularity", "acyclicity", "depth", "equality", "complexity")
# Below this absolute overall change the score counts as unchanged: it is noise.
NOISE = 0.0005
# A drop past this is flagged as a regression. Mirrors `archy score --strict`'s
# default tolerance, so the comment and the gate agree on what counts.
TOLERANCE = 0.02
API_DEFAULT = "https://api.github.com"
NO_CHANGE = "\N{HEAVY MINUS SIGN}"


def load(path: str) -> dict[str, object] | None:
    """The score JSON, or None for a missing, malformed, or failed run (`{}`)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("overall"), float | int):
        return data
    return None


def _components(score: dict[str, object]) -> dict[str, float]:
    raw = score.get("components")
    if not isinstance(raw, dict):
        return {}
    return {k: float(v) for k, v in raw.items() if isinstance(v, float | int)}


def _overall(score: dict[str, object]) -> float:
    value = score["overall"]
    return float(value) if isinstance(value, float | int) else 0.0


def _delta(d: float) -> str:
    return f"+{d:.4f}" if d > 0 else f"{d:.4f}"


def _arrow(d: float) -> str:
    if abs(d) < NOISE:
        return NO_CHANGE
    return "🔺" if d > 0 else "🔻"


def _verdict(d: float) -> str:
    if d <= -TOLERANCE:
        return f"🔴 **regression** (drop of {TOLERANCE:g} or more)"
    if d < -NOISE:
        return "⚠️ slight drop"
    if d > NOISE:
        return "✅ improved"
    return f"{NO_CHANGE} no change"


def render(base: dict[str, object] | None, head: dict[str, object] | None, base_sha: str) -> str:
    """The comment body. Says so, in words, when a number could not be computed."""
    lines = [MARKER, "## 🏛️ Architecture score (archy)", ""]
    if head is None:
        lines.append("⚠️ Could not score this PR's head (the archy run failed). See the job log.")
        return "\n".join(lines) + "\n"
    head_overall = _overall(head)
    head_axes = _components(head)
    if base is None:
        lines += [
            f"**Overall: {head_overall:.4f}** (no comparable score on base `{base_sha}`)",
            "",
            "| Axis | this PR |",
            "|------|--------:|",
        ]
        lines += [f"| {a} | {head_axes[a]:.4f} |" for a in AXES if a in head_axes]
        return "\n".join(lines) + "\n"
    base_overall = _overall(base)
    base_axes = _components(base)
    change = head_overall - base_overall
    lines += [
        f"**Overall: {base_overall:.4f} → {head_overall:.4f} ({_delta(change)})** "
        f"{_verdict(change)}",
        "",
        f"| Axis | base (`{base_sha}`) | this PR | Δ |",
        "|------|--------:|--------:|--------:|",
    ]
    for axis in AXES:
        if axis not in base_axes or axis not in head_axes:
            lines.append(f"| {axis} | n/a | n/a | n/a |")
            continue
        d = head_axes[axis] - base_axes[axis]
        lines.append(
            f"| {axis} | {base_axes[axis]:.4f} | {head_axes[axis]:.4f} | {_arrow(d)} {_delta(d)} |"
        )
    lines += [
        "",
        "_Both scored by this PR's archy, so the delta is the code and not the scorer. "
        f"Non-blocking; a drop of {TOLERANCE:g} or more overall is flagged. Higher is better._",
    ]
    return "\n".join(lines) + "\n"


class Unavailable(Exception):
    """The comment could not be written (read-only token, no PR, network)."""


class Client:
    def __init__(self, api: str, repo: str, token: str) -> None:
        self.api = api.rstrip("/")
        self.repo = repo
        self.token = token

    def request(
        self, method: str, path: str, body: dict[str, str] | None = None
    ) -> tuple[int, object]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "archy-score-delta",
        }
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.api}/repos/{self.repo}/{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            return exc.code, None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise Unavailable(f"could not reach {self.api}: {exc}") from exc


def find_comment(client: Client, pr: int) -> int | None:
    """The id of OUR marker comment. Authorship is checked, so a comment from anyone
    else that merely contains the marker can neither be edited nor hide ours."""
    page = 1
    while True:
        status, payload = client.request("GET", f"issues/{pr}/comments?per_page=100&page={page}")
        if status != 200 or not isinstance(payload, list):
            raise Unavailable(f"listing PR comments failed: HTTP {status}")
        for comment in payload:
            user = comment.get("user") if isinstance(comment, dict) else None
            if (
                isinstance(comment, dict)
                and isinstance(user, dict)
                and user.get("login") == BOT
                and MARKER in str(comment.get("body", ""))
            ):
                return int(comment["id"])
        if len(payload) < 100:
            return None
        page += 1


def upsert(client: Client, pr: int, body: str) -> str:
    existing = find_comment(client, pr)
    if existing is None:
        status, _ = client.request("POST", f"issues/{pr}/comments", {"body": body})
        verb = "created"
    else:
        status, _ = client.request("PATCH", f"issues/comments/{existing}", {"body": body})
        verb = "updated"
    if status not in (200, 201):
        raise Unavailable(f"writing the comment failed: HTTP {status}")
    return verb


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("base_json")
    parser.add_argument("head_json")
    parser.add_argument("base_sha")
    parser.add_argument("--pr", type=int, default=None)
    args = parser.parse_args(argv)

    body = render(load(args.base_json), load(args.head_json), args.base_sha[:7])
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(body.replace(MARKER + "\n", "", 1))

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if args.pr is None or not token or not repo:
        print("::notice::no PR comment written (no PR number or token); see the job summary")
        return 0
    client = Client(os.environ.get("GITHUB_API_URL", API_DEFAULT), repo, token)
    try:
        print(f"{upsert(client, args.pr, body)} the archy score comment")
    except Unavailable as exc:
        # Normal on a fork PR, whose token cannot write. The job summary has it.
        print(f"::notice::no PR comment written ({exc}); see the job summary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
