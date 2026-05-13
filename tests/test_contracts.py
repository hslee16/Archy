"""Integration test for the import-linter wrap.

Drives `run_contracts` against a minimal fixture project written to a tmp dir.
The wrap depends on a non-public entry point in import-linter
(`_register_contract_types`), so this test is the canary that catches breakage
on import-linter version upgrades.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import cast

import pytest

from archy.contracts import (
    ContractsConfigError,
    ContractsNotAvailable,
    ContractsResult,
    run_contracts,
)


def _write_fixture(root: Path, *, with_violation: bool) -> None:
    """Lay out a 3-module package: top.A imports top.B; if with_violation,
    top.B also imports top.A (closing a forbidden cycle for the contract)."""
    pkg = root / "top"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from top import b  # noqa: F401\n")
    if with_violation:
        (pkg / "b.py").write_text("from top import a  # noqa: F401\n")
    else:
        (pkg / "b.py").write_text("")
    (root / ".importlinter").write_text(
        textwrap.dedent(
            """
            [importlinter]
            root_package = top
            include_external_packages = False

            [importlinter:contract:b-must-not-reach-a]
            name = top.b must not reach top.a
            type = forbidden
            source_modules =
                top.b
            forbidden_modules =
                top.a
            """
        ).strip()
        + "\n"
    )


def _purge_top(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each fixture writes its own `top` package; clear any cached imports
    # so import-linter's grimp builder sees the fresh tree.
    for name in list(sys.modules):
        if name == "top" or name.startswith("top."):
            monkeypatch.delitem(sys.modules, name, raising=False)


def test_run_contracts_clean_project_all_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _purge_top(monkeypatch)
    _write_fixture(tmp_path, with_violation=False)
    result = run_contracts(tmp_path)
    assert isinstance(result, ContractsResult)
    assert result.all_kept
    assert result.broken == 0
    assert result.kept == 1
    contract = result.contracts[0]
    assert contract.kept
    assert contract.contract_type == "ForbiddenContract"


def test_run_contracts_violation_surfaces_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _purge_top(monkeypatch)
    _write_fixture(tmp_path, with_violation=True)
    result = run_contracts(tmp_path)
    assert not result.all_kept
    assert result.broken == 1
    contract = result.contracts[0]
    assert not contract.kept
    chains = cast(list[dict[str, object]], contract.metadata.get("invalid_chains"))
    assert chains, "expected invalid_chains in violation metadata"
    chain = chains[0]
    # The shape import-linter exposes; surface it through the wrap unchanged.
    assert chain["downstream_module"] == "top.b"
    assert chain["upstream_module"] == "top.a"


def test_run_contracts_missing_config_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _purge_top(monkeypatch)
    # No .importlinter present at the project root: verify the wrap raises
    # the typed error rather than silently passing or surfacing a misleading
    # FileNotFoundError from import-linter's reader.
    with pytest.raises(ContractsConfigError):
        run_contracts(tmp_path)


def test_contracts_not_available_when_module_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If `importlinter` import fails, run_contracts should raise the typed
    error so callers (CLI / MCP) can render an actionable message."""
    _write_fixture(tmp_path, with_violation=False)
    monkeypatch.setitem(sys.modules, "importlinter", None)
    with pytest.raises(ContractsNotAvailable):
        run_contracts(tmp_path)


# --- archy.yaml fallback ------------------------------------------------------


def _write_yaml_fixture(root: Path, *, with_violation: bool) -> None:
    """Same 3-module package as the .importlinter fixture, but configure
    contracts via archy.yaml's layer rules instead. parser.a may import
    parser.b (same layer); a violation means cli imports parser, which
    archy.yaml forbids."""
    pkg = root / "top"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "parser.py").write_text("")
    if with_violation:
        # cli reaches into parser - forbidden by archy.yaml below.
        (pkg / "cli.py").write_text("from top import parser  # noqa: F401\n")
    else:
        (pkg / "cli.py").write_text("")
    (root / "archy.yaml").write_text(
        textwrap.dedent(
            """
            layers:
              parser:
                modules:
                  - "top.parser"
              cli:
                modules:
                  - "top.cli"
            forbid:
              - {from: cli, to: parser}
            """
        ).strip()
        + "\n"
    )


