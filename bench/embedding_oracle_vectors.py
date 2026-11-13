"""Strict loading of the vectors an embedding job returns for the #587 oracle.

The embedding runs on a single CUDA GPU host and returns, per model and shard, a
float32 matrix plus a manifest. Nothing here trusts either: every claim the manifest
makes is checked against the shard on this machine, the addendum's revision pins, and
the other shard of the same model. Vectors from different dtypes, batch sizes, library
versions or determinism settings are never merged, and a cutoff is only applied to the
configuration that calibrated it (addendum 1, "Numerics and what is recorded").

Layout this module reads (all gitignored, under `bench/cache/emb587/`):

    pilot.jsonl, calibration.jsonl, SHARDS.json      (the extractor's output)
    vectors/<model>/<shard>.npy                      float32 [rows, dim], shard row order
    vectors/<model>/manifest.<shard>.json

Vectors are raw pooled; this module L2-normalises them, so a dot product is a cosine.

archy:owns        VectorError, VectorSet, check_manifest, check_remote_code,
                  check_shards_meta, config_key, file_sha256, load_vectors,
                  read_shards_meta, require_same_config, shard_ids
archy:mirrored-by VectorError -> bench.embedding_oracle_binding,
                  bench.embedding_oracle_generate_run,
                  VectorSet -> bench.embedding_oracle_binding,
                  bench.embedding_oracle_generate
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np  # ty: ignore[unresolved-import]  (bench-only: `uv run --with numpy`)

# Hugging Face commit pins, copied from addendum 1 (table and "Pins"). A manifest that
# names any other revision is refused, whatever else it says.
PINS: dict[str, str] = {
    "jina-v2-base-code": "516f4baf13dec4ddddda8631e019b5737c8bc250",
    "jina-code-0.5b": "4db235132dafbe56a8b9c5f59b59795ecf58a4a7",
    "jina-code-1.5b": "39aeb4fb9b60f930934c78ae5d749a46287c248a",
    "qwen3-embedding-0.6b": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
    "qwen3-embedding-8b": "1d8ad4ca9b3dd8059ad90a75d4983776a23d44af",
    "unixcoder-base": "5604afdc964f6c53782a6813140ade5216b99006",
    "bge-code-v1": "bd67852057c5d7ddcc7b8234d9d6c410117ed851",
}
POOLING: dict[str, str] = {
    "jina-v2-base-code": "mean",
    "unixcoder-base": "mean",
    "jina-code-0.5b": "last",
    "jina-code-1.5b": "last",
    "qwen3-embedding-0.6b": "last",
    "qwen3-embedding-8b": "last",
    "bge-code-v1": "last",
}
SHARDS = ("pilot", "calibration")  # the evaluation shard has no name here, on purpose
CODE_REPO = "jinaai/jina-bert-v2-qk-post-norm"  # the primary's remote code, addendum 1 "Pins"
CODE_REV = "3baf9e3ac750e76e8edd3019170176884695fb94"
REQUIRED_LIBS = ("torch", "transformers", "cuda")
CUBLAS_FORM = re.compile(r"(:[0-9]+:[0-9]+)+")
CONFIG_FIELDS = (
    "dtype",
    "tf32",
    "deterministic_algorithms",
    "cublas_workspace_config",
    "batch_size",
    "pooling",
    "max_len",
    "revision",
)


class VectorError(ValueError):
    """The vectors, or what their manifest says about them, cannot be trusted."""


@dataclass(frozen=True)
class VectorSet:
    """Normalised vectors for one (model, shard), addressable by text id."""

    model: str
    shard: str
    index: dict[str, int]
    vecs: np.ndarray
    config: tuple[object, ...]
    sha256: str = ""

    def get(self, text_id: str) -> np.ndarray:
        return self.vecs[self.index[text_id]]

    def has(self, text_id: str) -> bool:
        return text_id in self.index


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def shard_ids(path: Path) -> list[str]:
    """A shard's ids in file order (sorted and unique: the extractor's contract)."""
    ids = [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").split("\n") if line]
    if ids != sorted(set(ids)):
        raise VectorError(f"{path.name}: ids are not unique and sorted")
    return ids


def config_key(manifest: dict[str, object]) -> tuple[object, ...]:
    """Everything that makes two vectors comparable: numerics, batch size, context, libraries."""
    libs = tuple(sorted((manifest.get("libs") or {}).items()))
    remote = json.dumps(manifest.get("remote_code"), sort_keys=True)
    return (*(manifest.get(f) for f in CONFIG_FIELDS), libs, remote)


def _require(cond: bool, what: str) -> None:
    if not cond:
        raise VectorError(what)


def check_manifest(man: dict[str, object], model: str, shard: str, meta: dict[str, object]) -> None:
    """Every claim of the manifest that can be checked without the matrix."""
    _require(model in PINS, f"unknown model {model!r}")
    _require(man.get("model") == model, f"manifest names model {man.get('model')!r}, not {model!r}")
    _require(man.get("revision") == PINS[model], f"revision {man.get('revision')!r} is not the pin")
    _require(man.get("shard") == shard, f"manifest is for shard {man.get('shard')!r}")
    _require(man.get("dtype") == "float32", f"dtype {man.get('dtype')!r} is not float32")
    _require(man.get("tf32") is False, "TF32 was not disabled")
    _require(man.get("deterministic_algorithms") is True, "deterministic algorithms were not on")
    _require(
        man.get("pooling") == POOLING[model],
        f"pooling {man.get('pooling')!r} is not the addendum's",
    )
    want = meta["shards"][shard]
    _require(
        man.get("shard_sha256") == want["sha256"], "manifest shard_sha256 differs from SHARDS.json"
    )
    _require(man.get("rows") == want["rows"], "manifest rows differ from SHARDS.json")
    det = man.get("determinism") or {}
    _require(det.get("full_second_pass_bitwise_equal") is True, "not bitwise deterministic")
    _check_comparable(man)
    check_remote_code(man, model)


def _check_comparable(man: dict[str, object]) -> None:
    """Every field that makes two vector sets comparable must be present, or two manifests
    that both lack it would compare equal."""
    for field in CONFIG_FIELDS:
        _require(man.get(field) is not None, f"manifest lacks {field!r}")
    for field in ("batch_size", "max_len"):
        _check_positive_int(man, field)
    _check_cublas(man.get("cublas_workspace_config"))
    libs = man.get("libs")
    _require(isinstance(libs, dict), "manifest lacks the library versions")
    for lib in REQUIRED_LIBS:
        _require(bool(libs.get(lib)), f"manifest libs lack {lib!r}")  # type: ignore[union-attr]


def _check_positive_int(man: dict[str, object], field: str) -> None:
    value = man.get(field)
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    _require(ok, f"{field} must be an integer of at least 1, not {value!r}")


def _check_cublas(value: object) -> None:
    """CUDA's forms of CUBLAS_WORKSPACE_CONFIG: one or more `:SIZE:COUNT`, e.g. `:4096:8`."""
    ok = isinstance(value, str) and CUBLAS_FORM.fullmatch(value) is not None
    _require(ok, f"cublas_workspace_config {value!r} is not a CUDA form such as ':4096:8'")


