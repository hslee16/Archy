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
    EXTRACT_VERSION,
    NO_NEW_FUNCTION,
    PILOT_LEDGER,
    audit_shard,
    check_disjoint,
    draw_calibration,
    extract_pr,
    extract_tree,
    function_texts,
    git_sha,
    is_final,
    measured_rows,
    normalise,
    parity_mismatches,
    pr_result,
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
    fns, texts, _ = extract_tree(tmp_path, None)
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
    assert manifest["ok"] is True
    assert manifest["shards"]["pilot"]["extractor_git_sha"] == "sha"  # recorded PER shard
    assert manifest["shards"]["pilot"]["extractor_version"] == EXTRACT_VERSION
    liar = {**pilot_pr, "dropped": "no added python lines"}
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [liar]})
    assert mod.main(["--shard", "pilot"]) == 3
    # the manifest itself says so, so a downstream reader cannot miss the exit code
    assert json.loads((mod.OUT / "SHARDS.json").read_text())["ok"] is False


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


# --- review fixes: line splitting, index keys, counterpart --------------------------


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
    _floor()
    body = (_big("dup") + "\n" + _big("dup")).splitlines()
    src = "if True:\n" + "".join("    " + line + "\n" for line in body)
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/m.py": src})
    fns = extract_tree(tmp_path, None)[0]
    dup = sorted((f for f in fns if f.qualname == "dup"), key=lambda f: f.line)
    assert len(dup) == 2  # the fixture reaches the duplicate case
    assert [f.occurrence for f in dup] == [0, 1]
    assert len({(f.side, f.path, f.qualname, f.occurrence) for f in fns}) == len(fns)  # unique


def test_counterpart_sees_a_function_that_shrank_below_the_floor_and_a_touched_def_line(
    repo: Path,
) -> None:
    _floor()
    shrunk = "def shrinks(a):\n    return a + 1\n"
    base = _commit(
        repo, {"pkg/__init__.py": "", "pkg/a.py": _big("existing") + "\n" + _big("shrinks")}
    )
    touched = _big("existing", extra="  # touched")  # the def line itself changes
    head = _commit(repo, {"pkg/a.py": touched + "\n" + shrunk, "pkg/b.py": _big("fresh")})
    assert _size(shrunk, "shrinks") < 30  # the fixture reaches "below the floor at head"
    out = extract_pr(repo, base, head)
    rows = {(f["side"], f["qualname"]): f for f in out["fns"]}
    assert ("head", "shrinks") not in rows  # below the floor at head, so no head row
    assert rows[("base", "shrinks")]["counterpart"] is True  # but it still exists at head
    assert rows[("base", "existing")]["counterpart"] is True
    assert rows[("head", "existing")]["in_added"] is True  # def line touched: counted as new
    assert rows[("head", "existing")]["counterpart"] is True  # and a self-pair to exclude
    assert rows[("head", "fresh")]["counterpart"] is False  # genuinely new


# --- review fixes: what is cached, and one PR never ends the run ---------------------


@pytest.mark.parametrize(
    ("result", "final"),
    [
        ({"fns": [], "texts": {}}, True),
        ({"dropped": "no added python lines"}, True),
        ({"dropped": NO_NEW_FUNCTION}, True),
        ({"dropped": "QualnameCollisionError: 2 module names map"}, True),
        ({"dropped": "scan too large"}, True),
        ({"dropped": "git: fatal: unable to access"}, False),
        ({"dropped": "head moved: ledger ab, fetched cd"}, False),
        ({"dropped": "unresolvable: repo gone"}, False),
        ({"dropped": "TimeoutExpired: Command timed out"}, False),
    ],
)
def test_only_a_result_a_rerun_would_reproduce_is_final(result: dict, final: bool) -> None:
    assert is_final(result) is final


def test_pr_result_caches_final_results_and_retries_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    calls: list[int] = []
    answers = iter([{"dropped": "git: transient"}, {"fns": [], "texts": {}}])

    def measure(row: dict) -> dict:
        calls.append(1)
        return next(answers)

    monkeypatch.setattr(mod, "_measure", measure)
    row = {"key": "9"}
    assert pr_result(tmp_path, row, "sha") == {"dropped": "git: transient"}
    assert not (tmp_path / "9.json").exists()  # a transient drop is not cached
    assert pr_result(tmp_path, row, "sha") == {"fns": [], "texts": {}}  # so it was retried
    assert len(calls) == 2
    assert pr_result(tmp_path, row, "sha") == {"fns": [], "texts": {}}
    assert len(calls) == 2  # the final result is served from the cache


