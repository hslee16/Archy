"""The #587 function-text extractor: normalisation, keys, spans, floor, filters, shards.

Expected values are worked by hand, never taken from the code under test. Each test
asserts its fixture reaches the branch before asserting the outcome. The driver (cache,
manifest, parity, the draw, interrupts) is in `test_embedding_oracle_extract_run.py`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

# bench/ is not a package; append it (not insert(0)) so its generic module names cannot
# shadow stdlib/installed imports, mirroring tests/test_embedding_oracle_pairs.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_extract import (  # ty: ignore[unresolved-import]  (path set by the helpers)
    audit_shard,
    extract_pr,
    extract_tree,
    function_texts,
    normalise,
    text_id,
    write_shard,
)

from tests.embedding_oracle_extract_helpers import (
    SMALL,
    big,
    commit,
    floor,
    git,
    shard_texts,
    size,
    write,
)


def test_normalise_changes_only_line_endings_and_trailing_newlines() -> None:
    src = 'def f(x):\r\n    """Doc."""\r\n    return  x\r\n\r\n'
    assert (
        normalise(src) == 'def f(x):\n    """Doc."""\n    return  x'
    )  # docstring and spacing kept
    assert normalise("a\rb") == "a\nb"
    assert normalise("def myIdentifier(): pass") == "def myIdentifier(): pass"  # no folding
    assert normalise("  indented\n") == "  indented"  # no dedent


def test_key_is_sha256_of_normalised_utf8_and_ignores_line_ending_style() -> None:
    assert text_id("x = 1") == hashlib.sha256(b"x = 1").hexdigest()
    assert text_id("a\r\nb\n") == text_id("a\nb") == hashlib.sha256(b"a\nb").hexdigest()
    assert text_id("é") == hashlib.sha256("é".encode()).hexdigest()
    assert text_id("a") != text_id("b")


SPANS = (
    "import os\n"  # 1
    "\n"  # 2
    "@decorator\n"  # 3
    "def top(a):\n"  # 4
    '    """Doc."""\n'  # 5
    "    return a\n"  # 6
    "\n"  # 7
    "class C:\n"  # 8
    "    async def m(self):\n"  # 9
    "        x = 1\n"  # 10
    "        def inner():\n"  # 11
    "            return x\n"  # 12
    "        return inner"  # 13, no trailing newline
)


def test_function_texts_runs_def_line_through_last_line() -> None:
    texts = function_texts(SPANS.encode())
    assert set(texts) == {4, 9, 11}  # keyed by the def line, not the decorator line
    assert texts[4] == 'def top(a):\n    """Doc."""\n    return a'  # decorator excluded
    assert texts[9] == (
        "    async def m(self):\n        x = 1\n        def inner():\n"
        "            return x\n        return inner"
    )  # method keeps its indentation; async keyword kept; last line without newline
    assert texts[11] == "        def inner():\n            return x"  # the nested def stops early


AT_FLOOR = "def at(a, b):\n    a = a + 1\n    a = a + 1\n    a = a + 1\n    pass\n    return a\n"


BELOW_FLOOR = "def below(a, b):\n    a = a + 1\n    a = a + 1\n    pass\n    f(a)\n    return a\n"


def test_floor_is_inclusive_at_exactly_30_nodes(tmp_path: Path) -> None:
    assert size(AT_FLOOR, "at") == 30 and size(BELOW_FLOOR, "below") == 29  # worked by hand
    write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": AT_FLOOR + "\n" + BELOW_FLOOR})
    assert [f.qualname for f in extract_tree(tmp_path, None)[0]] == ["at"]


def test_extract_tree_applies_floor_and_source_only_filter(tmp_path: Path) -> None:
    floor()
    files = {
        "pkg/__init__.py": "",
        "pkg/mod.py": big("kept") + "\n" + SMALL,
        "pkg/tests/__init__.py": "",
        "pkg/tests/test_x.py": big("in_tests"),
        "pkg/_vendor/__init__.py": "",
        "pkg/_vendor/v.py": big("vendored"),
    }
    write(tmp_path, files)
    fns, texts, _ = extract_tree(tmp_path, None)
    assert [(f.side, f.path, f.qualname) for f in fns] == [("base", "pkg/mod.py", "kept")]
    only = fns[0]
    assert only.id == text_id(big("kept"))  # id is the hash of the verbatim function
    assert texts == {only.id: normalise(big("kept"))}
    assert only.in_added is None  # base side has no hunk notion


def test_extract_tree_marks_head_functions_added_by_def_line(tmp_path: Path) -> None:
    write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": big("old") + "\n" + big("new")})
    # Each big function is 10 lines (def, 8 statements, return), then one blank line, so
    # `old` defines on line 1 and `new` on line 12.
    fns = sorted(extract_tree(tmp_path, {"pkg/m.py": [(12, 21)]})[0], key=lambda f: f.line)
    assert [(f.qualname, f.line, f.in_added) for f in fns] == [("old", 1, False), ("new", 12, True)]
    # a hunk that covers the BODY but not the def line does not make a function new
    body_only = {
        f.qualname: f.in_added for f in extract_tree(tmp_path, {"pkg/m.py": [(13, 21)]})[0]
    }
    assert body_only == {"old": False, "new": False}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    git(root, "init", "-q")
    return root


def test_extract_pr_ships_base_and_head_and_marks_the_new_function(repo: Path) -> None:
    floor()
    base = commit(repo, {"pkg/__init__.py": "", "pkg/a.py": big("existing")})
    head = commit(repo, {"pkg/b.py": big("fresh")})
    out = extract_pr(repo, base, head)
    assert "dropped" not in out  # reached the success branch
    rows = {(f["side"], f["path"], f["qualname"]): f for f in out["fns"]}
    assert set(rows) == {
        ("base", "pkg/a.py", "existing"),
        ("head", "pkg/a.py", "existing"),
        ("head", "pkg/b.py", "fresh"),
    }  # base never contains the new function; head contains both
    assert rows[("head", "pkg/b.py", "fresh")]["in_added"] is True
    assert rows[("head", "pkg/a.py", "existing")]["in_added"] is False
    existing, fresh = text_id(big("existing")), text_id(big("fresh"))
    assert set(out["texts"]) == {existing, fresh}
    assert (
        rows[("base", "pkg/a.py", "existing")]["id"]
        == existing
        == (rows[("head", "pkg/a.py", "existing")]["id"])
    )  # unchanged function: same id on both sides


def test_extract_pr_ships_head_text_only_for_added_functions(repo: Path) -> None:
    floor()
    base = commit(repo, {"pkg/__init__.py": "", "pkg/a.py": big("existing")})
    edited = big("existing").replace("v3 = a + 3", "v3 = a + 33")
    assert edited != big("existing")  # reached: the existing function really changed
    head = commit(repo, {"pkg/a.py": edited, "pkg/b.py": big("fresh")})
    out = extract_pr(repo, base, head)
    assert "dropped" not in out
    # the edited body is on an added line but its def line is not: not new, text not shipped
    assert set(out["texts"]) == {text_id(big("existing")), text_id(big("fresh"))}
    head_existing = next(
        f for f in out["fns"] if (f["side"], f["qualname"]) == ("head", "existing")
    )
    assert head_existing["in_added"] is False and head_existing["id"] == text_id(edited)  # indexed


def test_extract_pr_drops_rather_than_scores_clean(repo: Path) -> None:
    floor()
    base = commit(repo, {"pkg/__init__.py": "", "pkg/a.py": big("existing")})
    no_py = commit(repo, {"README.md": "x"})
    assert extract_pr(repo, base, no_py) == {"dropped": "no added python lines"}
    only_small = commit(repo, {"pkg/c.py": SMALL})
    assert extract_pr(repo, no_py, only_small) == {"dropped": "no new function clears the floor"}
    bad = extract_pr(repo, "0" * 40, only_small)
    assert str(bad["dropped"]).startswith("git:")  # an unknown commit is a recorded drop


def test_write_shard_is_sorted_unique_text_only_and_audits_clean(tmp_path: Path) -> None:
    texts = shard_texts("def b(): pass", "def a(): pass", "def é(): pass")
    info = write_shard(tmp_path, "pilot", texts, [{"pr": "1", "id": "k"}])
    shard = tmp_path / "pilot.jsonl"
    audit_shard(shard)
    rows = [json.loads(line) for line in shard.read_text(encoding="utf-8").splitlines()]
    assert [r["id"] for r in rows] == sorted(texts)
    assert all(set(r) == {"id", "text"} for r in rows)  # text only
    assert info["rows"] == 3 and info["sha256"] == hashlib.sha256(shard.read_bytes()).hexdigest()
    assert b"\r" not in shard.read_bytes()
    assert "pr" in (tmp_path / "pilot.units.jsonl").read_text()  # the index is a separate file


def test_audit_shard_fires_on_each_contract_breach(tmp_path: Path) -> None:
    good = shard_texts("a = 1", "b = 2")
    ka, kb = sorted(good)

    def write(rows: list[dict]) -> Path:
        path = tmp_path / "s.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    audit_shard(write([{"id": k, "text": good[k]} for k in (ka, kb)]))  # passes
    with pytest.raises(AssertionError, match="sorted"):
        audit_shard(write([{"id": k, "text": good[k]} for k in (kb, ka)]))
    with pytest.raises(AssertionError, match="duplicate"):
        audit_shard(write([{"id": ka, "text": good[ka]}] * 2))
    with pytest.raises(AssertionError, match="sha256"):
        audit_shard(write([{"id": ka, "text": good[kb]}]))
    with pytest.raises(AssertionError, match="normalised"):
        audit_shard(write([{"id": text_id("x\n\n"), "text": "x\n\n"}]))
    with pytest.raises(AssertionError, match="fields"):
        audit_shard(write([{"id": ka, "text": good[ka], "pr": "1"}]))


FORM_FEED = (
    "# header\x0c comment\ndef f(x):\n    a = 1\n    return a + x\n\ndef g(y):\n    return y * 2\n"
)


SEPARATORS = ("\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


def test_function_texts_splits_on_newline_only_so_odd_line_separators_cannot_shift_a_slice() -> (
    None
):
    """Tree-sitter rows count a newline only; `str.splitlines` also splits on these."""
    texts = function_texts(FORM_FEED.encode())
    assert set(texts) == {2, 6}  # `f` defines on line 2 and `g` on line 6 (hand-counted)
    assert texts[2] == "def f(x):\n    a = 1\n    return a + x"
    assert texts[6] == "def g(y):\n    return y * 2"
    for sep in SEPARATORS:
        src = f'S = "a{sep}b"\ndef f(x):\n    a = 1\n    return a + x\n'
        # the fixture reaches the case: splitlines would count one more line than a newline split
        assert len(src.splitlines()) > len(src.split("\n")) - 1, repr(sep)
        assert function_texts(src.encode()) == {2: "def f(x):\n    a = 1\n    return a + x"}, repr(
            sep
        )


def test_same_named_functions_of_one_file_get_distinct_occurrence_numbers(tmp_path: Path) -> None:
    floor()
    body = (big("dup") + "\n" + big("dup")).splitlines()
    src = "if True:\n" + "".join("    " + line + "\n" for line in body)
    write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": src})
    fns = extract_tree(tmp_path, None)[0]
    dup = sorted((f for f in fns if f.qualname == "dup"), key=lambda f: f.line)
    assert len(dup) == 2  # the fixture reaches the duplicate case
    assert [f.occurrence for f in dup] == [0, 1]
    assert len({(f.side, f.path, f.qualname, f.occurrence) for f in fns}) == len(fns)  # unique


def test_counterpart_sees_a_function_that_shrank_below_the_floor_and_a_touched_def_line(
    repo: Path,
) -> None:
    floor()
    shrunk = "def shrinks(a):\n    return a + 1\n"
    base = commit(
        repo, {"pkg/__init__.py": "", "pkg/a.py": big("existing") + "\n" + big("shrinks")}
    )
    touched = big("existing", extra="  # touched")  # the def line itself changes
    head = commit(repo, {"pkg/a.py": touched + "\n" + shrunk, "pkg/b.py": big("fresh")})
    assert size(shrunk, "shrinks") < 30  # the fixture reaches "below the floor at head"
    out = extract_pr(repo, base, head)
    rows = {(f["side"], f["qualname"]): f for f in out["fns"]}
    assert ("head", "shrinks") not in rows  # below the floor at head, so no head row
    assert rows[("base", "shrinks")]["counterpart"] is True  # but it still exists at head
    assert rows[("base", "existing")]["counterpart"] is True
    assert rows[("head", "existing")]["in_added"] is True  # def line touched: counted as new
    assert rows[("head", "existing")]["counterpart"] is True  # and a self-pair to exclude
    assert rows[("head", "fresh")]["counterpart"] is False  # genuinely new


def test_shard_bytes_are_ascii_so_no_reader_can_split_a_row_on_a_unicode_separator(
    tmp_path: Path,
) -> None:
    texts = shard_texts("S = 'a\u2028b'\n", "x = 'café\x85'\n", "y = 1\n")
    write_shard(tmp_path, "pilot", texts, [])
    raw = (tmp_path / "pilot.jsonl").read_bytes()
    assert raw.isascii()
    assert len(raw.decode().splitlines()) == len(texts) == 3  # one row per line, whoever splits
    audit_shard(tmp_path / "pilot.jsonl")
