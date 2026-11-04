"""`diff --strict` fails when archy.yaml drops or narrows a recorded rule (#585).

The numbers in the reasons are the rule names from the fixture, worked out
here, not copied back out of a helper the test also calls.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from archy.cli import main
from archy.diff import DiffReport
from archy.mcp_payloads import DiffErrorPayload
from archy.mcp_runs import run_diff

_ISSUE_YAML = """\
layers:
  domain:
    modules: [myapp.domain.**]
  infra:
    modules: [myapp.infra.**]
forbid:
  - {from: domain, to: infra}
"""

_IMPORT = "from myapp.infra.db import connect\n"


def _project(tmp_path: Path, yaml_text: str, *, service: str = "") -> Path:
    root = tmp_path / "app"
    for rel in (
        "myapp/__init__.py",
        "myapp/domain/__init__.py",
        "myapp/domain/service.py",
        "myapp/infra/__init__.py",
        "myapp/infra/db.py",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(service if rel.endswith("service.py") else "")
    (root / "archy.yaml").write_text(yaml_text)
    return root


def _snapshot(root: Path) -> None:
    result = CliRunner().invoke(
        main,
        ["snapshot", str(root), "--findings-only", "--out", str(root / "base.json")],
    )
    assert result.exit_code == 0, result.output


def _surfaces(root: Path):
    """CLI text, CLI JSON `strict`, and MCP `archy_diff` `strict` via model_dump."""
    text = CliRunner().invoke(
        main,
        ["diff", str(root), "--strict", "--baseline", str(root / "base.json")],
    )
    raw = CliRunner().invoke(
        main,
        [
            "diff",
            str(root),
            "--strict",
            "--baseline",
            str(root / "base.json"),
            "--format",
            "json",
        ],
    )
    assert raw.exit_code == text.exit_code, raw.output
    cli = json.loads(raw.output)["strict"]
    mcp_result = run_diff(root, baseline="base.json", strict=True)
    assert isinstance(mcp_result, DiffReport)
    mcp = mcp_result.model_dump()["strict"]
    assert mcp["passed"] is cli["passed"]
    return text, cli, mcp


def _assert_failed(root: Path, phrase: str) -> None:
    text, cli, mcp = _surfaces(root)
    assert text.exit_code == 1, text.output
    assert phrase in text.output
    assert cli["passed"] is False
    assert phrase in cli["reason"]
    assert mcp["passed"] is False
    assert phrase in mcp["reason"]


def _assert_passed(root: Path) -> None:
    text, cli, mcp = _surfaces(root)
    assert text.exit_code == 0, text.output
    assert "# strict: PASS" in text.output
    assert cli["passed"] is True
    assert mcp["passed"] is True
    for reason in (cli["reason"], mcp["reason"]):
        assert "removed since" not in reason
        assert "narrowed since" not in reason
        assert "added since" not in reason


def test_findings_only_records_a_sorted_rules_fingerprint(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)

    payload = json.loads((root / "base.json").read_text())

    assert payload["rules"] == {
        "exclude": [],
        "forbid": [["domain", "infra"]],
        "layers": [
            ["domain", ["myapp.domain.**"]],
            ["infra", ["myapp.infra.**"]],
        ],
        "required": [],
        "roots": [],
    }


def test_full_snapshot_omits_rules_and_a_scan_without_config_omits_them(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    full = CliRunner().invoke(main, ["snapshot", str(root), "--out", str(root / "full.json")])
    assert full.exit_code == 0, full.output
    assert "rules" not in json.loads((root / "full.json").read_text())

    bare = tmp_path / "bare"
    pkg = bare / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("")
    _snapshot(bare)
    assert "rules" not in json.loads((bare / "base.json").read_text())


def test_rule_intact_and_a_new_import_still_fails(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    (root / "myapp" / "domain" / "service.py").write_text(_IMPORT)

    _assert_failed(root, "new layer violation (domain -> infra)")
    text, cli, mcp = _surfaces(root)
    for reason in (text.output, cli["reason"], mcp["reason"]):
        assert "forbid domain -> infra removed" not in reason


def test_deleting_forbid_fails_and_names_the_rule(tmp_path: Path):
    """The clean-baseline hole: the import is real, the rule is gone, so no
    violation is computed and the old coverage check has nothing to hold."""
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    (root / "myapp" / "domain" / "service.py").write_text(_IMPORT)
    (root / "archy.yaml").write_text(
        _ISSUE_YAML.replace("forbid:\n  - {from: domain, to: infra}\n", "forbid: []\n")
    )

    _assert_failed(root, "forbid domain -> infra removed since")


def test_exclude_domain_fails_and_names_exclude(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    (root / "myapp" / "domain" / "service.py").write_text(_IMPORT)
    (root / "archy.yaml").write_text(_ISSUE_YAML + "exclude: [domain]\n")

    _assert_failed(root, "exclude domain added since")


def test_exclude_still_fails_when_the_baseline_recorded_the_violation(tmp_path: Path):
    """Excluding the package drops both modules out of the scan, so the
    ungoverned-violation check never sees them. The fingerprint still does."""
    root = _project(tmp_path, _ISSUE_YAML, service=_IMPORT)
    _snapshot(root)
    assert json.loads((root / "base.json").read_text())["violations"]
    (root / "archy.yaml").write_text(_ISSUE_YAML + "exclude: [domain]\n")

    _assert_failed(root, "exclude domain added since")


def test_a_recorded_violation_the_forbid_no_longer_covers_still_refuses(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML, service=_IMPORT)
    _snapshot(root)
    (root / "archy.yaml").write_text(
        _ISSUE_YAML.replace("forbid:\n  - {from: domain, to: infra}\n", "forbid: []\n")
    )

    _assert_failed(root, "no longer cover")


def test_deleting_the_config_file_names_the_removed_forbid(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    (root / "archy.yaml").unlink()

    _assert_failed(root, "forbid domain -> infra removed since")
    _assert_failed(root, "layer domain removed since")


def test_adding_a_forbid_does_not_fail(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    (root / "archy.yaml").write_text(
        _ISSUE_YAML + "  - {from: infra, to: domain}\n",
    )

    _assert_passed(root)


def test_widening_a_layer_pattern_does_not_fail(tmp_path: Path):
    yaml = _ISSUE_YAML.replace("modules: [myapp.domain.**]", "modules: [myapp.domain.service]")
    root = _project(tmp_path, yaml)
    _snapshot(root)
    (root / "archy.yaml").write_text(
        yaml.replace("modules: [myapp.domain.service]", "modules: [myapp.domain.**]")
    )

    _assert_passed(root)


def test_narrowing_a_layer_pattern_fails_and_names_it(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    (root / "myapp" / "domain" / "service.py").write_text(_IMPORT)
    (root / "archy.yaml").write_text(
        _ISSUE_YAML.replace("modules: [myapp.domain.**]", "modules: [myapp.domain.gone.**]")
    )

    _assert_failed(root, "layer domain pattern myapp.domain.** narrowed since")


def test_removing_an_exclude_does_not_fail(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML + "exclude: [vendor]\n")
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML)

    _assert_passed(root)


def test_removing_a_required_contract_fails_and_adding_one_does_not(tmp_path: Path):
    required = (
        "required:\n  - {source: myapp.domain.**, must_reach: myapp.infra.db, reason: bootstrap}\n"
    )
    root = _project(tmp_path, _ISSUE_YAML + required)
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML)
    _assert_failed(root, "required myapp.domain.** must reach myapp.infra.db removed since")

    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML + required)
    _assert_passed(root)


def test_narrowing_an_entrypoints_contract_fails_and_widening_it_does_not(tmp_path: Path):
    wide = (
        "required:\n"
        "  - source: '@entrypoints'\n"
        "    must_reach: myapp.infra.db\n"
        "    within: myapp.domain.**\n"
        "    reason: bootstrap\n"
    )
    narrow = wide.replace("within: myapp.domain.**", "within: myapp.domain.gone.**")
    root = _project(tmp_path, _ISSUE_YAML + wide)
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML + narrow)
    _assert_failed(
        root,
        "required @entrypoints within myapp.domain.** must reach myapp.infra.db removed since",
    )

    held = wide.replace("within: myapp.domain.**", "within: myapp.domain.service")
    root = _project(tmp_path, _ISSUE_YAML + held)
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML + wide)
    _assert_passed(root)


def test_an_easier_must_reach_fails_and_a_harder_one_does_not(tmp_path: Path):
    exact = (
        "required:\n  - {source: myapp.domain.**, must_reach: myapp.infra.db, reason: bootstrap}\n"
    )
    easier = exact.replace("must_reach: myapp.infra.db", "must_reach: myapp.infra.**")
    root = _project(tmp_path, _ISSUE_YAML + exact)
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML + easier)
    _assert_failed(root, "required myapp.domain.** must reach myapp.infra.db removed since")

    root = _project(tmp_path, _ISSUE_YAML + easier)
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML + exact)
    _assert_passed(root)


def test_removing_a_root_fails_and_adding_one_does_not(tmp_path: Path):
    rooted = _ISSUE_YAML + "roots:\n  - ns\n"
    root = _project(tmp_path, rooted)
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML)
    _assert_failed(root, "root ns removed since")

    root = _project(tmp_path, rooted)
    _snapshot(root)
    (root / "archy.yaml").write_text(rooted + "  - extra\n")
    _assert_passed(root)


def test_old_baseline_without_rules_does_not_fail_just_for_that(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    _strip_rules(root)
    (root / "myapp" / "domain" / "service.py").write_text(_IMPORT)
    (root / "archy.yaml").write_text(
        _ISSUE_YAML.replace("forbid:\n  - {from: domain, to: infra}\n", "forbid: []\n")
    )

    text, cli, mcp = _surfaces(root)

    assert text.exit_code == 0, text.output
    assert cli["passed"] is True
    assert mcp["passed"] is True
    assert cli["refused"] is None
    assert mcp["refused"] is None
    for reason in (text.output, cli["reason"], mcp["reason"]):
        assert "no rules fingerprint" in reason
        assert "forbid domain -> infra removed" not in reason
        assert "--findings-only" in reason


def test_old_baseline_without_rules_still_fails_a_new_cycle(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    _strip_rules(root)
    (root / "myapp" / "domain" / "other.py").write_text("import myapp.domain.service\n")
    (root / "myapp" / "domain" / "service.py").write_text("import myapp.domain.other\n")

    text, cli, mcp = _surfaces(root)

    assert text.exit_code == 1, text.output
    assert cli["passed"] is False
    assert mcp["passed"] is False
    assert cli["refused"] is None
    assert mcp["refused"] is None
    for reason in (text.output, cli["reason"], mcp["reason"]):
        assert "new import cycle" in reason
        assert "no rules fingerprint" in reason


def test_unchanged_old_baseline_passes_with_a_regenerate_hint(tmp_path: Path):
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    _strip_rules(root)

    text, cli, mcp = _surfaces(root)

    assert text.exit_code == 0, text.output
    assert cli["passed"] is True
    assert mcp["passed"] is True
    assert "no rules fingerprint" in cli["reason"]
    assert "no rules fingerprint" in mcp["reason"]


def _strip_rules(root: Path) -> None:
    path = root / "base.json"
    payload = json.loads(path.read_text())
    assert "rules" in payload
    del payload["rules"]
    path.write_text(json.dumps(payload) + "\n")


def test_mcp_dump_is_not_an_error_payload(tmp_path: Path):
    """`run_diff` returns the report; a mistaken error payload has no strict."""
    root = _project(tmp_path, _ISSUE_YAML)
    _snapshot(root)
    (root / "archy.yaml").write_text(_ISSUE_YAML + "exclude: [domain]\n")

    result = run_diff(root, baseline="base.json", strict=True)

    assert not isinstance(result, DiffErrorPayload)
    assert isinstance(result, DiffReport)
    dumped = result.model_dump()["strict"]
    assert dumped["passed"] is False
    assert "exclude domain added since" in dumped["reason"]