def test_pr_result_refuses_a_stale_or_torn_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "_measure", lambda row: {"fns": [], "texts": {"fresh": "x"}})
    path = tmp_path / "9.json"
    old = {"fns": [], "texts": {"old": "y"}}
    stale = {"version": "0", "extractor_git_sha": "old", "result": old}
    assert stale["version"] != EXTRACT_VERSION  # the fixture really is another version
    path.write_text(json.dumps(stale))
    assert pr_result(tmp_path, {"key": "9"}, "sha")["texts"] == {"fresh": "x"}  # recomputed
    path.write_text('{"version": "2", "res')  # a torn write
    assert pr_result(tmp_path, {"key": "9"}, "sha")["texts"] == {"fresh": "x"}  # not a crash


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.CalledProcessError(128, "git", stderr="fatal: bad object"),
        subprocess.TimeoutExpired("git", 900),
        OSError("disk full"),
    ],
)
def test_one_prs_failure_is_a_drop_and_never_an_exception(
    exc: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    def boom(row: dict) -> Path:
        raise exc

    monkeypatch.setattr(mod, "_clone_and_pin", boom)
    out = mod._measure({"key": "1", "base": "a", "head": "b"})
    assert set(out) == {"dropped"} and out["dropped"]  # recorded with a reason
    assert is_final(out) is False  # and retried next run


def test_an_interrupt_exits_cleanly_and_says_how_to_resume(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    def interrupted(name: str, rows: object) -> dict:
        raise KeyboardInterrupt

    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [pilot_pr]})
    monkeypatch.setattr(mod, "run_shard", interrupted)
    assert mod.main(["--shard", "pilot"]) == 130
    assert "run the same command again to resume" in capsys.readouterr().err


def test_a_failed_write_leaves_the_previous_file_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    target = tmp_path / "x.json"
    mod._atomic_write(target, b"old")
    assert target.read_bytes() == b"old" and not list(tmp_path.glob("*.tmp"))

    def fail(src: object, dst: object) -> None:
        raise OSError("killed mid-rename")

    monkeypatch.setattr(mod.os, "replace", fail)
    with pytest.raises(OSError):
        mod._atomic_write(target, b"new")
    assert target.read_bytes() == b"old"  # never a torn file at the target


# --- review fixes: parity with generator A, the shard bytes, the draw wiring ---------


def test_parity_with_the_ledger_firings_is_exact_on_path_name_line_and_newness(
    repo: Path,
) -> None:
    _floor()
    base = _commit(repo, {"pkg/__init__.py": "", "pkg/a.py": _big("existing")})
    head = _commit(repo, {"pkg/b.py": _big("fresh")})
    result = extract_pr(repo, base, head)
    new = {"path": "pkg/b.py", "qualified_name": "fresh", "line": 1, "new": True}
    old = {"path": "pkg/a.py", "qualified_name": "existing", "line": 1, "new": False}
    row = {"firings": [{"members": [new, old]}]}
    assert parity_mismatches(row, result) == []
    wrong_new = {"firings": [{"members": [{**new, "new": False}]}]}  # the head row IS added
    assert parity_mismatches(wrong_new, result) == ["pkg/b.py:fresh@1 new=False"]
    wrong_line = {"firings": [{"members": [{**old, "line": 5}]}]}
    assert parity_mismatches(wrong_line, result) == ["pkg/a.py:existing@5 new=False"]
    assert parity_mismatches(row, {"dropped": "no added python lines"}) == []  # nothing to compare
    assert parity_mismatches({"firings": []}, result) == []


def test_main_exits_3_on_a_parity_mismatch_even_when_the_classes_agree(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    ghost = {"path": "pkg/zzz.py", "qualified_name": "ghost", "line": 1, "new": True}
    row = {**pilot_pr, "firings": [{"members": [ghost]}]}
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [row]})
    assert mod.main(["--shard", "pilot"]) == 3  # the ledger says measured, and so do we
    manifest = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert manifest["ok"] is False
    parity = manifest["shards"]["pilot"]["parity_mismatches"]
    assert parity == {"7": ["pkg/zzz.py:ghost@1 new=True"]}


