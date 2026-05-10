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
    chains = contract.metadata.get("invalid_chains")
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
