"""#801/#802: an unreadable or unsupported contracts config is a clear refusal.

Each case first proves import-linter itself refuses the fixture, then checks
that `run_contracts`, the CLI, MCP and brief all report it as a
`ContractsConfigError` naming the file, with process state restored.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NamedTuple

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.contracts import ContractsConfigError, run_contracts
from tests.test_contracts import _purge_top, _write_fixture


class _Case(NamedTuple):
    filename: str
    contents: str
    error_type: str
    reason: str


_CASES = {
    "malformed": _Case(
        ".importlinter",
        "not an INI file\n",
        "MissingSectionHeaderError",
        "File contains no section headers",
    ),
    "unknown": _Case(
        ".importlinter",
        "[importlinter]\nroot_package = configcase\n\n"
        "[importlinter:contract:bad]\nname = bad\ntype = nosuchtype\n",
        "NoSuchContractType",
        "nosuchtype",
    ),
    "no-section": _Case(
        "pyproject.toml",
        '[project]\nname = "example"\n',
        "FileNotFoundError",
        "Could not read any configuration",
    ),
    "yaml": _Case(
        "archy.yaml",
        "layers:\n  app:\n    modules: [configcase.**]\n",
        "MissingSectionHeaderError",
        "File contains no section headers",
    ),
    "toml": _Case(
        "pyproject.toml",
        "[tool.importlinter]\nroot_packages = [\n",
        "TOMLDecodeError",
        "Invalid value",
    ),
    "dotted": _Case(
        ".importlinter",
        "[importlinter]\nroot_package = configcase.sub\n",
        "NotATopLevelModule",
        "use 'configcase'",
    ),
    "overlap": _Case(
        ".importlinter",
        "[importlinter]\nroot_package = configcase\n\n"
        "[importlinter:contract:bad]\nname = overlap\ntype = independence\n"
        "modules =\n    configcase\n    configcase.sub\n",
        "ValueError",
        "Modules have shared descendants.",
    ),
}


class _Project(NamedTuple):
    root: Path
    name: str
    case: _Case
    config: Path


def _error_class(error_type: str) -> type[BaseException]:
    import configparser

    from grimp.exceptions import NotATopLevelModule
    from importlinter.adapters.user_options import tomllib
    from importlinter.domain.contract import NoSuchContractType

    return {
        "MissingSectionHeaderError": configparser.MissingSectionHeaderError,
        "NoSuchContractType": NoSuchContractType,
        "FileNotFoundError": FileNotFoundError,
        "TOMLDecodeError": tomllib.TOMLDecodeError,
        "NotATopLevelModule": NotATopLevelModule,
        "ValueError": ValueError,
    }[error_type]


@pytest.fixture(params=sorted(_CASES))
def project(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> _Project:
    """A `configcase` package whose contracts config import-linter refuses."""
    for name in list(sys.modules):
        if name == "configcase" or name.startswith("configcase."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    sub = tmp_path / "configcase" / "sub"
    sub.mkdir(parents=True)
    (sub / "__init__.py").write_text("")
    (sub.parent / "__init__.py").write_text("")
    (sub.parent / "a.py").write_text("from configcase import b\n")
    (sub.parent / "b.py").write_text("")
    (tmp_path / "archy.yaml").write_text("layers:\n  app:\n    modules: [configcase.**]\n")
    case = _CASES[request.param]
    config = tmp_path / case.filename
    config.write_text(case.contents)
    return _Project(tmp_path, request.param, case, config)


def test_import_linter_itself_refuses_the_fixture(
    project: _Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#801/#802: first prove the real dependency refuses this exact fixture."""
    from importlinter import configuration
    from importlinter.application.use_cases import (
        _register_contract_types,
        create_report,
        read_user_options,
    )

    with monkeypatch.context() as raw:
        raw.chdir(project.root)
        raw.syspath_prepend(str(project.root))
        configuration.configure()
        with pytest.raises(_error_class(project.case.error_type)) as dependency_error:
            options = read_user_options(config_filename=project.case.filename)
            _register_contract_types(options)
            create_report(options, cache_dir=None)
    if project.name == "dotted":
        assert str(dependency_error.value) == ""
    else:
        assert project.case.reason in str(dependency_error.value)


def test_run_contracts_refusal_names_the_file_and_restores_state(project: _Project) -> None:
    prior_cwd = Path.cwd()
    prior_path = sys.path[:]
    # Repeating the refusal must preserve the process state and the cause.
    for _ in range(2):
        with pytest.raises(ContractsConfigError) as caught:
            run_contracts(project.root, config_filename=project.config)
        message = str(caught.value)
        assert str(project.config) in message
        assert project.case.reason in message
        assert isinstance(caught.value.__cause__, _error_class(project.case.error_type))
        assert (Path.cwd(), sys.path) == (prior_cwd, prior_path)
    if project.name == "dotted":
        assert "configcase.sub" in message
        assert "NotATopLevelModule" in message
    if project.name == "overlap":
        assert "not in the import graph" not in message
        assert ".." not in message


def test_cli_contracts_refusal_is_one_line(project: _Project) -> None:
    argv = ["contracts", str(project.root), "--config", str(project.config)]
    standalone = CliRunner().invoke(main, argv)
    assert standalone.exit_code == 1, standalone.output
    assert isinstance(standalone.exception, SystemExit), standalone.exception
    assert str(project.config) in standalone.output
    assert project.case.reason in standalone.output
    assert "Traceback" not in standalone.output
    assert len(standalone.output.strip().splitlines()) == 1


@pytest.mark.parametrize("violates", [False, True])
def test_check_keeps_the_layer_verdict(project: _Project, violates: bool) -> None:
    """Both passing and failing layer verdicts survive the contracts refusal."""
    (project.root / "archy.yaml").write_text(
        "layers:\n  a:\n    modules: [configcase.a]\n  b:\n    modules: [configcase.b]\n"
        + ("forbid:\n  - {from: a, to: b}\n" if violates else "")
    )
    # The explicit yaml config must still be unreadable to import-linter.
    check = ["check", str(project.root), "--format", "json"]
    runner = CliRunner()
    plain = runner.invoke(main, check)
    result = runner.invoke(main, [*check, "--contracts", "--contracts-config", str(project.config)])
    assert plain.exit_code == int(violates), plain.output
    assert result.exit_code == plain.exit_code, result.output
    payload = json.loads(result.stdout)
    assert payload["contracts"]["available"] is True
    assert payload["contracts"]["error"]
    assert payload["violations"] == json.loads(plain.stdout)["violations"]


def test_mcp_and_brief_report_the_contracts_error(project: _Project) -> None:
    from archy.mcp_runs import run_brief
    from archy.mcp_runs import run_contracts as mcp_contracts

    contracts = mcp_contracts(project.root, config_filename=project.config).model_dump()
    # brief has no explicit contracts-config flag: use the canonical INI path.
    (project.root / ".importlinter").write_text(project.case.contents)
    brief = CliRunner().invoke(
        main, ["brief", str(project.root), "--contracts", "--format", "json"]
    )
    assert brief.exit_code == 0, brief.output
    cli_brief = json.loads(brief.stdout)
    mcp_brief = run_brief(
        project.root, top_n=5, with_contracts=True, include_tests=False
    ).model_dump()
    for payload in (cli_brief, mcp_brief):
        assert payload["conventions"] is not None
    for section in (contracts, cli_brief["contracts"], mcp_brief["contracts"]):
        assert section["available"] is True
        assert section["error"]


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
