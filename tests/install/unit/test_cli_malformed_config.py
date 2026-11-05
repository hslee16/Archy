"""CLI handles malformed user configs as actionable errors without tracebacks (#590)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.install.base import INSTRUCTIONS_BEGIN, INSTRUCTIONS_END


def test_install_rejects_non_object_json_config(simulate_os):
    fake = simulate_os("linux")
    bad_file = fake.home / ".claude.json"
    bad_file.write_text("[]\n", encoding="utf-8")
    assert bad_file.read_text(encoding="utf-8") == "[]\n"

    result = CliRunner().invoke(main, ["install", "--target", "claude", "-y"])

    assert result.exit_code == 1
    assert str(bad_file) in result.output
    assert "expected a JSON object at the top level" in result.output
    assert "Traceback" not in result.output


def test_install_rejects_malformed_json_config(simulate_os):
    fake = simulate_os("linux")
    bad_file = fake.home / ".cursor" / "mcp.json"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("{ not json\n", encoding="utf-8")
    assert bad_file.read_text(encoding="utf-8") == "{ not json\n"

    result = CliRunner().invoke(main, ["install", "--target", "cursor", "-y"])

    assert result.exit_code == 1
    assert str(bad_file) in result.output
    assert "JSON decode error" in result.output
    assert "line 1 column 3" in result.output
    assert "Traceback" not in result.output


def test_install_rejects_malformed_toml_config(simulate_os):
    fake = simulate_os("linux")
    bad_file = fake.home / ".codex" / "config.toml"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("[[[\n", encoding="utf-8")
    assert bad_file.read_text(encoding="utf-8") == "[[[\n"

    result = CliRunner().invoke(main, ["install", "--target", "codex", "-y"])

    assert result.exit_code == 1
    assert str(bad_file) in result.output
    assert "TOML decode error" in result.output
    assert "line 1, column 3" in result.output
    assert "Traceback" not in result.output


def test_install_rejects_config_path_that_is_a_directory(simulate_os):
    fake = simulate_os("linux")
    bad_dir = fake.home / ".claude.json"
    bad_dir.mkdir(parents=True, exist_ok=True)
    assert bad_dir.is_dir()

    result = CliRunner().invoke(main, ["install", "--target", "claude", "-y"])

    assert result.exit_code == 1
    assert str(bad_dir) in result.output
    assert "is a directory" in result.output
    assert "Traceback" not in result.output


def test_uninstall_rejects_malformed_config(simulate_os):
    fake = simulate_os("linux")
    bad_file = fake.home / ".cursor" / "mcp.json"
    bad_file.parent.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("{ not json\n", encoding="utf-8")
    assert bad_file.read_text(encoding="utf-8") == "{ not json\n"

    result = CliRunner().invoke(main, ["uninstall", "--target", "cursor", "-y"])

    assert result.exit_code == 1
    assert str(bad_file) in result.output
    assert "JSON decode error" in result.output
    assert "Traceback" not in result.output


# #799: a begin marker with no end marker cannot say where archy's text stops.
UNCLOSED_MARKER_FILES = [
    pytest.param(
        f"# My global rules\n- rule A\n\n{INSTRUCTIONS_BEGIN}\nold archy text\n\n"
        "## My own section after\n- important user note\n",
        4,
        id="user-text-on-both-sides",
    ),
    pytest.param(
        f"{INSTRUCTIONS_BEGIN}\nold archy text\n\n## My notes\n- precious 1\n",
        1,
        id="marker-on-line-1",
    ),
]


def _claude_md(fake, content: str):
    path = fake.home / ".claude" / "CLAUDE.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
    assert INSTRUCTIONS_BEGIN in content
    assert INSTRUCTIONS_END not in content
    return path


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["install", "--target", "claude", "-y"], id="install"),
        pytest.param(["install", "--print-config", "claude"], id="print-config"),
        pytest.param(["uninstall", "--target", "claude", "-y"], id="uninstall"),
        pytest.param(["uninstall", "--target", "claude", "--dry-run"], id="uninstall-dry-run"),
    ],
)
@pytest.mark.parametrize(("content", "line"), UNCLOSED_MARKER_FILES)
def test_unclosed_archy_block_is_refused_and_keeps_the_file(simulate_os, args, content, line):
    fake = simulate_os("linux")
    claude_md = _claude_md(fake, content)

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 1, result.output
    assert str(claude_md) in result.output
    assert f"line {line}" in result.output
    assert "Traceback" not in result.output
    assert claude_md.read_bytes() == content.encode("utf-8")


def test_install_replaces_only_the_block_when_the_end_marker_is_present(simulate_os):
    fake = simulate_os("linux")
    claude_md = fake.home / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(
        f"# mine\n\n{INSTRUCTIONS_BEGIN}\nold\n{INSTRUCTIONS_END}\n\n## After\n- kept\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(main, ["install", "--target", "claude", "-y"])

    assert result.exit_code == 0, result.output
    text = claude_md.read_text(encoding="utf-8")
    assert text.startswith("# mine\n")
    assert text.endswith("## After\n- kept\n")
    assert "\nold\n" not in text
