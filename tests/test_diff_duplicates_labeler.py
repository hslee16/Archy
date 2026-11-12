"""Tests for labeler B: checkpoint, pause/resume, usage-limit handling, blindness.

No model is called: `label` and `sleep` are injected fakes, because what needs
testing is the loop's failure handling and what the prompt carries, not the model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import diff_duplicates_labeler as lb  # ty: ignore[unresolved-import]  (added to sys.path above)
import diff_duplicates_precision as ddp  # ty: ignore[unresolved-import]  (added to sys.path above)
from _supervise import Ledger  # ty: ignore[unresolved-import]  (added to sys.path above)
from q1b_run import RateLimited  # ty: ignore[unresolved-import]  (added to sys.path above)

ONE = lb.LabelOptions(workers=1)


def _packets(tmp_path: Path, ids: list[str]) -> Path:
    out = tmp_path / "labels"
    (out / "packets").mkdir(parents=True)
    key = {}
    for fid in ids:
        key[fid] = {"head": "h" * 40}
        packet = {"id": fid, "repo": "o/r", "instructions": "RUBRIC", "new": [], "existing": []}
        (out / "packets" / f"{fid.replace(':', '_')}.json").write_text(json.dumps(packet))
    (out / "key.json").write_text(json.dumps(key))
    return out


def _ok(packet: dict, head: str) -> dict:
    return {"label": "TRUE", "reason": None, "note": "n"}


def _rows(out: Path) -> list[dict]:
    return [json.loads(x) for x in (out / "labels_B.jsonl").read_text().splitlines()]


def test_resume_skips_ok_rows_and_retries_errors(tmp_path: Path) -> None:
    out = _packets(tmp_path, ["1:0", "2:0", "3:0"])
    ledger = Ledger(out / "labels_B.jsonl")
    ledger.record("1:0", {"id": "1:0", "label": "FALSE", "reason": "sibling"})
    ledger.record("2:0", {"id": "2:0", "error": "stalled"}, status="error")
    seen: list[str] = []

    def label(packet: dict, head: str) -> dict:
        seen.append(packet["id"])
        return _ok(packet, head)

    assert lb.label_all(out, label=label, options=ONE) == 0
    assert sorted(seen) == ["2:0", "3:0"]  # the ok row is not re-labeled; the error is retried


def test_a_crash_in_one_firing_does_not_end_the_run(tmp_path: Path) -> None:
    out = _packets(tmp_path, ["1:0", "2:0", "3:0"])

    def label(packet: dict, head: str) -> dict:
        if packet["id"] == "2:0":
            raise ValueError("unparseable labeler answer")
        return _ok(packet, head)

    lb.label_all(out, label=label, options=ONE)
    by_id = {r["id"]: r for r in _rows(out)}
    assert by_id["1:0"]["status"] == by_id["3:0"]["status"] == "ok"
    assert by_id["2:0"]["status"] == "error"


def test_pause_stops_new_work_and_resume_finishes(tmp_path: Path) -> None:
    out = _packets(tmp_path, ["1:0", "2:0", "3:0"])
    calls: list[str] = []

    def label(packet: dict, head: str) -> dict:
        calls.append(packet["id"])
        (out / "PAUSE").write_text("")  # an operator pauses after the first firing
        return _ok(packet, head)

    assert lb.label_all(out, label=label, options=ONE) == 0
    assert calls == ["1:0"]  # no new firing started after PAUSE appeared
    assert [r["id"] for r in _rows(out)] == ["1:0"]  # and what finished is checkpointed

    (out / "PAUSE").unlink()
    lb.label_all(out, label=_ok, options=ONE)
    assert sorted(r["id"] for r in _rows(out)) == ["1:0", "2:0", "3:0"]


def test_usage_limit_is_waited_out_not_recorded(tmp_path: Path) -> None:
    out = _packets(tmp_path, ["1:0"])
    attempts: list[int] = []
    waits: list[int] = []

    def label(packet: dict, head: str) -> dict:
        attempts.append(1)
        if len(attempts) == 1:
            raise RateLimited("usage limit reached")
        return _ok(packet, head)

    lb.label_all(
        out, label=label, options=lb.LabelOptions(workers=1, sleep=lambda exc, n: waits.append(n))
    )
    assert waits == [0] and len(attempts) == 2
    rows = _rows(out)
    assert [r["status"] for r in rows] == ["ok"]  # the limit left no error row behind


def test_limit_bound_is_recorded_as_retryable_error(tmp_path: Path) -> None:
    out = _packets(tmp_path, ["1:0"])

    def label(packet: dict, head: str) -> dict:
        raise RateLimited("usage limit reached")

    lb.label_all(out, label=label, options=lb.LabelOptions(workers=1, sleep=lambda exc, n: None))
    assert [r["status"] for r in _rows(out)] == ["error"]  # retried on resume, never a label


def test_parse_answer() -> None:
    assert lb.parse_answer('x {"label": "TRUE", "reason": null, "note": "n"} y')["label"] == "TRUE"
    folded = lb.parse_answer('{"label": "FALSE", "reason": "made-up", "note": ""}')
    assert folded is not None and folded["reason"] == "other"
    assert lb.parse_answer('{"label": "MAYBE"}') is None  # not a verdict
    assert lb.parse_answer("no json at all") is None
    true_with_reason = lb.parse_answer('{"label": "TRUE", "reason": "sibling"}')
    assert true_with_reason is not None and true_with_reason["reason"] is None


def test_prompt_carries_the_sources_and_nothing_hidden() -> None:
    packet = {
        "id": "1:0",
        "repo": "o/r",
        "instructions": "RUBRIC TEXT",
        "new": [{"path": "p/b.py", "qualified_name": "f", "source": "def f(): NEWBODY"}],
        "existing": [{"path": "p/a.py", "qualified_name": "g", "source": "def g(): OLDBODY"}],
        "similarity": 0.9123,  # a leaked field must not reach the prompt
    }
    assert packet["similarity"] == 0.9123
    prompt = lb.build_prompt(packet)
    for shown in ("o/r", "RUBRIC TEXT", "NEWBODY", "OLDBODY", "p/b.py", "p/a.py"):
        assert shown in prompt
    assert "0.9123" not in prompt and "similarity" not in prompt


def test_export_context_is_the_member_directories_without_git(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    ddp._git(repo, "init", "-q")
    head = ddp._commit_fixture(
        repo, {"pkg/a.py": "A = 1\n", "pkg/helper.py": "H = 2\n", "other/z.py": "Z = 3\n"}, "c"
    )
    dest = tmp_path / "ctx"
    dest.mkdir()
    packet = {
        "new": [{"path": "pkg/a.py"}],
        "existing": [{"path": "pkg/helper.py"}],
    }
    lb.export_context(repo, head, packet, dest)
    assert (dest / "pkg" / "a.py").exists() and (dest / "pkg" / "helper.py").exists()
    assert not (dest / "other").exists()  # only the member directories
    assert not (dest / ".git").exists()  # no history to read the outcome from


def test_a_second_labeler_has_its_own_ledger_and_never_reads_the_first(tmp_path: Path) -> None:
    """Labeler A must be independent of B: its own file, and B's finished rows must not
    make A skip a firing."""
    out = _packets(tmp_path, ["1:0", "2:0"])
    lb.label_all(out, label=_ok, options=lb.LabelOptions(workers=1))  # labeler B, default
    assert (out / "labels_B.jsonl").exists()

    seen: list[str] = []

    def label(packet: dict, head: str) -> dict:
        seen.append(packet["id"])
        return _ok(packet, head)

    lb.label_all(out, label=label, options=lb.LabelOptions(workers=1, name="A"))
    assert sorted(seen) == ["1:0", "2:0"]  # B's rows did not mark A's work as done
    rows_a = [json.loads(x) for x in (out / "labels_A.jsonl").read_text().splitlines()]
    assert sorted(r["id"] for r in rows_a) == ["1:0", "2:0"]
    assert len((out / "labels_B.jsonl").read_text().splitlines()) == 2  # B untouched
