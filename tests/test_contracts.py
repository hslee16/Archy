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


def test_run_contracts_config_filename_directory_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _purge_top(monkeypatch)
    config_dir = tmp_path / "config-dir"
    config_dir.mkdir()
    with pytest.raises(ContractsConfigError, match="must be a file, not a directory"):
        run_contracts(tmp_path, config_filename=config_dir)


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


def _write_transitive_fixture(root: Path, *, patterns: str) -> None:
    """top.store -> top.service -> top.api, with store forbidden from api.

    The violation is reachable ONLY transitively: there is no direct
    store -> api edge, which is the entire reason `contracts` exists next to
    `check`. `patterns` selects the dialect the layers are written in.
    """
    pkg = root / "top"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "api.py").write_text("VALUE = 1\n")
    (pkg / "service.py").write_text("from top import api  # noqa: F401\n")
    (pkg / "store.py").write_text("from top import service  # noqa: F401\n")
    (root / "archy.yaml").write_text(
        textwrap.dedent(
            f"""
            layers:
              api:
                modules:
                  - "top.api{patterns}"
              service:
                modules:
                  - "top.service{patterns}"
              store:
                modules:
                  - "top.store{patterns}"
            forbid:
              - {{from: store, to: api}}
            """
        ).strip()
        + "\n"
    )


def test_a_glob_layer_pattern_still_catches_the_transitive_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#435. archy's `pkg.**` means "pkg AND its descendants"; import-linter's
    means the descendants only. Passing the pattern through unchanged left the
    contract with zero source modules, so it was vacuously kept and a project
    with a forbidden transitive path reported a clean pass at exit 0.

    `.**` is the form archy's own hint tells users to write, so this was the
    recommended config, not an exotic one.
    """
    _purge_top(monkeypatch)
    _write_transitive_fixture(tmp_path, patterns=".**")
    with pytest.warns(UserWarning, match="best-effort fallback"):
        result = run_contracts(tmp_path)

    assert result.broken == 1
    assert not result.verified
    contract = result.contracts[0]
    assert not contract.kept
    # The chain is the point: no direct store -> api edge exists, so a
    # contract that only saw direct edges would have nothing to report.
    chains = cast(list[dict[str, object]], contract.metadata.get("invalid_chains"))
    assert chains, "expected the transitive chain, not merely a broken verdict"
    assert chains[0]["downstream_module"] == "top.store"
    assert chains[0]["upstream_module"] == "top.api"


def test_the_bare_and_glob_dialects_reach_the_same_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two ways of writing the same layer must agree. Before #435 they did
    not: the bare form caught this violation and the `.**` form reported OK."""
    _purge_top(monkeypatch)
    _write_transitive_fixture(tmp_path, patterns="")
    with pytest.warns(UserWarning, match="best-effort fallback"):
        bare = run_contracts(tmp_path)

    _purge_top(monkeypatch)
    (tmp_path / "archy.yaml").unlink()
    for stale in ("api.py", "service.py", "store.py", "__init__.py"):
        (tmp_path / "top" / stale).unlink()
    (tmp_path / "top").rmdir()
    _write_transitive_fixture(tmp_path, patterns=".**")
    with pytest.warns(UserWarning, match="best-effort fallback"):
        glob = run_contracts(tmp_path)

    # Both must actually have found something, or "they agree" is vacuous.
    assert bare.broken == 1
    assert (glob.broken, glob.verified) == (bare.broken, bare.verified)


def test_a_layer_pattern_matching_no_module_is_not_reported_as_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contract with no source modules holds whatever the code does. Calling
    that `kept` at exit 0 is indistinguishable from real protection (#435)."""
    _purge_top(monkeypatch)
    pkg = tmp_path / "top"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "api.py").write_text("VALUE = 1\n")
    (pkg / "store.py").write_text("from top import api  # noqa: F401\n")
    (tmp_path / "archy.yaml").write_text(
        textwrap.dedent(
            """
            layers:
              api:
                modules:
                  - "top.api.**"
              store:
                modules:
                  - "top.*.handlers"
            forbid:
              - {from: store, to: api}
            """
        ).strip()
        + "\n"
    )
    with pytest.warns(UserWarning, match="best-effort fallback"):
        result = run_contracts(tmp_path)

    contract = result.contracts[0]
    # import-linter still calls it kept, which is why the flag has to exist.
    assert contract.kept
    assert contract.matched_nothing
    assert contract.unmatched_expressions == ("top.*.handlers",)
    assert result.unverifiable == 1
    assert result.all_kept and not result.verified


def test_a_contract_naming_an_absent_module_is_a_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import-linter aborts the whole run with a bare `ValueError: Module 'x'
    does not exist.`, which reaches a user as a traceback and reads as archy
    being broken rather than the config."""
    _purge_top(monkeypatch)
    pkg = tmp_path / "top"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "api.py").write_text("VALUE = 1\n")
    (pkg / "store.py").write_text("from top import api  # noqa: F401\n")
    (tmp_path / "archy.yaml").write_text(
        textwrap.dedent(
            """
            layers:
              api:
                modules:
                  - "top.api.**"
              store:
                modules:
                  - "top.stoer.**"
            forbid:
              - {from: store, to: api}
            """
        ).strip()
        + "\n"
    )
    with (
        pytest.warns(UserWarning, match="best-effort fallback"),
        pytest.raises(ContractsConfigError, match="not in the import graph"),
    ):
        run_contracts(tmp_path)


def test_the_unverifiable_facts_survive_model_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`verified` and `unverifiable` are derived, and FastMCP sends
    `model_dump()`, which drops a plain `@property` silently (AGENTS.md)."""
    _purge_top(monkeypatch)
    _write_transitive_fixture(tmp_path, patterns=".**")
    with pytest.warns(UserWarning, match="best-effort fallback"):
        wire = run_contracts(tmp_path).model_dump()

    assert wire["verified"] is False
    assert wire["unverifiable"] == 0
    assert wire["contracts"][0]["matched_nothing"] is False


def test_run_contracts_prefers_importlinter_over_archy_yaml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both files present: .importlinter wins. The archy.yaml fixture would
    define a different contract (cli vs parser), so seeing the .importlinter
    contract name in the result confirms precedence."""
    _purge_top(monkeypatch)
    # `.importlinter` present so the resolution order's #2 branch fires; the
    # archy.yaml below would generate a *differently named* contract if the
    # fallback were taken, which is what makes the assertion below load-bearing.
    _write_fixture(tmp_path, with_violation=False)
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
    # The resolution order must surface a clean ContractsConfigError when no
    # config exists, rather than silently passing or letting import-linter
    # raise its own less-actionable error from deeper in the call.
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
