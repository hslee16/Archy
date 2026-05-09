from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from archy.history import (
    HistoryRow,
    append,
    git_metadata,
    read,
    row_from_score,
)
from archy.score import Score, ScoreInputs


def _score(overall: float = 0.5) -> Score:
    return Score(
        overall=overall,
        modularity=0.6,
        acyclicity=0.5,
        depth=0.7,
        equality=0.4,
        inputs=ScoreInputs(
            module_count=10,
            edge_count=20,
            cycle_count=1,
            max_depth=2,
            community_count=3,
            raw_modularity=0.4,
            raw_gini=0.4,
        ),
    )


def test_read_missing_file_returns_empty(tmp_path: Path):
    assert read(tmp_path / "missing.jsonl") == []


def test_append_creates_parent_dir_and_writes_one_line(tmp_path: Path):
    history = tmp_path / "deep" / "nested" / "history.jsonl"
    row = row_from_score(_score(0.5), commit="abc1234", branch="main")
    append(history, row)
    assert history.exists()
    [line] = history.read_text().splitlines()
    payload = json.loads(line)
    assert payload["score"]["overall"] == 0.5
    assert payload["commit"] == "abc1234"


def test_round_trip_preserves_rows(tmp_path: Path):
    history = tmp_path / "history.jsonl"
    rows = [
        row_from_score(_score(0.4), commit="a", branch="main"),
        row_from_score(_score(0.6), commit="b", branch="main"),
        row_from_score(_score(0.5), commit="c", branch="dev"),
    ]
    for row in rows:
        append(history, row)
    out = read(history)
    assert [r.overall for r in out] == [0.4, 0.6, 0.5]
    assert [r.branch for r in out] == ["main", "main", "dev"]


def test_malformed_lines_are_skipped(tmp_path: Path):
    history = tmp_path / "history.jsonl"
    good = row_from_score(_score(0.7), commit="a", branch="main")
    append(history, good)
    with history.open("a") as fh:
        fh.write("{not valid json\n")
        fh.write("\n")
        fh.write('{"timestamp": "x", "score": "wrong"}\n')
    append(history, row_from_score(_score(0.8), commit="b", branch="main"))
    rows = read(history)
    assert [r.overall for r in rows] == [0.7, 0.8]


def test_row_from_score_uses_injected_now():
    fixed = dt.datetime(2026, 5, 9, 13, 45, 7, tzinfo=dt.timezone.utc)
    row = row_from_score(_score(0.42), commit="abc", branch="main", now=fixed)
    assert row.timestamp == "2026-05-09T13:45:07Z"


def test_git_metadata_on_non_git_dir(tmp_path: Path):
    commit, branch = git_metadata(tmp_path)
    assert commit is None
    assert branch is None


def test_git_metadata_on_fresh_repo(tmp_path: Path):
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "test"],
        check=True,
        capture_output=True,
    )
    (tmp_path / "x.txt").write_text("hi\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "x.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    commit, branch = git_metadata(tmp_path)
    assert commit is not None
    assert len(commit) == 40
    assert branch == "main"


def test_history_row_is_frozen_dataclass():
    # @dataclass(frozen=True) enforces immutability at runtime; this test
    # documents the intent. (ty also rejects assignment statically.)
    assert HistoryRow.__dataclass_params__.frozen is True
