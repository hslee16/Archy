"""`archy install --print-config` shows archy's lines, not the user's file (#519).

Shared client configs such as ~/.claude.json hold the user's other MCP servers
and account data, so echoing the merged file printed their secrets. Every
fixture here plants a sentinel where a plausible implementation would leak it.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from archy.cli import main
from archy.install.base import TOOL_NAMES

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
    assert "also changes" in out


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
    assert "also changes" in out


def test_user_text_kept_in_an_archy_owned_rules_file_is_not_printed(simulate_os):
    """Cursor's rules file unrenders by deletion, but install keeps text outside
    the marker block, so an empty baseline would print it."""
    fake = simulate_os("linux")
    _seed(
        fake.home / ".cursor" / "rules" / "archy.mdc",
        f"my notes: token={SENTINEL}\n\n<!-- archy:begin -->\nold\n<!-- archy:end -->\n",
    )

    out = _print_config("cursor")

    assert "archy:begin" in out
    assert SENTINEL not in out


def test_only_added_lines_print_so_a_stripped_empty_container_does_not(simulate_os):
    fake = simulate_os("linux")
    (fake.home / ".cursor").mkdir(parents=True)
    (fake.home / ".cursor" / "mcp.json").write_text("{}\n")

    out = _print_config("cursor")

    # The rules file is new and prints whole; its markdown bullets start with "-".
    mcp = out.split("mcp.json", 1)[1].split("archy.mdc", 1)[0]
    assert '"archy"' in mcp
    assert not [line for line in mcp.splitlines() if line[:1] == "-" and line[:3] != "---"]


def test_archys_entry_prints_exactly_after_another_server(simulate_os):
    """The user's `}` gains a comma. A line diff paired it with archy's own
    closing brace, printing a stray `},` and dropping archy's `}`; a brace
    count could not see that, since the two errors cancel out."""
    fake = simulate_os("linux")
    _claude_json(fake.home)

    out = _print_config()

    section = out.split(".claude.json", 1)[1].split("# ", 1)[0]
    added = [line for line in section.splitlines() if line.startswith("+")]
    assert added[0] == '+    "archy": {'
    assert added[-1] == "+    }"


def test_an_installed_entry_in_a_reformatted_file_prints_archy_not_the_user(simulate_os):
    fake = simulate_os("linux")
    installed = CliRunner().invoke(main, ["install", "--target", "claude", "--yes"])
    assert installed.exit_code == 0, installed.output
    path = fake.home / ".claude.json"
    config = json.loads(path.read_text())
    config["note"] = "rewritten by the client, not by archy"
    path.write_text(json.dumps(config, indent=4) + "\n")

    out = _print_config()

    assert '+    "archy": {' in out
    assert "note" not in out
    assert "install also changes" in out  # re-indenting every line changes more


def test_raw_non_ascii_written_by_the_client_is_not_reported_as_a_change(simulate_os):
    # #538: install used to re-escape the user's own non-ASCII text, so a file
    # the client had just written appeared changed on the next --print-config.
    fake = simulate_os("linux")
    installed = CliRunner().invoke(main, ["install", "--target", "claude", "--yes"])
    assert installed.exit_code == 0, installed.output
    path = fake.home / ".claude.json"
    config = json.loads(path.read_text())
    config["note"] = "café"
    # Bytes, LF: the preview compares against the raw file, and Windows writes
    # CRLF by default, which is the separate CRLF-is-a-rewrite case.
    path.write_bytes((json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    out = _print_config()

    assert "café" not in out
    assert "(no changes)" in out.split(".claude.json", 1)[1].split("# ", 1)[0]


def test_the_rewrite_count_includes_lines_that_start_with_dashes(simulate_os):
    """CRLF front matter: all four lines are rewritten, `---` lines included.
    Skipping every line starting `---` (the file header) undercounted to 2."""
    fake = simulate_os("linux")
    path = fake.home / ".claude" / "CLAUDE.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"---\r\ntitle: x\r\n---\r\nbody\r\n")

    out = _print_config()

    assert "changes 4 other" in out


def test_full_without_print_config_is_a_usage_error_and_writes_nothing(simulate_os):
    fake = simulate_os("linux")

    result = CliRunner().invoke(main, ["install", "--full", "--target", "cursor", "--yes"])

    assert result.exit_code == 2
    assert "--full only applies with --print-config" in result.output
    assert not (fake.home / ".cursor").exists()


def test_archy_tool_names_elsewhere_do_not_hide_the_allow_entries(simulate_os):
    """The tool names sit in `deny`, so install still adds them to `allow`.
    A line-presence check called that "already present" and printed nothing."""
    fake = simulate_os("linux")
    deny = [f"mcp__archy__{name}" for name in TOOL_NAMES]
    path = fake.home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"permissions": {"deny": deny}}, indent=2) + "\n")

    out = _print_config()

    section = out.split("settings.json", 1)[1]
    assert f'+      "mcp__archy__{TOOL_NAMES[0]}",' in section


def test_an_owned_file_without_markers_prints_whole_and_drops_the_old_env(simulate_os):
    """Continue's archy.yaml is archy's own and has no marker block. The old
    version's user `env` is deleted, so it must be counted, never printed."""
    fake = simulate_os("linux")
    _seed(
        fake.home / ".continue" / "mcpServers" / "archy.yaml",
        f"name: archy\nmcpServers:\n  - name: archy\n    command: /opt/old/archy\n"
        f"    env:\n      TOKEN: {SENTINEL}\n",
    )

    out = _print_config("continue")

    section = out.split("archy.yaml", 1)[1].split("# ", 1)[0]
    assert "+mcpServers:" in section
    assert SENTINEL not in out
    assert "also changes" in out
