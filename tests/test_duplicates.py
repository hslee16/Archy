from __future__ import annotations

import json as _json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

from archy.cli import main
from archy.complexity import (
    FunctionComplexity,
    compute_function_complexity,
    extract_function_features,
)
from archy.duplicates import (
    DEFAULT_MIN_SIZE,
    DuplicateGroup,
    DuplicateMember,
    _is_test_path,
    _is_vendored_path,
    _same_class,
    classify_variants,
    compute_duplicates,
)
from archy.graph import Module, parse_project
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
    # min_size=20 keeps both clusters (size 25 and 40); this asserts ranking, not the floor.
    groups = compute_duplicates(modules, results, min_size=20)
    assert [g.shape_hash for g in groups] == ["B", "S"]  # 40 redundancy before 25


def test_default_min_size_is_calibrated_to_30():
    # Locked by the FP spot-check (RESEARCH_METRICS.md section 12): trivial-boilerplate
    # false positives cluster below ~30 normalized nodes.
    assert DEFAULT_MIN_SIZE == 30


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


def test_min_members_below_2_is_rejected():
    with pytest.raises(ValueError, match="min_members must be >= 2"):
        compute_duplicates([], {}, min_members=1)


# --------------------------------------------------------------------------- #
# Semantic de-noiser signals (#242): same-class, decorators, triviality
# --------------------------------------------------------------------------- #


def _member(module: str, qualified_name: str) -> DuplicateMember:
    return DuplicateMember(module=module, qualified_name=qualified_name, path="/x.py", line=1)


def test_same_class_true_for_methods_of_one_class():
    members = (_member("pkg.a", "Foo.bar"), _member("pkg.a", "Foo.baz"))
    assert _same_class(members) is True


def test_same_class_false_across_different_classes():
    members = (_member("pkg.a", "Foo.bar"), _member("pkg.a", "Qux.bar"))
    assert _same_class(members) is False


def test_same_class_false_for_module_level_functions():
    # Free functions have no parent class, so duplicated helpers stay primary.
    members = (_member("pkg.a", "helper"), _member("pkg.a", "helper2"))
    assert _same_class(members) is False


def test_same_class_false_across_modules():
    members = (_member("pkg.a", "Foo.bar"), _member("pkg.b", "Foo.bar"))
    assert _same_class(members) is False


def test_classify_variants_overload_reason(tmp_path: Path):
    # @overload stubs cluster by shape but are type surface, not duplication.
    # Precedence: overload wins even though the bodies are also trivial.
    # The `line=` args on the members below must be the `def` lines: a=3, b=6.
    src = tmp_path / "m.py"
    src.write_text(
        "from typing import overload\n"
        "@overload\n"
        "def a(x):\n    return x\n"
        "@overload\n"
        "def b(x):\n    return x\n"
    )
    members = (
        DuplicateMember(module="m", qualified_name="a", path=str(src), line=3),
        DuplicateMember(module="m", qualified_name="b", path=str(src), line=6),
    )
    group = DuplicateGroup(shape_hash="h", size=10, member_count=2, redundancy=10, members=members)
    [out] = classify_variants([group])
    assert out.variant_reason == "overload"
    assert out.category == "variant"


def test_classify_variants_unreadable_member_abstains(tmp_path: Path):
    # A cross-module cluster whose files cannot be read stays a plain duplicate.
    members = (
        DuplicateMember(module="a", qualified_name="f", path="/does/not/exist.py", line=1),
        DuplicateMember(module="b", qualified_name="g", path="/also/missing.py", line=1),
    )
    group = DuplicateGroup(shape_hash="h", size=10, member_count=2, redundancy=10, members=members)
    [out] = classify_variants([group])
    assert out.category == "duplicate"
    assert out.variant_reason is None


# --------------------------------------------------------------------------- #
# Path-based de-noise signals (#247): test suites, vendored / isolation dirs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "pkg/tests/test_x.py",
        "tests/helpers.py",
        "src/pkg/test/thing.py",
        "numpy/random/test_random.py",  # basename convention, not in a tests/ dir
        "pkg/conftest.py",
        "pkg/features_test.py",
    ],
)
def test_is_test_path_true(path: str):
    assert _is_test_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "pkg/testing.py",  # 'testing' is not 'test'
        "pkg/latest.py",  # substring, not a segment / basename convention
        "src/pkg/core.py",
        "contest/a.py",
    ],
)
def test_is_test_path_false(path: str):
    assert _is_test_path(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "pip/_vendor/requests/api.py",
        "ansible/module_utils/basic.py",
        "pkg/third_party/lib.py",
        ".venv/lib/python3.12/site-packages/x.py",
    ],
)
def test_is_vendored_path_true(path: str):
    assert _is_vendored_path(path) is True