def test_run_contracts_falls_back_to_archy_yaml_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _purge_top(monkeypatch)
    _write_yaml_fixture(tmp_path, with_violation=False)
    with pytest.warns(UserWarning, match="best-effort fallback"):
        result = run_contracts(tmp_path)
    assert result.all_kept
    assert result.kept == 1
    assert result.contracts[0].contract_type == "ForbiddenContract"
    assert "cli" in result.contracts[0].name and "parser" in result.contracts[0].name


def test_run_contracts_falls_back_to_archy_yaml_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _purge_top(monkeypatch)
    _write_yaml_fixture(tmp_path, with_violation=True)
    with pytest.warns(UserWarning, match="best-effort fallback"):
        result = run_contracts(tmp_path)
    assert not result.all_kept
    assert result.broken == 1


def test_run_contracts_prefers_importlinter_over_archy_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both files present: .importlinter wins. The archy.yaml fixture would
    define a different contract (cli vs parser), so seeing the .importlinter
    contract name in the result confirms precedence."""
    _purge_top(monkeypatch)
    _write_fixture(tmp_path, with_violation=False)  # writes .importlinter + top/{a,b}.py
    # Add an archy.yaml that would generate a different contract if used.
    (tmp_path / "archy.yaml").write_text(
        textwrap.dedent(
            """
            layers:
              x:
                modules: ["top.a"]
              y:
                modules: ["top.b"]
            forbid:
              - {from: x, to: y}
            """
        ).strip()
        + "\n"
    )
    result = run_contracts(tmp_path)
    assert result.contracts[0].name == "top.b must not reach top.a"


def test_run_contracts_no_config_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _purge_top(monkeypatch)
    # Empty project: neither .importlinter nor archy.yaml.
    with pytest.raises(ContractsConfigError, match="no contracts config"):
        run_contracts(tmp_path)


# --- import-linter API contract ----------------------------------------------
#
# The wrap depends on a small, non-public surface in import-linter. These
# tests assert the surface still has the shape we expect at the pinned
# version, so a pin override surfaces the breakage here rather than at
# runtime in someone's CI pipeline.


def test_importlinter_user_options_surface() -> None:
    """`UserOptions(session_options=..., contracts_options=...)` is the
    constructor we call from `_archy_yaml_to_user_options`. If import-linter
    renames either kwarg, the YAML fallback breaks silently; assert the
    contract here."""
    from importlinter.application.user_options import UserOptions

    options = UserOptions(
        session_options={"root_package": "top"},
        contracts_options=[
            {
                "id": "x",
                "name": "x",
                "type": "forbidden",
                "source_modules": ["top.a"],
                "forbidden_modules": ["top.b"],
            }
        ],
    )
    assert options.session_options == {"root_package": "top"}
    assert len(options.contracts_options) == 1


def test_importlinter_use_case_entry_points_present() -> None:
    """The wrap calls `_register_contract_types`, `read_user_options`, and
    `create_report` from import-linter's use_cases module. None are part
    of the public API; this test fails loudly if any get renamed."""
    from importlinter.application import use_cases

    for name in ("_register_contract_types", "read_user_options", "create_report"):
        assert hasattr(use_cases, name), f"import-linter missing expected entry point: {name}"


def test_importlinter_pinned_to_supported_minor() -> None:
    """pyproject.toml pins import-linter to >=2.11,<2.12. Assert the
    installed version stays inside that window so a CI environment that
    overrides the pin (and breaks the wrap) is caught here instead of
    in production."""
    from importlib.metadata import version

    installed = version("import-linter")
    major_minor = ".".join(installed.split(".")[:2])
    assert major_minor == "2.11", (
        f"import-linter {installed} is outside the supported pin (2.11.x). "
        "Update pyproject.toml and re-verify the wrap before bumping the pin."
    )
