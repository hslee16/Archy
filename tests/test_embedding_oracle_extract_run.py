"""The #587 extractor's driver: cache, manifest, parity with the ledger, the draw, interrupts.

Expected values are worked by hand, never taken from the code under test. Each test
asserts its fixture reaches the branch before asserting the outcome. Extraction itself is
in `test_embedding_oracle_extract.py`.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names cannot
# shadow stdlib/installed imports, mirroring tests/test_embedding_oracle_pairs.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_extract import (  # ty: ignore[unresolved-import]  (path set by the helpers)
    CALIBRATION_N,
    DATA,
    EXTRACT_VERSION,
    NO_NEW_FUNCTION,
    PILOT_LEDGER,
    check_disjoint,
    draw_calibration,
    extract_pr,
    git_sha,
    is_final,
    measured_rows,
    parity_mismatches,
    pr_result,
    reason_class,
)

from tests.embedding_oracle_extract_helpers import (
    big,
    commit,
    floor,
    git,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    return root


def test_reason_class_groups_a_drop_by_its_text_before_the_colon() -> None:
    assert reason_class(None) == "measured"
    assert reason_class(NO_NEW_FUNCTION) == "measured"  # #548 analysed such a PR
    assert reason_class("no added python lines") == "no added python lines"
    assert reason_class("QualnameCollisionError: 2 module names map") == "QualnameCollisionError"
    assert reason_class("head moved: ledger ab, fetched cd") == "head moved"


@pytest.fixture
def pilot_pr(repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """A real two-commit repository wired in as the clone, with a ledger row for it."""
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    base = commit(repo, {"pkg/__init__.py": "", "pkg/a.py": big("existing")})
    head = commit(repo, {"pkg/b.py": big("fresh")})
    monkeypatch.setattr(mod, "OUT", tmp_path / "out")
    monkeypatch.setattr(mod, "_clone_and_pin", lambda row: repo)
    return {"key": "7", "repo": "o/r", "base": base, "head": head}


def test_run_shard_reports_agreement_and_disagreement_with_the_ledger(pilot_pr: dict) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    agree = mod.run_shard("pilot", [pilot_pr])
    assert agree["reproduction"] == {"7": {"ledger": "measured", "mine": "measured"}}
    assert agree["reproduction_mismatches"] == {}
    assert agree["rows"] == 2  # base `existing` and head `fresh`, text only
    liar = {**pilot_pr, "dropped": "no added python lines"}  # the ledger claims a drop
    clash = mod.run_shard("pilot", [liar])
    assert clash["reproduction_mismatches"] == {
        "7": {"ledger": "no added python lines", "mine": "measured"}
    }


def test_main_exits_3_on_a_reproduction_mismatch_and_0_otherwise(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [pilot_pr]})
    assert mod.main(["--shard", "pilot"]) == 0
    manifest = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert manifest["ok"] is True
    assert manifest["shards"]["pilot"]["extractor_git_sha"] == "sha"  # recorded PER shard
    assert manifest["shards"]["pilot"]["extractor_version"] == EXTRACT_VERSION
    liar = {**pilot_pr, "dropped": "no added python lines"}
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [liar]})
    assert mod.main(["--shard", "pilot"]) == 3
    # the manifest itself says so, so a downstream reader cannot miss the exit code
    assert json.loads((mod.OUT / "SHARDS.json").read_text())["ok"] is False


def test_draw_calibration_caps_per_repo_and_stops_at_n() -> None:
    keys = {f"{i:04d}": ("big/repo" if i < 30 else f"r/{i}") for i in range(60)}
    assert sum(1 for r in keys.values() if r == "big/repo") > 5  # the cap can bind
    picked = draw_calibration(keys, n=20, cap=5, seed=587)
    assert len(picked) == 20 and len(set(picked)) == 20
    assert sum(1 for k in picked if keys[k] == "big/repo") <= 5
    assert sum(1 for k in picked if keys[k] == "big/repo") == 5  # 30 candidates, so it binds


def test_draw_calibration_is_over_sorted_keys_and_seeded() -> None:
    items = [(f"{i:03d}", f"r/{i % 7}") for i in range(40)]
    forward = dict(items)
    backward = dict(reversed(items))
    assert list(forward) != list(backward)  # different insertion order, same content
    a = draw_calibration(forward, n=10, cap=5, seed=587)
    assert a == draw_calibration(backward, n=10, cap=5, seed=587)  # sorted first
    assert a != draw_calibration(forward, n=10, cap=5, seed=588)  # the seed matters


def test_draw_calibration_returns_fewer_when_the_cap_starves_it() -> None:
    assert len(draw_calibration({"1": "r", "2": "r", "3": "r"}, n=40, cap=2)) == 2


def test_check_disjoint_fires_on_overlap_and_passes_on_disjoint() -> None:
    check_disjoint(["1", "2"], ["3"])
    with pytest.raises(AssertionError, match="overlap"):
        check_disjoint(["1", "2"], ["2", "3"])


def test_measured_rows_keeps_only_ok_undropped_rows(tmp_path: Path) -> None:
    rows = [
        {"key": 1, "status": "ok", "repo": "a"},
        {"key": 2, "status": "ok", "dropped": "scan too large"},
        {"key": 3, "status": "error"},
        {"key": 4, "status": "ok", "repo": "b"},
    ]
    path = tmp_path / "l.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert sorted(measured_rows([path])) == ["1", "4"]


def test_the_reuse_set_is_the_preregistered_one() -> None:
    measured = measured_rows(sorted(DATA.glob("precision.*.jsonl")))
    assert len(measured) == 1049  # "1,049 PRs" in the pre-registration
    pilot = [json.loads(line) for line in PILOT_LEDGER.read_text().splitlines()]
    assert len(pilot) == 20
    assert sum("dropped" not in r for r in pilot) == 7  # "7 measured PRs"
    check_disjoint((str(r["key"]) for r in pilot), measured)
    picked = draw_calibration({k: r["repo"] for k, r in measured.items()})
    assert len(picked) == CALIBRATION_N == 40
    repos = [measured[k]["repo"] for k in picked]
    assert max(repos.count(r) for r in set(repos)) <= 5
    check_disjoint((str(r["key"]) for r in pilot), picked)


def test_git_sha_marks_tracked_changes_dirty_and_ignores_untracked_files(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    sha = commit(repo, {"bench/x.py": "a = 1\n", "src/y.py": "b = 1\n"})
    monkeypatch.setattr(mod, "REPO_ROOT", repo)
    assert git_sha() == sha  # clean
    (repo / "bench" / "untracked.jsonl").write_text("{}")
    assert git_sha() == sha  # an untracked file is not a change to the extractor
    (repo / "bench" / "x.py").write_text("a = 2\n")
    assert git_sha() == sha + "+dirty"  # a tracked edit is


@pytest.mark.parametrize(
    ("result", "final"),
    [
        ({"fns": [], "texts": {}}, True),
        ({"dropped": "no added python lines"}, True),
        ({"dropped": NO_NEW_FUNCTION}, True),
        ({"dropped": "QualnameCollisionError: 2 module names map"}, True),
        ({"dropped": "scan too large"}, True),
        ({"dropped": "git: fatal: unable to access"}, False),
        ({"dropped": "head moved: ledger ab, fetched cd"}, False),
        ({"dropped": "unresolvable: repo gone"}, False),
        ({"dropped": "TimeoutExpired: Command timed out"}, False),
    ],
)
def test_only_a_result_a_rerun_would_reproduce_is_final(result: dict, final: bool) -> None:
    assert is_final(result) is final


def test_pr_result_caches_final_results_and_retries_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    calls: list[int] = []
    answers = iter([{"dropped": "git: transient"}, {"fns": [], "texts": {}}])

    def measure(row: dict) -> dict:
        calls.append(1)
        return next(answers)

    monkeypatch.setattr(mod, "_measure", measure)
    row = {"key": "9", "base": "b", "head": "h"}
    assert pr_result(tmp_path, row, "sha") == {"dropped": "git: transient"}
    assert not (tmp_path / "9.json").exists()  # a transient drop is not cached
    assert pr_result(tmp_path, row, "sha") == {"fns": [], "texts": {}}  # so it was retried
    assert len(calls) == 2
    assert pr_result(tmp_path, row, "sha") == {"fns": [], "texts": {}}
    assert len(calls) == 2  # the final result is served from the cache


def _entry(**override: object) -> dict:
    base = {
        "version": EXTRACT_VERSION,
        "extractor_git_sha": "abc",
        "base": "b",
        "head": "h",
        "result": {"fns": [], "texts": {"old": "y"}},
    }
    return {**base, **override}


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        (_entry(version="0"), "another extractor version"),
        (_entry(extractor_git_sha="abc+dirty"), "made on a dirty tree"),
        (_entry(base="other"), "another base commit of the same PR key"),
        (_entry(head="other"), "another head commit of the same PR key"),
        ([1, 2], "valid JSON that is not an entry"),
        ({"version": EXTRACT_VERSION}, "an entry with no result"),
    ],
)
def test_pr_result_refuses_a_cache_entry_it_cannot_trust(
    entry: object, why: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "_measure", lambda row: {"fns": [], "texts": {"fresh": "x"}})
    path = tmp_path / "9.json"
    row = {"key": "9", "base": "b", "head": "h"}
    path.write_text(json.dumps(_entry()))
    assert pr_result(tmp_path, row, "sha")["texts"] == {"old": "y"}  # the control IS trusted
    path.write_text(json.dumps(entry))
    assert pr_result(tmp_path, row, "sha")["texts"] == {"fresh": "x"}, why  # recomputed
    path.write_text('{"version": "2", "res')  # a torn write
    assert pr_result(tmp_path, row, "sha")["texts"] == {"fresh": "x"}  # not a crash


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.CalledProcessError(128, "git", stderr="fatal: bad object"),
        subprocess.TimeoutExpired("git", 900),
        OSError("disk full"),
    ],
)
def test_one_prs_failure_is_a_drop_and_never_an_exception(
    exc: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    def boom(row: dict) -> Path:
        raise exc

    monkeypatch.setattr(mod, "_clone_and_pin", boom)
    out = mod._measure({"key": "1", "base": "a", "head": "b"})
    assert set(out) == {"dropped"} and out["dropped"]  # recorded with a reason
    assert is_final(out) is False  # and retried next run


def test_an_interrupt_exits_cleanly_and_says_how_to_resume(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    def interrupted(name: str, rows: object) -> dict:
        raise KeyboardInterrupt

    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [pilot_pr]})
    monkeypatch.setattr(mod, "run_shard", interrupted)
    assert mod.main(["--shard", "pilot"]) == 130
    assert "run the same command again to resume" in capsys.readouterr().err


def test_a_failed_write_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    target = tmp_path / "x.json"
    mod._atomic_write(target, b"old")
    assert target.read_bytes() == b"old" and not list(tmp_path.glob("*.tmp"))

    def fail(src: object, dst: object) -> None:
        raise OSError("killed mid-rename")

    monkeypatch.setattr(mod.os, "replace", fail)
    with pytest.raises(OSError):
        mod._atomic_write(target, b"new")
    assert target.read_bytes() == b"old"  # never a torn file at the target


def test_parity_with_the_ledger_firings_is_exact_on_path_name_line_and_newness(
    repo: Path,
) -> None:
    floor()
    base = commit(repo, {"pkg/__init__.py": "", "pkg/a.py": big("existing")})
    head = commit(repo, {"pkg/b.py": big("fresh")})
    result = extract_pr(repo, base, head)
    new = {"path": "pkg/b.py", "qualified_name": "fresh", "line": 1, "new": True}
    old = {"path": "pkg/a.py", "qualified_name": "existing", "line": 1, "new": False}
    row = {"firings": [{"members": [new, old]}]}
    assert parity_mismatches(row, result) == []
    wrong_new = {"firings": [{"members": [{**new, "new": False}]}]}  # the head row IS added
    assert parity_mismatches(wrong_new, result) == ["pkg/b.py:fresh@1 new=False"]
    wrong_line = {"firings": [{"members": [{**old, "line": 5}]}]}
    assert parity_mismatches(wrong_line, result) == ["pkg/a.py:existing@5 new=False"]
    assert parity_mismatches({"firings": []}, result) == []  # no firing, nothing to compare
    assert parity_mismatches({}, {"dropped": NO_NEW_FUNCTION}) == []
    # a PR with firings and NO extractor rows is the clearest disagreement: every member shows
    for no_rows in ({"dropped": NO_NEW_FUNCTION}, {"dropped": "no added python lines"}):
        assert parity_mismatches(row, no_rows) == [
            "pkg/b.py:fresh@1 new=True",
            "pkg/a.py:existing@1 new=False",
        ]


def test_main_exits_3_on_a_parity_mismatch_even_when_the_classes_agree(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    ghost = {"path": "pkg/zzz.py", "qualified_name": "ghost", "line": 1, "new": True}
    row = {**pilot_pr, "firings": [{"members": [ghost]}]}
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [row]})
    assert mod.main(["--shard", "pilot"]) == 3  # the ledger says measured, and so do we
    manifest = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert manifest["ok"] is False
    parity = manifest["shards"]["pilot"]["parity_mismatches"]
    assert parity == {"7": ["pkg/zzz.py:ghost@1 new=True"]}


@pytest.fixture
def ledgers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Ledgers on disk, wired in as the data directory and the pilot ledger; returns the
    measured rows. One repository holds 20 of the 60 measured PRs, so the cap of 5 binds in a
    draw of 40; two more rows are unmeasured and must never be drawn."""
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    def row(i: int) -> dict:
        repo = "r/big" if i < 20 else f"r/{i}"
        return {"key": 1000 + i, "status": "ok", "repo": repo, "base": "b", "head": "h"}

    measured = [row(i) for i in range(60)]
    junk = [
        {"key": 5001, "status": "ok", "dropped": "scan too large", "repo": "r/0"},
        {"key": 5002, "status": "error", "repo": "r/1"},
    ]
    data = tmp_path / "data"
    data.mkdir()
    lines = "".join(json.dumps(r) + "\n" for r in measured + junk)
    (data / "precision.run0.jsonl").write_text(lines)
    pilot = [{"key": 9000 + i, "repo": "p/x", "base": "b", "head": "h"} for i in range(3)]
    pilot_file = tmp_path / "pilot.jsonl"
    pilot_file.write_text("".join(json.dumps(r) + "\n" for r in pilot))
    monkeypatch.setattr(mod, "DATA", data)
    monkeypatch.setattr(mod, "PILOT_LEDGER", pilot_file)
    return measured


