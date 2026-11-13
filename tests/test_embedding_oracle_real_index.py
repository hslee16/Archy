"""The extractor's real shards and unit index against the loader's join rules (read-only).

Skips when `bench/cache/emb587` is absent (it is gitignored, and CI never has it). Set
`EMB587_ROOT` to read a copy elsewhere. Nothing here writes, embeds, scores or reads a label.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_units import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    check_schema,
    load_units,
    split_pr,
)

DEFAULT = Path(__file__).resolve().parent.parent / "bench" / "cache" / "emb587"
ROOT = Path(os.environ.get("EMB587_ROOT", DEFAULT))
HAVE = (ROOT / "SHARDS.json").exists() and (ROOT / "pilot.units.jsonl").exists()
SHARDS = ("pilot", "calibration")


def shard_ids(shard: str) -> set[str]:
    lines = (ROOT / f"{shard}.jsonl").read_text(encoding="utf-8").split("\n")
    return {json.loads(line)["id"] for line in lines if line}


@pytest.mark.skipif(not HAVE, reason="no extractor output under bench/cache/emb587")
def test_shards_file_says_ok_and_names_the_extractor_version() -> None:
    meta = json.loads((ROOT / "SHARDS.json").read_text(encoding="utf-8"))
    assert meta["ok"] is True
    assert all("extractor_version" in meta["shards"][s] for s in SHARDS)


@pytest.mark.skipif(not HAVE, reason="no extractor output under bench/cache/emb587")
@pytest.mark.parametrize("shard", SHARDS)
def test_the_join_key_is_unique_and_every_row_has_it(shard: str) -> None:
    units = load_units(ROOT / f"{shard}.units.jsonl")
    keys = []
    for pr, rows in units.items():
        check_schema(rows)
        keys += [(pr, r["side"], r["path"], r["qualname"], r["occurrence"]) for r in rows]
    assert keys  # reached
    assert len(keys) == len(set(keys))


@pytest.mark.skipif(not HAVE, reason="no extractor output under bench/cache/emb587")
@pytest.mark.parametrize("shard", SHARDS)
def test_every_id_the_generator_will_look_up_is_in_the_shard(shard: str) -> None:
    """New head functions and base neighbours are shipped; a head row that was not added is not."""
    ids, units = shard_ids(shard), load_units(ROOT / f"{shard}.units.jsonl")
    looked_up = 0
    for pr, rows in units.items():
        split = split_pr(pr, rows)
        for fn in (*split.new, *split.existing):
            looked_up += 1
            assert fn.id in ids, (pr, fn.path, fn.qualname)
    assert looked_up > 0


@pytest.mark.skipif(not HAVE, reason="no extractor output under bench/cache/emb587")
@pytest.mark.parametrize("shard", SHARDS)
def test_shipping_prs_are_exactly_the_prs_with_a_new_function(shard: str) -> None:
    meta = json.loads((ROOT / "SHARDS.json").read_text(encoding="utf-8"))["shards"][shard]
    units = load_units(ROOT / f"{shard}.units.jsonl")
    assert sorted(meta["pr_keys"]) == sorted(units)
    assert all(split_pr(pr, rows).new for pr, rows in units.items())
    assert not set(meta["drops"]) & set(units)  # a dropped PR ships no unit
