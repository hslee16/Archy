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

**Supporting both is deliberate, and cheaper than it looks.** Of the four API
surfaces archy uses -- the constructor, `.tool()`, `.prompt()`, and `.run()` --
three take the same arguments in both majors, verified signature by signature.
So the choice was never "port to 2.x or stay on 1.x"; it was "alias one name, or
force every user to move in lockstep with archy". A user whose agent host pins
mcp 1.x should not need a new archy, and vice versa.

The constructor is the one exception, which is why `make_server` exists below:
2.x takes `version=`, 1.x does not, and the difference is visible on the wire.

What this file does NOT promise is that the two majors behave identically on
the wire. Signature compatibility is not serialization compatibility, and
archy's documented contract (an `outputSchema` derived per tool,
`structuredContent` alongside the text block, zero content blocks for an empty
result) is a claim about the SDK's behavior, not its call shape. That is
verified against BOTH majors by an actual stdio handshake in
`tests/test_mcp_protocol.py`, which is the test that would catch a divergence
this alias cannot.

archy:owns        make_server, sdk_major
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

__all__ = ["Server", "ToolError", "make_server", "sdk_major"]


def make_server(name: str, version: str) -> Server:
    """Construct the server so it reports OUR version, on either major.

    Left to itself the handshake advertises something that is not archy: on
    mcp 1.x `FastMCP` takes no `version`, and the wrapped low-level server
    falls back to reporting the SDK's own version (a client asking archy what
    it is was told `1.28.1`); on 2.x the same omission yields an empty string.
    Both are wrong, and they are wrong differently, which is worse -- a client
    cannot even parse the mistake consistently (#462).

    2.x accepts `version=` on the constructor. 1.x does not, so the value goes
    onto the low-level server it wraps; that attribute is private because
    `FastMCP` exposes no setter for it, not because writing it is unsupported.
    `tests/test_mcp_protocol.py` asserts the result over a real handshake on
    both majors, which is what would catch either path silently breaking.
    """
    if sdk_major() >= 2:
        # Same suppression convention as the imports above: uv.lock pins 1.x,
        # so `ty` sees `FastMCP`, which has no `version`. The guard above is
        # what makes this reachable only on 2.x. Working against a 2.x
        # environment inverts it; flip the suppression, do not delete it.
        return Server(name, version=version)  # ty: ignore[unknown-argument]
    # `_mcp_server` is private because `FastMCP` exposes no setter, not because
    # the attribute is off limits: its own constructor takes `version` and
    # passes it straight here.
    server = Server(name)
    server._mcp_server.version = version
    return server


def sdk_major() -> int:
    """Major version of the installed `mcp` SDK, for tests that must branch.

    Read from the class's module path rather than package metadata: what
    matters is which class this module actually bound, and a metadata lookup
    could disagree with it in a broken environment.
    """
    return 2 if Server.__module__.startswith("mcp.server.mcpserver") else 1


# Same bare try/except shape as `Server` above, rather than a function called
# once on the next line: one spelling of "resolve a compat symbol" in this file
# is easier to extend to a third SDK generation than two. Resolving at module
# scope also surfaces an import error here, next to the explanation, rather
# than inside whichever test first reaches for it.
try:  # mcp >= 2
    from mcp.server.mcpserver.exceptions import (  # ty: ignore[unresolved-import]
        ToolError as ToolError,
    )
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError as ToolError
