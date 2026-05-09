from __future__ import annotations

import json
from pathlib import Path

import pytest

from archy.mcp import (
    _run_check,
    _run_cycles,
    _run_score,
    _run_trend,
    create_server,
)


@pytest.fixture
def cyclic_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n")
    return tmp_path


@pytest.fixture
def acyclic_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    return tmp_path


@pytest.fixture
def layered_project(tmp_path: Path):
    def build(core_api_src: str = "", cli_runner_src: str = "") -> Path:
        pkg = tmp_path / "myapp"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        core = pkg / "core"
        core.mkdir()
        (core / "__init__.py").write_text("")
        (core / "api.py").write_text(core_api_src)
        cli = pkg / "cli"
        cli.mkdir()
        (cli / "__init__.py").write_text("")
        (cli / "runner.py").write_text(cli_runner_src)
        (tmp_path / "archy.yaml").write_text(
            "layers:\n"
            "  core: {modules: [myapp.core.**]}\n"
            "  cli: {modules: [myapp.cli.**]}\n"
            "forbid:\n"
            "  - {from: core, to: cli}\n"
        )
        return tmp_path

    return build


# --- create_server ----------------------------------------------------------


def test_create_server_registers_expected_tools():
    import asyncio

    server = create_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "archy_score",
        "archy_cycles",
        "archy_check",
        "archy_trend",
        "archy_record_baseline",
    }


# --- _run_score -------------------------------------------------------------


def test_run_score_returns_full_payload(acyclic_project: Path):
    payload = _run_score(
        acyclic_project,
        internal_only=True,
        record=False,
        strict=False,
        strict_tolerance=0.02,
    )
    assert "overall" in payload
    assert set(payload["components"]) == {"modularity", "acyclicity", "depth", "equality"}
    assert "gate" not in payload
    assert not (acyclic_project / ".archy" / "history.jsonl").exists()


def test_run_score_with_record_appends_history(acyclic_project: Path):
    _run_score(
        acyclic_project,
        internal_only=True,
        record=True,
        strict=False,
        strict_tolerance=0.02,
    )
    history = acyclic_project / ".archy" / "history.jsonl"
    assert history.exists()
    [line] = history.read_text().splitlines()
    row = json.loads(line)
    assert "score" in row


def test_run_score_strict_no_history_passes(acyclic_project: Path):
    payload = _run_score(
        acyclic_project,
        internal_only=True,
        record=False,
        strict=True,
        strict_tolerance=0.02,
    )
    gate = payload["gate"]
    assert gate["previous"] is None
    assert gate["passed"] is True


def test_run_score_strict_against_seeded_history(acyclic_project: Path):
    history = acyclic_project / ".archy" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-09T10:00:00Z",
                "commit": "deadbee",
                "branch": "main",
                "score": {
                    "overall": 0.95,
                    "modularity": 0.5,
                    "acyclicity": 1.0,
                    "depth": 0.5,
                    "equality": 0.5,
                },
                "inputs": {
                    "module_count": 3,
                    "edge_count": 1,
                    "cycle_count": 0,
                    "max_depth": 1,
                    "community_count": 2,
                },
            }
        )
        + "\n"
    )
    payload = _run_score(
        acyclic_project,
        internal_only=True,
        record=False,
        strict=True,
        strict_tolerance=0.05,
    )
    assert payload["gate"]["passed"] is False
    assert payload["gate"]["previous"] == 0.95


# --- _run_cycles ------------------------------------------------------------


def test_run_cycles_finds_cycle(cyclic_project: Path):
    cycles = _run_cycles(cyclic_project, min_size=2, internal_only=True)
    assert len(cycles) == 1
    [cycle] = cycles
    assert sorted(cycle["modules"]) == ["pkg.a", "pkg.b"]
    pairs = {(e["source"], e["target"]) for e in cycle["edges"]}
    assert pairs == {("pkg.a", "pkg.b"), ("pkg.b", "pkg.a")}


def test_run_cycles_clean(acyclic_project: Path):
    assert _run_cycles(acyclic_project, min_size=2, internal_only=True) == []


# --- _run_check -------------------------------------------------------------


def test_run_check_with_layered_project_clean(layered_project):
    project = layered_project(cli_runner_src="from myapp.core import api\n")
    result = _run_check(project, config_path=None)
    assert result["passed"] is True
    assert result["violations"] == []


def test_run_check_with_violation(layered_project):
    project = layered_project(core_api_src="from myapp.cli.runner import go\n")
    result = _run_check(project, config_path=None)
    assert result["passed"] is False
    assert len(result["violations"]) == 1
    assert result["violations"][0]["rule"] == {"from": "core", "to": "cli"}


# --- _run_trend -------------------------------------------------------------


def test_run_trend_empty_history(acyclic_project: Path):
    assert _run_trend(acyclic_project, last_n=10) == []


def test_run_trend_returns_recorded_rows(acyclic_project: Path):
    _run_score(
        acyclic_project,
        internal_only=True,
        record=True,
        strict=False,
        strict_tolerance=0.02,
    )
    _run_score(
        acyclic_project,
        internal_only=True,
        record=True,
        strict=False,
        strict_tolerance=0.02,
    )
    rows = _run_trend(acyclic_project, last_n=10)
    assert len(rows) == 2
    assert "score" in rows[0]
    assert "overall" in rows[0]["score"]


def test_run_trend_last_n_truncates(acyclic_project: Path):
    for _ in range(5):
        _run_score(
            acyclic_project,
            internal_only=True,
            record=True,
            strict=False,
            strict_tolerance=0.02,
        )
    rows = _run_trend(acyclic_project, last_n=3)
    assert len(rows) == 3


# --- exclude config plumbed through MCP --------------------------------------


def test_run_cycles_honors_archy_yaml_exclude(tmp_path: Path):
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