def test_is_vendored_path_false():
    assert _is_vendored_path("src/pkg/core.py") is False
    assert _is_vendored_path("pkg/vendor.py") is False  # 'vendor' segment != '_vendor'


def _path_group(*paths: str) -> DuplicateGroup:
    """A cluster whose members live at the given (nonexistent) paths.

    The files need not exist: the path signals are pure-path, and the
    source-reading signals abstain on unreadable files.
    """
    members = tuple(
        DuplicateMember(module=f"m{i}", qualified_name=f"m{i}.f", path=p, line=1)
        for i, p in enumerate(paths)
    )
    return DuplicateGroup(
        shape_hash="h", size=30, member_count=len(members), redundancy=30, members=members
    )


def test_all_test_members_demote_to_variant():
    group = _path_group("pkg/tests/test_a.py", "pkg/tests/test_b.py")
    [out] = classify_variants([group])
    assert out.category == "variant"
    assert out.variant_reason == "test"


def test_all_vendored_members_demote_to_variant():
    group = _path_group("ansible/module_utils/a.py", "ansible/module_utils/b.py")
    [out] = classify_variants([group])
    assert out.category == "variant"
    assert out.variant_reason == "vendored"


def test_cross_tier_test_and_source_stays_primary():
    # One test member sharing a body with real source is a genuine finding, not
    # scaffolding: it must not be demoted.
    group = _path_group("pkg/tests/test_a.py", "pkg/core.py")
    [out] = classify_variants([group])
    assert out.category == "duplicate"
    assert out.variant_reason is None


def test_vendored_takes_precedence_over_test():
    # A cluster that is both all-vendored and all-test reports the stronger
    # (vendored) intent; either way it is demoted.
    group = _path_group("pip/_vendor/tests/test_a.py", "pip/_vendor/tests/test_b.py")
    [out] = classify_variants([group])
    assert out.variant_reason == "vendored"


def test_demoted_test_cluster_still_flagged_exact(tmp_path: Path):
    # A byte-identical clone that lives wholly in test files is demoted as
    # `test`, but `exact` is still computed so the reader sees it is copy-paste.
    pkg = _make_pkg(tmp_path)
    tests = pkg / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    body = "def {n}(xs):\n    out = []\n    for x in xs:\n        out.append(x)\n    return out\n"
    (tests / "test_a.py").write_text(body.format(n="alpha"))
    (tests / "test_b.py").write_text(body.format(n="beta"))
    modules, results = parse_project(tmp_path)
    [out] = classify_variants(compute_duplicates(modules, results, min_size=5))
    assert out.category == "variant"
    assert out.variant_reason == "test"
    assert out.exact is True


def _feat(src: bytes, line: int = 1):
    return extract_function_features(src)[line]


def test_features_capture_decorators():
    src = b"import typing\n\n@typing.overload\ndef f(x): ...\n"
    assert _feat(src, line=4).decorators == ("typing.overload",)


def test_features_strip_call_decorator_args():
    src = b"@app.get('/x')\ndef f():\n    return 1\n"
    assert _feat(src, line=2).decorators == ("app.get",)


def test_is_trivial_true_for_pure_assignment_and_getter():
    assign = _feat(b"def __init__(self, x, y):\n    self.x = x\n    self.y = y\n")
    getter = _feat(b"def val(self):\n    return self._val\n")
    assert assign.is_trivial and getter.is_trivial


def test_is_trivial_false_for_call_or_branch():
    call = _feat(b"def f(self):\n    return self._d.get('k')\n")
    branch = _feat(b"def f(self, x):\n    if x:\n        return 1\n    return 0\n")
    assert not call.is_trivial and not branch.is_trivial


def test_concrete_hash_identical_bodies_match_differ_by_literal_do_not():
    # Same body (name differs only in the def line) -> same concrete hash.
    a = _feat(b"def a(x):\n    return x + 1\n")
    b = _feat(b"def totally_other(x):\n    return x + 1\n")
    assert a.concrete_hash == b.concrete_hash != ""
    # Differ by a literal constant -> same shape, different concrete hash.
    c = _feat(b"def a(x):\n    return x + 2\n")
    assert c.concrete_hash != a.concrete_hash


