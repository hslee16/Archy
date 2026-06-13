from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from archy.history import (
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
        complexity=0.55,
        inputs=ScoreInputs(
            module_count=10,
            edge_count=20,
            cycle_count=1,
            tangle_ratio=0.2,
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


def test_pre_v0_20_row_without_complexity_reads_as_none(tmp_path: Path):
    # Backwards-compat: a JSONL row written by archy < 0.20 has no
    # `complexity` key in the score dict. We accept it and surface
    # `complexity=None` rather than refusing the whole row.
    history = tmp_path / "history.jsonl"
    legacy_payload = {
        "timestamp": "2025-12-01T12:00:00Z",
        "commit": "deadbeef",
        "branch": "main",
        "score": {
            "overall": 0.55,
            "modularity": 0.6,
            "acyclicity": 0.5,
            "depth": 0.7,
            "equality": 0.4,
        },
        "inputs": {
            "module_count": 10,
            "edge_count": 20,
            "cycle_count": 1,
            "tangle_ratio": 0.2,
            "max_depth": 2,
            "community_count": 3,
        },
    }
    history.write_text(json.dumps(legacy_payload) + "\n")
    rows = read(history)
    assert len(rows) == 1
    assert rows[0].overall == 0.55
    assert rows[0].complexity is None


def test_pre_v0_7_row_without_tangle_ratio_reads_as_zero(tmp_path: Path):
    # Backwards-compat: a JSONL row written by archy < 0.7.x has no
    # `tangle_ratio` key in inputs. We accept it and default to 0.0 rather
    # than refusing the whole row. (Parallels the pre-v0.20 complexity test.)
    history = tmp_path / "history.jsonl"
    legacy_payload = {
        "timestamp": "2025-06-01T12:00:00Z",
        "commit": "feedface",
        "branch": "main",
        "score": {
            "overall": 0.5,
            "modularity": 0.6,
            "acyclicity": 0.5,
            "depth": 0.7,
            "equality": 0.4,
            "complexity": 0.55,
        },
        "inputs": {
            "module_count": 10,
            "edge_count": 20,
            "cycle_count": 1,
            "max_depth": 2,
            "community_count": 3,
        },
    }
    history.write_text(json.dumps(legacy_payload) + "\n")
    rows = read(history)
    assert len(rows) == 1
    assert rows[0].tangle_ratio == 0.0


def test_float_encoded_integer_fields_are_accepted(tmp_path: Path):
    # JSON has no distinct integer type, so exporters / manual edits often
    # write count fields as `10.0`. Those rows must read, not be silently
    # dropped. A fractional value, however, is corruption and is rejected.
    history = tmp_path / "history.jsonl"
    base = {
        "timestamp": "2026-06-13T00:00:00Z",
        "commit": None,
        "branch": None,
        "score": {
            "overall": 0.5,
            "modularity": 0.6,
            "acyclicity": 0.5,
            "depth": 0.7,
            "equality": 0.4,
            "complexity": 0.55,
        },
        "inputs": {
            "module_count": 10.0,
            "edge_count": 20.0,
            "cycle_count": 0.0,
            "tangle_ratio": 0.2,
            "max_depth": 2.0,
            "community_count": 3.0,
        },
    }
    history.write_text(json.dumps(base) + "\n")
    rows = read(history)
    assert len(rows) == 1
    assert rows[0].module_count == 10
    assert isinstance(rows[0].module_count, int)

    # A fractional count is not a whole number: corruption, row dropped.
    base["inputs"]["module_count"] = 10.5
    history.write_text(json.dumps(base) + "\n")
    assert read(history) == []


def test_present_but_corrupt_complexity_drops_row(tmp_path: Path):
    # Distinct from a *missing* complexity (older row -> None, kept): a
    # present-but-non-numeric value is corruption and drops the row, the same
    # as any other corrupt field, instead of being disguised as None.
    history = tmp_path / "history.jsonl"
    payload = {
        "timestamp": "2026-06-13T00:00:00Z",
        "commit": None,
        "branch": None,
        "score": {
            "overall": 0.5,
            "modularity": 0.6,
            "acyclicity": 0.5,
            "depth": 0.7,
            "equality": 0.4,
            "complexity": "not-a-number",
        },
        "inputs": {
            "module_count": 10,
            "edge_count": 20,
            "cycle_count": 1,
            "tangle_ratio": 0.2,
            "max_depth": 2,
            "community_count": 3,
        },
    }
    history.write_text(json.dumps(payload) + "\n")
    assert read(history) == []


def test_append_record_is_newline_terminated(tmp_path: Path):
    # Each append must write a complete, newline-terminated line so a later
    # append never merges onto a previous record (the crash-mid-write data
    # loss path). N appends -> N parseable lines, file ends with a newline.
    history = tmp_path / "history.jsonl"
    append(history, row_from_score(_score(0.4), commit="a", branch="main"))
    append(history, row_from_score(_score(0.6), commit="b", branch="main"))
    text = history.read_text()
    assert text.endswith("\n")
    assert len(text.splitlines()) == 2
    assert [r.overall for r in read(history)] == [0.4, 0.6]


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
