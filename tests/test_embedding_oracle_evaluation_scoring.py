"""Scoring the registered evaluation shard of #587: refused until the cutoff is frozen, refused
for any draw but the registered one, and then scored and emitted like the other shards.

Synthetic 2-D vectors only; every expected cosine is worked by hand (cos(n1, b1) = 0.9).
"""

# ruff: noqa: E402  (importorskip must run before the bench imports, which need numpy)

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # bench-only dependency: skip where it is absent

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import embedding_oracle_generate_run as run  # ty: ignore[unresolved-import]  (path set above)
import embedding_oracle_vectors as vec  # ty: ignore[unresolved-import]  (path set above)
from embedding_oracle_registered import keys_sha256  # ty: ignore[unresolved-import]

from tests.emb587_helpers import sha, unit, write_meta, write_shard, write_vectors

MODEL = "jina-v2-base-code"
B1, B2, N1, N2 = "b1 text", "b2 text", "n1 text", "n2 text"
V = {B1: (0.9, 0.43588989), B2: (0.8, 0.6), N1: (1.0, 0.0), N2: (0.0, 1.0)}
TEXT_OF = {sha(t): t for t in V}


def _units(pr: str, texts_new: list[str]) -> list[dict[str, object]]:
    rows = [
        unit(f"{pr}/base/a.py:b1", sha(B1), counterpart=True),
        unit(f"{pr}/head/a.py:b1", f"unshipped-{pr}", in_added=False, counterpart=True),
    ]
    rows += [unit(f"{pr}/head/a.py:{t[:2]}", sha(t), in_added=True) for t in texts_new]
    return rows


def _build(tmp: Path) -> Path:
    shard_texts = {
        "calibration": [B1, B2, N1, N2],
        "pilot": [B1, N1],
        "evaluation": [B1, N1],
    }
    ids = {s: write_shard(tmp, s, texts) for s, texts in shard_texts.items()}
    write_meta(
        tmp,
        {"calibration": ["10"], "pilot": ["20"], "evaluation": ["30"]},
        {"calibration": {}, "pilot": {}, "evaluation": {"31": run.NO_NEW_FUNCTION}},
    )
    units = {
        "calibration": [
            *_units("10", [N1, N2]),
            unit("10/base/a.py:b2", sha(B2), counterpart=True),
        ],
        "pilot": _units("20", [N1]),
        "evaluation": _units("30", [N1]),
    }
    for shard, shard_ids in ids.items():
        arr = np.array([V[TEXT_OF[i]] for i in shard_ids], dtype=np.float32)
        write_vectors(tmp, MODEL, shard, arr)
        body = "".join(json.dumps(r) + "\n" for r in units[shard])
        (tmp / f"{shard}.units.jsonl").write_text(body, encoding="utf-8")
    return tmp


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(run, "ROOT", _build(tmp_path))
    return tmp_path


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make this synthetic evaluation draw ({30, 31}) the registered one."""
    monkeypatch.setattr(vec, "EVALUATION_KEYS_SHA256", keys_sha256(["30", "31"]))


def _calibrated(model: str = MODEL, freeze: bool = True) -> None:
    assert run.cmd_score(model, "calibration") == 0
    assert run.cmd_calibrate(model, 1.0, "test", "all-measured") == 0
    if freeze:
        assert run.cmd_freeze(model, "addendum (test)") == 0


def test_the_evaluation_shard_is_a_valid_command_line_choice_but_refused_here(root: Path) -> None:
    assert run.main(["score", "--model", MODEL, "--shard", "evaluation"]) == 5  # no cutoff yet


def test_scoring_the_evaluation_shard_needs_a_frozen_cutoff(
    root: Path, registered: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _calibrated(freeze=False)
    cut = json.loads((root / f"cutoff.{MODEL}.json").read_text())
    assert cut["frozen"] is False, "the fixture must reach the unfrozen branch"
    assert run.cmd_score(MODEL, "evaluation") == 5
    assert "frozen" in capsys.readouterr().err
    assert not (root / "ledger" / f"{MODEL}.evaluation.score.jsonl").exists()


def test_a_draw_that_is_not_the_registered_one_is_refused_even_when_frozen(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _calibrated()  # no `registered` fixture: the real registered hash does not match {30, 31}
    assert run.cmd_score(MODEL, "evaluation") == 5
    assert "registered" in capsys.readouterr().err
    assert run.cmd_emit(MODEL, "evaluation") == 5


def test_a_frozen_cutoff_scores_and_emits_the_evaluation_shard(
    root: Path, registered: None
) -> None:
    _calibrated()
    assert run.cmd_score(MODEL, "evaluation") == 0
    assert run.cmd_emit(MODEL, "evaluation") == 0
    pairs = [
        json.loads(line)
        for line in (root / f"pairs.{MODEL}.evaluation.jsonl").read_text().splitlines()
    ]
    # cutoff 0.6 (calibrated on the calibration shard), n1 -> b1 at 0.9 clears it
    assert [p["new_qualname"] for p in pairs] == ["n1"]
    assert pairs[0]["pr"] == "30"


def test_the_evaluation_ledger_is_kept_apart_from_the_other_shards(
    root: Path, registered: None
) -> None:
    _calibrated()
    assert run.cmd_score(MODEL, "evaluation") == 0
    rows = [
        json.loads(line)
        for line in (root / "ledger" / f"{MODEL}.evaluation.score.jsonl").read_text().splitlines()
    ]
    assert [r["key"] for r in rows] == [f"{MODEL}|evaluation|30"]
    cal = (root / "ledger" / f"{MODEL}.calibration.score.jsonl").read_text()
    assert "|evaluation|" not in cal


def test_calibrate_and_freeze_still_cannot_take_a_shard() -> None:
    for cmd in ("calibrate", "freeze"):
        with pytest.raises(SystemExit):
            run.main([cmd, "--model", MODEL, "--shard", "evaluation"])
