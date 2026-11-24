"""A contracts config that defines no contract verifies nothing (#775).

Present-but-empty is the case a missing config does not cover: an
`.importlinter` with no contract sections, or an `archy.yaml` with layers and no
`forbid:` rules. Every surface used to report it as a verified pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.contracts import run_contracts
from archy.mcp import create_server
from archy.mcp_runs import run_brief
from tests.mcp_helpers import call_tool as _call_tool

_LAYERS_NO_FORBID = 'layers:\n  hi:\n    modules: ["pkg.a"]\n  lo:\n    modules: ["pkg.b"]\n'


def _package(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import pkg.b\n")
    (pkg / "b.py").write_text("X = 1\n")
    return tmp_path


def _empty_importlinter(tmp_path: Path) -> Path:
    project = _package(tmp_path)
    (project / ".importlinter").write_text("[importlinter]\nroot_package = pkg\n")
    return project


def _forbidless_yaml(tmp_path: Path) -> Path:
    project = _package(tmp_path)
    (project / "archy.yaml").write_text(_LAYERS_NO_FORBID)
    return project


@pytest.mark.filterwarnings("ignore:deriving transitive contracts")
@pytest.mark.parametrize(
    ("make", "config_name"),
    [(_empty_importlinter, ".importlinter"), (_forbidless_yaml, "archy.yaml")],
)
def test_zero_contracts_is_not_verified(tmp_path: Path, make, config_name: str):
    project = make(tmp_path)
    result = run_contracts(project)
    # The zero branch is what is under test, not some other way to fail.
    assert result.contracts == ()
    assert result.broken == 0
    assert result.unverifiable == 0

    dumped = result.model_dump()
    assert dumped["verified"] is False
    assert config_name in dumped["unverified_reason"]

    cli = CliRunner().invoke(main, ["contracts", str(project), "--format", "json"])
    assert cli.exit_code == 1, cli.output
    payload = json.loads(cli.stdout)
    assert payload["verified"] is False
    assert "no contracts defined" in payload["unverified_reason"]

    text = CliRunner().invoke(main, ["contracts", str(project)])
    assert text.exit_code == 1, text.output
    assert "not verified: no contracts defined" in text.stdout


@pytest.mark.filterwarnings("ignore:deriving transitive contracts")
def test_check_and_brief_do_not_claim_a_transitive_check_over_zero_contracts(tmp_path: Path):
    project = _forbidless_yaml(tmp_path)
    for cmd in ("check", "brief"):
        cli = CliRunner().invoke(main, [cmd, str(project), "--contracts", "--format", "json"])
        payload = json.loads(cli.stdout)
        assert payload["contracts"]["verified"] is False, cmd
        assert payload["transitive_checked"] is False, cmd
        assert "no contracts defined" in payload["transitive_unverified_reason"], cmd

    brief = run_brief(project, top_n=8, with_contracts=True, include_tests=False).model_dump()
    assert brief["contracts"]["verified"] is False
    assert brief["transitive_checked"] is False
    assert "no contracts defined" in brief["transitive_unverified_reason"]

    _c, wire = _call_tool(create_server(), "archy_check", {"path": str(project), "contracts": True})
    assert isinstance(wire, dict)
    assert wire["result"]["contracts"]["verified"] is False
    assert wire["result"]["transitive_checked"] is False
    assert "no contracts defined" in wire["result"]["transitive_unverified_reason"]


@pytest.mark.filterwarnings("ignore:deriving transitive contracts")
def test_one_kept_contract_still_verifies(tmp_path: Path):
    """The comparison case: the fix must not flip every result."""
    project = _package(tmp_path)
    (project / "archy.yaml").write_text(_LAYERS_NO_FORBID + "forbid:\n  - {from: lo, to: hi}\n")
    result = run_contracts(project)
    assert len(result.contracts) == 1
    assert result.model_dump()["verified"] is True
    assert result.model_dump()["unverified_reason"] is None

    cli = CliRunner().invoke(main, ["contracts", str(project)])
    assert cli.exit_code == 0, cli.output
    check = json.loads(
        CliRunner().invoke(main, ["check", str(project), "--contracts", "--format", "json"]).stdout
    )
    assert check["transitive_checked"] is True


@pytest.mark.parametrize("with_layers", [False, True])
@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_brief_zero_contracts_reason_does_not_require_layer_config(
    tmp_path: Path, with_layers: bool, surface: str
):
    project = _empty_importlinter(tmp_path)
    if with_layers:
        (project / "archy.yaml").write_text(_LAYERS_NO_FORBID)

    if surface == "cli":
        cli = CliRunner().invoke(main, ["brief", str(project), "--contracts", "--format", "json"])
        assert cli.exit_code == 0, cli.output
        payload = json.loads(cli.stdout)
    else:
        _c, payload = _call_tool(
            create_server(), "archy_brief", {"path": str(project), "contracts": True}
        )
        assert isinstance(payload, dict)

    assert payload["contracts"]["contracts"] == []
    assert payload["contracts"]["verified"] is False
    assert payload["transitive_checked"] is False
    assert payload["transitive_unverified_reason"] is not None
    assert "no contracts defined" in payload["transitive_unverified_reason"]
    assert ".importlinter" in payload["transitive_unverified_reason"]


@pytest.mark.filterwarnings("ignore:deriving transitive contracts")
def test_zero_contracts_remedy_names_the_config_that_was_read(tmp_path: Path):
    # `.importlinter` wins over archy.yaml, so beside a forbid rule the advice
    # must not be "add forbid rules": that rule is already there and ignored.
    project = _empty_importlinter(tmp_path)
    (project / "archy.yaml").write_text(_LAYERS_NO_FORBID + "forbid:\n  - from: lo\n    to: hi\n")
    shadowed = run_contracts(project).unverified_reason
    assert shadowed is not None
    assert "import-linter contract section" in shadowed
    assert "forbid" not in shadowed

    (tmp_path / "y").mkdir()
    derived = run_contracts(_forbidless_yaml(tmp_path / "y")).unverified_reason
    assert derived is not None
    assert "add `forbid:` rules" in derived
