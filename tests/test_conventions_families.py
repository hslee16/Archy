"""Tests for conventions: families."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from archy.cli_render import _conventions_to_text
from archy.conventions import (
    camel_suffix,
    compute_conventions,
    compute_module_view,
)
from archy.conventions_collect import (
    _inner_names_by_prefix,
    _ModuleFacts,
    _names_outside_prefix,
    _package_reexports,
    _shadow_roots,
)
from archy.conventions_families import _collection_fields, _frozen_config
from archy.graph import build_graph
from tests.conventions_helpers import (
    _HOME_UNDER_SEED,
    _TIED_FAMILY,
    _families,
    _new_project,
    _project,
    _tmp,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ReachViolation", "Violation"),
        ("Violation", "Violation"),
        # An acronym run is one part, so the family is the word after it, not
        # a per-letter split. `DSMDiff` belongs with `DiffReport`'s `Diff`.
        ("DSMDiff", "Diff"),
        ("MCPServer", "Server"),
        ("Payload2", "Payload2"),
        ("lowercase", "lowercase"),
    ],
)
def test_camel_suffix_takes_the_trailing_segment(name: str, expected: str):
    assert camel_suffix(name) == expected


def test_naming_family_reports_home_module_and_concentration(tmp_path: Path):
    project = _project(
        tmp_path,
        {
            "layers.py": (
                "class Violation: pass\nclass ReachViolation: pass\nclass SdpViolation: pass\n"
            ),
            "other.py": "class StrayViolation: pass\n",
        },
    )
    report = compute_conventions(project)
    family = next(f for f in _families(report) if f.suffix == "Violation")
    assert family.count == 4
    assert family.home_module == "pkg.layers"
    assert family.home_count == 3
    assert family.concentration == pytest.approx(0.75)
    assert family.modules == ("pkg.layers", "pkg.other")


def test_naming_singletons_are_below_the_family_floor(tmp_path: Path):
    project = _project(
        tmp_path,
        {"m.py": "class Alone: pass\nclass FirstPair: pass\nclass SecondPair: pass\n"},
    )
    report = compute_conventions(project)
    suffixes = {f.suffix for f in _families(report)}
    # `FirstPair`/`SecondPair` share a suffix; `Alone` does not, and a
    # one-member family is a name, not a convention.
    assert "Pair" in suffixes
    assert "Alone" not in suffixes


def test_min_family_raises_the_floor(tmp_path: Path):
    project = _project(tmp_path, {"m.py": "class OnePayload: pass\nclass TwoPayload: pass\n"})
    assert any(f.suffix == "Payload" for f in _families(compute_conventions(project)))
    assert not compute_conventions(project, min_family=3).naming


def test_helper_surfaces_group_by_trailing_segment(tmp_path: Path):
    project = _project(
        tmp_path,
        {
            "cli.py": (
                "def _thing_to_text(): pass\n"
                "def _thing_to_json(): pass\n"
                "def _thing_to_mcp(): pass\n"
                "def unrelated(): pass\n"
            )
        },
    )
    report = compute_conventions(project)
    helper = next(s for s in report.surfaces if s.kind == "helper" and s.stem == "_thing_to")
    assert helper.surfaces == ("json", "mcp", "text")
    assert helper.surface_count == 3
    assert helper.module == "pkg.cli"


def test_advisory_project_reports_no_gates(tmp_path: Path):
    project = _project(tmp_path, {"a.py": "def render():\n    return 'ok'\n"})
    report = compute_conventions(project)
    assert report.gates == ()
    assert report.gate_modules == ()


def test_model_census_counts_frozen_pydantic_and_field_style(tmp_path: Path):
    project = _project(
        tmp_path,
        {
            "models.py": (
                "from pydantic import BaseModel, ConfigDict\n"
                "class Frozen(BaseModel):\n"
                "    model_config = ConfigDict(frozen=True)\n"
                "    rows: tuple[str, ...]\n"
                "    name: str\n"
                "class Loose(BaseModel):\n"
                "    rows: list[str]\n"
                "class Plain:\n"
                "    pass\n"
            )
        },
    )
    census = compute_conventions(project).models
    assert census.total_classes == 3
    assert census.value_classes == 2
    assert census.frozen_classes == 1
    assert census.dominant_base == "BaseModel"
    assert census.frozen_ratio == pytest.approx(0.5)
    assert census.tuple_fields == 1
    assert census.list_fields == 1
    assert census.tuple_ratio == pytest.approx(0.5)
    assert census.config_flags == (("frozen", 1),)


def test_unparsable_file_is_counted_not_raised(tmp_path: Path):
    # Advisory command: a half-written file must not turn reporting into an
    # error, and the count is how the caller learns coverage was partial.
    project = _project(tmp_path, {"good.py": "class APayload: pass\n", "bad.py": "def (\n"})
    report = compute_conventions(project)
    assert report.modules_unparsed == 1
    # `pkg/__init__.py`, `pkg/good.py` -- `pkg/bad.py` was skipped.
    assert report.modules_scanned == 2


def test_min_family_below_two_is_rejected_by_the_callers_not_here(tmp_path: Path):
    # compute_conventions itself has no floor; a min_family of 1 makes every
    # single class a "family", which is why both callers validate >= 2.
    project = _project(tmp_path, {"a.py": "class Alone: pass\n"})
    assert [f.suffix for f in _families(compute_conventions(project, min_family=1))] == ["Alone"]


def test_report_is_frozen(tmp_path: Path):
    report = compute_conventions(_project(tmp_path, {"a.py": ""}))
    with pytest.raises(ValidationError):
        report.root = "elsewhere"


def test_computed_fields_survive_model_dump(tmp_path: Path):
    # The MCP wire form is `model_dump()`, which drops plain properties. Every
    # derived value a consumer reads must therefore be a `computed_field`.
    project = _project(
        tmp_path,
        {
            "cli.py": (
                "import sys\n"
                "class OnePayload: pass\n"
                "class TwoPayload: pass\n"
                "def _x_to_text(): pass\n"
                "def _x_to_json(): pass\n"
                "def go():\n"
                "    sys.exit(1)\n"
            ),
            "errors.py": (
                "class Base(Exception): pass\n"
                "class FirstProblem(Base): pass\n"
                "class SecondProblem(Base): pass\n"
            ),
        },
    )
    payload = compute_conventions(project).model_dump()
    assert "gate_modules" in payload
    assert "concentration" in payload["naming"][0]["families"][0]
    assert {"total", "family_count"} <= set(payload["naming"][0])
    assert "surface_count" in payload["surfaces"][0]
    # `suffix_agreement` is the one an agent acts on: it says whether the base
    # or the name is the rule, so a plain property here would silently answer
    # "the name" on every MCP call.
    assert {"concentration", "suffix_agreement"} <= set(payload["bases"][0])
    assert "optional" in payload["gates"][0]
    assert {"frozen_ratio", "tuple_ratio", "dominant_base"} <= set(payload["models"])


def test_module_level_entry_point_is_not_a_gate(tmp_path: Path):
    # `if __name__ == "__main__": sys.exit(main())` forwards a return value,
    # it does not state a verdict. Counting it would inflate every Click
    # project by exactly one.
    project = _project(
        tmp_path,
        {"cli.py": ("import sys\ndef main():\n    return 0\nsys.exit(main())\n")},
    )
    assert compute_conventions(project).gates == ()


def test_naming_groups_by_home_module_so_a_small_family_stays_visible(tmp_path: Path):
    # The regression this shape exists to prevent: ranked as a flat list of
    # suffixes, a big generic family buries a small sharply-located one, and
    # concentration-weighting does not help because the big one is perfectly
    # concentrated too.
    payloads = "".join(f"class P{i}Payload: pass\n" for i in range(13))
    project = _project(
        tmp_path,
        {
            "mcp.py": payloads,
            "layers.py": "class Violation: pass\nclass ReachViolation: pass\n",
        },
    )
    report = compute_conventions(project)
    modules = [home.module for home in report.naming]
    assert modules == ["pkg.mcp", "pkg.layers"]
    layers = report.naming[1]
    assert layers.total == 2
    assert layers.family_count == 1
    assert [f.suffix for f in layers.families] == ["Violation"]


def test_home_families_are_ordered_largest_first(tmp_path: Path):
    project = _project(
        tmp_path,
        {
            "m.py": (
                "class OneRule: pass\n"
                "class TwoRule: pass\n"
                "class ThreeRule: pass\n"
                "class OneSpec: pass\n"
                "class TwoSpec: pass\n"
            )
        },
    )
    home = compute_conventions(project).naming[0]
    assert [f.suffix for f in home.families] == ["Rule", "Spec"]
    assert home.total == 5


def test_kind_family_follows_inheritance_past_the_intermediate_class():
    """`click` names `ClickException` on three classes; the rest arrive via
    `UsageError`. A direct-edge census reports the intermediate as the family."""
    src = """
