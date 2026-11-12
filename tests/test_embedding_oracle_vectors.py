"""Strict loading of embedding vectors for #587: every refusal fires, and a good set loads.

Synthetic vectors only (no real model output exists yet). Expected values are worked by hand.
"""

# ruff: noqa: E402  (importorskip must run before the bench imports, which need numpy)

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # bench-only dependency: skip where it is absent

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_vectors import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    PINS,
    POOLING,
    SHARDS,
    VectorError,
    config_key,
    load_vectors,
    require_same_config,
)

from tests.emb587_helpers import write_meta, write_shard, write_vectors

MODEL = "jina-v2-base-code"
ARR = np.array([[3.0, 4.0], [0.0, 2.0], [1.0, 0.0]], dtype=np.float32)


def _root(tmp_path: Path) -> Path:
    write_shard(tmp_path, "pilot", ["a", "b", "c"])
    write_shard(tmp_path, "calibration", ["d", "e", "f"])
    write_meta(tmp_path, {}, {})
    return tmp_path


def test_a_good_set_loads_and_is_normalised(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR)
    vs = load_vectors(root, MODEL, "pilot")
    norms = np.linalg.norm(vs.vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-6)
    ids = sorted(vs.index, key=vs.index.get)  # type: ignore[arg-type]
    assert np.allclose(vs.get(ids[0]), [0.6, 0.8], atol=1e-6)  # (3,4) / 5, worked by hand
    assert vs.has(ids[1]) and not vs.has("nope")


@pytest.mark.parametrize(
    ("override", "why"),
    [
        ({"revision": "0" * 40}, "revision"),
        ({"model": "unixcoder-base"}, "names model"),
        ({"dtype": "float16"}, "float32"),
        ({"tf32": True}, "TF32"),
        ({"deterministic_algorithms": False}, "deterministic"),
        ({"pooling": "last"}, "pooling"),
        ({"shard_sha256": "f" * 64}, "shard_sha256"),
        ({"rows": 99}, "rows"),
        ({"shard": "calibration"}, "for shard"),
        ({"vectors_sha256": "f" * 64}, "sha256"),
        ({"dim": 3}, "width"),
        ({"determinism": {"full_second_pass_bitwise_equal": False}}, "bitwise"),
    ],
)
def test_every_manifest_claim_is_checked(
    tmp_path: Path, override: dict[str, object], why: str
) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR, **override)
    with pytest.raises(VectorError, match=why):
        load_vectors(root, MODEL, "pilot")


def test_nan_and_zero_vectors_are_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bad = ARR.copy()
    bad[1, 0] = np.nan
    write_vectors(root, MODEL, "pilot", bad)
    with pytest.raises(VectorError, match="NaN"):
        load_vectors(root, MODEL, "pilot")
    zero = ARR.copy()
    zero[2] = 0.0
    write_vectors(root, MODEL, "pilot", zero)
    with pytest.raises(VectorError, match="zero norm"):
        load_vectors(root, MODEL, "pilot")


def test_a_row_count_that_is_not_the_shard_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR[:2], rows=3)
    with pytest.raises(VectorError, match="2 vectors for 3 ids"):
        load_vectors(root, MODEL, "pilot")


def test_a_tampered_matrix_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR)
    np.save(root / "vectors" / MODEL / "pilot.npy", ARR + 1.0)
    with pytest.raises(VectorError, match="differ from the manifest"):
        load_vectors(root, MODEL, "pilot")


def test_a_tampered_shard_file_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR)
    (root / "pilot.jsonl").write_text((root / "pilot.jsonl").read_text() + "\n", encoding="utf-8")
    with pytest.raises(VectorError, match="shard file differs"):
        load_vectors(root, MODEL, "pilot")


def test_a_missing_manifest_is_refused(tmp_path: Path) -> None:
    with pytest.raises(VectorError, match="no manifest"):
        load_vectors(_root(tmp_path), MODEL, "pilot")


def test_the_evaluation_shard_cannot_be_named(tmp_path: Path) -> None:
    assert SHARDS == ("pilot", "calibration")
    with pytest.raises(VectorError, match="not authorised"):
        load_vectors(_root(tmp_path), MODEL, "evaluation")


def test_an_unknown_model_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR)
    with pytest.raises(VectorError, match="unknown model"):
        load_vectors(root, "not-a-model", "pilot")


def test_pins_and_pooling_cover_the_same_seven_models() -> None:
    assert set(PINS) == set(POOLING)
    assert len(PINS) == 7
    assert all(len(rev) == 40 for rev in PINS.values())


def test_vectors_from_different_configurations_are_never_combined(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR)
    write_vectors(root, MODEL, "calibration", ARR)
    a, b = load_vectors(root, MODEL, "pilot"), load_vectors(root, MODEL, "calibration")
    require_same_config(a, b)  # same configuration: fine
    for field, value in (("batch_size", 16), ("libs", {"torch": "2.12.0"}), ("max_len", 512)):
        write_vectors(root, MODEL, "calibration", ARR, **{field: value})
        other = load_vectors(root, MODEL, "calibration")
        with pytest.raises(VectorError, match="different configurations"):
            require_same_config(a, other)


def test_config_key_carries_every_comparability_field() -> None:
    man = json.loads(json.dumps({"revision": "r", "libs": {"b": "2", "a": "1"}, "batch_size": 8}))
    assert config_key(man)[-1] == (("a", "1"), ("b", "2"))
    assert 8 in config_key(man)


def test_a_shards_file_that_is_not_ok_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR)
    write_meta(root, {}, {}, ok=False)
    with pytest.raises(VectorError, match=r"not .ok."):
        load_vectors(root, MODEL, "pilot")


def test_a_shards_file_without_an_extractor_version_is_refused(tmp_path: Path) -> None:
    root = _root(tmp_path)
    write_vectors(root, MODEL, "pilot", ARR)
    meta = json.loads((root / "SHARDS.json").read_text())
    del meta["shards"]["pilot"]["extractor_version"]
    (root / "SHARDS.json").write_text(json.dumps(meta))
    with pytest.raises(VectorError, match="extractor_version"):
        load_vectors(root, MODEL, "pilot")


def test_ids_are_read_whole_when_a_text_holds_u2028_or_a_form_feed(tmp_path: Path) -> None:
    texts = ["x\u2028y", "p\x0cq", "plain"]
    write_shard(tmp_path, "pilot", texts, ascii_only=False)
    write_shard(tmp_path, "calibration", ["d", "e", "f"])
    assert (
        len((tmp_path / "pilot.jsonl").read_text().splitlines()) > 3
    )  # reached: splitlines() would cut rows
    write_meta(tmp_path, {}, {})
    write_vectors(tmp_path, MODEL, "pilot", ARR)
    assert len(load_vectors(tmp_path, MODEL, "pilot").index) == 3


@pytest.mark.parametrize("fault", ["reversed", "duplicated"])
def test_a_shard_whose_ids_are_not_unique_and_sorted_is_refused(tmp_path: Path, fault: str) -> None:
    root = _root(tmp_path)
    lines = (root / "pilot.jsonl").read_text().splitlines()
    assert len(lines) == 3  # reached
    lines = lines[::-1] if fault == "reversed" else [*lines, lines[0]]
    (root / "pilot.jsonl").write_text("".join(line + "\n" for line in lines))
    write_meta(root, {}, {})
    write_vectors(root, MODEL, "pilot", ARR if fault == "reversed" else np.vstack([ARR, ARR[:1]]))
    with pytest.raises(VectorError, match="unique and sorted"):
        load_vectors(root, MODEL, "pilot")
