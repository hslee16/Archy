from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from archy.layers import ForbidRule, LayerConfig, LayerSpec


@pytest.fixture(scope="session")
def _git_test_global_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    config = tmp_path_factory.mktemp("git-config") / "global.gitconfig"
    config.write_text(
        "[user]\n"
        "\tname = Archy Tests\n"
        "\temail = tests@example.invalid\n"
        "[commit]\n"
        "\tgpgsign = false\n"
        "[tag]\n"
        "\tgpgsign = false\n",
        encoding="utf-8",
    )
    return config


@pytest.fixture(autouse=True)
def _isolate_git_config(
    monkeypatch: pytest.MonkeyPatch,
    _git_test_global_config: Path,
) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(_git_test_global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture(autouse=True)
def _stop_mcp_index_managers() -> Iterator[None]:
    """Stop every IndexManager an MCP tool call started, once the test is over (#699).

    ``archy.mcp_runs_core._MANAGERS`` caches one manager per (project root, config): an
    open sqlite connection plus a watchdog observer. Every tool call on a fresh
    ``tmp_path`` project adds one and nothing stops it, so a full run ended with
    about 2,600 open file descriptors, past the 1023 that ``select.select``
    accepts. ``stop()`` releases all of a manager's descriptors. Looked up through
    ``sys.modules`` so a test that never imports the MCP layer pays nothing.
    """
    yield
    runs = sys.modules.get("archy.mcp_runs_core")
    if runs is None:
        return
    with runs._MANAGERS_LOCK:
        managers = list(runs._MANAGERS.values())
        runs._MANAGERS.clear()
    for manager in managers:
        manager.stop()


@pytest.fixture
def acyclic_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    return tmp_path


@pytest.fixture
def project_with_caller(tmp_path: Path) -> Path:
    # Shared by direction='in'/'out' tests: pkg.a imports pkg.b (so pkg.a has
    # a downstream dependency), and pkg.c imports pkg.a (so pkg.a has an
    # upstream caller). Anchoring focus on pkg.a then exercises both halves.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("from pkg.a import other\n")
    return tmp_path


@pytest.fixture
def pkg(tmp_path: Path) -> Path:
    """An empty `pkg/` package rooted at tmp_path. Tests fill in the .py files.

    Most test bodies need only the `pkg` Path; tests that build the graph
    pass `tmp_path` to `build_graph`. A few tests that exercise non-empty
    `__init__.py` re-exports overwrite it with `(pkg / "__init__.py").write_text(...)`.
    """
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    return pkg_dir


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A small fake project with a src layout and a few modules."""
    pkg = tmp_path / "src" / "myapp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("import os\nfrom myapp import utils\n")
    (pkg / "utils.py").write_text("from . import core\nimport requests\n")
    (pkg / "cli.py").write_text("from myapp.core import thing\nfrom .. import escapes\n")
    sub = pkg / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "leaf.py").write_text("from ..utils import helper\nimport json\n")
    return tmp_path


@pytest.fixture
def core_cli_config() -> LayerConfig:
    return LayerConfig(
        layers=(
            LayerSpec(name="core", patterns=("myapp.core.**",)),
            LayerSpec(name="cli", patterns=("myapp.cli.**",)),
        ),
        forbid=(ForbidRule(from_layer="core", to_layer="cli"),),
    )
