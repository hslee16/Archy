"""Generator B for #587: top-1 neighbour, calibration denominators, cutoff, cap, and the runner.

Synthetic 2-D vectors only (no real embedding exists yet); every expected cosine is worked by
hand. Each test asserts its fixture reaches the branch before asserting the outcome.
"""

# ruff: noqa: E402  (importorskip must run before the bench imports, which need numpy)

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # bench-only dependency: skip where it is absent

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import embedding_oracle_generate_run as run  # ty: ignore[unresolved-import]  (added to sys.path above)
from embedding_oracle_generate import (  # ty: ignore[unresolved-import]
    DENOMINATORS,
    PrScores,
    Scored,
    _groups,
    b_pairs,
    calibrate,
    calibration_scores,
    mean_capped,
    score_pr,
    volume_report,
)
from embedding_oracle_units import PrUnits, UnitFn, split_pr  # ty: ignore[unresolved-import]

from tests.emb587_helpers import sha, unit, vector_set, write_meta, write_shard, write_vectors


def fn(fid: str, path: str = "a.py", qual: str = "f", nodes: int = 40) -> UnitFn:
    return UnitFn(
        id=fid, pr="1", repo="o/r", path=path, qualname=qual, nodes=nodes, shape=f"s-{fid}"
    )


def units(new: list[UnitFn], existing: list[UnitFn], lost: int = 0) -> PrUnits:
    return PrUnits(
        pr="1", repo="o/r", new=tuple(new), existing=tuple(existing), lost_not_at_head=lost
    )


# cosines with q=(1,0), worked by hand: hi (0.6,0.8) -> 0.6, mid (0.8,0.6) -> 0.8, lo (0,1) -> 0.0
VECS = {"q": (1.0, 0.0), "hi": (3.0, 4.0), "mid": (4.0, 3.0), "lo": (0.0, 1.0)}


def test_top1_is_the_highest_cosine() -> None:
    vs = vector_set(VECS)
    ps = score_pr(
        units([fn("q", qual="n")], [fn("hi", qual="h"), fn("mid", qual="m"), fn("lo", qual="l")]),
        vs,
    )
    assert len(ps.scored) == 1  # reached
    assert ps.scored[0].existing.id == "mid"
    assert ps.scored[0].sim == pytest.approx(0.8, abs=1e-6)


def test_ties_break_on_path_then_name_whatever_the_order() -> None:
    vs = vector_set({"q": (1.0, 0.0), "t1": (1.0, 1.0), "t2": (1.0, 1.0)})
    a, b = fn("t1", path="a.py", qual="z"), fn("t2", path="b.py", qual="a")
    for order in ([a, b], [b, a]):
        got = score_pr(units([fn("q", qual="n")], order), vs).scored[0].existing
        assert got.name_key == ("a.py", "z")


def test_a_new_function_never_pairs_with_itself() -> None:
    vs = vector_set({"q": (1.0, 0.0), "own": (1.0, 0.0), "other": (4.0, 3.0)})
    own_base = fn("own", path="a.py", qual="f")  # the base version of the new function: cosine 1.0
    ps = score_pr(units([fn("q", path="a.py", qual="f")], [own_base, fn("other", qual="o")]), vs)
    assert ps.scored[0].existing.id == "other"  # the 1.0 self-pair was available and refused


def test_self_exclusion_is_by_path_and_name_never_by_id_or_occurrence() -> None:
    vs = vector_set({"q": (1.0, 0.0), "own": (1.0, 0.0), "other": (4.0, 3.0)})
    base_twin = UnitFn(
        id="own", pr="1", repo="o/r", path="a.py", qualname="f", nodes=40, shape="s", occurrence=1
    )
    new_first = UnitFn(
        id="q", pr="1", repo="o/r", path="a.py", qualname="f", nodes=40, shape="s", occurrence=0
    )
    ps = score_pr(units([new_first], [base_twin, fn("other", qual="o")]), vs)
    assert ps.scored[0].existing.id == "other"  # same name, other occurrence: still itself
    same_id = fn("q", path="b.py", qual="elsewhere")  # a different function with the SAME text id
    ps = score_pr(units([new_first], [same_id, fn("other", qual="o")]), vs)
    assert ps.scored[0].existing.id == "q"  # not excluded by id: it is a valid neighbour at 1.0


