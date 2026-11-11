"""Tests for the #548 blind labeling packets.

Blindness is the property that matters: a packet must carry nothing the
pre-registration hides from labelers. The test builds a packet from a firing whose
hidden fields are all set to recognisable values, asserts the fixture really holds
them, then asserts none of them reach the packet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# bench/ is not a package; append it (not insert(0)) so its generic module names
# cannot shadow stdlib/installed imports, mirroring tests/test_q1b_run.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

import diff_duplicates_packets as pk  # ty: ignore[unresolved-import]  (added to sys.path above)
import diff_duplicates_precision as ddp  # ty: ignore[unresolved-import]  (added to sys.path above)

SOURCE = """\
import functools


def first(x):
    return x + 1


@functools.cache
def second(y):
    total = 0
    for item in y:
        total += item
    return total


def third(z):
    return z
"""


def test_function_source_takes_the_decorated_function_exactly() -> None:
    got = pk.function_source(SOURCE, 9)  # the `def second` line
    assert got is not None
    assert got.splitlines()[0] == "@functools.cache"
    assert got.splitlines()[-1] == "    return total"
    assert "def third" not in got and "def first" not in got


def test_function_source_is_none_when_no_def_is_on_the_line() -> None:
    assert pk.function_source(SOURCE, 1) is None


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "r"
    repo.mkdir()
    ddp._git(repo, "init", "-q")
    head = ddp._commit_fixture(repo, {"pkg/a.py": SOURCE, "pkg/b.py": SOURCE}, "c")
    return repo, head


def _firing_row(head: str) -> tuple[dict, dict]:
    firing = {
        "category": "near_miss",
        "exact": True,
        "similarity": 0.9123,
        "size": 4177,
        "members": [
            {"path": "pkg/a.py", "qualified_name": "second", "line": 9, "new": False},
            {"path": "pkg/b.py", "qualified_name": "second", "line": 9, "new": True},
        ],
    }
    row = {
        "repo": "o/r",
        "number": 31337,
        "agent": "Claude_Code",
        "merged": True,
        "base": "b" * 40,
        "head": head,
    }
    return row, firing


def test_packet_hides_everything_the_prereg_hides(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    row, firing = _firing_row(head)
    # The hidden fields exist in the inputs, so absence below is not vacuous.
    assert firing["similarity"] == 0.9123 and row["number"] == 31337

    packet = pk.build_packet("1:0", row, firing, repo)
    blob = json.dumps(packet)

    assert set(packet) == {"id", "repo", "instructions", "new", "existing"}
    for hidden in ("0.9123", "near_miss", "similarity", "4177", "31337", "Claude_Code", head):
        assert hidden not in blob
    assert [m["path"] for m in packet["new"]] == ["pkg/b.py"]
    assert [m["path"] for m in packet["existing"]] == ["pkg/a.py"]
    assert packet["new"][0]["source"].startswith("@functools.cache")
    assert "TRUE" in packet["instructions"] and "FALSE" in packet["instructions"]


def test_unreadable_member_raises_instead_of_building_a_packet(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    row, firing = _firing_row(head)
    firing["members"][0]["line"] = 1  # no def there
    try:
        pk.build_packet("1:0", row, firing, repo)
    except LookupError:
        return
    raise AssertionError("expected LookupError")


def test_draw_takes_all_up_to_the_cap_and_a_seeded_sample_above_it() -> None:
    ids = [f"{i}:0" for i in range(150)]
    assert pk.draw_for_labeling(ids[:120]) == sorted(ids[:120])
    drawn = pk.draw_for_labeling(ids)
    assert len(drawn) == 120  # the fixture exceeds the cap, so the draw has to bind
    assert set(drawn) < set(ids)
    assert drawn != sorted(ids)[:120]  # a random draw, not the first 120 by id
    assert drawn == pk.draw_for_labeling(ids)
    assert drawn == pk.draw_for_labeling(list(reversed(ids)))  # order of arrival is irrelevant
