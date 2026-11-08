"""The skill must not strand an agent that has the skill but not the MCP tools (#533).

A directory install (`npx skills add hslee16/archy`) copies SKILL.md without wiring
the MCP server. The skill used to tell the agent to stop and not run the CLI. These
tests pin that the fallback is stated, and that the CLI table it points at only
names commands and flags that exist, so it cannot rot into a second refusal.
"""

from __future__ import annotations

import re
from pathlib import Path

import click

from archy.cli import main

SKILL = Path(__file__).resolve().parent.parent / "skills" / "archy" / "SKILL.md"
TICK = re.compile(r"`([^`]+)`")


def _section(text: str, heading: str) -> str:
    """The body under a `###` heading, up to the next heading of any level."""
    start = text.index(heading) + len(heading)
    nxt = re.search(r"^#{1,3} ", text[start:], re.MULTILINE)
    return text[start : start + nxt.start()] if nxt else text[start:]


def _cli_rows() -> list[tuple[str, str, str]]:
    """(mcp tool, cli cell, note cell) for every data row of the CLI table."""
    rows = []
    for line in _section(SKILL.read_text(), "### CLI equivalents").splitlines():
        if not line.startswith("| `archy_"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        tool = TICK.search(cells[0])
        assert tool, line
        rows.append((tool.group(1), cells[1], cells[2]))
    return rows


def _options(command: click.Command) -> dict[str, click.Parameter]:
    return {opt: p for p in command.params for opt in getattr(p, "opts", [])}


def _check_command(snippet: str, bare_flags: list[str], primary: str | None) -> list[str]:
    """Problems with one `archy ...` snippet; flags it carries must exist on its command."""
    words = snippet.split()
    assert words[0] == "archy", snippet
    name = words[1]
    if name not in main.commands:  # type: ignore[attr-defined]
        return [f"unknown command `archy {name}`"]
    options = _options(main.commands[name])  # type: ignore[attr-defined]
    problems = [
        f"`archy {name}` has no {w}" for w in words if w.startswith("--") and w not in options
    ]
    if "--format" in words:
        param = options.get("--format")
        choices = getattr(getattr(param, "type", None), "choices", [])
        if "json" not in choices:
            problems.append(f"`archy {name} --format` does not offer json")
    if name == primary:
        problems += [f"`archy {name}` has no {f}" for f in bare_flags if f not in options]
    return problems


def test_skill_no_longer_forbids_the_cli_fallback():
    text = SKILL.read_text().lower()
    assert "do not fall back" not in text
    assert "uvx archy" in text, "the fallback must name how to run archy without a PATH install"


def test_cli_table_covers_every_mcp_tool_in_the_reference():
    reference = {
        line.split("`", 2)[1]
        for line in _section(SKILL.read_text(), "## Tool reference").splitlines()
        if line.startswith("| `archy_")
    }
    table = {tool for tool, _, _ in _cli_rows()}
    assert len(reference) >= 14, "the tool reference table parsed too few tools"
    assert table == reference, f"CLI table drifted from the tool reference: {table ^ reference}"


def test_every_cli_command_and_flag_in_the_table_exists():
    problems: list[str] = []
    rows = _cli_rows()
    assert rows, "CLI table parsed no rows"
    for tool, cli_cell, note_cell in rows:
        snippets = [t for t in TICK.findall(cli_cell + " " + note_cell) if t.startswith("archy ")]
        bare = [t.split()[0] for t in TICK.findall(note_cell) if t.startswith("--")]
        primary = snippets[0].split()[1]
        for snippet in snippets:
            problems += [f"{tool}: {p}" for p in _check_command(snippet, bare, primary)]
    assert problems == []


def test_table_pins_the_forms_a_wrong_edit_would_break():
    """Assert the ANSWER for the mappings the ticket names, not only that things exist."""
    by_tool = {tool: cli for tool, cli, _ in _cli_rows()}
    assert "archy conventions . --module X" in by_tool["archy_module_view"]
    assert "archy impact" in by_tool["archy_impact"]
    assert "archy snapshot" in by_tool["archy_snapshot"]
    assert "--format" not in by_tool["archy_snapshot"], "snapshot has no --format"
