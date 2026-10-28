"""`scripts/archy_history.py` against a fake GitHub REST server (#605).

The fake enforces what the real contents API does and the script relies on: a PUT
over an existing file needs the `sha` it was read at (409 when stale, 422 when
omitted), a missing branch is a 404, and a ref that already exists is a 422.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "archy_history.py"
_spec = importlib.util.spec_from_file_location("archy_history", SCRIPT)
assert _spec is not None and _spec.loader is not None
ah = importlib.util.module_from_spec(_spec)
sys.modules["archy_history"] = ah
_spec.loader.exec_module(ah)

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
COMMIT_C = "c" * 40


def row(commit: str, month: str = "2026-10", day: str = "28", overall: float = 0.5) -> str:
    return json.dumps(
        {"commit": commit, "timestamp": f"{month}-{day}T12:00:00Z", "score": {"overall": overall}},
        sort_keys=True,
    )


class FakeGitHub:
    """An in-memory repo: one branch's files, plus the call log."""

    def __init__(self) -> None:
        self.branch_exists = False
        self.files: dict[str, tuple[str, str]] = {}  # path -> (text, sha)
        self.calls: list[tuple[str, str]] = []
        self.auth_headers: list[str | None] = []
        self.created_commits: list[dict[str, object]] = []
        self.before_put: Callable[[FakeGitHub], None] | None = None
        self.before_ref: Callable[[FakeGitHub], None] | None = None
        self._n = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def set_file(self, path: str, text: str) -> None:
        self._n += 1
        self.files[path] = (text, f"sha{self._n}")
        self.branch_exists = True

    def text(self, path: str) -> str:
        return self.files[path][0]

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def _send(self, status: int, payload: object) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> dict[str, object]:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else {}

            def do_GET(self) -> None:
                outer.calls.append(("GET", self.path))
                outer.auth_headers.append(self.headers.get("Authorization"))
                path = self.path.split("?")[0].removeprefix("/repos/o/r/")
                if path == "contents/history":
                    names = sorted(p.split("/")[-1] for p in outer.files)
                    if not outer.branch_exists or not names:
                        return self._send(404, {"message": "Not Found"})
                    return self._send(200, [{"name": n, "type": "file"} for n in names])
                if path.startswith("contents/"):
                    entry = outer.files.get(path.removeprefix("contents/"))
                    if entry is None:
                        return self._send(404, {"message": "Not Found"})
                    text, sha = entry
                    return self._send(
                        200, {"content": base64.b64encode(text.encode()).decode(), "sha": sha}
                    )
                if path.startswith("git/ref/heads/"):
                    return self._send(200 if outer.branch_exists else 404, {})
                return self._send(404, {})

            def do_PUT(self) -> None:
                outer.calls.append(("PUT", self.path))
                outer.auth_headers.append(self.headers.get("Authorization"))
                body = self._body()
                if outer.before_put is not None:
                    hook, outer.before_put = outer.before_put, None
                    hook(outer)
                path = self.path.split("?")[0].removeprefix("/repos/o/r/contents/")
                if not outer.branch_exists:
                    return self._send(404, {"message": "Branch not found"})
                text = base64.b64decode(str(body["content"])).decode()
                current = outer.files.get(path)
                if current is not None and "sha" not in body:
                    return self._send(422, {"message": "sha wasn't supplied"})
                if current is not None and body["sha"] != current[1]:
                    return self._send(409, {"message": f"{path} does not match"})
                outer.set_file(path, text)
                return self._send(200 if current else 201, {})

            def do_POST(self) -> None:
                outer.calls.append(("POST", self.path))
                body = self._body()
                tail = self.path.split("/")[-1]
                if tail == "trees":
                    entries = body["tree"]
                    assert isinstance(entries, list)
                    entry = cast(dict[str, str], entries[0])
                    outer._pending_tree = (entry["path"], entry["content"])
                    return self._send(201, {"sha": "tree1"})
                if tail == "commits":
                    outer.created_commits.append(body)
                    return self._send(201, {"sha": "commit1"})
                if tail == "refs":
                    if outer.before_ref is not None:
                        hook, outer.before_ref = outer.before_ref, None
                        hook(outer)
                    if outer.branch_exists:
                        return self._send(422, {"message": "Reference already exists"})
                    outer.branch_exists = True
                    tree = outer._pending_tree
                    outer.set_file(tree[0], tree[1])
                    return self._send(201, {})
                return self._send(404, {})

        return Handler

    _pending_tree: tuple[str, str] = ("", "")


@pytest.fixture
def gh():
    fake = FakeGitHub()
    yield fake
    fake.close()


def client(gh: FakeGitHub, token: str | None = "t0ken"):
    return ah.Client(gh.url, "o/r", token)


