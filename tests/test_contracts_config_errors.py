"""#801/#802: an unreadable or unsupported contracts config is a clear refusal.

Each case first proves import-linter itself refuses the fixture, then checks
that `run_contracts`, the CLI, MCP and brief all report it as a
`ContractsConfigError` naming the file, with process state restored.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from archy.contracts import ContractsConfigError, run_contracts
from tests.test_contracts import _purge_top, _write_fixture


@pytest.mark.parametrize(
    "case,filename,contents,error_type,reason",
    [
        (
            "malformed",
            ".importlinter",
            "not an INI file\n",
            "MissingSectionHeaderError",
            "File contains no section headers",
        ),
        (
            "unknown",
            ".importlinter",
            "[importlinter]\nroot_package = configcase\n\n"
            "[importlinter:contract:bad]\nname = bad\ntype = nosuchtype\n",
            "NoSuchContractType",
            "nosuchtype",
        ),
        (
            "no-section",
            "pyproject.toml",
            '[project]\nname = "example"\n',
            "FileNotFoundError",
            "Could not read any configuration",
        ),
        (
            "yaml",
            "archy.yaml",
            "layers:\n  app:\n    modules: [configcase.**]\n",
            "MissingSectionHeaderError",
            "File contains no section headers",
        ),
        (
            "toml",
            "pyproject.toml",
            "[tool.importlinter]\nroot_packages = [\n",
            "TOMLDecodeError",
            "Invalid value",
        ),
        (
            "dotted",
            ".importlinter",
            "[importlinter]\nroot_package = configcase.sub\n",
            "NotATopLevelModule",
            "use 'configcase'",
        ),
        (
            "overlap",
            ".importlinter",
            "[importlinter]\nroot_package = configcase\n\n"
            "[importlinter:contract:bad]\nname = overlap\ntype = independence\n"
            "modules =\n    configcase\n    configcase.sub\n",
            "ValueError",
            "Modules have shared descendants.",
        ),
    ],
)
def test_unreadable_contracts_keep_the_verdict_on_every_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    filename: str,
    contents: str,
    error_type: str,
    reason: str,
) -> None:
    """#801/#802: first prove the real dependency refuses this exact fixture."""
    import configparser

    from click.testing import CliRunner
    from grimp.exceptions import NotATopLevelModule
    from importlinter import configuration
    from importlinter.adapters.user_options import tomllib
    from importlinter.application.use_cases import (
        _register_contract_types,
        create_report,
        read_user_options,
    )
    from importlinter.domain.contract import NoSuchContractType

    from archy.cli import main
    from archy.mcp_runs import run_brief
    from archy.mcp_runs import run_contracts as mcp_contracts

    for name in list(sys.modules):
        if name == "configcase" or name.startswith("configcase."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    pkg = tmp_path / "configcase"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from configcase import b\n")
    (pkg / "b.py").write_text("")
    sub = tmp_path / "configcase" / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (tmp_path / "archy.yaml").write_text("layers:\n  app:\n    modules: [configcase.**]\n")
    config = tmp_path / filename
    config.write_text(contents)
    error_classes = {
        "MissingSectionHeaderError": configparser.MissingSectionHeaderError,
        "NoSuchContractType": NoSuchContractType,
        "FileNotFoundError": FileNotFoundError,
        "TOMLDecodeError": tomllib.TOMLDecodeError,
        "NotATopLevelModule": NotATopLevelModule,
        "ValueError": ValueError,
    }
    with monkeypatch.context() as raw:
        raw.chdir(tmp_path)
        raw.syspath_prepend(str(tmp_path))
        configuration.configure()
        with pytest.raises(error_classes[error_type]) as dependency_error:
            options = read_user_options(config_filename=filename)
            _register_contract_types(options)
            create_report(options, cache_dir=None)
    if case == "dotted":
        assert str(dependency_error.value) == ""
    else:
        assert reason in str(dependency_error.value)

    prior_cwd = Path.cwd()
    prior_path = sys.path[:]
    # Repeating the refusal must preserve the process state and the cause.
    for _ in range(2):
        with pytest.raises(ContractsConfigError) as caught:
            run_contracts(tmp_path, config_filename=config)
        message = str(caught.value)
        assert str(config) in message
        assert reason in message
        assert isinstance(caught.value.__cause__, error_classes[error_type])
        assert Path.cwd() == prior_cwd
        assert sys.path == prior_path
    if case == "dotted":
        assert "configcase.sub" in message
        assert "NotATopLevelModule" in message
    if case == "overlap":
        assert "not in the import graph" not in message
        assert ".." not in message

    runner = CliRunner()
    standalone = runner.invoke(main, ["contracts", str(tmp_path), "--config", str(config)])
    assert standalone.exit_code == 1, standalone.output
    assert isinstance(standalone.exception, SystemExit), standalone.exception
    assert str(config) in standalone.output
    assert reason in standalone.output
    assert "Traceback" not in standalone.output
    assert len(standalone.output.strip().splitlines()) == 1

    # Both passing and failing layer verdicts survive the contracts refusal.
    for violates in (False, True):
        (tmp_path / "archy.yaml").write_text(
            "layers:\n  a:\n    modules: [configcase.a]\n  b:\n    modules: [configcase.b]\n"
            + ("forbid:\n  - {from: a, to: b}\n" if violates else "")
        )
        # The explicit yaml config must still be unreadable to import-linter.
        plain = runner.invoke(main, ["check", str(tmp_path), "--format", "json"])
        result = runner.invoke(
            main,
            [
                "check",
                str(tmp_path),
                "--contracts",
                "--contracts-config",
                str(config),
                "--format",
                "json",
            ],
        )
        assert plain.exit_code == int(violates), plain.output
        assert result.exit_code == plain.exit_code, result.output
        payload = json.loads(result.stdout)
        assert payload["contracts"]["available"] is True
        assert payload["contracts"]["error"]
        assert payload["violations"] == json.loads(plain.stdout)["violations"]

    payload = mcp_contracts(tmp_path, config_filename=config).model_dump()
    assert payload["available"] is True
    assert payload["error"]
    # brief has no explicit contracts-config flag: use the canonical INI path.
    (tmp_path / ".importlinter").write_text(contents)
    brief = runner.invoke(main, ["brief", str(tmp_path), "--contracts", "--format", "json"])
    assert brief.exit_code == 0, brief.output
    payload = json.loads(brief.stdout)
    assert payload["conventions"] is not None
    assert payload["contracts"]["available"] is True
    assert payload["contracts"]["error"]
    payload = run_brief(tmp_path, top_n=5, with_contracts=True, include_tests=False).model_dump()
    assert payload["conventions"] is not None
    assert payload["contracts"]["available"] is True
    assert payload["contracts"]["error"]


@pytest.mark.parametrize(
    "stage", ["read_user_options", "_register_contract_types", "create_report"]
)
@pytest.mark.parametrize("error", [RuntimeError("bug"), KeyboardInterrupt()])
def test_contracts_unexpected_failures_restore_process_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, error: BaseException
) -> None:
    """An archy/dependency bug or interruption must still escape, with cleanup."""
    from importlinter.application import use_cases

    _purge_top(monkeypatch)
    _write_fixture(tmp_path, with_violation=False)

    def fail(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(use_cases, stage, fail)
    prior_cwd, prior_path = Path.cwd(), sys.path[:]
    with pytest.raises(type(error)) as caught:
        run_contracts(tmp_path)
    assert caught.value is error
    assert Path.cwd() == prior_cwd
    assert sys.path == prior_path


def test_derived_contracts_empty_grimp_error_names_its_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from grimp.exceptions import GrimpException
    from importlinter.application import use_cases
    from importlinter.application.user_options import UserOptions

    from archy.contracts import _drive_import_linter

    def fail(*args: object, **kwargs: object) -> None:
        raise GrimpException()

    monkeypatch.setattr(use_cases, "create_report", fail)
    with pytest.raises(
        ContractsConfigError, match=r"contracts derived from archy\.yaml: GrimpException"
    ) as caught:
        _drive_import_linter(user_options=UserOptions(session_options={}, contracts_options=[]))
    assert isinstance(caught.value.__cause__, GrimpException)
