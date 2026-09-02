"""Real stdio-protocol conformance for `archy mcp`, run against the installed SDK.

Every other MCP test in this suite calls the `_run_*` helpers directly, which
proves the payloads are right but never starts a server or speaks the protocol.
That gap is why `archy.mcp_compat` needs this file: aliasing `FastMCP` to
`MCPServer` makes both majors *import*, and signature comparison shows they take
the same arguments, but neither fact proves they put the same bytes on the wire.

archy's documented contract is a claim about SDK behavior, not call shape:

- every tool advertises an `outputSchema` derived from its return type
- results carry `structuredContent` alongside the text block
- read-only annotations survive to `tools/list`

So this drives an actual client over stdio: initialize, list, call. It passes on
whichever major is installed, which is what makes it the check that catches a
2.x divergence the alias cannot -- run it under both to clear a ceiling bump.

Kept out of `test_mcp.py` deliberately: these spawn a subprocess and take
seconds rather than milliseconds, and they fail for a different reason (the
transport, not the payload).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from archy.mcp_compat import sdk_major

mcp_client = pytest.importorskip("mcp.client.stdio", reason="stdio client not available")
from mcp import ClientSession, StdioServerParameters  # noqa: E402


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    """A tree with a `required:` rule that is violated, so a call has real output."""
    root = tmp_path_factory.mktemp("proto")
    app = root / "app"
    (app / "commands").mkdir(parents=True)
    (app / "core").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "core" / "__init__.py").write_text("")
    (app / "core" / "model_registry.py").write_text("REGISTRY = {}\n")
    (app / "commands" / "__init__.py").write_text("")
    (app / "commands" / "setup_user.py").write_text("x = 1\n")
    (root / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n  - source: 'app.commands.*'\n    must_reach: app.core.model_registry\n"
        "    reason: standalone entrypoints need the full mapper registry\n"
    )
    return root


# Spawn the server with the interpreter RUNNING THIS TEST, never the `archy` on
# PATH. `command="archy"` was the first cut and it silently tested the wrong
# software: this machine had a global uv-tool archy 0.41.0 first on PATH, which
# predates `required:` entirely, and 5 of these 6 tests passed against it anyway
# because they assert older behavior. A protocol test that can green-light a
# stale binary is worse than no protocol test. `-c` rather than `-m` because
# archy ships no `__main__.py`; this mirrors `cli.mcp` exactly.
_SERVE = "from archy.mcp import create_server; create_server().run()"


async def _session(fn):
    params = StdioServerParameters(command=sys.executable, args=["-c", _SERVE])
    async with (
        mcp_client.stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await fn(session)


def _run(fn):
    return asyncio.run(asyncio.wait_for(_session(fn), timeout=60))


def _wire(model) -> dict:
    """The result as it goes over the wire, not as Python spells it.

    mcp 2.0 renamed every client-side model attribute to snake_case
    (`isError` -> `is_error`, `structuredContent` -> `structured_content`,
    `outputSchema` -> `output_schema`) while keeping the serialization aliases
    identical. So attribute access breaks across the major and the PROTOCOL does
    not, which is exactly the distinction this file exists to test. Dumping by
    alias asserts the thing agents actually parse, and makes these tests read the
    same under both SDKs.
    """
    return model.model_dump(by_alias=True)


def test_server_completes_the_handshake_and_lists_tools():
    """The failure #391 shipped: on mcp 2.0 the server died before this point."""
    tools = _wire(_run(lambda s: s.list_tools()))
    names = {t["name"] for t in tools["tools"]}

    assert "archy_check" in names
    assert len(names) == 13, f"tool count changed: {sorted(names)}"


def test_every_tool_advertises_an_output_schema():
    """Structured output (#228) is derived by the SDK, so a major bump can drop it."""
    tools = _wire(_run(lambda s: s.list_tools()))

    missing = [t["name"] for t in tools["tools"] if not t.get("outputSchema")]
    assert missing == [], f"tools with no outputSchema on mcp {sdk_major()}.x: {missing}"


def test_read_only_annotations_survive_to_the_wire():
    """Without these a client cannot auto-approve archy's reads (#225)."""
    tools = _wire(_run(lambda s: s.list_tools()))
    check = next(t for t in tools["tools"] if t["name"] == "archy_check")

    assert check["annotations"] is not None
    assert check["annotations"]["readOnlyHint"] is True
    assert check["annotations"]["destructiveHint"] is False
    assert check["title"], "human-facing title missing"


def test_calling_a_tool_returns_structured_content(project: Path):
    """The end-to-end claim: an agent calling archy_check gets a usable verdict."""
    result = _wire(_run(lambda s: s.call_tool("archy_check", {"path": str(project)})))

    assert result["isError"] is False
    assert result["structuredContent"] is not None

    # `archy_check` returns `CheckPayload | CheckErrorPayload`, and the SDK wraps
    # a union return under a top-level `result` key. That wrapping is part of the
    # documented wire contract (module docstring), so assert it here rather than
    # reaching past it: if a major bump stopped wrapping, agents parsing this
    # would break and every in-process test would still pass.
    payload = result["structuredContent"]["result"]
    assert payload["passed"] is False

    # The reason has to reach the agent, not just the exit code (#387).
    [violation] = payload["required_violations"]
    assert violation["module"] == "app.commands.setup_user"
    assert "does not transitively reach" in violation["detail"]
    assert violation["rule"]["reason"].startswith("standalone entrypoints")


def test_a_missing_config_is_in_band_not_an_error(tmp_path_factory):
    """Tier 3 of the documented error model (#229), over the wire.

    A recoverable precondition returns `isError:false` with an `error` field, so
    the agent can create a config instead of treating archy as broken. Pinned at
    the protocol level because the distinction between tiers is only observable
    here: in-process, both look like a returned object.
    """
    empty = tmp_path_factory.mktemp("noconfig")
    (empty / "pkg").mkdir()
    (empty / "pkg" / "__init__.py").write_text("")

    result = _wire(_run(lambda s: s.call_tool("archy_check", {"path": str(empty)})))

    assert result["isError"] is False
    assert "archy.yaml" in result["structuredContent"]["result"]["error"]


def test_a_malformed_config_is_a_protocol_error(tmp_path_factory):
    """Tier 2: a broken config cannot be checked against, so it raises."""
    bad = tmp_path_factory.mktemp("badconfig")
    (bad / "pkg").mkdir()
    (bad / "pkg" / "__init__.py").write_text("")
    (bad / "archy.yaml").write_text("layers: [not a mapping\n")

    result = _wire(_run(lambda s: s.call_tool("archy_check", {"path": str(bad)})))

    assert result["isError"] is True
