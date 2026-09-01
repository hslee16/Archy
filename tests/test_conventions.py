from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from archy.conventions import camel_suffix, compute_conventions


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
    family = next(f for f in report.naming if f.suffix == "Violation")
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
    suffixes = {f.suffix for f in report.naming}
    # `FirstPair`/`SecondPair` share a suffix; `Alone` does not, and a
    # one-member family is a name, not a convention.
    assert "Pair" in suffixes
    assert "Alone" not in suffixes


def test_min_family_raises_the_floor(tmp_path: Path):
    project = _project(tmp_path, {"m.py": "class OnePayload: pass\nclass TwoPayload: pass\n"})
    assert any(f.suffix == "Payload" for f in compute_conventions(project).naming)
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
    assert [f.suffix for f in compute_conventions(project, min_family=1).naming] == ["Alone"]


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
    assert "concentration" in payload["naming"][0]
    assert "surface_count" in payload["surfaces"][0]
    assert "optional" in payload["gates"][0]
    assert {"frozen_ratio", "tuple_ratio", "dominant_base"} <= set(payload["models"])