def test_rows_wires_pilot_and_calibration_from_the_ledgers(ledgers: list[dict]) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    got = mod._rows(["pilot", "calibration"])
    assert [r["key"] for r in got["pilot"]] == [9000, 9001, 9002]
    cal = {str(r["key"]) for r in got["calibration"]}
    assert len(cal) == 40  # the fixture reaches a full draw
    assert cal <= {str(r["key"]) for r in ledgers}  # dropped and errored rows never enter
    assert cal.isdisjoint({"9000", "9001", "9002"})
    in_big = sum(1 for r in got["calibration"] if r["repo"] == "r/big")
    assert in_big == 5  # the per-repository cap bound (20 candidates), through `_rows`' wiring


def test_the_cli_has_no_way_to_name_the_evaluation_subset(ledgers: list[dict]) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    assert len(ledgers) == 60  # reached: the ledgers are wired in
    with pytest.raises(SystemExit):  # argparse refuses any other shard name
        mod.main(["--shard", "evaluation"])
    assert set(mod.SHARD_NAMES) == {"pilot", "calibration"}


def test_the_manifest_keeps_blocks_it_does_not_own_and_drops_the_legacy_sha(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [pilot_pr]})
    mod.OUT.mkdir(parents=True)
    kept = {"extractor_version": EXTRACT_VERSION}
    prior = {
        "extractor_git_sha": "old",
        "sample_repos": {"o/r": 3},
        "shards": {"calibration": kept},
    }
    (mod.OUT / "SHARDS.json").write_text(json.dumps(prior))
    assert mod.main(["--shard", "pilot"]) == 0
    got = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert got["sample_repos"] == {"o/r": 3}  # a consumer's block survives a regeneration
    assert "extractor_git_sha" not in got  # recorded per shard now, not once for all
    assert set(got["shards"]) == {"calibration", "pilot"}  # the other shard's entry is kept


