from __future__ import annotations

import json as _json
from pathlib import Path

from click.testing import CliRunner

from archy.cli import main
from archy.complexity import FunctionComplexity, compute_function_complexity
from archy.duplicates import DuplicateGroup, compute_duplicates
from archy.graph import Module
from archy.parser import ParseResult

# --------------------------------------------------------------------------- #
# Shape-hash / size unit tests (on the FunctionComplexity enrichment)
# --------------------------------------------------------------------------- #


def _rows(src: bytes) -> dict[str, FunctionComplexity]:
    return {f.qualified_name: f for f in compute_function_complexity(src)}


def test_renamed_identifiers_and_literals_share_a_shape():
    a = _rows(b"def a(x):\n    y = x + 1\n    return y\n")["a"]
    b = _rows(b"def totally_different(zz):\n    w = zz + 999\n    return w\n")["totally_different"]
    assert a.shape_hash == b.shape_hash != ""
    assert a.size == b.size > 0


def test_different_operator_changes_the_shape():
    plus = _rows(b"def a(x):\n    return x + 1\n")["a"]
    minus = _rows(b"def a(x):\n    return x - 1\n")["a"]
    assert plus.shape_hash != minus.shape_hash


def test_size_counts_nodes_not_lines():
    # Same expression, reflowed across lines: shape and size must be identical.
    one_line = _rows(b"def a(xs):\n    return [x for x in xs if x]\n")["a"]
    multi_line = _rows(
        b"def a(xs):\n    return [\n        x\n        for x in xs\n        if x\n    ]\n"
    )["a"]
    assert one_line.shape_hash == multi_line.shape_hash
    assert one_line.size == multi_line.size


def test_nested_def_gets_its_own_shape_and_does_not_inflate_parent():
    src = b"def outer():\n    def inner(x):\n        return x + 1\n    return inner\n"
    rows = _rows(src)
    solo = _rows(b"def inner(x):\n    return x + 1\n")["inner"]
    # Each def's body hashes independently of its enclosing scope, so a nested
    # def matches the same function defined at module level.
    assert rows["outer.inner"].shape_hash == solo.shape_hash
    # ...and the outer body (which only calls/returns inner) differs from it.
    assert rows["outer"].shape_hash != rows["outer.inner"].shape_hash


def test_empty_body_has_no_shape():
    row = _rows(b"def a():\n    ...\n")["a"]
    # `...` is a real (tiny) body, so it hashes but stays small; a genuinely
    # empty def is impossible in Python. Assert the tiny body is well below the
    # default threshold so it is filtered downstream.
    assert row.size < 20


# --------------------------------------------------------------------------- #
# compute_duplicates clustering tests (constructed fixtures, no disk)
# --------------------------------------------------------------------------- #


def _fn(name: str, *, shape: str, size: int, line: int) -> FunctionComplexity:
    return FunctionComplexity(
        name=name.split(".")[-1],
        qualified_name=name,
        line=line,
        cyclomatic=1,
        shape_hash=shape,
        size=size,
    )


def _project(mods: dict[str, list[FunctionComplexity]]):
    modules = [
        Module(qualname=q, path=Path(f"/src/{q.replace('.', '/')}.py"), is_package=False)
        for q in mods
    ]
    parse_results = {
        q: ParseResult(imports=(), has_errors=False, functions=tuple(fns))
        for q, fns in mods.items()
    }
    return modules, parse_results


def test_clusters_matching_shapes_across_modules():
    modules, results = _project(
        {
            "pkg.a": [_fn("f", shape="H1", size=30, line=1)],
            "pkg.b": [_fn("g", shape="H1", size=30, line=5)],
        }
    )
    groups = compute_duplicates(modules, results)
    assert len(groups) == 1
    g = groups[0]
    assert g.shape_hash == "H1"
    assert g.member_count == 2
    assert g.redundancy == 30  # size * (count - 1)
    assert {m.module for m in g.members} == {"pkg.a", "pkg.b"}
    assert g.members[0].path == "/src/pkg/a.py"


