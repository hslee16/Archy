"""MCP server exposing archy's analysis as tools an AI agent can call.

Built on the official Python `mcp` SDK's ergonomic server class, imported via
`archy.mcp_compat` so both SDK majors work (`FastMCP` on 1.x, `MCPServer` on
2.x; see #391). The registered tools cover archy's analysis surface
(`archy_score`, `archy_cycles`, `archy_check`, `archy_impact`,
`archy_snapshot`, `archy_diff`, `archy_graph`, `archy_what_to_refactor_next`,
`archy_dsm`, `archy_simulate`, `archy_duplicates`, `archy_conventions`,
`archy_module_view`, `archy_brief`) so an agent
can treat archy as a structural sensor in its own feedback loop, the way
the README pitches. Several tools carry a mode/lens/param switch that
absorbs what used to be a separate tool: `archy_impact(mode='affected')`
(was `archy_affected`), `archy_graph(focus=...)` (was `archy_graph_focus`)
and `archy_graph(response_format='summary')` (was `archy_graph_summary`),
`archy_what_to_refactor_next(lens=...)` (was `archy_hotspots` /
`archy_high_risk_modules`), `archy_score(record=True)` (was
`archy_record_baseline`), `archy_score(view='history')` (was
`archy_trend`), and `archy_check(contracts=True)` (was `archy_contracts`).
See docs/LEARNINGS.md for the v0.36/v0.41 consolidations. The removed
`archy_status` freshness check now lives in the CLI as
`archy index status` (#267).

The server runs over stdio (the MCP convention for local tools); start
it from the CLI via `archy mcp`.

Tool returns are pydantic models; the SDK serializes them to JSON for
the MCP client. The model shapes are the public wire contract for any
agent calling these tools.

## Structured output (2025-06-18 spec)

The SDK derives an `outputSchema` (JSON Schema) from each tool's return
annotation and attaches it to the `tools/list` entry, so every archy tool
declares its result shape up front. That derivation is validation mode,
which omits `@computed_field`. `create_server` rewrites each schema in
serialization mode after registration so the advertised shape matches
`model_dump()`. Each `tools/call` then returns BOTH a
machine-readable `structuredContent` (the JSON object, validated against
that schema) AND a `TextContent` block carrying the same JSON serialized,
which clients may prefer for token-efficient agent UX. No per-tool opt-in
is needed.

Two wrapping rules worth knowing:

- A tool whose return is a bare sequence (`list[Cycle]` for `archy_cycles`,
  `list[TrendRow]` for `archy_score(view='history')`) is wrapped under a
  top-level `result` key, because `structuredContent` MUST be a JSON object,
  not an array.
- A tool whose return is a union (`DiffReport | DiffErrorPayload`,
  `GraphPayload | GraphTooLargePayload`, `DSM | DSMDiff | DSMErrorPayload`)
  is likewise wrapped under `result`, with the union expressed as the
  schema's `anyOf`; every branch (including the `*ErrorPayload` ones) is a
  conforming member, so an in-band error result still validates.

One benign edge: an *empty* sequence return yields `structuredContent`
`{"result": []}` with no accompanying `TextContent` block (the SDK emits one
content block per element). The structured form is unambiguous, so this is
not a correctness gap. tests/test_mcp.py locks the whole contract.

## Error model

archy maps failures onto MCP's two error mechanisms (2025-06-18 server spec)
with one convention, so an agent has a single recovery contract:

1. **Protocol error (JSON-RPC, SDK-handled):** unknown tool, or a missing
   / mistyped required argument. The framework rejects the call; archy does
   nothing.
2. **Usage error -> `isError:true` (raise):** an invalid argument *value* (e.g.
   `response_format='xml'`, `last_n=0`, `min_risk>1`, `strict_tolerance` outside
   `[0, 1]`), a project `path` that is
   not an existing directory (`_project_dir`, same rule as the CLI's
   `click.Path(exists=True, file_okay=False)`), a *malformed* `archy.yaml`
   (`LayerConfigError` from `load_config`), or a project over the scan ceiling
   (`ScanTooLargeError`). The caller must fix the call or the environment; a
   raised exception becomes an `isError:true` tool result the model sees.
3. **Recoverable / advisory -> in-band result (`isError:false`):** an expected
   precondition that isn't met but the agent can recover from, or a valid but
   degraded result. These are *normal* results the agent branches on, not
   errors. Two shapes, by "is there a usable result?":
   - **Union variant** when there is no success result to return: no baseline
     (`DiffErrorPayload`), output too large (`GraphTooLargePayload` /
     `DSMTooLargePayload`), no `archy.yaml` found (`CheckErrorPayload`), no DSM
     snapshot (`DSMErrorPayload`). Each carries an `error` (or
     `error`+`*_count`) field and is a conforming `anyOf` member of the tool's
     `outputSchema`.
   - **Advisory field** on an otherwise-valid payload: contracts extra not
     installed (`ContractsPayload.available=False`), project not under git
     (`WhatToRefactorPayload.git_available` / its lens-aware `note`), or an
     honest-null empty result with a `note`.

The de-facto marker an agent can key on: a tier-3 "no usable result" variant is
a payload with an `error` field and no success data. tests/test_mcp.py asserts
tier-2 conditions surface as `isError` and tier-3 conditions return in-band.

archy:owns        create_server
"""

