"""One import that works on both major versions of the `mcp` SDK.

mcp 2.0.0 renamed the ergonomic server class and moved its module:
`mcp.server.fastmcp.FastMCP` became `mcp.server.MCPServer`, and
`mcp.server.fastmcp.exceptions` became `mcp.server.mcpserver.exceptions`. The
old paths are gone in 2.x, not deprecated, so a module-scope import of either
one hard-fails on the other major version.

That is not a hypothetical. archy shipped `mcp>=1.28.1` with no ceiling, mcp
2.0.0 released, and every fresh `pip install archy` resolved a version where
`archy mcp` died on import (#391). The CLI kept working, so nothing looked
wrong until an agent tried to start the server. v0.43.0 capped at `<2` to stop
the bleeding; this module is what lets the cap come back off.

**Supporting both is deliberate, and cheaper than it looks.** The four API
surfaces archy uses -- the constructor, `.tool()`, `.prompt()`, and `.run()` --
take the same arguments in both majors, verified signature by signature. So the
choice was never "port to 2.x or stay on 1.x"; it was "alias one name, or force
every user to move in lockstep with archy". A user whose agent host pins mcp
1.x should not need a new archy, and vice versa.

What this file does NOT promise is that the two majors behave identically on
the wire. Signature compatibility is not serialization compatibility, and
archy's documented contract (an `outputSchema` derived per tool,
`structuredContent` alongside the text block, zero content blocks for an empty
result) is a claim about the SDK's behavior, not its call shape. That is
verified against BOTH majors by an actual stdio handshake in
`tests/test_mcp_protocol.py`, which is the test that would catch a divergence
this alias cannot.
"""

from __future__ import annotations

# `ty` resolves imports against the INSTALLED environment, which pins one major
# at a time, so the branch that is not installed is always unresolvable here.
# That is the nature of a version shim, not a mistake; the runtime `ImportError`
# handler is the real check, and `tests/test_mcp_protocol.py` exercises both.
#
# Only the 2.x lines carry a suppression because uv.lock pins 1.x for dev and
# CI. Working against an mcp 2.x environment inverts this: ty will flag the 1.x
# lines instead and call these suppressions unused. Flip them, do not delete
# them, and do not "fix" it by dropping a branch.
try:  # mcp >= 2
    from mcp.server import MCPServer as Server  # ty: ignore[unresolved-import]
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server

__all__ = ["Server", "ToolError", "sdk_major"]


def sdk_major() -> int:
    """Major version of the installed `mcp` SDK, for tests that must branch.

    Read from the class's module path rather than package metadata: what
    matters is which class this module actually bound, and a metadata lookup
    could disagree with it in a broken environment.
    """
    return 2 if Server.__module__.startswith("mcp.server.mcpserver") else 1


def _load_tool_error() -> type[Exception]:
    try:  # mcp >= 2
        from mcp.server.mcpserver.exceptions import (  # ty: ignore[unresolved-import]
            ToolError as _ToolError,
        )
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp.exceptions import ToolError as _ToolError
    return _ToolError


# Resolved eagerly so an import error surfaces here, next to the explanation,
# rather than inside whichever test first reaches for it.
ToolError: type[Exception] = _load_tool_error()
