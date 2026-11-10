"""The brief tool and the transitive contracts it reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from archy.mcp import (
    create_server,
)
from archy.mcp_payloads import ContractsPayload
from archy.mcp_runs import (
    run_brief,
)
from tests.mcp_helpers import _myapp_core_cli, _write_brief_project, _write_core_cli_forbid_config
from tests.mcp_helpers import call_tool as _call_tool


@pytest.mark.parametrize("relative", [True, False], ids=["relative", "absolute"])
def test_check_contracts_config_path_is_relative_to_project(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    relative: bool,
):
    _myapp_core_cli(tmp_path, api="from myapp.cli import runner\n")
    _write_core_cli_forbid_config(tmp_path)
    config = tmp_path / "contracts.ini"
    config.write_text(
        "[importlinter]\n"
        "root_package = myapp\n"
        "[importlinter:contract:core-must-not-reach-cli]\n"
        "name = core must not reach cli\n"
        "type = forbidden\n"
        "source_modules = myapp.core.api\n"
        "forbidden_modules = myapp.cli.runner\n"
    )
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    assert config.is_file()
    assert not Path(config.name).exists()

    _content, wire = _call_tool(
        create_server(),
        "archy_check",
        {
            "path": str(tmp_path),
            "contracts": True,
            "contracts_config_path": config.name if relative else str(config),
        },
    )

    assert isinstance(wire, dict)
    result = wire["result"]
    assert result["transitive_checked"] is True
    contracts = result["contracts"]
    assert contracts["error"] is None
    assert contracts["all_kept"] is False
    assert contracts["broken"] == 1
    assert contracts["contracts"][0]["name"] == "core must not reach cli"


def test_check_contracts_reports_an_unfalsifiable_contract_as_unverified(tmp_path: Path):
    """The surface agents actually call. A contract whose pattern matches no
    module is `kept` by import-linter, so an agent reading `all_kept` concludes
    it is protected; `verified` is the field that says otherwise (#435)."""
    pkg = tmp_path / "top"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "api.py").write_text("VALUE = 1\n")
    (pkg / "store.py").write_text("from top import api\n")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        '  api:\n    modules: ["top.api.**"]\n'
        '  store:\n    modules: ["top.*.handlers"]\n'
        "forbid:\n  - {from: store, to: api}\n"
    )

    server = create_server()
    _c, wire = _call_tool(server, "archy_check", {"path": str(tmp_path), "contracts": True})

    assert isinstance(wire, dict)
    contracts = wire["result"]["contracts"]
    assert contracts["all_kept"] is True
    assert contracts["verified"] is False
    assert contracts["unverifiable"] == 1
    assert contracts["contracts"][0]["matched_nothing"] is True
    assert contracts["contracts"][0]["unmatched_expressions"] == ["top.*.handlers"]
    # #457: the run did not evaluate the rule, so it must not say it did. Same
    # rule as the CLI's `_transitive_checked`, and a verdict without a reason
    # is indistinguishable from a bug in archy.
    assert wire["result"]["transitive_checked"] is False
    assert "could not have failed" in wire["result"]["transitive_unverified_reason"]


def test_check_contracts_flag_nests_contract_results(tmp_path: Path):
    # #268: archy_check(contracts=True) folds the old archy_contracts tool,
    # nesting the transitive import-linter result under CheckPayload.contracts.
    # contracts=False (the default) leaves the field None, so a routine check
    # stays a pure direct-edge layer-rule result.
    _myapp_core_cli(tmp_path)
    _write_core_cli_forbid_config(tmp_path)

    server = create_server()
    path = str(tmp_path)

    # CheckPayload | CheckErrorPayload is a union return, so FastMCP wraps it
    # under a top-level `result` key (see the module docstring's wrapping rules).
    _c, plain = _call_tool(server, "archy_check", {"path": path})
    assert isinstance(plain, dict)
    assert plain["result"]["passed"] is True
    assert plain["result"]["contracts"] is None

    _c2, withc = _call_tool(server, "archy_check", {"path": path, "contracts": True})
    assert isinstance(withc, dict)
    result = withc["result"]
    assert result["passed"] is True
    # import-linter is a dev dependency (see tests/test_contracts.py), so the
    # fold runs the real contract derived from the archy.yaml `forbid` rule and
    # keeps it clean here.
    assert isinstance(result["contracts"], dict)
    assert result["contracts"]["available"] is True
    assert result["contracts"]["all_kept"] is True
    # `all_kept` is also true of a contract that matched no module, so the
    # payload carries `verified` separately (#435). Here the contract really
    # was evaluated, so both are true and `unverifiable` is zero.
    assert result["contracts"]["verified"] is True
    assert result["contracts"]["unverifiable"] == 0
    # The other side of #457: a contract that really was evaluated still
    # reports the transitive check as done, so the flag is not just False.
    assert result["transitive_checked"] is True
    assert result["transitive_unverified_reason"] is None