from __future__ import annotations

import types

from pydantic import ConfigDict, TypeAdapter, create_model

from archy import __version__
from archy.graph import ScanTooLargeError
from archy.layers import LayerConfigError
from archy.mcp_compat import Server, ToolError, make_server
from archy.mcp_payloads import (
    CheckErrorPayload,
    CheckPayload,
    DiffErrorPayload,
    DSMErrorPayload,
    DSMTooLargePayload,
    GraphPayload,
    GraphSummaryPayload,
    GraphTooLargePayload,
)
from archy.mcp_prompts import _register_prompts
from archy.mcp_tools_check import (
    register_archy_brief,
    register_archy_check,
    register_archy_cycles,
    register_archy_diff,
    register_archy_score,
    register_archy_simulate,
    register_archy_snapshot,
)
from archy.mcp_tools_conventions import register_archy_conventions, register_archy_module_view
from archy.mcp_tools_git import register_archy_duplicates, register_archy_what_to_refactor_next
from archy.mcp_tools_graph import register_archy_dsm, register_archy_graph, register_archy_impact


def _unknown_arguments_message(unknown: list[str]) -> str:
    """One-line refusal naming every unexpected tool argument (#662)."""
    if len(unknown) == 1:
        return f"Unknown argument {unknown[0]!r}"
    return "Unknown arguments: " + ", ".join(repr(name) for name in unknown)


def _forbid_unknown_tool_arguments(server: Server) -> None:
    """Reject unknown tool kwargs and advertise ``additionalProperties: false`` (#662).

    FastMCP's generated argument models ignore extras, so a misspelled parameter
    was answered as if it had never been sent. Rebuild each tool's arg model with
    ``extra='forbid'``, rewrite the advertised ``inputSchema``, and refuse unknown
    keys with a one-line ``ValueError`` that ``Tool.run`` surfaces as ``ToolError``
    on both mcp majors.
    """

    for tool in server._tool_manager.list_tools():
        meta = tool.fn_metadata
        old_model = meta.arg_model
        strict_model = create_model(
            old_model.__name__,
            __base__=old_model,
            __config__=ConfigDict(arbitrary_types_allowed=True, extra="forbid"),
        )
        meta.arg_model = strict_model

        schema = strict_model.model_json_schema(by_alias=True)
        schema["additionalProperties"] = False
        tool.parameters.clear()
        tool.parameters.update(schema)

        allowed = set(strict_model.model_fields)
        for info in strict_model.model_fields.values():
            if info.alias:
                allowed.add(info.alias)

        original = type(meta).call_fn_with_arg_validation

        async def call_fn_with_arg_validation(
            self,
            fn,
            fn_is_async,
            arguments_to_validate,
            arguments_to_pass_directly,
            *,
            _allowed=allowed,
            _original=original,
        ):
            unknown = sorted(name for name in arguments_to_validate if name not in _allowed)
            if unknown:
                raise ValueError(_unknown_arguments_message(unknown))
            return await _original(
                self,
                fn,
                fn_is_async,
                arguments_to_validate,
                arguments_to_pass_directly,
            )

        object.__setattr__(
            meta,
            "call_fn_with_arg_validation",
            types.MethodType(call_fn_with_arg_validation, meta),
        )