def test_shard_bytes_are_ascii_so_no_reader_can_split_a_row_on_a_unicode_separator(
    tmp_path: Path,
) -> None:
    texts = _texts("S = 'a\u2028b'\n", "x = 'café\x85'\n", "y = 1\n")
    write_shard(tmp_path, "pilot", texts, [])
    raw = (tmp_path / "pilot.jsonl").read_bytes()
    assert raw.isascii()
    assert len(raw.decode().splitlines()) == len(texts) == 3  # one row per line, whoever splits
    audit_shard(tmp_path / "pilot.jsonl")


@pytest.fixture
def ledgers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Ledgers on disk, wired in as the data directory and the pilot ledger; returns the
    measured rows. One repository holds 20 of the 60 measured PRs, so the cap of 5 binds in a
    draw of 40; two more rows are unmeasured and must never be drawn."""
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    def row(i: int) -> dict:
        repo = "r/big" if i < 20 else f"r/{i}"
        return {"key": 1000 + i, "status": "ok", "repo": repo, "base": "b", "head": "h"}

    measured = [row(i) for i in range(60)]
    junk = [
        {"key": 5001, "status": "ok", "dropped": "scan too large", "repo": "r/0"},
        {"key": 5002, "status": "error", "repo": "r/1"},
    ]
    data = tmp_path / "data"
    data.mkdir()
    lines = "".join(json.dumps(r) + "\n" for r in measured + junk)
    (data / "precision.run0.jsonl").write_text(lines)
    pilot = [{"key": 9000 + i, "repo": "p/x", "base": "b", "head": "h"} for i in range(3)]
    pilot_file = tmp_path / "pilot.jsonl"
    pilot_file.write_text("".join(json.dumps(r) + "\n" for r in pilot))
    monkeypatch.setattr(mod, "DATA", data)
    monkeypatch.setattr(mod, "PILOT_LEDGER", pilot_file)
    return measured


def test_rows_wires_pilot_and_calibration_from_the_ledgers(ledgers: list[dict]) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    got = mod._rows(["pilot", "calibration"])
    assert [r["key"] for r in got["pilot"]] == [9000, 9001, 9002]
    cal = {str(r["key"]) for r in got["calibration"]}
    assert len(cal) == 40  # the fixture reaches a full draw
    assert cal <= {str(r["key"]) for r in ledgers}  # dropped and errored rows never enter
    assert cal.isdisjoint({"9000", "9001", "9002"})
    in_big = sum(1 for r in got["calibration"] if r["repo"] == "r/big")
    assert in_big == 5  # the per-repository cap bound (20 candidates), through `_rows`' wiring


def test_the_cli_has_no_way_to_name_the_evaluation_subset(ledgers: list[dict]) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    assert len(ledgers) == 60  # reached: the ledgers are wired in
    with pytest.raises(SystemExit):  # argparse refuses any other shard name
        mod.main(["--shard", "evaluation"])
    assert set(mod.SHARD_NAMES) == {"pilot", "calibration"}


def test_the_manifest_keeps_blocks_it_does_not_own_and_drops_the_legacy_sha(
    pilot_pr: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import embedding_oracle_extract as mod  # ty: ignore[unresolved-import]  (bench/ on sys.path)

    monkeypatch.setattr(mod, "git_sha", lambda: "sha")
    monkeypatch.setattr(mod, "_rows", lambda names: {"pilot": [pilot_pr]})
    mod.OUT.mkdir(parents=True)
    prior = {"extractor_git_sha": "old", "sample_repos": {"o/r": 3}, "shards": {"calibration": {}}}
    (mod.OUT / "SHARDS.json").write_text(json.dumps(prior))
    assert mod.main(["--shard", "pilot"]) == 0
    got = json.loads((mod.OUT / "SHARDS.json").read_text())
    assert got["sample_repos"] == {"o/r": 3}  # a consumer's block survives a regeneration
    assert "extractor_git_sha" not in got  # recorded per shard now, not once for all
    assert set(got["shards"]) == {"calibration", "pilot"}  # the other shard's entry is kept
