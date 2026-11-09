from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


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

    ``archy.mcp_runs._MANAGERS`` caches one manager per (project root, config): an
    open sqlite connection plus a watchdog observer. Every tool call on a fresh
    ``tmp_path`` project adds one and nothing stops it, so a full run ended with
    about 2,600 open file descriptors, past the 1023 that ``select.select``
    accepts. ``stop()`` releases all of a manager's descriptors. Looked up through
    ``sys.modules`` so a test that never imports the MCP layer pays nothing.
    """
    yield
    runs = sys.modules.get("archy.mcp_runs")
    if runs is None:
        return
    with runs._MANAGERS_LOCK:
        managers = list(runs._MANAGERS.values())
        runs._MANAGERS.clear()
    for manager in managers:
        manager.stop()