class ClickException(Exception):
    exit_code = 1
class UsageError(ClickException):
    exit_code = 2
class BadParameter(UsageError): pass
class MissingParameter(BadParameter): pass
"""
    report = compute_conventions(_tmp(src))
    family = next(b for b in report.bases if b.base == "ClickException")
    assert family.count == 4  # the three descendants and the base itself
    assert "MissingParameter" in family.members


def test_kind_family_ignores_bases_this_project_does_not_define():
    """`ABC`, `Protocol` and `Exception` are used identically everywhere and
    out-count every real family, so a local definition is required."""
    src = """
from abc import ABC
class A(ABC): pass
class B(ABC): pass
class C(ABC): pass
"""
    report = compute_conventions(_tmp(src))
    assert not [b for b in report.bases if b.base == "ABC"]


def test_kind_family_home_is_the_same_under_every_hash_seed(tmp_path: Path):
    """#875: `_base_families` counts members from a `set` of names, so a tie
    was broken in string-hash order and the home changed with PYTHONHASHSEED.
    `pkg.zeta` and `pkg.alpha` host two members each (`pkg.base` hosts one);
    the tie goes to the name that sorts first, worked out by hand: `pkg.alpha`."""
    root = _project(tmp_path, _TIED_FAMILY)
    family = next(b for b in compute_conventions(root).bases if b.base == "Widget")
    # The fixture must really tie, or the assertion below proves nothing.
    assert family.count == 5
    assert family.home_count == 2  # five members, at most two per module: a 2-2-1 tie
    per_module = {name: source.count("(Widget)") for name, source in _TIED_FAMILY.items()}
    assert per_module == {"base.py": 0, "zeta.py": 2, "alpha.py": 2}

    homes = set()
    for seed in ("0", "1", "2", "3", "4", "5"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run(
            [sys.executable, "-c", _HOME_UNDER_SEED, str(root)],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        homes.add(done.stdout.strip())
    assert homes == {"pkg.alpha 2"}


def test_shared_constant_reports_the_split_not_just_the_values():
    """The gate convention lives in the declaration. `click`'s single
    `sys.exit` is in generic dispatch code, so an exit-site census finds
    nothing in the module that actually decides severity."""
    src = """
