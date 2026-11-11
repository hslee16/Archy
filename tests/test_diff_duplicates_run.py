"""Tests for the #548 sampler and resumable runner (no network, no git).

The properties that decide whether the study is honest: the pilot and main samples
are disjoint, the per-repo cap really binds in draw order, a crash in one PR does not
end the loop, `error` rows are retried while `ok` rows (including drops) are not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import diff_duplicates_pr as pr_mod  # ty: ignore[unresolved-import]  (added to sys.path above)
import diff_duplicates_run as run_mod  # ty: ignore[unresolved-import]  (added to sys.path above)
from _supervise import Ledger  # ty: ignore[unresolved-import]  (added to sys.path above)


def _pool(repos: dict[str, int]) -> list[pr_mod.PoolPR]:
    out, i = [], 0
    for repo, count in repos.items():
        for n in range(count):
            i += 1
            out.append(pr_mod.PoolPR(id=i, number=n + 1, repo=repo, agent="a", merged=True))
    return out


def test_pilot_and_main_are_disjoint_and_deterministic() -> None:
    pool = _pool({"o/a": 40, "o/b": 40, "o/c": 40})
    pilot = pr_mod.draw_pilot(pool, pr_mod.SampleSpec(pilot_n=20))
    main = pr_mod.draw_main(pool, 100, pr_mod.SampleSpec(pilot_n=20, cap=100))
    assert len(pilot) == 20
    assert len(main) == 100  # the fixture is big enough to reach the branch
    assert {p.id for p in pilot}.isdisjoint({p.id for p in main})
    assert pilot == pr_mod.draw_pilot(pool, pr_mod.SampleSpec(pilot_n=20))
    assert main == pr_mod.draw_main(pool, 100, pr_mod.SampleSpec(pilot_n=20, cap=100))


def test_repo_cap_binds_in_draw_order() -> None:
    pool = _pool({"o/big": 200, "o/small": 3})
    uncapped = pr_mod.draw_main(pool, 500, pr_mod.SampleSpec(pilot_n=0, cap=10_000))
    assert sum(p.repo == "o/big" for p in uncapped) > 5  # the cap has something to bind on

    capped = pr_mod.draw_main(pool, 500, pr_mod.SampleSpec(pilot_n=0, cap=5))
    assert sum(p.repo == "o/big" for p in capped) == 5
    # draw order: the capped sample is the uncapped stream with later big-repo PRs skipped
    first_big = [p for p in uncapped if p.repo == "o/big"][:5]
    assert [p for p in capped if p.repo == "o/big"] == first_big


def _ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "l.jsonl")


def test_crash_in_one_pr_does_not_end_the_loop(tmp_path: Path) -> None:
    prs = _pool({"o/a": 3})
    calls: list[int] = []

    def process(p: pr_mod.PoolPR) -> dict:
        calls.append(p.id)
        if p.id == 2:
            raise RuntimeError("boom")
        return {"firings": []}

    ledger = _ledger(tmp_path)
    assert run_mod.run_loop(prs, ledger, process) == 0
    assert calls == [1, 2, 3]  # PR 3 ran after PR 2 crashed
    assert ledger.is_done("1") and ledger.is_done("3") and not ledger.is_done("2")


def test_resume_retries_errors_and_skips_ok_and_drops(tmp_path: Path) -> None:
    prs = _pool({"o/a": 3})
    path = tmp_path / "l.jsonl"

    def first(p: pr_mod.PoolPR) -> dict:
        if p.id == 1:
            return {"firings": []}
        if p.id == 2:
            raise pr_mod.Unresolvable("gone")
        raise RuntimeError("stalled")

    run_mod.run_loop(prs, Ledger(path), first)
    seen: list[int] = []

    def second(p: pr_mod.PoolPR) -> dict:
        seen.append(p.id)
        return {"firings": []}

    run_mod.run_loop(prs, Ledger(path), second)
    assert seen == [3]  # ok (1) and the permanent drop (2) are not re-run; the error (3) is


def test_summary_excludes_drops_from_the_denominator(tmp_path: Path) -> None:
    path = tmp_path / "l.jsonl"
    rows = [
        {"key": "1", "status": "ok", "repo": "o/a", "firings": [{"x": 1}], "seconds": 2.0},
        {"key": "2", "status": "ok", "repo": "o/b", "firings": [], "seconds": 4.0},
        {"key": "3", "status": "ok", "repo": "o/c", "dropped": "scan too large"},
        {"key": "4", "status": "error", "repo": "o/d", "error": "x"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    s = run_mod.summarize(path)
    assert s["measured"] == 2
    assert s["firing_rate"] == pytest.approx(0.5)
    assert s["dropped"] == {"scan too large": 1}
    assert s["mean_seconds_per_pr"] == pytest.approx(3.0)


def test_shards_partition_the_sample_and_never_split_a_repo() -> None:
    pool = _pool({f"o/r{i}": 4 for i in range(30)})
    shards = [pr_mod.take_shard(pool, k, 4) for k in range(4)]
    assert sum(len(s) for s in shards) == len(pool)  # nothing lost, nothing doubled
    assert len({p.id for s in shards for p in s}) == len(pool)
    assert sum(bool(s) for s in shards) > 1  # the fixture really spreads over shards
    for s in shards:
        for repo in {p.repo for p in s}:
            assert sum(p.repo == repo for p in s) == 4  # all 4 PRs of a repo in one shard


def test_skip_keys_are_not_rerun_and_summary_merges_ledgers(tmp_path: Path) -> None:
    prs = _pool({"o/a": 3})
    seen: list[int] = []

    def process(p: pr_mod.PoolPR) -> dict:
        seen.append(p.id)
        return {"firings": []}

    run_mod.run_loop(prs, _ledger(tmp_path), process, skip_keys=frozenset({"2"}))
    assert seen == [1, 3]  # 2 was done in another ledger

    other = tmp_path / "other.jsonl"
    other.write_text(json.dumps({"key": "9", "status": "ok", "repo": "o/z", "firings": []}) + "\n")
    assert run_mod.summarize(tmp_path / "l.jsonl", other)["measured"] == 3