def test_a_manifest_that_mixes_extractor_versions_is_not_ok(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [pilot_pr]})
    mod.OUT.mkdir(parents=True)
    stale = {"extractor_version": "1"}  # the other shard was written by an older version
    (mod.OUT / "SHARDS.json").write_text(json.dumps({"shards": {"calibration": stale}}))
    assert mod.main(["--shard", "pilot"]) == 3  # pilot is fine, but the whole is not
    got = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert got["ok"] is False and got["shards"]["pilot"]["extractor_version"] == EXTRACT_VERSION


def test_an_interrupted_run_leaves_the_manifest_not_ok(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    rows = {"pilot": [pilot_pr], "calibration": [pilot_pr]}
    monkeypatch.setattr(mod, "_rows", lambda names: rows)
    assert mod.main([]) == 0  # both shards good first
    good = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert good["ok"] is True  # the fixture reaches a good manifest to spoil

    real = mod.run_shard

    def second_shard_interrupted(name: str, rs: list[dict]) -> dict:
        if name == "calibration":
            raise KeyboardInterrupt
        return real(name, rs)

    monkeypatch.setattr(mod, "run_shard", second_shard_interrupted)
    assert mod.main([]) == 130
    # pilot's files were replaced and recorded; the half-run must not read as good
    assert json.loads((mod.OUT / "SHARDS.json").read_text())["ok"] is False


def test_each_shard_is_recorded_as_soon_as_it_audits(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    rows = {"pilot": [pilot_pr], "calibration": [pilot_pr]}
    monkeypatch.setattr(mod, "_rows", lambda names: rows)
    real = mod.run_shard

    def second_shard_crashes(name: str, rs: list[dict]) -> dict:
        if name == "calibration":
            raise OSError("disk full")
        return real(name, rs)

    monkeypatch.setattr(mod, "run_shard", second_shard_crashes)
    with pytest.raises(OSError):
        mod.main([])
    got = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert set(got["shards"]) == {"pilot"}  # pilot is on record although calibration crashed
    assert (
        got["shards"]["pilot"]["sha256"]
        == hashlib.sha256((mod.OUT / "pilot.jsonl").read_bytes()).hexdigest()
    )  # and the record matches the file on disk


def test_the_manifest_states_how_much_the_parity_check_covered(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    new = {"path": "pkg/b.py", "qualified_name": "fresh", "line": 1, "new": True}
    old = {"path": "pkg/a.py", "qualified_name": "existing", "line": 1, "new": False}
    with_firing = {**pilot_pr, "firings": [{"members": [new, old]}]}
    without = {**pilot_pr, "key": "8"}
    empty = {**pilot_pr, "key": "6", "firings": []}  # a ledger row that recorded no firing
    shard = mod.run_shard("pilot", [with_firing, without, empty])
    # one PR of three has a firing, and it has two members: the check covered nothing else
    assert shard["parity_checked"] == {"prs_with_firings": 1, "members": 2}
    assert shard["parity_mismatches"] == {}
