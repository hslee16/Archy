"""The #587 function-text extractor: normalisation, keys, spans, floor, filters, draw, shards.

Expected values are worked by hand, never taken from the code under test. Each test
asserts its fixture reaches the branch before asserting the outcome.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from archy.complexity import compute_function_complexity

# bench/ is not a package; append it (not insert(0)) so its generic module names cannot
# shadow stdlib/installed imports, mirroring tests/test_embedding_oracle_pairs.py.
sys.path.append(str(Path(__file__).resolve().parent.parent / "bench"))

from embedding_oracle_extract import (  # ty: ignore[unresolved-import]  (added to sys.path above)
    CALIBRATION_N,
    DATA,
    NO_NEW_FUNCTION,
    PILOT_LEDGER,
    audit_shard,
    check_disjoint,
    draw_calibration,
    extract_pr,
    extract_tree,
    function_texts,
    git_sha,
    measured_rows,
    normalise,
    reason_class,
    text_id,
    write_shard,
)


def _big(name: str, extra: str = "") -> str:
    """A function whose body is far above the 30-node floor (checked by `_floor`)."""
    body = "\n".join(f"    v{i} = a + {i} * b - len(c) ** 2" for i in range(8))
    return f"def {name}(a, b, c):{extra}\n{body}\n    return v0 + v7\n"


SMALL = "def tiny(a):\n    return a + 1\n"


def _size(source: str, name: str) -> int:
    return next(f.size for f in compute_function_complexity(source.encode()) if f.name == name)


def _floor() -> None:
    """The fixture reaches both sides of the floor: big clears it, tiny does not."""
    assert _size(_big("big"), "big") >= 30
    assert _size(SMALL, "tiny") < 30


# --- normalise and id -------------------------------------------------------------


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


# --- spans -------------------------------------------------------------------------

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
    assert _size(AT_FLOOR, "at") == 30 and _size(BELOW_FLOOR, "below") == 29  # worked by hand
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": AT_FLOOR + "\n" + BELOW_FLOOR})
    assert [f.qualname for f in extract_tree(tmp_path, None)[0]] == ["at"]


# --- extract_tree: the floor and the source-only filter ----------------------------


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def test_extract_tree_applies_floor_and_source_only_filter(tmp_path: Path) -> None:
    _floor()
    files = {
        "pkg/__init__.py": "",
        "pkg/mod.py": _big("kept") + "\n" + SMALL,
        "pkg/tests/__init__.py": "",
        "pkg/tests/test_x.py": _big("in_tests"),
        "pkg/_vendor/__init__.py": "",
        "pkg/_vendor/v.py": _big("vendored"),
    }
    _write(tmp_path, files)
    fns, texts = extract_tree(tmp_path, None)
    assert [(f.side, f.path, f.qualname) for f in fns] == [("base", "pkg/mod.py", "kept")]
    only = fns[0]
    assert only.id == text_id(_big("kept"))  # id is the hash of the verbatim function
    assert texts == {only.id: normalise(_big("kept"))}
    assert only.in_added is None  # base side has no hunk notion


def test_extract_tree_marks_head_functions_added_by_def_line(tmp_path: Path) -> None:
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": _big("old") + "\n" + _big("new")})
    # Each big function is 10 lines (def, 8 statements, return), then one blank line, so
    # `old` defines on line 1 and `new` on line 12.
    fns = sorted(extract_tree(tmp_path, {"pkg/m.py": [(12, 21)]})[0], key=lambda f: f.line)
    assert [(f.qualname, f.line, f.in_added) for f in fns] == [("old", 1, False), ("new", 12, True)]
    # a hunk that covers the BODY but not the def line does not make a function new
    body_only = {
        f.qualname: f.in_added for f in extract_tree(tmp_path, {"pkg/m.py": [(13, 21)]})[0]
    }
    assert body_only == {"old": False, "new": False}


# --- extract_pr, against a real git repository -------------------------------------


def _git(root: Path, *args: str) -> str:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=True, env=env
    ).stdout.strip()


def _commit(root: Path, files: dict[str, str]) -> str:
    _write(root, files)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    _git(root, "init", "-q")
    return root


def test_extract_pr_ships_base_and_head_and_marks_the_new_function(repo: Path) -> None:
    _floor()
    base = _commit(repo, {"pkg/__init__.py": "", "pkg/a.py": _big("existing")})
    head = _commit(repo, {"pkg/b.py": _big("fresh")})
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
    existing, fresh = text_id(_big("existing")), text_id(_big("fresh"))
    assert set(out["texts"]) == {existing, fresh}
    assert (
        rows[("base", "pkg/a.py", "existing")]["id"]
        == existing
        == (rows[("head", "pkg/a.py", "existing")]["id"])
    )  # unchanged function: same id on both sides


def test_extract_pr_ships_head_text_only_for_added_functions(repo: Path) -> None:
    _floor()
    base = _commit(repo, {"pkg/__init__.py": "", "pkg/a.py": _big("existing")})
    edited = _big("existing").replace("v3 = a + 3", "v3 = a + 33")
    assert edited != _big("existing")  # reached: the existing function really changed
    head = _commit(repo, {"pkg/a.py": edited, "pkg/b.py": _big("fresh")})
    out = extract_pr(repo, base, head)
    assert "dropped" not in out
    # the edited body is on an added line but its def line is not: not new, text not shipped
    assert set(out["texts"]) == {text_id(_big("existing")), text_id(_big("fresh"))}
    head_existing = next(
        f for f in out["fns"] if (f["side"], f["qualname"]) == ("head", "existing")
    )
    assert head_existing["in_added"] is False and head_existing["id"] == text_id(edited)  # indexed


def test_extract_pr_drops_rather_than_scores_clean(repo: Path) -> None:
    _floor()
    base = _commit(repo, {"pkg/__init__.py": "", "pkg/a.py": _big("existing")})
    no_py = _commit(repo, {"README.md": "x"})
    assert extract_pr(repo, base, no_py) == {"dropped": "no added python lines"}
    only_small = _commit(repo, {"pkg/c.py": SMALL})
    assert extract_pr(repo, no_py, only_small) == {"dropped": "no new function clears the floor"}
    bad = extract_pr(repo, "0" * 40, only_small)
    assert str(bad["dropped"]).startswith("git:")  # an unknown commit is a recorded drop


# --- the pilot reproduction check --------------------------------------------------


def test_reason_class_groups_a_drop_by_its_text_before_the_colon() -> None:
    assert reason_class(None) == "measured"
    assert reason_class(NO_NEW_FUNCTION) == "measured"  # #548 analysed such a PR
    assert reason_class("no added python lines") == "no added python lines"
    assert reason_class("QualnameCollisionError: 2 module names map") == "QualnameCollisionError"
    assert reason_class("head moved: ledger ab, fetched cd") == "head moved"


@pytest.fixture
def pilot_pr(repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """A real two-commit repository wired in as the clone, with a ledger row for it."""
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    base = _commit(repo, {"pkg/__init__.py": "", "pkg/a.py": _big("existing")})
    head = _commit(repo, {"pkg/b.py": _big("fresh")})
    monkeypatch.setattr(mod, "OUT", tmp_path / "out")
    monkeypatch.setattr(mod, "_clone_and_pin", lambda row: repo)
    return {"key": "7", "repo": "o/r", "base": base, "head": head}


def test_run_shard_reports_agreement_and_disagreement_with_the_ledger(pilot_pr: dict) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    agree = mod.run_shard("pilot", [pilot_pr])
    assert agree["reproduction"] == {"7": {"ledger": "measured", "mine": "measured"}}
    assert agree["reproduction_mismatches"] == {}
    assert agree["rows"] == 2  # base `existing` and head `fresh`, text only
    liar = {**pilot_pr, "dropped": "no added python lines"}  # the ledger claims a drop
    clash = mod.run_shard("pilot", [liar])
    assert clash["reproduction_mismatches"] == {
        "7": {"ledger": "no added python lines", "mine": "measured"}
    }


def test_main_exits_3_on_a_reproduction_mismatch_and_0_otherwise(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [pilot_pr]})
    assert mod.main(["--shard", "pilot"]) == 0
    manifest = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert manifest["extractor_git_sha"] == "sha" and "pilot" in manifest["shards"]
    liar = {**pilot_pr, "dropped": "no added python lines"}
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [liar]})
    assert mod.main(["--shard", "pilot"]) == 3


# --- the draw ----------------------------------------------------------------------


def test_draw_calibration_caps_per_repo_and_stops_at_n() -> None:
    keys = {f"{i:04d}": ("big/repo" if i < 30 else f"r/{i}") for i in range(60)}
    assert sum(1 for r in keys.values() if r == "big/repo") > 5  # the cap can bind
    picked = draw_calibration(keys, n=20, cap=5, seed=587)
    assert len(picked) == 20 and len(set(picked)) == 20
    assert sum(1 for k in picked if keys[k] == "big/repo") <= 5
    assert sum(1 for k in picked if keys[k] == "big/repo") == 5  # 30 candidates, so it binds


def test_draw_calibration_is_over_sorted_keys_and_seeded() -> None:
    items = [(f"{i:03d}", f"r/{i % 7}") for i in range(40)]
    forward = dict(items)
    backward = dict(reversed(items))
    assert list(forward) != list(backward)  # different insertion order, same content
    a = draw_calibration(forward, n=10, cap=5, seed=587)
    assert a == draw_calibration(backward, n=10, cap=5, seed=587)  # sorted first
    assert a != draw_calibration(forward, n=10, cap=5, seed=588)  # the seed matters


def test_draw_calibration_returns_fewer_when_the_cap_starves_it() -> None:
    assert len(draw_calibration({"1": "r", "2": "r", "3": "r"}, n=40, cap=2)) == 2


def test_check_disjoint_fires_on_overlap_and_passes_on_disjoint() -> None:
    check_disjoint(["1", "2"], ["3"])
    with pytest.raises(AssertionError, match="overlap"):
        check_disjoint(["1", "2"], ["2", "3"])


def test_measured_rows_keeps_only_ok_undropped_rows(tmp_path: Path) -> None:
    rows = [
        {"key": 1, "status": "ok", "repo": "a"},
        {"key": 2, "status": "ok", "dropped": "scan too large"},
        {"key": 3, "status": "error"},
        {"key": 4, "status": "ok", "repo": "b"},
    ]
    path = tmp_path / "l.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert sorted(measured_rows([path])) == ["1", "4"]


def test_the_reuse_set_is_the_preregistered_one() -> None:
    measured = measured_rows(sorted(DATA.glob("precision.*.jsonl")))
    assert len(measured) == 1049  # "1,049 PRs" in the pre-registration
    pilot = [json.loads(line) for line in PILOT_LEDGER.read_text().splitlines()]
    assert len(pilot) == 20
    assert sum("dropped" not in r for r in pilot) == 7  # "7 measured PRs"
    check_disjoint((str(r["key"]) for r in pilot), measured)
    picked = draw_calibration({k: r["repo"] for k, r in measured.items()})
    assert len(picked) == CALIBRATION_N == 40
    repos = [measured[k]["repo"] for k in picked]
    assert max(repos.count(r) for r in set(repos)) <= 5
    check_disjoint((str(r["key"]) for r in pilot), picked)


# --- shards ------------------------------------------------------------------------


def _texts(*sources: str) -> dict[str, str]:
    return {text_id(s): normalise(s) for s in sources}


def test_write_shard_is_sorted_unique_text_only_and_audits_clean(tmp_path: Path) -> None:
    texts = _texts("def b(): pass", "def a(): pass", "def é(): pass")
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
    good = _texts("a = 1", "b = 2")
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


def test_git_sha_marks_tracked_changes_dirty_and_ignores_untracked_files(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    sha = _commit(repo, {"bench/x.py": "a = 1\n", "src/y.py": "b = 1\n"})
    monkeypatch.setattr(mod, "REPO_ROOT", repo)
    assert git_sha() == sha  # clean
    (repo / "bench" / "untracked.jsonl").write_text("{}")
    assert git_sha() == sha  # an untracked file is not a change to the extractor
    (repo / "bench" / "x.py").write_text("a = 2\n")
    assert git_sha() == sha + "+dirty"  # a tracked edit is