class Base(Exception):
    exit_code = 1
class Usage(Base):
    exit_code = 2
class Other(Base): pass
"""
    report = compute_conventions(_tmp(src))
    family = next(b for b in report.bases if b.base == "Base")
    const = next(c for c in family.shared_constants if c.name == "exit_code")
    assert const.setters == 2
    assert dict(const.distribution) == {"1": 1, "2": 1}


def test_registry_reports_keyword_defaults_of_repeated_construction():
    """`mypy` declares 79 error codes as module-level assignments, so a census
    that walks only ClassDef sees none of them -- including `default_enabled`,
    the keyword that answers whether a new code gates."""
    src = """
class ErrorCode:
    def __init__(self, code, desc, default_enabled=True): ...
A = ErrorCode("arg-type", "d")
B = ErrorCode("attr-defined", "d")
C = ErrorCode("unimported-reveal", "d", default_enabled=False)
D = ErrorCode("explicit-any", "d", default_enabled=False)
"""
    report = compute_conventions(_tmp(src))
    entry = next(r for r in report.registries if r.constructor == "ErrorCode")
    assert entry.count == 4
    assert "arg-type" in entry.literal_names
    kw = next(c for c in entry.keyword_defaults if c.name == "default_enabled")
    # Two of four *pass* it; the other two take the default. Reporting the
    # setter count is what keeps that from reading as "all of them are False".
    assert kw.setters == 2


def test_registry_does_not_echo_prose_as_a_name():
    """`mypy.message_registry` has 102 entries whose first argument is a full
    diagnostic sentence. Echoing them would spend the context this saves."""
    src = """
