"""MCP-specific tests.

Underlying analysis behavior (cycle detection, score computation, layer
violations, etc.) is covered by test_cli.py and the per-module unit
suites. This file only verifies what the MCP layer adds:

- the registered tool surface (names) the agent sees
- the dict shapes each tool returns, since agents read them by key
- exclude/roots config plumbing through the MCP path (not just CLI)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from archy.mcp import (
    _run_check,
    _run_cycles,
    _run_diff,
    _run_impact,
    _run_score,
    _run_snapshot,
    _run_trend,
    create_server,
)


@pytest.fixture
def acyclic_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    return tmp_path


def test_create_server_registers_expected_tools():
    server = create_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "archy_score",
        "archy_cycles",
        "archy_check",
        "archy_contracts",
        "archy_trend",
        "archy_impact",
        "archy_snapshot",
        "archy_diff",
        "archy_record_baseline",
    }


def test_create_server_registers_loop_prompt():
    server = create_server()
    prompts = asyncio.run(server.list_prompts())
    assert any(p.name == "loop" for p in prompts)


def test_run_score_payload_shape(acyclic_project: Path):
    payload = _run_score(
        acyclic_project,
        internal_only=True,
        record=False,
        strict=False,
        strict_tolerance=0.02,
    )
    assert "overall" in payload
    assert set(payload["components"]) == {"modularity", "acyclicity", "depth", "equality"}
    assert {"module_count", "edge_count", "cycle_count", "max_depth", "community_count"} <= set(
        payload["inputs"]
    )
    assert "gate" not in payload


def test_run_score_strict_includes_gate_block(acyclic_project: Path):
    payload = _run_score(
        acyclic_project,
        internal_only=True,
        record=False,
        strict=True,
        strict_tolerance=0.02,
    )
    gate = payload["gate"]
    assert {"previous", "current", "delta", "tolerance", "passed"} <= set(gate)


def test_run_cycles_payload_shape(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n")
    [cycle] = _run_cycles(tmp_path, min_size=2, internal_only=True)
    assert sorted(cycle["modules"]) == ["pkg.a", "pkg.b"]
    edge = cycle["edges"][0]
    assert {"source", "target", "lines"} <= set(edge)


def test_run_check_payload_shape(tmp_path: Path):
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    core = pkg / "core"
    core.mkdir()
    (core / "__init__.py").write_text("")
    (core / "api.py").write_text("from myapp.cli.runner import go\n")
    cli = pkg / "cli"
    cli.mkdir()
    (cli / "__init__.py").write_text("")
    (cli / "runner.py").write_text("")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    result = _run_check(tmp_path, config_path=None)
    assert result["passed"] is False
    [violation] = result["violations"]
    assert violation["rule"] == {"from": "core", "to": "cli"}
    assert {"source", "target", "lines"} <= set(violation)


def test_run_trend_payload_shape(acyclic_project: Path):
    _run_score(
        acyclic_project,
        internal_only=True,
        record=True,
        strict=False,
        strict_tolerance=0.02,
    )
    [row] = _run_trend(acyclic_project, last_n=10)
    assert {"timestamp", "commit", "branch", "score", "inputs"} <= set(row)
    assert "overall" in row["score"]


def test_run_impact_payload_shape(tmp_path: Path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "lib.py").write_text("")
    (pkg / "main.py").write_text("from app.lib import x\n")
    result = _run_impact(tmp_path, files=[Path("app/lib.py")])
    assert {"changed", "unresolved", "impacted"} == set(result)
    assert result["changed"] == ["app.lib"]
    assert result["impacted"] == ["app.main"]


def test_run_snapshot_writes_baseline_and_returns_payload(acyclic_project: Path):
    payload = _run_snapshot(acyclic_project)
    assert {"score", "cycles", "violations", "baseline_path"} <= set(payload)
    assert (acyclic_project / ".archy" / "baseline.json").exists()


def test_run_diff_without_baseline_returns_error(acyclic_project: Path):
    result = _run_diff(acyclic_project)
    assert "error" in result


def test_run_diff_after_snapshot_reports_zero_delta(acyclic_project: Path):
    _run_snapshot(acyclic_project)
    result = _run_diff(acyclic_project)
    assert result["score_delta"]["overall"] == 0.0
    assert result["cycles"] == {"added": [], "resolved": []}


def test_archy_yaml_exclude_plumbed_through_mcp(tmp_path: Path):
    # Verifies the MCP path honors `exclude:` (CLI tests cover the same
    # config for the CLI path; this protects the agent-facing surface).
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "real.py").write_text("import os\n")
    gen = pkg / "baml_client"
    gen.mkdir()
    (gen / "__init__.py").write_text("from myapp.baml_client.b import x\n")
    (gen / "b.py").write_text("from myapp.baml_client import other\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nexclude: [baml_client]\n")
    assert _run_cycles(tmp_path, min_size=2, internal_only=True) == []