def test_identical_text_neighbours_are_scored_once_and_named_by_the_smallest() -> None:
    a = UnitFn(id="d", pr="1", repo="o/r", path="b.py", qualname="z", nodes=40, shape="s")
    b = UnitFn(id="d", pr="1", repo="o/r", path="a.py", qualname="y", nodes=40, shape="s")
    assert len(_groups([a, b])) == 1 and _groups([a, b])["d"][0] is b  # one group, smallest first
    vs = vector_set({"q": (1.0, 0.0), "d": (4.0, 3.0)})
    for order in ([a, b], [b, a]):
        assert score_pr(units([fn("q", qual="n")], order), vs).scored[0].existing.name_key == (
            "a.py",
            "y",
        )


def test_scoring_runs_in_float64() -> None:
    rng = np.random.default_rng(7)
    raw = rng.standard_normal((2, 768)).astype(np.float32)
    raw /= np.linalg.norm(raw, axis=1, keepdims=True)
    vs = vector_set({"q": tuple(raw[0]), "e": tuple(raw[1])})
    got = score_pr(units([fn("q", qual="n")], [fn("e", qual="e")]), vs).scored[0].sim
    exact = float(np.dot(vs.get("q").astype(np.float64), vs.get("e").astype(np.float64)))
    assert got == pytest.approx(exact, rel=1e-13)  # a float32 dot product is off by ~1e-8


def test_only_self_available_means_no_neighbour() -> None:
    vs = vector_set({"q": (1.0, 0.0), "own": (1.0, 0.0)})
    ps = score_pr(units([fn("q", qual="f")], [fn("own", qual="f")]), vs)
    assert ps.scored == () and ps.no_neighbour == 1


def test_no_existing_function_means_no_neighbour_and_lost_is_carried() -> None:
    vs = vector_set(VECS)
    ps = score_pr(units([fn("q", qual="n")], [], lost=3), vs)
    assert ps.no_neighbour == 1 and ps.lost_not_at_head == 3 and ps.n_new == 1


def test_an_id_with_no_vector_is_skipped_and_counted_never_an_error_or_a_miss() -> None:
    vs = vector_set(VECS)
    ps = score_pr(units([fn("q", qual="n")], [fn("absent", qual="x"), fn("mid", qual="m")]), vs)
    assert ps.scored[0].existing.id == "mid"  # reached: the shipped neighbour was used
    assert ps.unshipped == 1 and ps.no_neighbour == 0
    lone = score_pr(units([fn("absent", qual="n")], [fn("mid", qual="m")]), vs)
    assert lone.scored == () and lone.unshipped == 1
    assert lone.no_neighbour == 0  # not shipped is not "no neighbour"


def test_an_unchanged_head_function_resolves_to_its_base_twin_vector() -> None:
    """Its head row carries the base twin's id (same text); only the base row is looked up."""
    rows = [
        unit("1/base/a.py:f", "twin", counterpart=True),
        unit("1/head/a.py:f", "twin", in_added=False, counterpart=True),
        unit("1/head/a.py:n", "q", in_added=True),
    ]
    split = split_pr("1", rows)
    assert [f.id for f in split.existing] == ["twin"]  # one neighbour, not two
    vs = vector_set(
        {"q": (1.0, 0.0), "twin": (4.0, 3.0)}
    )  # a vector for the twin id and nothing else
    ps = score_pr(split, vs)
    assert ps.scored[0].existing.id == "twin" and ps.unshipped == 0


def test_calibration_denominators_differ_and_unknown_is_refused() -> None:
    per_pr = {"1": [0.9, 0.5], "2": [0.7], "3": []}
    allm = calibration_scores(per_pr, ["4"], "all-measured")
    only = calibration_scores(per_pr, ["4"], "scored-only")
    assert sorted(allm) == ["1", "2", "3", "4"] and sorted(only) == ["1", "2"]
    # target 0.5: all-measured needs t=0.7 (2 of 4 PRs have one); scored-only has it at t=0.9
    assert calibrate(allm, 0.5).cutoff == 0.7
    assert calibrate(only, 0.5).cutoff == 0.9
    assert DENOMINATORS == ("all-measured", "scored-only")
    with pytest.raises(ValueError, match="denominator"):
        calibration_scores(per_pr, [], "median")


