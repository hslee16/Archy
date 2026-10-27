from __future__ import annotations

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