class ErrorMessage:
    def __init__(self, text): ...
A = ErrorMessage("Cannot infer type of lambda from the surrounding context")
B = ErrorMessage("Argument after ** must be a mapping, not a sequence type")
C = ErrorMessage("Overloaded function signatures 1 and 2 overlap")
"""
    report = compute_conventions(_tmp(src))
    entry = next(r for r in report.registries if r.constructor == "ErrorMessage")
    assert entry.count == 3
    assert entry.literal_names == ()


def test_registry_skips_type_system_plumbing():
    src = """
from typing import TypeVar
A = TypeVar("A")
B = TypeVar("B")
C = TypeVar("C")
"""
    assert not [r for r in compute_conventions(_tmp(src)).registries if r.constructor == "TypeVar"]


def test_vendored_subtree_is_set_aside_by_overlap_with_its_parent(tmp_path: Path):
    """`pydantic`'s naming home came back as `pydantic.v1.errors` -- the legacy
    copy, 93 classes -- in place of `pydantic.errors` with 6. Detected by
    overlap rather than by name: a project whose `v1` is the live one is fine."""
    pkg = tmp_path / "pkg"
    (pkg / "v1").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "v1" / "__init__.py").write_text("")
    for name in ("errors", "main", "fields", "types", "networks", "utils"):
        (pkg / f"{name}.py").write_text("class Thing: pass\n")
        (pkg / "v1" / f"{name}.py").write_text(
            "\n".join(f"class Legacy{i}: pass" for i in range(10))
        )
    report = compute_conventions(tmp_path)
    assert report.partition is not None
    assert "pkg.v1" in report.partition.shadow_roots
    assert report.partition.shadowed == 7
    assert all("v1" not in home.module for home in report.naming)


def test_tests_are_set_aside_unless_asked_for(tmp_path: Path):
    """Fixture classes outnumber the code they exercise and win any count they
    are entered in; measured across four projects the top mirrored surface was
    a test fixture in four cases out of four."""
    pkg = tmp_path / "pkg"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "tests" / "__init__.py").write_text("")
    (pkg / "real.py").write_text("class Base: pass\nclass RealThing(Base): pass\n")
    (pkg / "tests" / "test_it.py").write_text(
        "\n".join(f"class Base: pass\nclass Fixture{i}(Base): pass" for i in range(8))
    )
    default = compute_conventions(tmp_path)
    assert default.partition is not None and default.partition.tests == 2
    assert all("test" not in home.module for home in default.naming)

    with_tests = compute_conventions(tmp_path, include_tests=True)
    assert with_tests.partition is not None and with_tests.partition.tests == 0
    assert with_tests.modules_scanned > default.modules_scanned


def test_conventions_reports_nothing_when_a_project_ships_no_docs(tmp_path: Path):
    _project(tmp_path, {"m.py": "class Base: pass\nclass A(Base): pass\nclass B(Base): pass\n"})
    report = compute_conventions(tmp_path)
    assert report.docs_scanned == 0
    assert report.doc_gaps == ()


def test_two_definitions_of_one_name_stay_separate_families():
    """Keying the census on the bare name merged them: importers of `pkg.a.foo`
    were reported under `pkg.b` because the last home written won. Each
    definition consumed unambiguously is its own co-update set."""
    root = _new_project(
        {
            "a.py": "def foo(): return 1\n",
            "b.py": "def foo(): return 2\n",
            "c.py": "from pkg.a import foo\n",
            "d.py": "from pkg.a import foo\n",
            "e.py": "from pkg.b import foo\n",
            "f.py": "from pkg.b import foo\n",
        }
    )
    fams = [s for s in compute_conventions(root).surfaces if s.kind == "consumer"]
    assert {(s.module, s.surfaces) for s in fams} == {
        ("pkg.a", ("pkg.c", "pkg.d")),
        ("pkg.b", ("pkg.e", "pkg.f")),
    }


def test_cross_module_families_outrank_larger_same_module_ones():
    """Sorting by member count alone buried this project's own render surfaces
    at rank 38 of 50, behind a 13-member family of one-helper-per-tool that no
    caller has to keep in step. A family confined to one file is wired in a
    single edit; a family spanning several is the one that gets half-wired."""
    root = _new_project(
        {
            "core.py": "def render(): ...\n",
            "a.py": "from pkg.core import render\n",
            "b.py": "from pkg.core import render\n",
            # a large same-module helper family, of the shape that used to win
            "big.py": "\n".join(f"def _run_{n}(): ..." for n in "abcdefghij") + "\n",
        },
    )
    report = compute_conventions(root)
    kinds = [s.kind for s in report.surfaces]
    assert kinds.index("consumer") < kinds.index("helper")
    big = next(s for s in report.surfaces if s.stem == "_run")
    small = next(s for s in report.surfaces if s.kind == "consumer" and s.stem == "render")
    assert big.surface_count > small.surface_count  # and still ranks below it


def test_truncated_sections_say_how_to_see_the_rest(tmp_path: Path):
    """A reader told "showing 12" and not told how to reach the other 138 cannot
    tell a missing fact from an unranked one. Measured: 24 pieces of real agent
    reasoning, every one chosen because this command could in principle answer
    it, scored zero -- and both blind readers named truncation rather than the
    analysis. The number was computed and then withheld."""
    # one distinct suffix family per module, so there are many homes to truncate
    root = _project(
        tmp_path,
        {f"m{i}.py": f"class AlphaKind{i}: pass\nclass BetaKind{i}: pass\n" for i in range(8)},
    )
    report = compute_conventions(root)
    text = _conventions_to_text(report, top_n=2)
    assert "--top" in text, "a truncated section must name the flag that widens it"
    # and an untruncated section must not nag
    wide = _conventions_to_text(report, top_n=500)
    assert "--top" not in wide


def test_setting_tests_aside_says_how_to_include_them(tmp_path: Path):
    """Setting tests aside is right when a fixture pile would win every count and
    wrong when the question IS about tests -- "where do helpers live in this test
    file and what are they called" is a real convention. One of the 24 scored
    derivations asked exactly that, and the report could not answer it."""
    pkg = tmp_path / "pkg"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "tests" / "__init__.py").write_text("")
    (pkg / "tests" / "test_it.py").write_text("def test_x(): ...\n")
    (pkg / "real.py").write_text("class Thing: pass\n")
    report = compute_conventions(tmp_path)
    assert report.partition is not None and report.partition.tests > 0
    assert "--include-tests" in _conventions_to_text(report, top_n=12)


def test_lookup_resolves_each_import_shape_to_a_distinct_module(tmp_path: Path):
    """Pin the ANSWERS, and make each shape reach somewhere only it can reach.

    Two earlier drafts of this test looked stronger than they were, so the
    fixture is built against both failures.

    The first asserted only that the lookup and the graph AGREE, which is
    tautological now that both call `graph.resolve_from_import`: break the
    shared function and both break identically, so they still agree.

    The second pinned answers but let the submodule shape and the relative
    shape resolve to the SAME module, so the set collapsed them and an
    off-by-one in `resolve_relative_import`'s level arithmetic still passed.
    Every shape below therefore resolves somewhere no other shape reaches.
    """
    pkg = tmp_path / "app"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("from app.impl import Thing\n")
    (pkg / "impl.py").write_text("Thing = 1\n")
    (pkg / "sibling.py").write_text("x = 1\n")
    # Reachable ONLY through the two-level relative walk below, so nothing else
    # in this fixture can put it in the result set.
    (pkg / "only_via_relative.py").write_text("y = 1\n")
    (pkg / "sub" / "__init__.py").write_text("")
    (pkg / "sub" / "uses.py").write_text(
        "from app import Thing\n"  # a re-exported name routes to the DEFINING module
        "from app import sibling\n"  # a submodule is an edge to the submodule
        "from .. import only_via_relative\n"  # walks up out of the subpackage
    )

    resolved = set(compute_module_view(tmp_path, "app.sub.uses").imports_internal)

    # `from app import Thing` must not stop at the package: `app.impl` defines it.
    assert "app.impl" in resolved
    # `from app import sibling` is the submodule, never the bare package.
    assert "app.sibling" in resolved
    # Only the `..` walk can reach this one, so it fails if level handling breaks.
    assert "app.only_via_relative" in resolved
    # Attributing the submodule import to `app` as well was one of the five.
    assert "app" not in resolved

    # And the graph, the surface the lookup must never contradict, agrees.
    g = build_graph(tmp_path)
    internal = {n for n, d in g.nodes(data=True) if not d.get("external")}
    assert {t for t in g.successors("app.sub.uses") if t in internal} == resolved


@pytest.mark.parametrize(
    "x_init",
    [
        pytest.param("", id="y-is-the-submodule"),
        pytest.param("def y():\n    return 2\n", id="y-is-a-symbol-shadowing-the-submodule"),
    ],
)
def test_lookup_stops_at_the_reexporting_submodule_when_the_name_is_also_a_module(
    tmp_path: Path, x_init: str
):
    """#436: `from .x import y` where `x/y.py` exists. Python prefers the attribute,
    so with a symbol `y` in `x` the file `y.py` is never imported; the syntax
    cannot say which shape this is, so both resolve to `app.x`, the one edge
    true in both. `app.x.y` would be an invented import in the second case."""
    pkg = tmp_path / "app"
    (pkg / "x").mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .x import y\n")
    (pkg / "x" / "__init__.py").write_text(x_init)
    (pkg / "x" / "y.py").write_text("VALUE = 1\n")
    (pkg / "consumer.py").write_text("from app import y\n")

    resolved = set(compute_module_view(tmp_path, "app.consumer").imports_internal)

    assert resolved == {"app.x"}
    g = build_graph(tmp_path)
    assert set(g.successors("app.consumer")) == {"app.x"}


def test_conventions_report_lists_unparsed_modules(tmp_path: Path):
    project = _project(tmp_path, {"good.py": "class APayload: pass\n", "bad.py": "def (\n"})
    report = compute_conventions(project)
    dumped = report.model_dump()
    assert dumped["modules_unparsed"] == 1
    assert [u["module"] for u in dumped["unparsed_modules"]] == ["pkg.bad"]
    text = _conventions_to_text(report, top_n=8)
    assert "pkg.bad" in text


# --- #889: one pin per conventions rule the mutation run found unguarded ---


def test_shadow_roots_decides_the_ratio_boundary_and_the_module_floor():
    """Hand-worked expectations for _shadow_roots (#889).

    pkg.v1 restates 5 of the parent's 10 names and adds 5 of its own, so
    its inner set holds 10 names with an overlap of exactly 5: a ratio of
    0.5, which is a shadow only because the comparison is >=. pkg.v2
    restates 4 names with an inner set of 4, a perfect overlap that stays
    under the min_modules floor of 5, so it is not a shadow. The other.*
    names sit under a different top-level package and count for neither
    side.
    """
    parent = [f"pkg.m{i:02d}" for i in range(1, 11)]
    names = (
        parent
        + ["pkg.v1", "pkg.v2"]
        + [f"pkg.v1.m{i:02d}" for i in range(1, 6)]
        + [f"pkg.v1.n{i:02d}" for i in range(1, 6)]
        + [f"pkg.v2.m{i:02d}" for i in range(1, 5)]
        + [f"other.m{i:02d}" for i in range(1, 6)]
    )
    by_prefix = _inner_names_by_prefix(names)
    # The fixture really reaches both branches: v1 has 10 inner names with
    # an overlap of exactly 5, and v2 has only 4 inner names.
    assert len(by_prefix["pkg.v1"]) == 10
    assert len(by_prefix["pkg.v1"] & _names_outside_prefix("pkg.v1", names)) == 5
    assert len(by_prefix["pkg.v2"]) == 4

    assert _shadow_roots(names) == frozenset({"pkg.v1"})


def test_shadow_roots_outside_names_exclude_the_prefix_itself():
    """A package's own modules are not outside names for itself (#889).

    lib.v1 restates 2 of its parent's names (alpha, beta) out of an inner
    set of 5, an overlap ratio of 0.4, so it is not a shadow. Its inner
    set also holds v1.alpha and v1.beta, which are exactly the names its
    own top-level modules strip to under the root; counting the prefix's
    own subtree as outside names would lift the overlap to 4 of 5 and
    invent a shadow.
    """
    names = [
        "lib.alpha",
        "lib.beta",
        "lib.v1.alpha",
        "lib.v1.beta",
        "lib.v1.extra",
        "lib.v1.v1.alpha",
        "lib.v1.v1.beta",
    ]
    assert _inner_names_by_prefix(names)["lib.v1"] == {
        "alpha",
        "beta",
        "extra",
        "v1.alpha",
        "v1.beta",
    }
    assert _names_outside_prefix("lib.v1", names) == {"alpha", "beta"}

    assert _shadow_roots(names) == frozenset()


def _package_facts(
    qualname: str, import_froms: list[tuple[str, tuple[tuple[str, str], ...]]]
) -> _ModuleFacts:
    facts = _ModuleFacts(qualname)
    facts.is_package = True
    facts.import_froms = import_froms
    return facts


def test_package_reexports_reject_self_unknown_and_outside_sources():
    """Only a known submodule of the package can be a re-export (#889)."""
    known = frozenset({"pkg", "pkg.mod"})
    cases: list[tuple[list[tuple[str, tuple[tuple[str, str], ...]]], dict[str, str]]] = [
        # A known submodule: the one shape that does re-export.
        ([("pkg.mod", (("Thing", "Thing"),))], {"Thing": "pkg.mod"}),
        # The package importing from itself is not a re-export.
        ([("pkg", (("Thing", "Thing"),))], {}),
        # A submodule-shaped base that does not exist is not a re-export.
        ([("pkg.ghost", (("Thing", "Thing"),))], {}),
        # An import from outside the package is not a re-export.
        ([("other.mod", (("Thing", "Thing"),))], {}),
    ]
    for import_froms, expected in cases:
        assert _package_reexports("pkg", _package_facts("pkg", import_froms), known) == expected


def _class_def(source: str) -> ast.ClassDef:
    return next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef))


def test_frozen_config_counts_only_the_literal_true():
    """frozen=1 is truthy, but only the literal True freezes (#889)."""
    cases = [
        ("model_config = ConfigDict(frozen=True)", True),
        ("model_config = ConfigDict(frozen=False)", False),
        ("model_config = ConfigDict(frozen=1)", False),
        ("model_config = ConfigDict(frozen=SOME_FLAG)", False),
    ]
    for line, expected in cases:
        frozen, flags = _frozen_config(_class_def(f"class A(BaseModel):\n    {line}\n"))
        assert frozen is expected
        assert flags == ["frozen"]


def test_collection_fields_count_tuple_spellings_not_just_the_exact_head():
    """tuple and typing.Tuple both count; Sequence and dict do not (#889)."""
    cls = _class_def(
        "class A:\n"
        "    a: tuple[int, ...]\n"
        "    b: typing.Tuple[int]\n"
        "    c: list[str]\n"
        "    d: Sequence[int]\n"
        "    e: dict[str, int]\n"
    )
    assert _collection_fields(cls) == (2, 1)
