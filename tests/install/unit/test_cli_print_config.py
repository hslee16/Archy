"""`archy install --print-config` shows archy's lines, not the user's file (#519).

Shared client configs such as ~/.claude.json hold the user's other MCP servers
and account data, so echoing the merged file printed their secrets. Every
fixture here plants a sentinel where a plausible implementation would leak it.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from archy.cli import main

SENTINEL = "SENTINEL_SECRET"


def _seed(path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    assert SENTINEL in path.read_text(encoding="utf-8")  # the fixture carries it
    return path


def _claude_json(home, *, indent: int = 2, extra: str = "") -> None:
    """The other server's flat `apiKey` is its last line, so it sits directly
    above archy's inserted entry: any context line at all would carry it."""
    config = {
        "oauthAccount": {"emailAddress": "someone@example.com"},
        "mcpServers": {"other": {"command": "other-mcp", "apiKey": SENTINEL + extra}},
    }
    _seed(home / ".claude.json", json.dumps(config, indent=indent, ensure_ascii=False) + "\n")


def _print_config(agent: str = "claude", *args: str) -> str:
    result = CliRunner().invoke(main, ["install", "--print-config", agent, *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_print_config_shows_archys_entry_and_not_the_neighbouring_secret(simulate_os):
    fake = simulate_os("linux")
    _claude_json(fake.home)

    out = _print_config()

    assert '"archy"' in out
    assert SENTINEL not in out
    assert "oauthAccount" not in out


def test_a_file_the_writer_reformats_still_shows_only_archys_lines(simulate_os):
    """4-space indent and a non-ASCII value: the writer rewrites both lines,
    which a plain on-disk diff printed twice. They are counted, not shown."""
    fake = simulate_os("linux")
    _claude_json(fake.home, indent=4, extra="-café")

    out = _print_config()

    assert '"archy"' in out
    assert SENTINEL not in out
    assert "also rewrites" in out


def test_a_codex_inline_env_table_rewritten_by_the_writer_does_not_leak(simulate_os):
    fake = simulate_os("linux")
    _seed(
        fake.home / ".codex" / "config.toml",
        f'[mcp_servers.other]\ncommand = "x"\nenv = {{ TOKEN = "{SENTINEL}" }}\n',
    )

    out = _print_config("codex")

    assert "archy" in out
    assert SENTINEL not in out


def test_a_permission_entry_that_only_gains_a_comma_is_not_printed(simulate_os):
    fake = simulate_os("linux")
    settings = {"permissions": {"allow": [f"Bash(curl -H 'token: {SENTINEL}')"]}}
    _seed(fake.home / ".claude" / "settings.json", json.dumps(settings, indent=2) + "\n")

    out = _print_config()

    assert "mcp__archy__" in out
    assert SENTINEL not in out


def test_a_last_line_without_a_newline_is_not_printed_or_glued(simulate_os):
    fake = simulate_os("linux")
    _seed(fake.home / ".claude" / "CLAUDE.md", f"# my notes\nAPI key is {SENTINEL}")

    out = _print_config()

    assert "archy:begin" in out
    assert SENTINEL not in out


def test_a_file_that_does_not_exist_yet_prints_whole(simulate_os):
    simulate_os("linux")

    out = _print_config("codex")

    # AGENTS.md is archy's own when absent; its closing marker proves "whole".
    assert "<!-- archy:end -->" in out
    assert "@@" not in out.split("AGENTS.md", 1)[1]


def test_print_config_full_still_prints_the_whole_merged_file(simulate_os):
    fake = simulate_os("linux")
    _claude_json(fake.home)

    out = _print_config("claude", "--full")

    assert SENTINEL in out
    assert '"archy"' in out


def test_print_config_says_no_changes_when_already_installed(simulate_os):
    simulate_os("linux")
    installed = CliRunner().invoke(main, ["install", "--target", "claude", "--yes"])
    assert installed.exit_code == 0, installed.output

    out = _print_config()

    assert "(no changes)" in out
    assert "@@" not in out


def test_a_crlf_file_is_not_reported_as_unchanged(simulate_os):
    """Install writes LF, so a CRLF file really is rewritten."""
    fake = simulate_os("linux")
    installed = CliRunner().invoke(main, ["install", "--target", "claude", "--yes"])
    assert installed.exit_code == 0, installed.output
    path = fake.home / ".claude.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert b"\r\n" in path.read_bytes()

    out = _print_config()

    assert ".claude.json\n(no changes)" not in out
    assert "also rewrites" in out