def _advertise_serialization_output_schemas(server: Server) -> None:
    """The SDK builds that schema with no `mode`, which is validation mode and
    omits `@computed_field`. The payload a client receives is `model_dump()`,
    which includes those fields.
    """
    for tool in server._tool_manager.list_tools():
        metadata = tool.fn_metadata
        model = metadata.output_model
        if model is None or metadata.output_schema is None:
            continue
        metadata.output_schema = TypeAdapter(model).json_schema(mode="serialization")
        # cached_property. A prior read would keep the validation-mode schema.
        tool.__dict__.pop("output_schema", None)


def create_server() -> Server:
    # `make_server`, not `Server(...)`: the constructor is the one API surface
    # the two SDK majors disagree about, and getting it wrong makes the
    # handshake advertise the SDK's version or nothing at all (#462).
    server: Server = make_server("archy", __version__)
    _register_tools(server)
    _register_prompts(server)
    _advertise_serialization_output_schemas(server)
    _forbid_unknown_tool_arguments(server)
    return server


def _with_expected_refusal_as_tool_error(fn):
    """Re-raise expected refusals as ToolError so mcp 2.x keeps the reason (#636).

    mcp 2.x drops the message of any exception a tool raises, surfacing only
    ``Error executing tool <name>``. Raising ``ToolError`` directly keeps the
    text on both majors. Unexpected exceptions stay generic.

    `LayerConfigError` and `ScanTooLargeError` are expected refusals too, and the
    two an agent is most likely to hit on a real repository (a malformed
    `archy.yaml`, a tree over `max_modules`). They subclass bare `Exception`, not
    `ValueError`, so they are named here rather than caught by accident; making
    them `ValueError`s would also change every `except ValueError` elsewhere (#793).
    """

    from functools import wraps

    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, LookupError, LayerConfigError, ScanTooLargeError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapped


def _register_tools(server: Server) -> None:
    # One registration wrapper so every tool converts expected ValueError /
    # LookupError refusals to ToolError without editing each body (#636).
    _decorate_tool = server.tool

    def tool(**kwargs):
        register = _decorate_tool(**kwargs)

        def decorate(fn):
            return register(_with_expected_refusal_as_tool_error(fn))

        return decorate

    server.tool = tool  # ty: ignore[invalid-assignment]

    register_archy_score(server)
    register_archy_cycles(server)
    register_archy_check(server)
    register_archy_impact(server)
    register_archy_snapshot(server)
    register_archy_diff(server)
    register_archy_simulate(server)
    register_archy_graph(server)
    register_archy_what_to_refactor_next(server)
    register_archy_dsm(server)
    register_archy_duplicates(server)
    register_archy_conventions(server)
    register_archy_module_view(server)
    register_archy_brief(server)

    # removed v0.41 (#267): archy_status demoted off the agent surface. Index
    # freshness is diagnostic plumbing, not a mid-task decision (every tool
    # syncs on demand, so a result is never stale); the capability now lives in
    # the CLI as `archy index status`.


# The payload types stay importable from here: callers and tests read the wire
# contract through `archy.mcp`.
__all__ = [
    "CheckErrorPayload",
    "CheckPayload",
    "DSMErrorPayload",
    "DSMTooLargePayload",
    "DiffErrorPayload",
    "GraphPayload",
    "GraphSummaryPayload",
    "GraphTooLargePayload",
    "_advertise_serialization_output_schemas",
    "create_server",
]