def test_first_record_creates_an_orphan_branch_holding_one_row(gh: FakeGitHub):
    line = row(COMMIT_A)
    assert not gh.branch_exists  # the fixture must start without the branch
    result = ah.append_row(client(gh), "archy-history", line, COMMIT_A, sleep=False)
    assert result == "recorded"
    assert gh.text("history/2026-10.jsonl") == line + "\n"
    [commit] = gh.created_commits
    assert commit["parents"] == []  # an orphan root, sharing no history with the code
    assert "aaaaaaa" in str(commit["message"])


def test_a_second_record_appends_in_order_to_the_same_month(gh: FakeGitHub):
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A, day="01") + "\n")
    assert ah.append_row(client(gh), "b", row(COMMIT_B, day="02"), COMMIT_B, sleep=False) == (
        "recorded"
    )
    lines = gh.text("history/2026-10.jsonl").splitlines()
    assert [json.loads(x)["commit"] for x in lines] == [COMMIT_A, COMMIT_B]
    assert [c for c in gh.calls if c[0] == "PUT"], "an existing file is updated, not recreated"


def test_the_same_commit_is_not_recorded_twice(gh: FakeGitHub):
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A) + "\n")
    before = gh.text("history/2026-10.jsonl")
    assert ah.append_row(client(gh), "b", row(COMMIT_A), COMMIT_A, sleep=False) == "skipped"
    assert gh.text("history/2026-10.jsonl") == before
    assert not [c for c in gh.calls if c[0] == "PUT"]


def test_a_losing_race_reapplies_only_its_own_row_on_a_fresh_read(gh: FakeGitHub):
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A, day="01") + "\n")
    raced: list[bool] = []

    def other_merge_lands_first(g: FakeGitHub) -> None:
        raced.append(True)
        g.set_file("history/2026-10.jsonl", g.text("history/2026-10.jsonl") + row(COMMIT_B) + "\n")

    gh.before_put = other_merge_lands_first
    result = ah.append_row(client(gh), "b", row(COMMIT_C, day="03"), COMMIT_C, sleep=False)
    assert raced == [True], "the fixture must actually race the write"
    assert result == "recorded"
    commits = [json.loads(x)["commit"] for x in gh.text("history/2026-10.jsonl").splitlines()]
    assert commits == [COMMIT_A, COMMIT_B, COMMIT_C]  # nobody's row lost, none duplicated
    assert len([c for c in gh.calls if c[0] == "PUT"]) == 2


def test_a_writer_creating_this_months_file_first_is_a_conflict_not_an_error(gh: FakeGitHub):
    # The branch exists (another month is on it) and our month's file does not, so
    # we PUT without a `sha`. Another merge creates the file first: GitHub answers
    # 422, which must be retried as an append, not reported as a failure.
    gh.set_file("history/2026-09.jsonl", row(COMMIT_A, "2026-09", "30") + "\n")
    created: list[bool] = []

    def other_merge_creates_this_month(g: FakeGitHub) -> None:
        created.append(True)
        g.set_file("history/2026-10.jsonl", row(COMMIT_B) + "\n")

    gh.before_put = other_merge_creates_this_month
    result = ah.append_row(client(gh), "b", row(COMMIT_C), COMMIT_C, sleep=False)
    assert created == [True], "the fixture must actually race the create"
    assert result == "recorded"
    commits = [json.loads(x)["commit"] for x in gh.text("history/2026-10.jsonl").splitlines()]
    assert commits == [COMMIT_B, COMMIT_C]


def test_losing_the_branch_creation_race_retries_as_an_append(gh: FakeGitHub):
    def other_writer_creates_the_branch(g: FakeGitHub) -> None:
        g.set_file("history/2026-10.jsonl", row(COMMIT_B) + "\n")

    gh.before_ref = other_writer_creates_the_branch
    result = ah.append_row(client(gh), "b", row(COMMIT_C), COMMIT_C, sleep=False)
    assert result == "recorded"
    commits = [json.loads(x)["commit"] for x in gh.text("history/2026-10.jsonl").splitlines()]
    assert commits == [COMMIT_B, COMMIT_C]


def test_it_gives_up_loudly_when_every_attempt_conflicts(gh: FakeGitHub):
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A) + "\n")

    def always_stale(g: FakeGitHub) -> None:
        g.set_file("history/2026-10.jsonl", g.text("history/2026-10.jsonl") + row(COMMIT_B) + "\n")
        g.before_put = always_stale

    gh.before_put = always_stale
    with pytest.raises(ah.HistoryError, match="gave up after 5"):
        ah.append_row(client(gh), "b", row(COMMIT_C), COMMIT_C, sleep=False)


def test_a_month_that_would_exceed_the_size_cap_is_not_written(gh: FakeGitHub):
    big = ("x" * 1000 + "\n") * 905
    gh.set_file("history/2026-10.jsonl", big)
    with pytest.raises(ah.HistoryError, match="would exceed"):
        ah.append_row(client(gh), "b", row(COMMIT_A), COMMIT_A, sleep=False)
    assert gh.text("history/2026-10.jsonl") == big


