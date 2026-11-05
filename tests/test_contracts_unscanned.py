"""A contract pass through a directory the engine never reads (#528).

grimp, the engine under `archy contracts`, prunes a directory that has no
`__init__.py` once it is inside a regular package. A path through it is invisible
and the contract is reported as kept. These tests pin that the result says so, on
every surface, and that the fixture really does hide a break.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.contracts import run_contracts
from archy.mcp_runs import run_contracts as mcp_run_contracts

PKG = "orphanpkg"


def _project(root: Path, *, ns_init: bool = False) -> Path:
    """`a -> ns.mid -> b`, with `a` forbidden from reaching `b`, through `ns/`."""
    ns = root / PKG / "ns"
    ns.mkdir(parents=True)
    (root / PKG / "__init__.py").write_text("")
    (root / PKG / "a.py").write_text(f"from {PKG}.ns import mid  # noqa: F401\n")
    (root / PKG / "b.py").write_text("VALUE = 1\n")
    (ns / "mid.py").write_text(f"from {PKG} import b  # noqa: F401\n")
    if ns_init:
        (ns / "__init__.py").write_text("")
    (root / ".importlinter").write_text(
        f"[importlinter]\nroot_package = {PKG}\n\n"
        "[importlinter:contract:a-not-b]\nname = a must not reach b\ntype = forbidden\n"
        f"source_modules =\n    {PKG}.a\nforbidden_modules =\n    {PKG}.b\n"
    )
    return root


def test_the_fixture_really_hides_a_break_from_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Without this the notes below could be asserted over a path nobody missed."""
    import grimp

    root = _project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    assert not (root / PKG / "ns" / "__init__.py").exists()
    assert PKG + ".ns.mid" not in grimp.build_graph(PKG).modules

    hidden = run_contracts(root)
    assert hidden.broken == 0 and hidden.verified

    (root / PKG / "ns" / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(root))
    visible = run_contracts(root)
    assert visible.broken == 1
    assert visible.unscanned_dirs == ()


def test_the_result_names_the_directory_the_engine_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _project(tmp_path)
    monkeypatch.syspath_prepend(str(root))

    result = run_contracts(root)

    assert result.unscanned_dirs == (f"{PKG}.ns",)
    # A note on the verdict, not a change to it: a stray scripts directory must
    # not fail every existing gate.
    assert result.verified is True


def test_only_directories_the_engine_would_prune_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = _project(tmp_path, ns_init=True)
    pkg = root / PKG
    (pkg / "data").mkdir()
    (pkg / "data" / "table.csv").write_text("x\n")  # no .py: nothing to miss
    (pkg / ".hidden").mkdir()
    (pkg / ".hidden" / "x.py").write_text("")
    (pkg / "ns" / "deep" / "inner").mkdir(parents=True)
    (pkg / "ns" / "deep" / "inner" / "z.py").write_text("")  # pruned with `deep`
    monkeypatch.syspath_prepend(str(root))

    assert run_contracts(root).unscanned_dirs == (f"{PKG}.ns.deep",)


def test_contracts_text_and_json_carry_the_note(tmp_path: Path):
    root = _project(tmp_path)

    text = CliRunner().invoke(main, ["contracts", str(root)])
    as_json = CliRunner().invoke(main, ["contracts", str(root), "--format", "json"])

    assert "1 directory not visible to the contracts engine" in text.output
    assert f"{PKG}.ns" in text.output
    assert json.loads(as_json.output)["unscanned_dirs"] == [f"{PKG}.ns"]


def test_check_contracts_text_and_json_carry_the_note(tmp_path: Path):
    root = _project(tmp_path)
    (root / "archy.yaml").write_text(
        "layers:\n"
        f"  a: {{modules: ['{PKG}.a']}}\n"
        f"  b: {{modules: ['{PKG}.b']}}\n"
        "forbid:\n  - {from: a, to: b}\n"
    )

    text = CliRunner().invoke(main, ["check", str(root), "--contracts"])
    as_json = CliRunner().invoke(main, ["check", str(root), "--contracts", "--format", "json"])

    assert "not visible to the contracts engine" in text.output
    assert json.loads(as_json.output)["contracts"]["unscanned_dirs"] == [f"{PKG}.ns"]


def test_mcp_payload_carries_the_note_on_the_wire(tmp_path: Path):
    root = _project(tmp_path)

    dumped = mcp_run_contracts(root, config_filename=None).model_dump()

    assert dumped["unscanned_dirs"] == (f"{PKG}.ns",)
