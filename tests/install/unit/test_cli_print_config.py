"""`archy install --print-config` shows what archy changes, not the whole file (#519).

Shared client configs such as ~/.claude.json hold the user's other MCP servers
and account data, so echoing the merged file printed their secrets.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from archy.cli import main

SENTINEL = "SENTINEL_SECRET"


def _seed_claude_json(home):
    """A ~/.claude.json whose other server's token sits right where archy's
    `mcpServers` entry is inserted, so any context line would carry it."""
    config = {
        "oauthAccount": {"emailAddress": "someone@example.com"},
        "mcpServers": {"other": {"command": "other-mcp", "env": {"TOKEN": SENTINEL}}},
    }
    path = home / ".claude.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def _print_config(*args: str) -> str:
    result = CliRunner().invoke(main, ["install", "--print-config", "claude", *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_print_config_shows_archys_entry_and_not_the_users_secrets(simulate_os):
    fake = simulate_os("linux")
    path = _seed_claude_json(fake.home)
    assert SENTINEL in path.read_text()  # the fixture carries the secret to leak

    out = _print_config()

    assert '"archy"' in out
    assert SENTINEL not in out
    assert "oauthAccount" not in out


def test_print_config_full_still_prints_the_whole_merged_file(simulate_os):
    fake = simulate_os("linux")
    _seed_claude_json(fake.home)

    out = _print_config("--full")

    assert SENTINEL in out
    assert '"archy"' in out


def test_print_config_says_no_changes_when_already_installed(simulate_os):
    simulate_os("linux")
    runner = CliRunner()
    installed = runner.invoke(main, ["install", "--target", "claude", "--yes"])
    assert installed.exit_code == 0, installed.output

    out = _print_config()

    assert "(no changes)" in out
    assert "@@" not in out
