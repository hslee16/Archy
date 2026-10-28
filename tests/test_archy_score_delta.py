"""`scripts/archy_score_delta.py`: the sticky score comment on a PR (#604).

Expected numbers are worked by hand, not read back from the code: overall 0.6000 to
0.5700 is a change of -0.0300, which is past the 0.02 tolerance, so a regression.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "archy_score_delta.py"
_spec = importlib.util.spec_from_file_location("archy_score_delta", SCRIPT)
assert _spec is not None and _spec.loader is not None
asd = importlib.util.module_from_spec(_spec)
sys.modules["archy_score_delta"] = asd
_spec.loader.exec_module(asd)

AXES = {"modularity": 0.6, "acyclicity": 1.0, "depth": 0.5, "equality": 0.4, "complexity": 0.7}


def score(overall: float, **axes: float) -> dict[str, object]:
    return {"overall": overall, "components": {**AXES, **axes}}


def test_a_drop_past_the_tolerance_is_a_regression_with_hand_worked_numbers():
    body = asd.render(score(0.6000), score(0.5700, modularity=0.5500), "abc1234")
    assert "**Overall: 0.6000 → 0.5700 (-0.0300)** 🔴 **regression**" in body
    # modularity 0.6000 -> 0.5500 is -0.0500; the untouched axes are exactly 0.
    assert "| modularity | 0.6000 | 0.5500 | 🔻 -0.0500 |" in body
    assert f"| acyclicity | 1.0000 | 1.0000 | {asd.NO_CHANGE} 0.0000 |" in body
    assert "base (`abc1234`)" in body


@pytest.mark.parametrize(
    ("base", "head", "words"),
    [
        (0.6000, 0.5801, "slight drop"),  # -0.0199: under the tolerance
        (0.6000, 0.5799, "regression"),  # -0.0201: past it
        (0.5000, 0.5100, "improved"),
        (0.5000, 0.5003, "no change"),  # +0.0003 is inside the 0.0005 noise band
        (0.5000, 0.5006, "improved"),  # +0.0006 is outside it
        (0.5000, 0.4994, "slight drop"),  # -0.0006 is outside it, far under the tolerance
    ],
)
def test_the_verdict_follows_the_noise_band_and_the_tolerance(base: float, head: float, words: str):
    assert words in asd.render(score(base), score(head), "abc1234").splitlines()[3]


def test_the_boundary_cases_actually_straddle_the_thresholds():
    # Guard the guard: if the constants drift, the parametrised table above stops
    # testing what it says. The cases are chosen around these two numbers.
    assert asd.TOLERANCE == 0.02
    assert asd.NOISE == 0.0005


def test_a_missing_base_says_so_instead_of_inventing_a_delta():
    body = asd.render(None, score(0.5), "abc1234")
    assert "no comparable score on base `abc1234`" in body
    assert "Δ" not in body
    assert "| modularity | 0.6000 |" in body


def test_a_failed_head_run_is_stated_not_hidden():
    assert "Could not score this PR's head" in asd.render(score(0.5), None, "abc1234")


def test_an_axis_missing_from_one_side_is_n_a_not_a_crash():
    base = {"overall": 0.5, "components": {"modularity": 0.5}}
    body = asd.render(base, score(0.5), "abc1234")
    assert "| complexity | n/a | n/a | n/a |" in body
    assert "| modularity | 0.5000 | 0.6000 |" in body


@pytest.mark.parametrize("text", ["{}", "not json", '{"overall": "high"}', "[]"])
def test_load_treats_a_failed_or_malformed_run_as_missing(tmp_path: Path, text: str):
    path = tmp_path / "s.json"
    path.write_text(text)
    assert asd.load(str(path)) is None
    assert asd.load(str(tmp_path / "absent.json")) is None


class FakeGitHub:
    def __init__(self, comments: list[dict[str, object]], *, write_status: int = 201) -> None:
        self.comments = comments
        self.write_status = write_status
        self.calls: list[tuple[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def _send(self, status: int, payload: object) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self) -> dict[str, str]:
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n)) if n else {}

            def do_GET(self) -> None:
                outer.calls.append(("GET", self.path))
                page = int(self.path.split("page=")[-1])
                self._send(200, outer.comments[(page - 1) * 100 : page * 100])

            def do_POST(self) -> None:
                outer.calls.append(("POST", self.path))
                body = self._body()
                if outer.write_status == 201:
                    outer.comments.append({"id": 999, "user": {"login": asd.BOT}, **body})
                self._send(outer.write_status, {})

            def do_PATCH(self) -> None:
                outer.calls.append(("PATCH", self.path))
                body = self._body()
                cid = int(self.path.rsplit("/", 1)[-1])
                for c in outer.comments:
                    if c["id"] == cid:
                        c["body"] = body["body"]
                self._send(200, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def bot_comment(cid: int, body: str) -> dict[str, object]:
    return {"id": cid, "user": {"login": asd.BOT}, "body": body}


@pytest.fixture
def make_gh():
    made: list[FakeGitHub] = []

    def make(comments: list[dict[str, object]], **kw: int) -> FakeGitHub:
        fake = FakeGitHub(comments, **kw)
        made.append(fake)
        return fake

    yield make
    for fake in made:
        fake.close()


def client(gh: FakeGitHub) -> object:
    return asd.Client(gh.url, "o/r", "tok")


def test_the_first_run_creates_the_comment(make_gh):
    gh = make_gh([{"id": 1, "user": {"login": "alice"}, "body": "LGTM"}])
    assert asd.upsert(client(gh), 7, f"{asd.MARKER}\nhello") == "created"
    assert [c[0] for c in gh.calls if c[0] != "GET"] == ["POST"]


def test_a_later_run_updates_the_same_comment_instead_of_adding_another(make_gh):
    gh = make_gh([bot_comment(5, f"{asd.MARKER}\nold")])
    assert asd.upsert(client(gh), 7, f"{asd.MARKER}\nnew") == "updated"
    assert [c for c in gh.calls if c[0] == "PATCH"] == [("PATCH", "/repos/o/r/issues/comments/5")]
    assert [c["body"] for c in gh.comments] == [f"{asd.MARKER}\nnew"]


def test_someone_elses_comment_containing_the_marker_is_never_edited(make_gh):
    # Anyone can paste the marker. Matching on it alone would try to edit their
    # comment (and fail), and our own would never update.
    gh = make_gh([{"id": 3, "user": {"login": "mallory"}, "body": f"{asd.MARKER} spoof"}])
    assert asd.upsert(client(gh), 7, f"{asd.MARKER}\nreal") == "created"
    assert not [c for c in gh.calls if c[0] == "PATCH"]


def test_another_bot_comment_without_the_marker_is_left_alone(make_gh):
    # CI posts other bot comments (coverage, previews). Matching on author alone
    # would overwrite one of those with the score.
    gh = make_gh([bot_comment(4, "coverage: 91%")])
    assert asd.upsert(client(gh), 7, f"{asd.MARKER}\nscore") == "created"
    assert not [c for c in gh.calls if c[0] == "PATCH"]
    assert gh.comments[0]["body"] == "coverage: 91%"


def test_the_comment_is_found_past_the_first_page(make_gh):
    filler = [{"id": i, "user": {"login": "alice"}, "body": "x"} for i in range(100)]
    gh = make_gh([*filler, bot_comment(500, f"{asd.MARKER}\nold")])
    assert len(gh.comments) == 101  # the fixture must need a second page
    assert asd.upsert(client(gh), 7, f"{asd.MARKER}\nnew") == "updated"
    assert ("PATCH", "/repos/o/r/issues/comments/500") in gh.calls


def test_a_fork_pr_that_cannot_write_gets_the_summary_and_a_clean_exit(
    make_gh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    gh = make_gh([], write_status=403)
    base, head = tmp_path / "b.json", tmp_path / "h.json"
    base.write_text(json.dumps(score(0.6)))
    head.write_text(json.dumps(score(0.57)))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_API_URL", gh.url)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert asd.main([str(base), str(head), "abcdef123456", "--pr", "7"]) == 0
    assert "regression" in summary.read_text()
    assert asd.MARKER not in summary.read_text()  # the marker is for the comment, not the page
    assert "no PR comment written" in capsys.readouterr().out
    assert [c for c in gh.calls if c[0] == "POST"], "the write must have been attempted"


def test_without_a_pr_number_it_writes_only_the_summary(
    make_gh, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    gh = make_gh([])
    base = tmp_path / "b.json"
    base.write_text(json.dumps(score(0.5)))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("GITHUB_API_URL", gh.url)
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert asd.main([str(base), str(base), "abc1234"]) == 0
    assert gh.calls == []
    assert (tmp_path / "summary.md").exists()
