"""Shared helper for tests that call an MCP tool in-process.

Lives in its own module, not in `tests/test_mcp.py`, so that a test file never
imports another test file: importing a test module from a test module couples
them and puts the importer one step deeper in archy's own import graph.
"""

from __future__ import annotations

import asyncio


def call_tool(server, name: str, args: dict) -> tuple[list, dict | None]:
    """Call a tool in-process and return `(content, structured)` on either SDK major.

    mcp 1.x returns that pair directly; 2.0 returns a `CallToolResult` instead.
    Normalized here rather than at each call site, and `structuredContent` is read
    from the wire form because 2.0 also renamed the attribute (see
    `archy.mcp_compat`).

    These in-process calls poke the SDK's own surface, which is why they are the
    only part of the suite a major bump touches. The protocol-level contract is
    covered by `tests/test_mcp_protocol.py`, which speaks stdio and passes
    unchanged on both majors.
    """
    result = asyncio.run(server.call_tool(name, args))
    if isinstance(result, tuple):  # mcp 1.x
        return result
    return result.content, result.model_dump(by_alias=True).get("structuredContent")
