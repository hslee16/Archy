from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from archy.conventions import (
    ConventionsReport,
    NamingFamily,
    camel_suffix,
    compute_conventions,
)


def _families(report: ConventionsReport) -> list[NamingFamily]:
    """Flatten the by-home-module naming report back to a list of families."""
    return [family for home in report.naming for family in home.families]


def _tmp(source: str) -> Path:
    """One module in a throwaway package, for cases reduced from a real project."""
    root = Path(tempfile.mkdtemp())
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(source)
    return root


def _project(tmp_path: Path, files: dict[str, str]) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name, source in files.items():
        (pkg / name).write_text(source)
    return tmp_path


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


def test_mirrored_names_report_every_module_defining_them(tmp_path: Path):
    project = _project(
        tmp_path,
        {"a.py": "class Cycle: pass\n", "b.py": "class Cycle: pass\n"},
    )
    report = compute_conventions(project)
    mirrored = next(s for s in report.surfaces if s.kind == "mirrored" and s.stem == "Cycle")
    assert mirrored.surfaces == ("pkg.a", "pkg.b")
    assert mirrored.module == ""


def test_gate_control_names_the_click_flag(tmp_path: Path):
    project = _project(
        tmp_path,
        {
            "cli.py": (
                "import click\n"
                "import sys\n"
                "@click.command()\n"
                '@click.option("--strict", is_flag=True)\n'
                "def run(strict):\n"
                "    if strict:\n"
                "        sys.exit(1)\n"
            )
        },
    )
    report = compute_conventions(project)
    gate = next(g for g in report.gates if g.function == "run")
    assert gate.control == "flag:--strict"
    assert gate.kind == "sys.exit"
    assert gate.category == "gate"
    assert gate.code == 1
    assert gate.is_command is True
    assert gate.optional is True
    assert report.gate_modules == ("pkg.cli",)


def test_gate_control_falls_back_to_config_attribute_then_hardcoded(tmp_path: Path):
    project = _project(
        tmp_path,
        {
            "a.py": (
                "import sys\n"
                "def gated(config):\n"
                "    if config.fail_on_error:\n"
                "        sys.exit(2)\n"
                "def always():\n"
                "    raise SystemExit(1)\n"
            )
        },
    )
    report = compute_conventions(project)
    by_function = {g.function: g for g in report.gates}
    # `config` is a parameter, but the ATTRIBUTE is the lever a caller sets, so
    # the more specific reading wins over `param:config`.
    assert by_function["gated"].control == "config:config.fail_on_error"
    assert by_function["always"].control == "hardcoded"
    assert by_function["always"].optional is False


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


# --- gate vs error: the split the command exists to make ----------------------


def test_finding_exits_and_user_error_exits_are_counted_separately(tmp_path: Path):
    # A `sys.exit(n)` says a FINDING failed (the code IS the result); a raised
    # ClickException says the USER supplied bad input. Pooling them makes the
    # headline count answer a different question than "should my finding gate".
    project = _project(
        tmp_path,
        {
            "cli.py": (
                "import click\n"
                "import sys\n"
                "def gate(violations):\n"
                "    if violations:\n"
                "        sys.exit(1)\n"
                "def bad_config(path):\n"
                "    if not path:\n"
                '        raise click.ClickException("no config")\n'
                "def bad_flag(top):\n"
                "    if top < 1:\n"
                '        raise click.UsageError("--top must be >= 1")\n'
            )
        },
    )
    report = compute_conventions(project)
    assert [g.function for g in report.gates] == ["gate"]
    assert sorted(g.function for g in report.errors) == ["bad_config", "bad_flag"]
    assert {g.category for g in report.gates} == {"gate"}
    assert {g.category for g in report.errors} == {"error"}
    # `gate_modules` is about finding-failure only; a module that merely
    # rejects bad input is not a module that gates.
    assert report.gate_modules == ("pkg.cli",)


def test_literal_exit_codes_are_reported_and_computed_ones_are_not(tmp_path: Path):
    # "everything fails with 1" is itself the convention; a computed code has
    # two possible values, so reporting either one would be a lie.
    project = _project(
        tmp_path,
        {
            "cli.py": (
                "import sys\n"
                "def fixed():\n"
                "    sys.exit(1)\n"
                "def computed(ok):\n"
                "    sys.exit(0 if ok else 1)\n"
                "def framework():\n"
                "    raise SystemExit(3)\n"
            )
        },
    )
    report = compute_conventions(project)
    codes = {g.function: g.code for g in report.gates}
    assert codes == {"fixed": 1, "computed": None, "framework": 3}
    assert report.gate_codes == (1, 3)


def test_module_level_entry_point_is_not_a_gate(tmp_path: Path):
    # `if __name__ == "__main__": sys.exit(main())` forwards a return value,
    # it does not state a verdict. Counting it would inflate every Click
    # project by exactly one.
    project = _project(
        tmp_path,
        {"cli.py": ("import sys\ndef main():\n    return 0\nsys.exit(main())\n")},
    )
    assert compute_conventions(project).gates == ()


