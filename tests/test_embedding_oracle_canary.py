"""The #587 canary: B finds a loop-comprehension pair that A misses; unrelated ones do not pair.

Pre-registration, "Harness requirements": a fixture where a loop and a comprehension compute the
same thing (Type-4) must be found by B and missed by A, and two unrelated functions of similar
length must not be paired. Each branch is asserted reached before its outcome.

Two layers. (1) The plumbing, on hand-built vectors, always runs: A is archy's real exact and
near-miss tiers on the real fixture files; B is the real `score_pr` / `b_pairs` on vectors whose
cosines are set by hand. (2) The model, on the embedding job's own `canary.json` per model, skips
while no vectors exist.

Limits, stated plainly. (1) The B side of layer (1) uses HAND-SET vectors, so it proves the
plumbing (top-1, cutoff, cap, no pairing of unrelated functions) and says nothing about whether any
model embeds a loop and a comprehension close together; only layer (2), on a model's returned
`canary.json`, does that. (2) These tests need numpy, which is not an archy dependency, so CI
(`uv sync`) skips them; run them with `uv run --with numpy python -m pytest`.

Note: the embedding job's canary fixture is small (under the 30-node floor), so "missed by A" is
vacuous on it; the A-side assertion here therefore uses functions that clear the floor, and
asserts so.
"""

# ruff: noqa: E402  (importorskip must run before the bench imports, which need numpy)

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")  # bench-only dependency: skip where it is absent

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from diff_duplicates_precision import MIN_NODES  # ty: ignore[unresolved-import]  (sys.path above)
from embedding_oracle_generate import b_pairs, score_pr  # ty: ignore[unresolved-import]
from embedding_oracle_units import PrUnits, UnitFn  # ty: ignore[unresolved-import]

from archy.complexity import compute_function_complexity
from archy.duplicates import compute_duplicates, compute_near_duplicates
from archy.graph import parse_project
from tests.emb587_helpers import sha, vector_set

LOOP = """def normalise_records(records, minimum):
    cleaned = []
    for record in records:
        name = record.get("name")
        if name is None:
            continue
        score = record.get("score", 0)
        if score < minimum:
            continue
        cleaned.append({"name": name.strip().lower(), "score": score})
    return cleaned
"""
COMPREHENSION = """def keep_strong(entries, floor):
    return [
        {"name": e["name"].strip().lower(), "score": e.get("score", 0)}
        for e in entries
        if e.get("name") is not None and e.get("score", 0) >= floor
    ]
"""
UNRELATED = """def open_connection(host, port, attempts):
    import socket
    last_error = None
    for attempt in range(attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0 + attempt)
        try:
            sock.connect((host, port))
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    raise ConnectionError(f"{host}:{port} unreachable after {attempts} attempts") from last_error
"""


def sizes(text: str) -> int:
    return compute_function_complexity(text.encode())[0].size


def a_cluster_members(tmp_path: Path, files: dict[str, str]) -> list[set[str]]:
    """The names of the members of every A cluster (exact and near-miss) over `files`."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name, text in files.items():
        (pkg / name).write_text(text)
    modules, results = parse_project(tmp_path, max_modules=100)
    groups = [
        *compute_duplicates(modules, results, min_size=MIN_NODES),
        *compute_near_duplicates(modules, results, min_size=MIN_NODES),
    ]
    return [{m.qualified_name for m in g.members} for g in groups]


def test_a_misses_the_loop_comprehension_pair_and_the_unrelated_pair(tmp_path: Path) -> None:
    for text in (LOOP, COMPREHENSION, UNRELATED):
        assert sizes(text) >= MIN_NODES  # reached: A really considered every function
    clusters = a_cluster_members(
        tmp_path, {"loop.py": LOOP, "comp.py": COMPREHENSION, "other.py": UNRELATED}
    )
    assert not any(
        {"normalise_records", "keep_strong"} <= c for c in clusters
    )  # Type-4: missed by A
    assert not any({"normalise_records", "open_connection"} <= c for c in clusters)


def test_a_does_find_a_plain_copy_so_the_canary_can_fail(tmp_path: Path) -> None:
    """The A-side check is only evidence if A can say yes: a renamed copy of the loop clusters."""
    clusters = a_cluster_members(
        tmp_path, {"loop.py": LOOP, "copy.py": LOOP.replace("normalise_records", "normalise_again")}
    )
    assert any({"normalise_records", "normalise_again"} <= c for c in clusters)


def fn(text: str, qual: str, nodes: int) -> UnitFn:
    return UnitFn(
        id=sha(text),
        pr="1",
        repo="o/r",
        path=f"{qual}.py",
        qualname=qual,
        nodes=nodes,
        shape=f"s-{qual}",
    )


# hand-set cosines: loop~comprehension 0.96, both ~ unrelated 0.2 (from the 2-D unit vectors below)
VECS = {
    sha(LOOP): (1.0, 0.0),
    sha(COMPREHENSION): (
        0.96,
        0.28,
    ),  # cos with LOOP = 0.96 / 1.0 = 0.96 (norm 1.0 by construction: 0.96^2 + 0.28^2 = 1)
    sha(UNRELATED): (0.2, 0.9798),  # cos with LOOP = 0.2
}


def test_b_finds_the_type4_pair_and_does_not_pair_the_unrelated_function() -> None:
    vs = vector_set(VECS)
    units = PrUnits(
        "1",
        "o/r",
        (fn(COMPREHENSION, "keep_strong", 83),),
        (fn(LOOP, "normalise_records", 101), fn(UNRELATED, "open_connection", 90)),
        0,
    )
    scored = score_pr(units, vs).scored
    assert len(scored) == 1  # reached: B scored the new function
    assert (
        scored[0].existing.qualname == "normalise_records"
    )  # top-1 is the loop, not the unrelated function
    assert scored[0].sim == pytest.approx(0.96, abs=1e-3)
    cutoff = 0.9  # between 0.2 and 0.96
    assert [p.existing_qualname for p in b_pairs([score_pr(units, vs)], cutoff)] == [
        "normalise_records"
    ]
    lone = PrUnits(
        "1",
        "o/r",
        (fn(UNRELATED, "open_connection", 90),),
        (fn(LOOP, "normalise_records", 101),),
        0,
    )
    other = score_pr(lone, vs)
    assert other.scored and other.scored[0].sim == pytest.approx(
        0.2, abs=1e-3
    )  # reached: it was scored
    assert b_pairs([other], cutoff) == []  # two unrelated functions are not paired


CANARY_ROOT = Path(__file__).resolve().parent.parent / "bench" / "cache" / "emb587" / "vectors"
COS = re.compile(r"cos\(type4 pair\)=(-?[0-9.]+) vs cos\(unrelated pair\)=(-?[0-9.]+)")


def real_canaries() -> list[Path]:
    return sorted(CANARY_ROOT.glob("*/canary.json")) if CANARY_ROOT.exists() else []


@pytest.mark.skipif(not real_canaries(), reason="no embedding vectors have been returned yet")
@pytest.mark.parametrize("path", real_canaries(), ids=lambda p: p.parent.name)
def test_every_returned_model_embeds_the_type4_pair_closer_than_the_unrelated_pair(
    path: Path,
) -> None:
    canary = json.loads(path.read_text(encoding="utf-8"))
    for name in ("mixed-batch", "pair-order", "no-prompt", "type4"):
        assert canary[name]["reached"] > 0  # each branch was reached before its outcome is read
    found = COS.search(canary["type4"]["detail"])
    assert found, canary["type4"]["detail"]
    assert float(found.group(1)) > float(found.group(2))
    assert canary["type4"]["ok"] is True
