from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from archy.conventions import ConventionsReport, camel_suffix, compute_conventions


def _families(report: ConventionsReport) -> list:
    """Flatten the by-home-module naming report back to a list of families."""
    return [family for home in report.naming for family in home.families]


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
            )
        },
    )
    payload = compute_conventions(project).model_dump()
    assert "gate_modules" in payload
    assert "concentration" in payload["naming"][0]["families"][0]
    assert {"total", "family_count"} <= set(payload["naming"][0])
    assert "surface_count" in payload["surfaces"][0]
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