# --- naming grouped by home module -------------------------------------------


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


# --------------------------------------------------------------------------
# Detection of conventions that a suffix-and-exit-site census cannot see.
#
# Every case below is reduced from a real project where the original census
# returned a wrong or empty answer: `click` (kinds, shared constants, PEP 484
# re-exports), `mypy` (registries, keyword defaults) and `pydantic` (a
# vendored subtree outranking the live package).


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


def test_export_gap_reads_pep484_re_exports_when_there_is_no_dunder_all(tmp_path: Path):
    """`click` publishes its API with `from .x import Y as Y` and no `__all__`.
    Reading only `__all__` reports that such a package exports nothing."""
    root = _project(
        tmp_path,
        {
            "errors.py": (
                "class Base(Exception): pass\n"
                "class First(Base): pass\n"
                "class Second(Base): pass\n"
                "class Forgotten(Base): pass\n"
            ),
            "__init__.py": (
                "from .errors import Base as Base\n"
                "from .errors import First as First\n"
                "from .errors import Second as Second\n"
                # a plain import is an implementation detail, not a promise
                "from .errors import Forgotten\n"
            ),
        },
    )
    report = compute_conventions(root)
    gap = next(g for g in report.export_gaps if g.family == "Base")
    assert gap.missing == ("Forgotten",)


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


def _documented_project(tmp_path: Path, doc: str) -> Path:
    root = _project(
        tmp_path,
        {
            "errors.py": (
                "class Base(Exception): pass\n"
                "class First(Base): pass\n"
                "class Second(Base): pass\n"
                "class Undocumented(Base): pass\n"
            )
        },
    )
    docs = root / "docs"
    docs.mkdir()
    (docs / "api.rst").write_text(doc)
    return root


def test_doc_gap_finds_a_member_its_own_docs_do_not_name(tmp_path: Path):
    """`pytest` ships `PytestFDWarning` exported and with no `autoclass`
    entry -- the same half-wired defect as a missing re-export."""
    root = _documented_project(
        tmp_path,
        ".. autoexception:: Base\n.. autoexception:: First\n.. autoexception:: Second\n",
    )
    report = compute_conventions(root)
    gap = next(g for g in report.doc_gaps if g.family == "Base")
    assert gap.missing == ("Undocumented",)
    assert (gap.documented, gap.defined) == (3, 4)


def test_doc_gap_stays_silent_when_the_docs_are_complete(tmp_path: Path):
    root = _documented_project(
        tmp_path,
        ".. autoexception:: Base\n.. autoexception:: First\n"
        ".. autoexception:: Second\n.. autoexception:: Undocumented\n",
    )
    assert not compute_conventions(root).doc_gaps


def test_doc_gap_ignores_a_family_the_docs_barely_touch(tmp_path: Path):
    """One member mentioned in passing is not a promise to document the rest;
    reporting it would bury the real gaps."""
    root = _documented_project(tmp_path, "See ``First`` for details.\n")
    assert not compute_conventions(root).doc_gaps


def test_doc_gap_matches_a_slug_in_prose_but_not_a_bare_identifier(tmp_path: Path):
    """Strictness scales with ambiguity. `mypy` documents each of its 79 codes
    as `[arg-type]` in a section heading and six of them are ordinary English
    words, so both a hyphen rule and a bracket rule are needed; matching a bare
    CamelCase word in a sentence would call anything documented."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "codes.py").write_text(
        "class ErrorCode:\n"
        "    def __init__(self, code): ...\n"
        'A = ErrorCode("arg-type")\n'
        'B = ErrorCode("override")\n'
        'C = ErrorCode("attr-defined")\n'
        'D = ErrorCode("no-untyped-call")\n'
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "codes.rst").write_text(
        "Check argument types [arg-type]\n"
        "Check overrides [override]\n"
        "An attr-defined problem is reported when...\n"
        # no-untyped-call is named nowhere
    )
    report = compute_conventions(tmp_path)
    gap = next(g for g in report.doc_gaps if g.family == "ErrorCode(...)")
    assert gap.missing == ("no-untyped-call",)


def test_doc_gap_does_not_report_private_members(tmp_path: Path):
    """A project is entitled to leave `_PydanticGeneralMetadata` out on purpose."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text(
        "class Base: pass\nclass First(Base): pass\n"
        "class Second(Base): pass\nclass _Private(Base): pass\n"
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "api.md").write_text("`Base` `First` `Second`\n")
    assert not compute_conventions(tmp_path).doc_gaps


def test_conventions_reports_nothing_when_a_project_ships_no_docs(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "m.py").write_text("class Base: pass\nclass A(Base): pass\nclass B(Base): pass\n")
    report = compute_conventions(tmp_path)
    assert report.docs_scanned == 0
    assert report.doc_gaps == ()