def test_calibration_reports_when_the_target_is_not_reached() -> None:
    res = calibrate({"1": [0.9, 0.5], "2": [0.7], "3": [], "4": []}, 5.0)
    assert res.cutoff == 0.5 and res.reached_target is False
    assert res.mean_at_cutoff == pytest.approx(0.75)  # (2 + 1 + 0 + 0) / 4


def test_calibration_counts_a_pr_at_most_k() -> None:
    assert mean_capped({"1": [0.9] * 12}, 0.9) == 10.0


def scored(nodes: int, sim: float, name: str) -> Scored:
    return Scored(
        fn(f"n{name}", qual=f"n{name}", nodes=nodes), fn(f"e{name}", qual=f"e{name}"), sim
    )


def test_the_cutoff_is_inclusive_and_the_cap_comes_after_it() -> None:
    big = [
        scored(100 + i, 0.5, f"big{i}") for i in range(10)
    ]  # the ten largest, all BELOW the cutoff
    small = [scored(31, 0.9, "s1"), scored(30, 0.8, "s2"), scored(32, 0.7999, "s3")]
    ps = PrScores("1", "o/r", 13, tuple(big + small), 0, 0)
    got = {p.new_qualname for p in b_pairs([ps], 0.8)}
    assert got == {
        "ns1",
        "ns2",
    }  # cap-before-cutoff would keep only the ten large ones and emit nothing
    assert len(big) == 10  # the fixture really had ten larger candidates


def test_the_cap_keeps_the_largest_ten_per_pr() -> None:
    rows = [scored(40 + i, 0.9, f"x{i:02d}") for i in range(12)]
    got = b_pairs([PrScores("1", "o/r", 12, tuple(rows), 0, 0)], 0.5)
    assert len(got) == 10
    assert min(p.new_nodes for p in got) == 42  # nodes 40 and 41 were cut
    assert {p.generators for p in got} == {("B",)}  # B pairs are tagged B, never A


def test_volume_report_counts_identical_text_and_unit_similarity() -> None:
    same = Scored(fn("x", qual="n"), fn("x", qual="e"), 1.0)
    near = Scored(fn("a", qual="n2"), fn("b", qual="e2"), 0.5)
    rep = volume_report([PrScores("1", "o/r", 3, (same, near), 1, 2)])
    assert rep == {
        "scored": 2,
        "identical_text": 1,
        "similarity_at_one": 1,
        "no_neighbour": 1,
        "lost_not_at_head": 2,
        "unshipped": 0,
        "ambiguous": 0,
    }


# ---- the runner, end to end on a synthetic root ------------------------------------------------

B1, B2, N1, N2 = "b1 text", "b2 text", "n1 text", "n2 text"
V = {
    B1: (0.9, 0.43588989),  # cos(N1, B1) = 0.9
    B2: (0.8, 0.6),  # cos(N1, B2) = 0.8, cos(N2, B2) = 0.6
    N1: (1.0, 0.0),
    N2: (0.0, 1.0),
}


def build(tmp: Path) -> Path:
    cal_ids = write_shard(tmp, "calibration", [B1, B2, N1, N2])
    pil_ids = write_shard(tmp, "pilot", [B1, N1])
    write_meta(
        tmp,
        {"calibration": ["10"], "pilot": ["20"]},
        {
            "calibration": {"11": run.NO_NEW_FUNCTION, "12": "git: boom", "14": "scan too large"},
            "pilot": {},
        },
    )
    rows = {
        "calibration": [
            unit("10/base/a.py:b1", sha(B1), counterpart=True),
            unit("10/base/a.py:b2", sha(B2), counterpart=True),
            unit("10/head/a.py:b1", "not-in-shard-1", in_added=False, counterpart=True),
            unit("10/head/a.py:b2", "not-in-shard-2", in_added=False, counterpart=True),
            unit("10/head/a.py:n1", sha(N1), in_added=True),
            unit("10/head/a.py:n2", sha(N2), in_added=True),
        ],
        "pilot": [
            unit("20/base/a.py:b1", sha(B1), counterpart=True),
            unit("20/head/a.py:b1", "not-in-shard-3", in_added=False, counterpart=True),
            unit("20/head/a.py:n1", sha(N1), in_added=True),
        ],
    }
    for shard, ids in (("calibration", cal_ids), ("pilot", pil_ids)):
        text_of = {sha(t): t for t in V}
        write_vectors(
            tmp,
            "jina-v2-base-code",
            shard,
            np.array([V[text_of[i]] for i in ids], dtype=np.float32),
        )
        body = "".join(json.dumps(r) + "\n" for r in rows[shard])
        (tmp / f"{shard}.units.jsonl").write_text(body, encoding="utf-8")
    return tmp


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(run, "ROOT", build(tmp_path))
    return tmp_path