def test_exact_marks_byte_identical_not_parameterized(tmp_path: Path):
    pkg = _make_pkg(tmp_path)
    same = "def {n}(xs):\n    out = []\n    for x in xs:\n        out.append(x)\n    return out\n"
    (pkg / "a.py").write_text(same.format(n="alpha"))  # byte-identical bodies
    (pkg / "b.py").write_text(same.format(n="beta"))
    param = "def {n}(xs):\n    t = 0\n    for x in xs:\n        t = t + {k}\n    return t\n"
    (pkg / "c.py").write_text(param.format(n="gamma", k="1"))  # same shape,
    (pkg / "d.py").write_text(param.format(n="delta", k="9"))  # differ by a literal
    modules, results = parse_project(tmp_path)
    groups = {
        frozenset(m.qualified_name for m in g.members): g
        for g in classify_variants(compute_duplicates(modules, results, min_size=5))
    }
    assert groups[frozenset({"alpha", "beta"})].exact is True
    assert groups[frozenset({"gamma", "delta"})].exact is False


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


def _make_pkg(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    return pkg


def _make_project(root: Path) -> None:
    pkg = _make_pkg(root)
    (pkg / "a.py").write_text(_dup_body("alpha"))
    (pkg / "b.py").write_text(_dup_body("beta"))  # same shape, different name


def test_cli_duplicates_text_smoke(tmp_path: Path):
    _make_project(tmp_path)
    result = CliRunner().invoke(main, ["duplicates", str(tmp_path), "--min-nodes", "5"])
    assert result.exit_code == 0, result.output
    assert "pkg.a" in result.output and "pkg.b" in result.output
    # alpha/beta have byte-identical bodies -> the exact (Type-1) tier.
    assert "exact duplicate(s)" in result.output


def test_cli_duplicates_json(tmp_path: Path):
    _make_project(tmp_path)
    result = CliRunner().invoke(
        main, ["duplicates", str(tmp_path), "--min-nodes", "5", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    # alpha/beta are cross-module (pkg.a, pkg.b) with a branch -> primary tier.
    assert payload["total"] == 1
    assert payload["variant_total"] == 0
    assert payload["exact_total"] == 1  # byte-identical bodies
    assert payload["note"] is None
    group = payload["duplicates"][0]
    assert group["member_count"] == 2
    assert group["category"] == "duplicate"
    assert group["exact"] is True
    assert {m["module"] for m in group["members"]} == {"pkg.a", "pkg.b"}


def test_cli_duplicates_empty_has_note(tmp_path: Path):
    _make_project(tmp_path)
    result = CliRunner().invoke(
        main, ["duplicates", str(tmp_path), "--min-nodes", "5000", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["total"] == 0
    assert payload["duplicates"] == []
    assert "lower --min-nodes" in payload["note"]


def _make_variant_project(root: Path) -> None:
    # One class with two shape-equal methods (same-class variant), plus a
    # cross-module real duplicate, so the two tiers are both populated.
    pkg = _make_pkg(root)
    # Non-trivial (a call) so `same_class` is the firing signal, not `trivial`.
    (pkg / "shapes.py").write_text(
        "class Point:\n"
        "    def scale_x(self, f):\n"
        "        v = round(self.x * f)\n"
        "        self.x = v + 1\n"
        "        return self.x\n"
        "    def scale_y(self, f):\n"
        "        v = round(self.y * f)\n"
        "        self.y = v + 1\n"
        "        return self.y\n"
    )
    real = (
        "def collect(items):\n"
        "    out = []\n"
        "    for it in items:\n"
        "        if it > 0:\n"
        "            out.append(it + 1)\n"
        "    return out\n"
    )
    (pkg / "a.py").write_text(real)
    (pkg / "b.py").write_text(real.replace("collect", "gather"))


def test_cli_duplicates_two_tiers(tmp_path: Path):
    _make_variant_project(tmp_path)
    result = CliRunner().invoke(
        main, ["duplicates", str(tmp_path), "--min-nodes", "5", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    # cross-module collect/gather -> primary; Point.scale_x/scale_y -> variant.
    dup_names = {m["qualified_name"] for g in payload["duplicates"] for m in g["members"]}
    var = payload["variants"]
    assert dup_names == {"collect", "gather"}
    assert len(var) == 1
    assert var[0]["variant_reason"] == "same_class"
    assert {m["qualified_name"] for m in var[0]["members"]} == {
        "Point.scale_x",
        "Point.scale_y",
    }


def test_cli_duplicates_rejects_bad_flags(tmp_path: Path):
    _make_project(tmp_path)
    assert (
        CliRunner().invoke(main, ["duplicates", str(tmp_path), "--min-nodes", "0"]).exit_code != 0
    )
    assert CliRunner().invoke(main, ["duplicates", str(tmp_path), "--members", "1"]).exit_code != 0


def test_cli_duplicates_isinstance_check():
    # Guards the public type name the MCP follow-up (PR2) will import.
    assert issubclass(DuplicateGroup, BaseModel)
