"""Which functions are new and which may be neighbours, for the #587 oracle.

Expected values are worked by hand. Each test asserts its fixture reaches the branch
(the PR really has a deleted function, a head-only function, ...) before asserting.
The join key is (pr, side, path, qualname, occurrence); a head row that was not added carries
an id that is not in the shard, so nothing here may join on it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_units import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    UnitIndexError,
    group_by_pr,
    load_units,
    split_pr,
)


def row(key: str, fid: str, **flags: bool | int | str | None) -> dict[str, object]:
    """A unit row. `key` is "side/path:qualname"; flags: in_added, counterpart, occurrence, pr."""
    side, rest = key.split("/", 1)
    path, qual = rest.rsplit(":", 1)
    return {
        "counterpart": flags.get("counterpart", False),
        "id": fid,
        "in_added": flags.get("in_added"),
        "line": 1,
        "nodes": 40,
        "occurrence": flags.get("occurrence", 0),
        "path": path,
        "pr": flags.get("pr", "1"),
        "qualname": qual,
        "repo": "o/r",
        "shape": f"s-{fid}",
        "side": side,
    }


def test_a_deleted_function_is_not_a_neighbour_and_is_counted() -> None:
    rows = [
        row("base/a.py:kept", "k", counterpart=True),
        row("base/a.py:gone", "g", counterpart=False),
        row("head/a.py:kept", "hk", in_added=False, counterpart=True),
        row("head/a.py:added", "n", in_added=True),
    ]
    u = split_pr("1", rows)
    assert [f.qualname for f in u.new] == ["added"]
    assert [f.qualname for f in u.existing] == ["kept"]
    assert u.lost_not_at_head == 1  # 'gone' reached the lost branch


def test_a_renamed_or_moved_function_is_not_a_neighbour() -> None:
    rows = [
        row("base/old.py:f", "x"),  # moved: no counterpart at the same path
        row("head/new.py:f", "x", in_added=True),
        row("base/a.py:g", "y"),  # renamed: no counterpart under the same qualified name
        row("head/a.py:g2", "y", in_added=True),
    ]
    u = split_pr("1", rows)
    assert u.existing == ()
    assert u.lost_not_at_head == 2
    assert len(u.new) == 2


def test_an_unchanged_head_function_is_not_new() -> None:
    rows = [
        row("base/a.py:f", "x", counterpart=True),
        row("head/a.py:f", "hx", in_added=False, counterpart=True),
    ]
    u = split_pr("1", rows)
    assert u.new == ()
    assert len(u.existing) == 1


def test_a_function_that_shrank_below_the_floor_at_head_is_still_a_neighbour() -> None:
    """No head row exists (under 30 nodes at head); the counterpart flag says it is there."""
    rows = [
        row("base/a.py:f", "x", counterpart=True),
        row("head/a.py:n", "n", in_added=True),
    ]
    assert not any(
        r["side"] == "head" and r["qualname"] == "f" for r in rows
    )  # reached: no head row
    u = split_pr("1", rows)
    assert [f.qualname for f in u.existing] == ["f"]
    assert u.lost_not_at_head == 0


def test_an_existing_function_whose_def_line_moved_is_still_a_neighbour() -> None:
    """Present at base and head by name; at head its def line sits in an added hunk."""
    rows = [
        row("base/a.py:f", "x", counterpart=True),
        row("head/a.py:f", "x2", in_added=True, counterpart=True),
        row("head/a.py:other", "o", in_added=True),
    ]
    u = split_pr("1", rows)
    assert {f.qualname for f in u.new} == {"f", "other"}
    assert [f.qualname for f in u.existing] == ["f"]


def test_a_head_row_that_was_not_added_is_never_joined_on_its_id() -> None:
    """Its id is not in the shard; whatever it is, it must not change the split."""
    with_id = [
        row("base/a.py:f", "x", counterpart=True),
        row("head/a.py:f", "NOT-IN-SHARD", in_added=False, counterpart=True),
    ]
    other_id = [
        row("base/a.py:f", "x", counterpart=True),
        row("head/a.py:f", "another", in_added=False, counterpart=True),
    ]
    assert split_pr("1", with_id) == split_pr("1", other_id)
    assert [f.id for f in split_pr("1", with_id).existing] == ["x"]


def test_a_duplicated_name_makes_the_new_function_ambiguous_and_it_is_not_scored() -> None:
    rows = [
        row("base/a.py:f", "x0", counterpart=True, occurrence=0),
        row("base/a.py:f", "x1", counterpart=True, occurrence=1),
        row("head/a.py:f", "h1", in_added=True, occurrence=1),
        row("head/a.py:g", "g1", in_added=True),
    ]
    u = split_pr("1", rows)
    assert u.ambiguous == 1  # reached: the same-named function was dropped
    assert [f.qualname for f in u.new] == ["g"]
    assert [f.sort_key for f in u.existing] == [("a.py", "f", 0), ("a.py", "f", 1)]


def test_a_property_getter_and_setter_are_ambiguous_not_self_pairs() -> None:
    """The setter was added; the getter was not. Both are `C.prop`, so neither is "itself"."""
    rows = [
        row("base/a.py:C.prop", "get", counterpart=True, occurrence=0),
        row("base/a.py:C.prop", "set0", counterpart=True, occurrence=1),
        row("head/a.py:C.prop", "get", in_added=False, counterpart=True, occurrence=0),
        row("head/a.py:C.prop", "set1", in_added=True, counterpart=True, occurrence=1),
    ]
    u = split_pr("1", rows)
    assert u.new == () and u.ambiguous == 1


def test_neighbours_and_new_are_sorted_by_path_then_name() -> None:
    rows = [
        row("base/b.py:z", "1", counterpart=True),
        row("base/a.py:y", "2", counterpart=True),
        row("head/c.py:n2", "4", in_added=True),
        row("head/a.py:n1", "3", in_added=True),
    ]
    u = split_pr("1", rows)
    assert [f.name_key[:2] for f in u.existing] == [("a.py", "y"), ("b.py", "z")]
    assert [f.name_key[:2] for f in u.new] == [("a.py", "n1"), ("c.py", "n2")]


@pytest.mark.parametrize("missing", ["occurrence", "counterpart"])
def test_an_index_without_the_join_key_is_refused(missing: str) -> None:
    bad = row("base/a.py:f", "x")
    del bad[missing]
    with pytest.raises(UnitIndexError, match="regenerate"):
        split_pr("1", [bad])


def test_grouping_and_loading_keep_prs_apart(tmp_path: Path) -> None:
    rows = [row("base/a.py:f", "x", pr="1"), row("base/a.py:f", "x", pr="2")]
    path = tmp_path / "u.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    grouped = load_units(path)
    assert sorted(grouped) == ["1", "2"]
    assert len(group_by_pr(rows)["1"]) == 1


def test_a_row_holding_u2028_or_a_form_feed_is_one_row(tmp_path: Path) -> None:
    """`splitlines()` splits on both; the old extractor's text bug was exactly this class."""
    rows = [row("base/a\u2028b\x0c.py:f", "x"), row("base/c.py:g", "y")]
    path = tmp_path / "u.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    raw = path.read_text(encoding="utf-8")
    assert len(raw.splitlines()) > 2  # reached: the characters really are on the line
    assert sum(len(v) for v in load_units(path).values()) == 2
