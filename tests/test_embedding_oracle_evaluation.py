"""The registered evaluation draw of #587: reproduced, refused when it differs, never selectable
by the old CLI. Expected values are worked by hand or fixed by the fourth addendum, never taken
from the code under test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)), as the sibling embedding_oracle tests do.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import embedding_oracle_evaluation as ev  # ty: ignore[unresolved-import]  (path set above)
from embedding_oracle_extract import (  # ty: ignore[unresolved-import]  (path set above)
    DATA,
    SHARD_NAMES,
    draw_calibration,
    measured_rows,
)


def _repos() -> dict[str, str]:
    """30 measured PRs over 3 repositories: r0 holds 14, r1 holds 10, r2 holds 6."""
    sizes = {"r0": 14, "r1": 10, "r2": 6}
    keys = (f"{i:04d}" for i in range(1, 31))
    return {next(keys): repo for repo, n in sizes.items() for _ in range(n)}


def test_the_hash_is_of_the_sorted_keys_joined_by_newlines() -> None:
    # `printf 'a\nb' | shasum -a 256`, computed outside this code
    want = "7e18f737311b2dc3b2f269dd78396b0351f14fb66efa879f768cb23181883c78"
    assert ev.keys_sha256(["b", "a"]) == want  # sorted first, so the input order is irrelevant
    assert ev.keys_sha256(["a", "b"]) == want
    assert ev.keys_sha256(["ab"]) != want  # the separator is a newline, not nothing


def test_the_evaluation_draw_excludes_calibration_and_honours_the_cap() -> None:
    repos = _repos()
    cal = ["0001", "0002"]
    drawn = ev.draw_evaluation(repos, cal, n=12, cap=4)
    per_repo = {r: sum(repos[k] == r for k in drawn) for r in set(repos.values())}
    assert max(per_repo.values()) == 4, "the fixture must reach the cap"
    assert not set(drawn) & set(cal)
    assert len(drawn) == 12 and len(set(drawn)) == 12


def test_a_smaller_m_is_a_prefix_of_a_larger_one() -> None:
    repos = _repos()
    small = ev.draw_evaluation(repos, [], n=7, cap=4)
    large = ev.draw_evaluation(repos, [], n=12, cap=4)
    assert large[:7] == small and len(large) > len(small)


def test_the_draw_runs_over_what_remains_after_calibration() -> None:
    repos = _repos()
    assert ev.draw_evaluation(repos, ["0001"], n=5, cap=5) == draw_calibration(
        {k: r for k, r in repos.items() if k != "0001"}, n=5, cap=5
    )


def test_a_draw_other_than_the_registered_one_is_refused() -> None:
    keys = ["1", "2", "3"]
    with pytest.raises(ev.EvaluationDrawError) as caught:
        ev.check_registered(keys)
    assert ev.keys_sha256(keys) in str(caught.value)
    assert ev.EVALUATION_KEYS_SHA256 in str(caught.value)
    ev.check_registered(keys, ev.keys_sha256(keys))  # the same keys pass their own hash


def test_the_registered_draw_is_reproduced_from_the_committed_ledger() -> None:
    measured = measured_rows(sorted(DATA.glob("precision.*.jsonl")))
    repos = {k: v["repo"] for k, v in measured.items()}
    cal = draw_calibration(repos)
    assert ev.keys_sha256(cal) == ev.CALIBRATION_KEYS_SHA256
    drawn = ev.draw_evaluation(repos, cal)
    assert len(drawn) == 250 == ev.EVALUATION_N
    assert ev.keys_sha256(drawn) == ev.EVALUATION_KEYS_SHA256
    by_repo = {r: sum(repos[k] == r for k in drawn) for r in {repos[k] for k in drawn}}
    assert len(by_repo) == 186  # the addendum's figure
    assert max(by_repo.values()) <= ev.EVALUATION_REPO_CAP
    assert not set(drawn) & set(cal)


def test_the_registered_sample_constants_are_the_addendums() -> None:
    assert (ev.EVALUATION_N, ev.EVALUATION_REPO_CAP) == (250, 25)  # fourth addendum: M, cap


def test_a_calibration_draw_that_moved_refuses_the_evaluation_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ev, "CALIBRATION_KEYS_SHA256", "0" * 64)
    with pytest.raises(ev.EvaluationDrawError):
        ev.evaluation_rows()


def test_evaluation_rows_returns_the_ledger_rows_of_the_registered_keys() -> None:
    rows = ev.evaluation_rows()
    assert len(rows) == 250
    assert ev.keys_sha256([str(r["key"]) for r in rows]) == ev.EVALUATION_KEYS_SHA256
    assert all(r["status"] == "ok" and "dropped" not in r for r in rows)


def test_the_rows_come_back_sorted_by_key() -> None:
    keys = [str(r["key"]) for r in ev.evaluation_rows()]
    assert keys == sorted(keys) and keys != list(reversed(keys))


def test_a_pilot_pr_in_the_draw_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    drawn = str(ev.evaluation_rows()[0]["key"])
    ledger = tmp_path / "pilot.jsonl"
    ledger.write_text(f'{{"key": "{drawn}"}}\n', encoding="utf-8")
    monkeypatch.setattr(ev, "PILOT_LEDGER", ledger)
    with pytest.raises(AssertionError, match="overlap"):
        ev.evaluation_rows()


def test_a_calibration_pr_in_the_draw_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    measured = measured_rows(sorted(DATA.glob("precision.*.jsonl")))
    cal = draw_calibration({k: v["repo"] for k, v in measured.items()})
    overlapping = [*cal[:1], *ev.draw_evaluation({k: v["repo"] for k, v in measured.items()}, cal)]
    monkeypatch.setattr(ev, "draw_evaluation", lambda *a, **k: overlapping)
    monkeypatch.setattr(ev, "EVALUATION_KEYS_SHA256", ev.keys_sha256(overlapping))
    with pytest.raises(AssertionError, match="overlap"):
        ev.evaluation_rows()


def test_the_old_cli_still_cannot_name_the_evaluation_shard() -> None:
    assert "evaluation" not in SHARD_NAMES


def test_main_refuses_and_extracts_nothing_when_the_draw_differs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[object] = []
    monkeypatch.setattr(ev, "EVALUATION_KEYS_SHA256", "0" * 64)
    monkeypatch.setattr(ev, "run_and_record", lambda rows: ran.append(rows) or 0)
    assert ev.main([]) == 5
    assert not ran, "nothing may be extracted from a draw that is not the registered one"
    assert "REFUSED" in capsys.readouterr().err


def test_main_hands_the_registered_rows_to_the_extractor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    got: list[dict] = []
    monkeypatch.setattr(ev, "run_and_record", lambda rows: got.append(rows) or 0)
    assert ev.main([]) == 0
    assert list(got[0]) == ["evaluation"] and len(got[0]["evaluation"]) == 250


def test_plan_prints_the_keys_and_extracts_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[object] = []
    monkeypatch.setattr(ev, "run_and_record", lambda rows: ran.append(rows) or 0)
    assert ev.main(["--plan"]) == 0
    assert not ran
    assert capsys.readouterr().out.startswith("evaluation 250 [")