def test_brief_bounds_ranked_sections_and_reports_what_it_dropped(tmp_path: Path):
    project = _write_brief_project(tmp_path, with_config=False)
    full = run_brief(project, top_n=50, with_contracts=False, include_tests=False)
    # The fixture must exercise the truncation before the outcome means anything.
    assert full.conventions.totals["naming"] >= 2

    bounded = run_brief(project, top_n=1, with_contracts=False, include_tests=False)
    assert len(bounded.conventions.naming) == 1
    assert bounded.conventions.top_n == 1
    assert bounded.conventions.totals == full.conventions.totals
    # Survives the wire form FastMCP sends.
    assert (
        bounded.model_dump()["conventions"]["totals"]["naming"] == full.conventions.totals["naming"]
    )


def test_brief_without_a_config_has_no_coverage_and_no_reason(tmp_path: Path):
    project = _write_brief_project(tmp_path, with_config=False)
    payload = run_brief(project, top_n=8, with_contracts=False, include_tests=False)
    assert payload.coverage is None
    assert payload.transitive_checked is False
    assert payload.transitive_unverified_reason is None
    assert payload.model_dump()["coverage_error"] is None


def test_brief_reports_invalid_coverage_in_model_dump(overlapping_layers_project: Path):
    payload = run_brief(
        overlapping_layers_project, top_n=8, with_contracts=False, include_tests=False
    ).model_dump()
    assert payload["coverage"] is None
    assert "multiple layers" in payload["coverage_error"]
    assert payload["conventions"] is not None


def test_brief_says_why_an_unrun_forbid_rule_is_unverified(tmp_path: Path):
    project = _write_brief_project(tmp_path, with_config=True)
    payload = run_brief(project, top_n=8, with_contracts=False, include_tests=False)
    assert payload.coverage is not None
    assert payload.transitive_checked is False
    dumped = payload.model_dump()
    assert dumped["coverage_error"] is None
    assert "1 forbid rule declared" in dumped["transitive_unverified_reason"]
    assert "archy_brief(contracts=True)" in dumped["transitive_unverified_reason"]


@pytest.mark.parametrize(
    ("contracts", "checked", "reason_has"),
    [
        (ContractsPayload(available=True, error="boom"), False, "boom"),
        (ContractsPayload(available=True, verified=True, unverifiable=0), True, None),
        (ContractsPayload(available=True, verified=False, unverifiable=2), False, "2 contract(s)"),
    ],
)
def test_brief_reads_the_contracts_verdict(
    tmp_path: Path, monkeypatch, contracts: ContractsPayload, checked: bool, reason_has
):
    monkeypatch.setattr("archy.mcp_runs_check.run_contracts", lambda *a, **k: contracts)
    project = _write_brief_project(tmp_path, with_config=True)
    payload = run_brief(project, top_n=8, with_contracts=True, include_tests=False)
    assert payload.transitive_checked is checked
    if reason_has is None:
        assert payload.transitive_unverified_reason is None
    else:
        assert reason_has in (payload.transitive_unverified_reason or "")


def test_brief_payload_has_the_cli_json_field_names(tmp_path: Path):
    # Derived independently: the CLI hand-rolls its dict, the MCP side is a model.
    from click.testing import CliRunner

    from archy.cli import main

    project = _write_brief_project(tmp_path, with_config=True)
    cli = json.loads(CliRunner().invoke(main, ["brief", str(project), "--format", "json"]).output)
    _c, wire = _call_tool(create_server(), "archy_brief", {"path": str(project)})
    assert wire is not None
    assert set(wire) == set(cli)
    assert set(wire["conventions"]) == set(cli["conventions"])
    assert wire["transitive_checked"] == cli["transitive_checked"]
    assert (wire["transitive_unverified_reason"] is None) == (
        cli["transitive_unverified_reason"] is None
    )


def test_brief_rejects_a_nonsense_top_n(tmp_path: Path):
    from archy.mcp_compat import ToolError

    project = _write_brief_project(tmp_path, with_config=False)
    with pytest.raises(ToolError):
        _call_tool(create_server(), "archy_brief", {"path": str(project), "top_n": 0})
