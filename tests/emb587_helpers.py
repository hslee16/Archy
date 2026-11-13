"""Synthetic fixtures for the #587 generator tests. No real vector and no model: every
vector here is hand-built, so every expected cosine is worked by hand.
"""

# ruff: noqa: E402  (importorskip must run before the bench imports, which need numpy)

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # bench-only dependency: skip where it is absent

# bench/ is not a package; appended (not inserted first) so its generic names cannot shadow others.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))
from embedding_oracle_vectors import (  # ty: ignore[unresolved-import]
    CODE_REPO,
    CODE_REV,
    PINS,
    POOLING,
    VectorSet,
    config_key,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_shard(root: Path, shard: str, texts: list[str], ascii_only: bool = True) -> list[str]:
    """Write `<shard>.jsonl` (sorted unique ids, the extractor's contract); return the ids.

    `ascii_only=False` writes raw non-ASCII, so a text with U+2028 or a form feed puts that
    character on the line itself (what a reader using `splitlines()` would split on).
    """
    rows = sorted({sha(t): t for t in texts}.items())
    root.mkdir(parents=True, exist_ok=True)
    body = "".join(
        json.dumps({"id": i, "text": t}, ensure_ascii=ascii_only) + "\n" for i, t in rows
    )
    (root / f"{shard}.jsonl").write_text(body, encoding="utf-8")
    return [i for i, _ in rows]


def write_meta(
    root: Path,
    pr_keys: dict[str, list[str]],
    drops: dict[str, dict[str, str]],
    ok: bool = True,
) -> None:
    """Describe every shard file present (pilot and calibration always; evaluation if written)."""
    shards: dict[str, object] = {}
    for shard in ("pilot", "calibration", "evaluation"):
        if shard == "evaluation" and not (root / "evaluation.jsonl").exists():
            continue
        data = (root / f"{shard}.jsonl").read_bytes()
        shards[shard] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "rows": data.count(b"\n"),
            "pr_keys": pr_keys.get(shard, []),
            "drops": drops.get(shard, {}),
            "extractor_version": "2",
        }
    (root / "SHARDS.json").write_text(json.dumps({"ok": ok, "shards": shards}), encoding="utf-8")


def remote_code(model: str) -> dict[str, object]:
    """The `remote_code` block the embedding job writes, as the loader requires it per model."""
    none: dict[str, object] = {
        "code_repo": None,
        "code_rev": None,
        "module": None,
        "executed_file_sha256": None,
        "tokenizer_code": {},
    }
    if model == "jina-v2-base-code":
        return {
            **none,
            "code_repo": CODE_REPO,
            "code_rev": CODE_REV,
            "module": f"transformers_modules.jinaai.pinned.{CODE_REV}.modeling_bert",
            "executed_file_sha256": "ab" * 32,
        }
    if model == "bge-code-v1":
        return {**none, "tokenizer_code": {"tokenization_qwen.py": "cd" * 32}}
    return none


def manifest(root: Path, model: str, shard: str, arr: np.ndarray) -> dict[str, object]:
    meta = json.loads((root / "SHARDS.json").read_text(encoding="utf-8"))["shards"][shard]
    npy = root / "vectors" / model / f"{shard}.npy"
    return {
        "model": model,
        "revision": PINS[model],
        "shard": shard,
        "shard_sha256": meta["sha256"],
        "rows": meta["rows"],
        "dim": int(arr.shape[1]),
        "vectors_sha256": hashlib.sha256(npy.read_bytes()).hexdigest(),
        "dtype": "float32",
        "tf32": False,
        "deterministic_algorithms": True,
        "cublas_workspace_config": ":4096:8",
        "batch_size": 8,
        "pooling": POOLING[model],
        "max_len": 8192,
        "libs": {"torch": "2.11.0", "transformers": "4.57.1", "cuda": "13.0"},
        "determinism": {"full_second_pass_bitwise_equal": True, "max_abs_diff": 0.0},
        "remote_code": remote_code(model),
    }


def write_vectors(
    root: Path, model: str, shard: str, arr: np.ndarray, /, **override: object
) -> dict[str, object]:
    """Write `vectors/<model>/<shard>.npy` and a consistent manifest; `override` edits it."""
    vdir = root / "vectors" / model
    vdir.mkdir(parents=True, exist_ok=True)
    np.save(vdir / f"{shard}.npy", arr)
    man = {**manifest(root, model, shard, arr), **override}
    (vdir / f"manifest.{shard}.json").write_text(json.dumps(man), encoding="utf-8")
    return man


def unit(key: str, fid: str, **flags: bool | int | None) -> dict[str, object]:
    """A unit row. `key` is "pr/side/path:qualname"; flags: in_added, counterpart, occurrence."""
    pr, side, rest = key.split("/", 2)
    path, qualname = rest.rsplit(":", 1)
    return {
        "counterpart": flags.get("counterpart", False),
        "occurrence": flags.get("occurrence", 0),
        "id": fid,
        "in_added": flags.get("in_added"),
        "line": 1,
        "nodes": 40,
        "path": path,
        "pr": pr,
        "qualname": qualname,
        "repo": "o/r",
        "shape": f"shape-{fid}",
        "side": side,
    }


def vector_set(
    vecs: Mapping[str, Sequence[float]],
    model: str = "jina-v2-base-code",
    shard: str = "calibration",
) -> VectorSet:
    """A VectorSet over hand-built vectors, normalised like `load_vectors` does."""
    arr = np.array(list(vecs.values()), dtype=np.float32)
    arr = arr / np.linalg.norm(arr, axis=1, keepdims=True)
    man = {
        "revision": PINS[model],
        "dtype": "float32",
        "tf32": False,
        "deterministic_algorithms": True,
    }
    return VectorSet(model, shard, {k: i for i, k in enumerate(vecs)}, arr, config_key(man))