def test_min_size_filters_trivial_functions():
    modules, results = _project(
        {
            "pkg.a": [_fn("f", shape="H1", size=10, line=1)],
            "pkg.b": [_fn("g", shape="H1", size=10, line=1)],
        }
    )
    assert compute_duplicates(modules, results, min_size=20) == []
    assert len(compute_duplicates(modules, results, min_size=5)) == 1


def test_singleton_shape_is_not_a_duplicate():
    modules, results = _project({"pkg.a": [_fn("f", shape="H1", size=30, line=1)]})
    assert compute_duplicates(modules, results) == []


def test_groups_ranked_by_redundancy_desc():
    modules, results = _project(
        {
            "pkg.a": [
                _fn("small1", shape="S", size=25, line=1),
                _fn("big1", shape="B", size=40, line=10),
            ],
            "pkg.b": [
                _fn("small2", shape="S", size=25, line=1),
                _fn("big2", shape="B", size=40, line=10),
            ],
        }
    )
    groups = compute_duplicates(modules, results)
    assert [g.shape_hash for g in groups] == ["B", "S"]  # 40 redundancy before 25


def test_deterministic_under_permuted_input():
    forward = _project(
        {
            "pkg.a": [_fn("f", shape="H1", size=30, line=3)],
            "pkg.b": [_fn("g", shape="H1", size=30, line=1)],
        }
    )
    reverse = _project(
        {
            "pkg.b": [_fn("g", shape="H1", size=30, line=1)],
            "pkg.a": [_fn("f", shape="H1", size=30, line=3)],
        }
    )
    assert compute_duplicates(*forward) == compute_duplicates(*reverse)


def test_empty_input_yields_no_groups():
    assert compute_duplicates([], {}) == []


# --------------------------------------------------------------------------- #
# CLI smoke tests
# --------------------------------------------------------------------------- #


def _dup_body(name: str) -> str:
    # A body large enough to clear a small --min-nodes floor.
    return (
        f"def {name}(items):\n"
        "    total = 0\n"
        "    for item in items:\n"
        "        if item > 0:\n"
        "            total = total + item\n"
        "    return total\n"
    )


def _make_project(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text(_dup_body("alpha"))
    (pkg / "b.py").write_text(_dup_body("beta"))  # same shape, different name


def test_cli_duplicates_text_smoke(tmp_path: Path):
    _make_project(tmp_path)
    result = CliRunner().invoke(main, ["duplicates", str(tmp_path), "--min-nodes", "5"])
    assert result.exit_code == 0, result.output
    assert "pkg.a" in result.output and "pkg.b" in result.output
    assert "duplicate cluster(s)" in result.output


def test_cli_duplicates_json(tmp_path: Path):
    _make_project(tmp_path)
    result = CliRunner().invoke(
        main, ["duplicates", str(tmp_path), "--min-nodes", "5", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["total"] == 1
    assert payload["note"] is None
    group = payload["groups"][0]
    assert group["member_count"] == 2
    assert {m["module"] for m in group["members"]} == {"pkg.a", "pkg.b"}


def test_cli_duplicates_empty_has_note(tmp_path: Path):
    _make_project(tmp_path)
    result = CliRunner().invoke(
        main, ["duplicates", str(tmp_path), "--min-nodes", "5000", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["total"] == 0
    assert payload["groups"] == []
    assert "lower --min-nodes" in payload["note"]


def test_cli_duplicates_rejects_bad_flags(tmp_path: Path):
    _make_project(tmp_path)
    assert (
        CliRunner().invoke(main, ["duplicates", str(tmp_path), "--min-nodes", "0"]).exit_code != 0
    )
    assert CliRunner().invoke(main, ["duplicates", str(tmp_path), "--members", "1"]).exit_code != 0


def test_cli_duplicates_isinstance_check():
    # Guards the public type name the MCP follow-up (PR2) will import.
    assert issubclass(DuplicateGroup, __import__("pydantic").BaseModel)