def test_score_calibrate_emit_end_to_end(root: Path) -> None:
    model = "jina-v2-base-code"
    assert run.cmd_score(model, "calibration") == 0
    assert run.cmd_score(model, "pilot") == 0
    # all-measured counts PRs 10 and 11 (12 is unmeasurable, excluded); scored-only counts PR 10
    assert run.cmd_calibrate(model, 1.0, "test", "all-measured") == 0
    cut = json.loads((root / f"cutoff.{model}.json").read_text())
    assert cut["frozen"] is False and cut["target_source"] == "test"
    assert cut["cutoff"] == pytest.approx(0.6, abs=1e-6)
    assert cut["cutoff_by_denominator"]["scored-only"] == pytest.approx(0.9, abs=1e-6)
    assert cut["calibration_prs"]["unmeasurable"] == {"12": "git: boom", "14": "scan too large"}
    assert cut["calibration_prs"]["empty"] == 1  # PR 11 only: a measured PR with no new function
    assert run.cmd_emit(model, "pilot") == 5  # reached: an unfrozen cutoff is only a trial
    assert run.cmd_freeze(model, "addendum 2 (test)") == 0
    assert run.cmd_emit(model, "pilot") == 0
    pairs = [
        json.loads(line) for line in (root / f"pairs.{model}.pilot.jsonl").read_text().splitlines()
    ]
    assert [p["new_qualname"] for p in pairs] == ["n1"]  # 0.9 >= 0.6


def test_a_rerun_skips_finished_prs(root: Path) -> None:
    model = "jina-v2-base-code"
    run.cmd_score(model, "calibration")
    ledger = root / "ledger" / f"{model}.calibration.score.jsonl"
    before = ledger.read_text()
    assert run.cmd_score(model, "calibration") == 0
    assert ledger.read_text() == before


def test_calibrate_refuses_an_incomplete_ledger(root: Path) -> None:
    assert run.cmd_calibrate("jina-v2-base-code", 1.0, "t", "all-measured") == 5


def test_emit_refuses_a_cutoff_from_another_configuration(root: Path) -> None:
    model = "jina-v2-base-code"
    run.cmd_score(model, "calibration")
    run.cmd_calibrate(model, 1.0, "t", "all-measured")
    run.cmd_freeze(model, "addendum (test)")
    ids = sorted(sha(t) for t in (B1, N1))
    text_of = {sha(t): t for t in V}
    write_vectors(
        root,
        model,
        "pilot",
        np.array([V[text_of[i]] for i in ids], dtype=np.float32),
        batch_size=16,
    )
    run.cmd_score(model, "pilot")
    assert run.cmd_emit(model, "pilot") == 5  # the cutoff was calibrated at batch size 8


def test_score_refuses_vectors_that_fail_validation(root: Path) -> None:
    write_vectors(root, "jina-v2-base-code", "pilot", np.zeros((2, 2), dtype=np.float32))
    assert run.cmd_score("jina-v2-base-code", "pilot") == 5


def test_the_evaluation_shard_cannot_be_named_on_the_command_line() -> None:
    with pytest.raises(SystemExit):
        run.main(["score", "--model", "jina-v2-base-code", "--shard", "evaluation"])