def check_remote_code(man: dict[str, object], model: str) -> None:
    """The code that ran, as the embedding job recorded it (addendum 1: pin what executes)."""
    rc = man.get("remote_code")
    _require(isinstance(rc, dict), "manifest lacks remote_code")
    if model == "jina-v2-base-code":
        _require(rc.get("code_repo") == CODE_REPO, "remote code repository is not the pinned one")  # type: ignore[union-attr]
        _require(rc.get("code_rev") == CODE_REV, "remote code revision is not the pin")  # type: ignore[union-attr]
        _require(
            CODE_REV in str(rc.get("module")), "the executed module is not the pinned snapshot"
        )  # type: ignore[union-attr]
        _require(bool(rc.get("executed_file_sha256")), "no hash of the executed code file")  # type: ignore[union-attr]
    elif model == "bge-code-v1":
        tok = rc.get("tokenizer_code")  # type: ignore[union-attr]
        _require(
            isinstance(tok, dict) and "tokenization_qwen.py" in tok, "tokenizer code not named"
        )
    else:
        _require(rc.get("code_rev") is None, f"{model} should run no remote model code")  # type: ignore[union-attr]


def check_shards_meta(meta: dict[str, object]) -> None:
    """SHARDS.json must say the extractor finished cleanly and which extractor wrote each shard."""
    _require(meta.get("ok") is True, 'SHARDS.json is not "ok": regenerate the shards')
    for name in SHARDS:
        _require(
            "extractor_version" in meta["shards"].get(name, {}),
            f"{name}: no extractor_version in SHARDS.json",
        )


def read_shards_meta(root: Path) -> dict[str, object]:
    meta = json.loads((root / "SHARDS.json").read_text(encoding="utf-8"))
    check_shards_meta(meta)
    return meta


def load_vectors(root: Path, model: str, shard: str) -> VectorSet:
    """The normalised vectors for `model` on `shard`, after every check, or VectorError."""
    _require(shard in SHARDS, f"shard {shard!r} is not authorised")
    _require(model in PINS, f"unknown model {model!r}")
    meta = read_shards_meta(root)
    vdir = root / "vectors" / model
    try:
        man = json.loads((vdir / f"manifest.{shard}.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VectorError(f"no manifest at {exc.filename}") from exc
    check_manifest(man, model, shard, meta)
    shard_file = root / f"{shard}.jsonl"
    _require(
        file_sha256(shard_file) == meta["shards"][shard]["sha256"],
        "shard file differs from SHARDS.json",
    )
    npy = vdir / f"{shard}.npy"
    _require(
        file_sha256(npy) == man.get("vectors_sha256"), "vectors differ from the manifest's sha256"
    )
    arr = np.load(npy, allow_pickle=False)
    ids = shard_ids(shard_file)
    return _build((model, shard), ids, arr, man)


def _build(
    which: tuple[str, str], ids: list[str], arr: np.ndarray, man: dict[str, object]
) -> VectorSet:
    _require(arr.dtype == np.float32 and arr.ndim == 2, "vectors are not a float32 matrix")
    _require(arr.shape[0] == len(ids), f"{arr.shape[0]} vectors for {len(ids)} ids")
    _require(arr.shape[1] == man.get("dim"), "vector width differs from the manifest")
    _require(bool(np.isfinite(arr).all()), "vectors contain NaN or Inf")
    wide = arr.astype(np.float64)  # a float32 norm overflows near 3e19 and would zero the row
    norms = np.linalg.norm(wide, axis=1, keepdims=True)
    _require(bool((norms > 0).all()), "a vector has zero norm")
    unit = (wide / norms).astype(np.float32)
    index = {t: i for i, t in enumerate(ids)}
    model, shard = which
    return VectorSet(model, shard, index, unit, config_key(man), str(man.get("vectors_sha256")))


def require_same_config(a: VectorSet, b: VectorSet) -> None:
    """Refuse to combine or compare vectors from different configurations."""
    _require(a.model == b.model, f"different models: {a.model} and {b.model}")
    _require(
        a.config == b.config,
        f"{a.model}: {a.shard} and {b.shard} come from different configurations",
    )