def test_a_new_month_starts_a_new_file_and_reads_come_back_oldest_first(gh: FakeGitHub):
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A, "2026-10", "30") + "\n")
    ah.append_row(client(gh), "b", row(COMMIT_B, "2026-11", "01"), COMMIT_B, sleep=False)
    assert sorted(gh.files) == ["history/2026-10.jsonl", "history/2026-11.jsonl"]
    everything = ah.fetch_all(client(gh), "b")
    assert [json.loads(x)["commit"] for x in everything.splitlines()] == [COMMIT_A, COMMIT_B]


def test_reading_needs_no_token_and_writing_sends_one(gh: FakeGitHub):
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A) + "\n")
    ah.fetch_all(client(gh, token=None), "b")
    assert set(gh.auth_headers) == {None}
    gh.auth_headers.clear()
    ah.append_row(client(gh, token="t0ken"), "b", row(COMMIT_B), COMMIT_B, sleep=False)
    assert set(gh.auth_headers) == {"Bearer t0ken"}


def test_an_unparseable_line_in_history_does_not_hide_the_good_ones():
    text = row(COMMIT_A) + "\nnot json\n" + row(COMMIT_B) + "\n"
    assert ah.commits_in(text) == {COMMIT_A, COMMIT_B}


FAKE_ARCHY = """#!/bin/bash
# score appends one row to <path>/.archy/history.jsonl; trend and render print fixed output.
cmd="$1"; shift
case "$cmd" in
  score)
    path="$1"; mkdir -p "$path/.archy"
    row='{"commit": "'"${GITHUB_SHA}"'", "timestamp": "2026-10-28T12:00:00Z"}'
    printf '%s\\n' "$row" >> "$path/.archy/history.jsonl"
    ;;
  trend)
    n=$(wc -l < "$1/.archy/history.jsonl" | tr -d ' ')
    echo "TREND-TABLE rows=$n"
    ;;
  render)
    out=""
    while [ $# -gt 0 ]; do
      [ "$1" = "--out" ] && out="$2"
      shift
    done
    echo "<html>trend</html>" > "$out"
    ;;
esac
"""


@pytest.fixture
def cli_env(tmp_path: Path, gh: FakeGitHub, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "archy"
    fake.write_text(FAKE_ARCHY)
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("GITHUB_API_URL", gh.url)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_SHA", COMMIT_B)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("ARCHY_HISTORY_TOKEN", "t0ken")
    project = tmp_path / "proj"
    project.mkdir()
    return project


def main_args(project: Path, *extra: str) -> list[str]:
    return ["--project", str(project), "--html-out", str(project / "trend.html"), *extra]


def test_main_records_a_merge_seeds_the_local_history_and_writes_the_summary(
    cli_env: Path, gh: FakeGitHub, tmp_path: Path
):
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A) + "\n")
    assert ah.main(main_args(cli_env, "--record")) == 0
    remote = [json.loads(x)["commit"] for x in gh.text("history/2026-10.jsonl").splitlines()]
    assert remote == [COMMIT_A, COMMIT_B]
    local = (cli_env / ".archy" / "history.jsonl").read_text().splitlines()
    assert [json.loads(x)["commit"] for x in local] == [COMMIT_A, COMMIT_B]
    assert "TREND-TABLE rows=2" in (tmp_path / "summary.md").read_text()
    assert (cli_env / "trend.html").read_text().strip() == "<html>trend</html>"


def test_main_does_not_record_twice_for_a_rerun_of_the_same_commit(cli_env: Path, gh: FakeGitHub):
    gh.set_file("history/2026-10.jsonl", row(COMMIT_B) + "\n")
    ah.main(main_args(cli_env, "--record"))
    assert not [c for c in gh.calls if c[0] == "PUT"]
    assert len(gh.text("history/2026-10.jsonl").splitlines()) == 1


def test_main_never_records_on_a_pull_request(
    cli_env: Path, gh: FakeGitHub, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A) + "\n")
    assert ah.main(main_args(cli_env, "--record")) == 0
    assert len(gh.text("history/2026-10.jsonl").splitlines()) == 1
    assert "not recording history on a pull request" in capsys.readouterr().out


def test_main_without_a_token_reads_but_does_not_write(
    cli_env: Path, gh: FakeGitHub, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.delenv("ARCHY_HISTORY_TOKEN")
    gh.set_file("history/2026-10.jsonl", row(COMMIT_A) + "\n")
    assert ah.main(main_args(cli_env, "--record")) == 0
    assert len(gh.text("history/2026-10.jsonl").splitlines()) == 1
    assert "no ARCHY_HISTORY_TOKEN" in capsys.readouterr().out


def test_an_unreachable_api_is_a_warning_not_a_failed_build(
    cli_env: Path, gh: FakeGitHub, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    monkeypatch.setenv("GITHUB_API_URL", "http://127.0.0.1:9")  # nothing listens on 9
    assert ah.main(main_args(cli_env, "--record")) == 0
    out = capsys.readouterr().out
    assert "::warning::archy history: could not reach" in out
    assert re.search(r"trend\.html", str(cli_env / "trend.html"))  # render still ran
    assert (cli_env / "trend.html").exists()