def test_one_crashing_pr_is_recorded_and_the_loop_goes_on(tmp_path: Path) -> None:
    from _supervise import Ledger  # ty: ignore[unresolved-import]

    ledger = Ledger(tmp_path / "l.jsonl")

    def work(pr: str) -> dict[str, str]:
        if pr == "b":
            raise RuntimeError("boom")
        return {"pr": pr}

    assert run.run_prs(["a", "b", "c"], lambda p: p, work, ledger) == 1
    assert (
        ledger.is_done("a") and ledger.is_done("c") and not ledger.is_done("b")
    )  # reached all three
    assert "boom" in (tmp_path / "l.jsonl").read_text()


def test_an_interrupt_exits_cleanly_with_a_resume_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from _supervise import Ledger  # ty: ignore[unresolved-import]

    def work(pr: str) -> dict[str, str]:
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as stop:
        run.run_prs(["a"], lambda p: p, work, Ledger(tmp_path / "l.jsonl"))
    assert stop.value.code == 130
    assert "rerun" in capsys.readouterr().err


def test_a_stale_unit_index_is_refused_before_any_pr_is_scored(root: Path) -> None:
    rows = [
        json.loads(line) for line in (root / "calibration.units.jsonl").read_text().splitlines()
    ]
    for r in rows:
        del r["counterpart"]
    (root / "calibration.units.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert run.cmd_score("jina-v2-base-code", "calibration") == 5
    assert not (root / "ledger").exists()  # reached: nothing was scored


def test_a_shards_file_that_is_not_ok_is_refused_by_the_command_line(root: Path) -> None:
    write_meta(root, {"calibration": ["10"], "pilot": ["20"]}, {}, ok=False)
    assert run.main(["score", "--model", "jina-v2-base-code", "--shard", "calibration"]) == 5


def test_emit_refuses_a_shard_from_another_extractor_version(root: Path) -> None:
    model = "jina-v2-base-code"
    run.cmd_score(model, "calibration")
    run.cmd_score(model, "pilot")
    run.cmd_calibrate(model, 1.0, "t", "all-measured")
    run.cmd_freeze(model, "addendum (test)")
    meta = json.loads((root / "SHARDS.json").read_text())
    meta["shards"]["pilot"]["extractor_version"] = "3"
    (root / "SHARDS.json").write_text(json.dumps(meta))
    assert run.cmd_emit(model, "pilot") == 5


def test_ledger_keys_keep_prs_apart() -> None:
    assert run._key("m", "calibration", "1") != run._key("m", "calibration", "2")


def test_two_prs_in_one_shard_are_both_scored(tmp_path: Path) -> None:
    from _supervise import Ledger  # ty: ignore[unresolved-import]

    ledger = Ledger(tmp_path / "l.jsonl")
    seen: list[str] = []
    run.run_prs(
        ["1", "2"],
        lambda p: run._key("m", "calibration", p),
        lambda p: seen.append(p) or {"pr": p},
        ledger,
    )
    assert (
        seen == ["1", "2"] and ledger.completed == 2
    )  # a key without the PR would stop after the first


def test_emit_refuses_a_cutoff_file_whose_configuration_is_not_the_shards(root: Path) -> None:
    """Pilot and calibration vectors agree; only the cutoff file's record differs."""
    model = "jina-v2-base-code"
    run.cmd_score(model, "calibration")
    run.cmd_score(model, "pilot")
    run.cmd_calibrate(model, 1.0, "t", "all-measured")
    run.cmd_freeze(model, "addendum (test)")
    path = root / f"cutoff.{model}.json"
    cut = json.loads(path.read_text())
    cut["config"][4] = 64  # the recorded batch size
    path.write_text(json.dumps(cut))
    assert run.cmd_emit(model, "pilot") == 5


MODEL = "jina-v2-base-code"


def scored_and_calibrated(root: Path) -> None:
    assert run.cmd_score(MODEL, "calibration") == 0
    assert run.cmd_score(MODEL, "pilot") == 0
    assert run.cmd_calibrate(MODEL, 1.0, "t", "all-measured") == 0


def add_unit(root: Path, shard: str, row: dict[str, object]) -> None:
    path = root / f"{shard}.units.jsonl"
    path.write_text(path.read_text() + json.dumps(row) + "\n")


def test_a_ledger_is_refused_after_the_vectors_are_re_embedded(root: Path) -> None:
    run.cmd_score(MODEL, "calibration")
    text_of = {sha(t): t for t in V}
    ids = sorted(text_of)
    arr = np.array([V[text_of[i]] for i in ids], dtype=np.float32)
    write_vectors(
        root, MODEL, "calibration", arr, batch_size=16
    )  # a re-embed: another configuration
    assert run.cmd_score(MODEL, "calibration") == 5  # it would otherwise skip every finished PR
    assert run.cmd_calibrate(MODEL, 1.0, "t", "all-measured") == 5


def test_calibrate_refuses_a_ledger_holding_rows_from_two_configurations(root: Path) -> None:
    run.cmd_score(MODEL, "calibration")
    ledger = root / "ledger" / f"{MODEL}.calibration.score.jsonl"
    row = json.loads(ledger.read_text().splitlines()[0])
    row.update(key="other-key", binding={**row["binding"], "vectors_sha256": "0" * 64})
    ledger.write_text(ledger.read_text() + json.dumps(row) + "\n")
    assert run.cmd_calibrate(MODEL, 1.0, "t", "all-measured") == 5


def test_a_changed_unit_index_is_refused_everywhere(root: Path) -> None:
    scored_and_calibrated(root)
    run.cmd_freeze(MODEL, "addendum (test)")
    path = root / "calibration.units.jsonl"
    path.write_text(path.read_text() + "\n")  # same rows, other bytes
    assert run.cmd_score(MODEL, "calibration") == 5
    assert run.cmd_emit(MODEL, "pilot") == 5  # the cutoff was made from the old index


def test_calibrate_and_emit_refuse_when_any_id_had_no_vector(root: Path) -> None:
    ghost = unit("10/base/a.py:ghost", "not-in-the-shard", counterpart=True)
    add_unit(root, "calibration", ghost)
    run.cmd_score(MODEL, "calibration")
    assert (
        run.cmd_calibrate(MODEL, 1.0, "t", "all-measured") == 5
    )  # skipped functions raise the cutoff
    assert not (root / f"cutoff.{MODEL}.json").exists()  # reached: nothing was written


def test_emit_refuses_a_shard_with_an_unshipped_id(root: Path) -> None:
    add_unit(root, "pilot", unit("20/base/a.py:ghost", "not-in-the-shard", counterpart=True))
    assert run.cmd_score(MODEL, "calibration") == 0
    assert run.cmd_score(MODEL, "pilot", allow_unshipped=True) == 0  # pilot plumbing, allowed
    assert run.cmd_calibrate(MODEL, 1.0, "t", "all-measured") == 0
    run.cmd_freeze(MODEL, "addendum (test)")
    assert run.cmd_emit(MODEL, "pilot") == 5


def test_a_frozen_cutoff_is_never_overwritten_and_freezing_needs_an_addendum(root: Path) -> None:
    scored_and_calibrated(root)
    path = root / f"cutoff.{MODEL}.json"
    assert run.cmd_freeze(MODEL, "  ") == 5  # no addendum named
    assert run.cmd_freeze(MODEL, "addendum 2 (test)") == 0
    before = path.read_text()
    assert run.cmd_freeze(MODEL, "again") == 5  # not twice
    assert run.cmd_calibrate(MODEL, 2.0, "t", "all-measured") == 5
    assert path.read_text() == before


@pytest.mark.parametrize("bad", ["nan", "0", "-1", "inf"])
def test_a_target_must_be_positive_and_finite(root: Path, bad: str) -> None:
    with pytest.raises(SystemExit):
        run.main(["calibrate", "--model", MODEL, "--target", bad, "--target-source", "t"])
    assert run.cmd_calibrate(MODEL, float(bad), "t", "all-measured") == 5


def test_score_refuses_unshipped_ids_unless_explicitly_allowed(root: Path) -> None:
    add_unit(root, "pilot", unit("20/base/a.py:ghost", "not-in-the-shard", counterpart=True))
    assert run.cmd_score(MODEL, "pilot") == 5  # reached: the ghost id had no vector
    assert run.cmd_score(MODEL, "pilot", allow_unshipped=True) == 0  # explicit, for pilot plumbing
    assert run.main(["score", "--model", MODEL, "--shard", "pilot"]) == 5
    assert run.main(["score", "--model", MODEL, "--shard", "pilot", "--allow-unshipped"]) == 0
