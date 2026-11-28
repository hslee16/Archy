"""The README's MCP tool table lists exactly the tools the server registers (#675).

It is hand-kept and has drifted before: 13 rows for 14 tools, `archy_brief`
missing. The spelled-out count above it is pinned too. Named `test_mcp_*` so
the mcp 2.x CI job runs it.
"""

from __future__ import annotations

import re
from pathlib import Path

from archy.mcp import create_server
from tests.mcp_helpers import _list_tools

README = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
SECTION = README.split("\n## MCP server (`archy mcp`)\n")[1].split("\n## ")[0]
_WORDS = [
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]


def _rows() -> list[str]:
    """The tool named in the first cell of each row of the section's tool table."""
    table = SECTION.split("\n| Tool | Purpose |\n")[1].split("\n\n")[0]
    # Any spelling of the header's separator row, as a formatter might write it.
    rows = [line for line in table.splitlines() if not re.fullmatch(r"[|:\- ]+", line)]
    names = [re.match(r"\| `([a-z_]+)` \|", line) for line in rows]
    assert all(names), "a table row does not start with a backticked tool name"
    return [match[1] for match in names if match]


def _registered() -> set[str]:
    return {tool.name for tool in _list_tools(create_server())}


def test_the_table_has_one_row_per_registered_tool():
    rows = _rows()

    assert len(rows) == len(set(rows)), "a tool has two rows"
    assert set(rows) == _registered()


def test_the_sentence_above_the_table_counts_them():
    said = re.search(r"`archy mcp` exposes (\w+) tools", SECTION)

    assert said, "the sentence that counts the tools is gone"
    assert said[1] in _WORDS, f"expected the count spelled out, ten to twenty, not {said[1]!r}"
    assert _WORDS.index(said[1]) + 10 == len(_registered())
