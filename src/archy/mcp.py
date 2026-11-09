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
from pathlib import Path

from mcp.types import ToolAnnotations
from pydantic import ConfigDict, TypeAdapter, create_model

from archy import __version__
from archy.affected import DEFAULT_DEPTH, Affected
from archy.conventions import (
    ConventionsReport,
    ModuleView,
)
from archy.cycles import Cycle, CycleScan
from archy.diff import (
    DiffReport,
)
from archy.dsm import (
    DSM,
    DSMDiff,
    DSMSummary,
)
from archy.duplicates import (
    DEFAULT_MIN_SIZE,
)
from archy.graph import ScanTooLargeError
from archy.impact import DEFAULT_MAX_CHAINS, Impact
from archy.layers import LayerConfigError
from archy.mcp_compat import Server, ToolError, make_server
from archy.mcp_payloads import (
    BriefPayload,
    CheckErrorPayload,
    CheckPayload,
    DiffErrorPayload,
    DSMErrorPayload,
    DSMTooLargePayload,
    DuplicatesPayload,
    GraphPayload,
    GraphSummaryPayload,
    GraphTooLargePayload,
    ScorePayload,
    SnapshotPayload,
    TrendRow,
    WhatToRefactorPayload,
)
from archy.mcp_runs import (
    DEFAULT_MAX_DSM_CELLS,
    contracts_verdict,
    resolve_against,
    run_affected,
    run_brief,
    run_check,
    run_contracts,
    run_conventions,
    run_cycles,
    run_diff,
    run_dsm,
    run_duplicates,
    run_graph,
    run_impact,
    run_module_view,
    run_score,
    run_simulate,
    run_snapshot,
    run_trend,
    run_what_to_refactor_next,
    validate_impact_mode,
)
from archy.refactor import DEFAULT_MIN_RISK
from archy.simulate import EdgeSpec, SimulateReport

_AGENT_LOOP_PROMPT = """\
# archy agent loop

archy turns the project's structural health into a number you can act on
between edits. A persistent parse cache, kept warm by a background file
watcher, makes calls fast (warm graph builds are a few seconds even on
10k+ module repos). Using archy around edits is optional; the step whose
verdict can block a commit is the diff, so run it at verification points:
before you commit, at the end of a task, and whenever an edit adds or removes
a cross-package import. You never need to
worry about staleness: every tool re-syncs the changed files on demand, so a
result always reflects the current code (the CLI `archy index status` reports
the cache state if you ever want to confirm it out of band). The loop is:

1. **Snapshot** at session start so you have a baseline:
   `archy_snapshot(path)`

   Read `invariant_brief` in the result first: the declared layers and
   the forbidden edges between them, whether the graph is currently
   acyclic, the baseline score per axis, and the load-bearing modules
   (highest `edit_risk`, treat as high blast radius). Knowing these up
   front lets you avoid proposing a cross-layer or cycle-introducing
   edit in the first place, instead of discovering it in step 4's diff.
2. **Look up impact** before editing a module so you know who breaks if
   the change is wrong:
   `archy_impact(path, files=[<file you plan to edit>])`

   For a bounded, bidirectional neighborhood with edge line numbers,
   use `archy_graph(path, focus=[<file or qualname>])` instead.
   `archy_graph(path)` gives a top-N overview (summary by default) when
   you don't yet know which module to look at; pass
   `response_format='full'` only when you actually need the whole node/
   edge dump. Before a non-trivial edit, call
   `archy_what_to_refactor_next(path, lens='structural')` to see whether
   your target sits in the project's central-and-fragile zone (high blast
   radius combined with high instability); if it does, scope down or pause
   for review.

   If the edit changes imports (adds or removes a dependency), call
   `archy_simulate(path, add=[{from, to}], remove=[...])` first: it
   returns the would-be cycles, layer violations, and score delta with
   no file written, so you can abandon or reshape a plan that introduces
   a cycle before touching code instead of catching it in step 4.
3. **Edit** the code as you normally would.
4. **Diff** after the edit to see what got better, what got worse, and
   exactly which cycles or layer rules changed:
   `archy_diff(path)`
5. Read `summary.headline` first - one structured sentence
   ("overall +X; driven by Y; cycles +A/-B"). Then walk
   `summary.top_regressions` in order; each item carries a `risk` score
   (0-1, from `compute_edit_risk`) so the most central/fragile breakage
   surfaces first. The raw `score_delta`, `cycles`, `violations`, and
   `sdp_violations` blocks remain available for when you need the full
   list, but the summary is the right starting point: it's the same
   ranking you would have done by hand. If the headline shows
   `overall -...` or any `top_regressions` exist, inspect the named
   modules, fix or revert, then loop back to step 4. Recurse until the
   diff is clean (empty `top_regressions`).

`archy_score(path, strict=True)` is a one-shot gate against the last
recorded run; it's lighter than the snapshot/diff loop and useful as
the final pre-commit check.

`archy_dsm(path, ...)` returns the Design Structure Matrix: a row/col
matrix the agent reads positionally, not a scalar. Use it when you
need *where*, not *how much*: orienting in a new repo
(`group_by='community'`), localizing a cycle to specific back-edges
(`group_by='topological'`), or inspecting cross-layer traffic
(`group_by='layer', weight='calls'`). It is summary-by-default (block
structure, counts, and the back-edges, without the full cell list);
pass `response_format='full'` for the complete matrix. Narrow large
projects with `focus=<qualname>` or `package=<prefix>`. Passing
`baseline_path` to a previously saved DSM JSON returns a structured
diff whose `new_back_edges` field flags cycles the most recent edit
just introduced.
"""


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


def _register_prompts(server: Server) -> None:
    @server.prompt(
        name="loop",
        description=(
            "How to use archy as an architectural feedback loop while editing code. "
            "Read this at session start so subsequent tool calls follow the right "
            "snapshot -> edit -> diff cadence."
        ),
    )
    def loop() -> str:
        return _AGENT_LOOP_PROMPT


_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
"""archy's tools are structural analysis: they compute over a project, are
closed-domain (no network/external world), and are idempotent for a fixed source
tree. Declaring this explicitly lets trusted clients auto-approve archy's calls
instead of prompting on every read (MCP tool-annotations, 2025-03-26 spec).
`archy_score` writes `.archy/history.jsonl` only when `record=True`, and keeps
this annotation so the common read-only call does not prompt; see
`_SNAPSHOT_ANNOTATIONS` for the tool that always writes."""

_SNAPSHOT_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
"""`archy_snapshot` always overwrites `.archy/baseline.json`. It used to claim
read-only on the grounds that the file is only a cache, which stopped being
true once a baseline could gate (`diff --strict`, #521): an auto-approved
re-snapshot is exactly how a new cycle gets grandfathered. The strict gate
refuses that file for the same reason; this annotation stops a client from
running the overwrite without asking."""


def _project_dir(path: str) -> Path:
    """Return `path` when it is an existing directory.

    Same rule as the CLI's `click.Path(exists=True, file_okay=False)`. MCP
    tools take a bare string. `open_index` then does
    `db_path.parent.mkdir(parents=True)`, which creates a typo path and
    `.archy/index.db` and the empty tree scores 1.0 (#552).
    """
    root = Path(path)
    if root.is_dir():
        return root
    if root.is_file():
        raise ValueError(f"Directory '{path}' is a file.")
    raise ValueError(f"Directory '{path}' does not exist.")


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

    @server.tool(
        name="archy_score",
        title="Score project structure",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Compute the composite quality score (modularity, acyclicity, depth, "
            "equality, complexity - geometric mean of five axes) for a Python "
            "project. `view='current'` (the DEFAULT) returns the five-axis score "
            "payload; optionally append the result to .archy/history.jsonl "
            "(record=True) and/or compare against the most recent recorded run as "
            "a regression gate (strict=True). `strict_tolerance` must be in "
            "[0, 1] (default 0.02). Pass record=True to record a "
            "baseline at session start (replaces the removed archy_record_baseline). "
            "`view='history'` instead reads the recent score history "
            "(.archy/history.jsonl), returning up to `last_n` rows oldest-first so "
            "you can compare deltas over time (replaces the removed archy_trend); "
            "last_n must be >= 1, and record/strict/strict_tolerance do not apply."
        ),
    )
    def archy_score(
        path: str,
        view: str = "current",
        internal_only: bool = True,
        record: bool = False,
        strict: bool = False,
        strict_tolerance: float = 0.02,
        last_n: int = 10,
    ) -> ScorePayload | list[TrendRow]:
        root = _project_dir(path)
        if view == "history":
            return run_trend(root, last_n=last_n)
        if view != "current":
            raise ValueError(f"view must be 'current' or 'history'; got {view!r}")
        return run_score(
            root,
            internal_only=internal_only,
            record=record,
            strict=strict,
            strict_tolerance=strict_tolerance,
        )

    @server.tool(
        name="archy_cycles",
        title="Find import cycles",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Find import cycles (Tarjan SCCs of size >= min_size, plus self-loops) "
            "in a Python project. Returns cycles sorted largest-first. Each cycle's "
            "`type_checking_edges` counts edges that exist only under "
            "`if TYPE_CHECKING:` (they never run); when it is non-zero, pass "
            "`exclude_type_checking=true` to leave them out. With it on the result "
            "is an object, `{cycles, excluded_type_checking_edges}`, so a clean "
            "result that is clean only because of the exclusion says so. Unset, it "
            "follows archy.yaml's `exclude_type_checking_imports`, else off."
        ),
    )
    def archy_cycles(
        path: str,
        min_size: int = 2,
        internal_only: bool = True,
        exclude_type_checking: bool | None = None,
    ) -> list[Cycle] | CycleScan:
        return run_cycles(
            _project_dir(path),
            min_size=min_size,
            internal_only=internal_only,
            exclude_type_checking=exclude_type_checking,
        )

    @server.tool(
        name="archy_check",
        title="Check layer boundaries",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "**Call after any Python edit that adds, removes, or changes an "
            "import statement.** Returns forbidden direct edges between layers "
            "declared in archy.yaml under `violations`, plus Stable Dependencies "
            "Principle violations (when `sdp.enabled: true` in archy.yaml) under "
            "`sdp_violations`. Empty lists on both mean no direct boundary "
            "crossings. `required_violations` reports the INVERSE constraint: "
            "`required:` rules in archy.yaml declare that every module matching "
            "`source` must TRANSITIVELY reach `must_reach` (e.g. every standalone "
            "entrypoint must reach a model registry that has to be imported "
            "before the framework configures itself). Reach counts indirect "
            "paths and the implicit package-`__init__` import, so satisfying "
            "such a rule once in a package `__init__.py` covers its submodules; "
            "each violation carries a `detail` saying which module fails and "
            "why, and a rule whose patterns match nothing is reported as a "
            "violation rather than passing silently. Fix by adding the missing "
            "import, not by deleting the rule. Pass `contracts=True` to ALSO run the transitive "
            "(multi-hop) import-linter contracts (Layers, Forbidden, "
            "Independence, Protected, AcyclicSiblings) - stricter than the direct "
            "archy.yaml edges - nested under the `contracts` field; a failed "
            "contract means an indirect import path violates the architecture, so "
            "restructure rather than weaken the rule. That path reads .importlinter "
            "(or pyproject.toml, override with `contracts_config_path`, relative to `path`) "
            "and needs "
            "`pip install archy[contracts]`; if the extra is absent, `contracts` "
            "comes back with `available=false` and an advisory rather than "
            "raising (replaces the removed archy_contracts tool). If no archy.yaml "
            "is found, returns an in-band CheckErrorPayload (an `error` field, not "
            "a raised error) so you can create one or pass `config_path` (relative to `path`), and "
            "contracts are not run in that case; a malformed archy.yaml instead "
            "raises (it cannot be checked against)."
        ),
    )
    def archy_check(
        path: str,
        config_path: str | None = None,
        contracts: bool = False,
        contracts_config_path: str | None = None,
    ) -> CheckPayload | CheckErrorPayload:
        root = _project_dir(path)
        result = run_check(
            root,
            config_path=resolve_against(root, [Path(config_path)])[0] if config_path else None,
        )
        if contracts and isinstance(result, CheckPayload):
            payload = run_contracts(
                root,
                config_filename=(
                    resolve_against(root, [Path(contracts_config_path)])[0]
                    if contracts_config_path
                    else None
                ),
            )
            # Only a verdict counts as "checked". Requesting contracts and having
            # import-linter turn out to be missing leaves the rules exactly as
            # unverified as not asking, so reporting transitive_checked=True
            # there would reintroduce the bug this field exists to close (#343).
            # A contract that could not have failed is not a verdict either, so
            # it leaves the rules as unverified as a missing import-linter does
            # (#457). Same rule as the CLI's `_transitive_checked`, so the two
            # surfaces cannot disagree about whether a run was checked.
            checked, unverifiable_reason = contracts_verdict(payload)
            # A stale reason is worse than none. `run_check` computed its reason
            # before contracts were attempted, so it names contracts=True -- and
            # replaying that at a caller who just passed contracts=True and
            # watched it fail sends them round a loop they have been through.
            # A non-None reason from `run_check` is also the signal that there
            # WAS something to verify, so it gates the no-verdict branch.
            if unverifiable_reason:
                # Unlike the no-verdict case this does not depend on there
                # having been a reason already: contracts ran and answered
                # nothing, which is worth saying however well-governed the
                # direct edges are.
                reason = unverifiable_reason
            elif checked or result.transitive_unverified_reason is None:
                reason = None
            else:
                reason = (
                    "the `forbid` rules are still unverified: archy_check(contracts=True) "
                    f"produced no transitive verdict ({payload.error})."
                )
            result = result.model_copy(
                update={
                    "contracts": payload,
                    "transitive_checked": checked,
                    "transitive_unverified_reason": reason,
                }
            )
        return result

    # removed v0.41 (#266): archy_trend folded into archy_score(view="history").
    # removed v0.41 (#268): archy_contracts folded into
    # archy_check(contracts=True), nesting the transitive import-linter results
    # under the CheckPayload.contracts field.

    @server.tool(
        name="archy_impact",
        title="Compute change blast radius",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Given a list of changed file paths, return what they affect. "
            "Relative `files` are tried under `path` first; missing paths are "
            "retried at its git work-tree root and used if they exist there, inside `path`. "
            "`mode='blast'` (the DEFAULT) returns the internal modules that "
            "transitively import any of them (the blast radius): use before "
            "refactoring or removing a module to see what would break. "
            "An empty `impacted` means no STATIC importer was found, not that "
            "removal is safe: archy cannot see importlib.import_module / "
            "__import__, entry points and plugin registries, Django "
            "INSTALLED_APPS, pytest plugins, config-driven dispatch, module "
            "__getattr__ hooks or string-keyed factories, so `rg` for those "
            "before deleting (`empty_caveat` is set whenever this applies). "
            "`propagation_cost` is the MacCormack-style scalar (fraction of "
            "internal modules the edit set can reach); `chains` explains *why* "
            "each impacted module is reachable (the shortest import path back to "
            "a changed module, hop by hop with the line numbers where each "
            "import lives) so you can cite the specific edge(s) to preserve. "
            "Chains are ranked closest-first and capped at `max_chains` "
            "(negative for all); `chains_omitted` reports how many were left "
            "out. `mode='affected'` (replaces the removed archy_affected) is the "
            "CI-shaped lookup instead: it returns the impact pre-classified into "
            "`impacted_tests` and `impacted_modules`, with traversal depth-capped "
            "(`depth`, default 5) so a single-line edit doesn't fan out to "
            "thousands of nodes; an empty `impacted_tests` carries the same "
            "static-only `empty_caveat` (no test imports it statically, not "
            "that no test exercises it). Test detection uses pytest conventions "
            "(test_*.py, *_test.py, files under tests/) unless `test_filter` "
            "overrides with a recursive glob; a `test_filter` that matches no "
            "file in the graph is an error, not an empty `impacted_tests`, and "
            "ends with a `did you mean` when a directory is close. Files that "
            "resolve to no module "
            "are returned in `unresolved`, except a `.py` path that is missing "
            "inside the project, which is `deleted` with `deleted_reason` "
            "(the scan sees only the current tree, so its dependents are not "
            "selected). Internal modules only. "
            "`co_change=true` (blast mode only, opt-in) adds a `co_changed` list: "
            "modules that historically co-change with the edited set in git but "
            "have NO import/call edge to it, so the structural blast radius above "
            "misses them (the behavioral complement, `archy coupling` scoped to "
            "your edit - 'you changed X; historically Y changes with it, check "
            "Y'). Source-only and best-effort (empty without git); the only path "
            "that makes this tool read git history."
        ),
    )
    def archy_impact(
        path: str,
        files: list[str],
        mode: str = "blast",
        max_chains: int = DEFAULT_MAX_CHAINS,
        depth: int = DEFAULT_DEPTH,
        test_filter: str | None = None,
        co_change: bool = False,
    ) -> Impact | Affected:
        root = _project_dir(path)
        validate_impact_mode(mode)
        resolved = [Path(f) for f in files]
        if mode == "affected":
            return run_affected(
                root,
                files=resolved,
                depth=depth,
                test_filter=test_filter,
            )
        return run_impact(root, files=resolved, max_chains=max_chains, co_change=co_change)

    # removed v0.36 (#227): archy_affected folded into archy_impact(mode='affected').

    @server.tool(
        name="archy_snapshot",
        title="Snapshot baseline",
        annotations=_SNAPSHOT_ANNOTATIONS,
        description=(
            "Capture score, cycles, and layer violations to .archy/baseline.json "
            "as a baseline that archy_diff will compare against. Call at the "
            "start of an editing session. Also returns `invariant_brief`: the "
            "constraints to know before your first edit (declared layers, "
            "forbidden inter-layer edges, whether the graph is acyclic, the "
            "baseline score per axis, and the top load-bearing / highest-risk "
            "modules). Read it up front to avoid proposing a cross-layer or "
            "cycle-introducing edit, rather than being told after the diff. "
            "See the `loop` prompt for full usage."
        ),
    )
    def archy_snapshot(path: str) -> SnapshotPayload:
        return run_snapshot(_project_dir(path))

    @server.tool(
        name="archy_diff",
        title="Diff against baseline",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Compare the current project state to the last snapshot. Returns "
            "a risk-weighted `summary` (headline + top regressions / "
            "improvements), per-component score deltas, and the cycles and "
            "layer violations that have been added or resolved since the "
            "baseline. Use after edits to localize regressions; see the `loop` "
            "prompt. `strict=True` with `baseline` (a committed file, relative "
            "to `path`) adds `strict`. `passed` is false when a cycle or layer "
            "violation is new, or when archy.yaml removes or narrows a rule the "
            "baseline fingerprint records. `reason` names it. A baseline with no "
            "fingerprint still checks cycles and violations."
        ),
    )
    def archy_diff(
        path: str, baseline: str | None = None, strict: bool = False
    ) -> DiffReport | DiffErrorPayload:
        return run_diff(_project_dir(path), baseline=baseline, strict=strict)

    @server.tool(
        name="archy_simulate",
        title="Simulate import-edge change",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Counterfactual pre-edit check: given a proposed import-edge delta "
            "(`add` / `remove` lists of {from, to} module-or-path pairs), return "
            "the structural consequence BEFORE any file is written -- new/resolved "
            "cycles, new back-edges, new layer/SDP violations, per-axis score "
            "delta, and the propagation_cost change -- with a risk-ranked "
            "`summary` phrased conditionally ('would form a cycle'). Use it to "
            "test a refactoring hypothesis and reshape the plan before editing. "
            "Endpoints matching no internal module are returned in "
            "`applied.unresolved` with a `did you mean` in "
            "`applied.unresolved_hints`; a spec that could not be applied is "
            "listed in `applied.skipped`, and `applied.coverage` is `none` when "
            "no requested edge was simulated (the zero deltas then describe an "
            "unchanged graph, not a free edit) or `partial` when some were. "
            "Caveat: it models the graph delta you "
            "describe, not arbitrary code edits (it cannot move the complexity "
            "axis), and one submodule import (`a.b.c`) also implies edges to its "
            "ancestor packages -- include them in `add` to model it exactly (a "
            "lone submodule edge is a lower bound; see "
            "docs/research/SIMULATE_ORACLE_EMPIRICS.md)."
        ),
    )
    def archy_simulate(
        path: str,
        add: list[EdgeSpec] | None = None,
        remove: list[EdgeSpec] | None = None,
    ) -> SimulateReport:
        return run_simulate(_project_dir(path), add=add or [], remove=remove or [])

    # removed v0.36 (#227): archy_record_baseline folded into archy_score(record=True).

    # removed v0.36 (#227): archy_graph_summary folded into
    # archy_graph(response_format='summary').

    # removed v0.36 (#227): archy_graph_focus folded into archy_graph(focus=[...]).

    @server.tool(
        name="archy_graph",
        title="Inspect dependency graph",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Inspect the dependency graph. With no `focus`, "
            "`response_format='summary'` (the DEFAULT, replaces the removed "
            "archy_graph_summary) returns a compact top-N overview (modules by "
            "fan-in, fan-out, PageRank, and edit-risk, plus top external deps; "
            "`top_n` controls N) so a routine call doesn't dump the whole graph "
            "into context; `response_format='full'` returns "
            "the complete node/edge dump matching `archy graph --format json`, "
            "but refuses graphs larger than `max_nodes` (default 500, must be "
            ">= 1) with a GraphTooLargePayload (bump it explicitly, or narrow "
            "with `focus`). Pass `focus=[<qualname or path>]` (replaces the "
            "removed archy_graph_focus) for a bounded subgraph centered on those "
            "modules: `depth` caps hop distance and `direction` is 'in' (who "
            "depends on me), 'out' (my dependencies), or 'both'; each edge "
            "carries the source line numbers of the import statements. With "
            "`focus` set, `response_format`/`max_nodes`/`top_n` do not apply (the "
            "neighborhood is already bounded)."
        ),
    )
    def archy_graph(
        path: str,
        response_format: str = "summary",
        focus: list[str] | None = None,
        depth: int = 2,
        direction: str = "both",
        internal_only: bool = True,
        max_nodes: int = 500,
        top_n: int = 20,
    ) -> GraphSummaryPayload | GraphPayload | GraphTooLargePayload:
        return run_graph(
            _project_dir(path),
            response_format=response_format,
            focus=focus,
            depth=depth,
            direction=direction,
            internal_only=internal_only,
            max_nodes=max_nodes,
            top_n=top_n,
        )

    # removed v0.36 (#227): archy_high_risk_modules folded into
    # archy_what_to_refactor_next(lens='structural').

    # removed v0.36 (#227): archy_hotspots folded into
    # archy_what_to_refactor_next(lens='behavioral').

    @server.tool(
        name="archy_what_to_refactor_next",
        title="Rank refactor priorities",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Ranked refactor-priority list (replaces the removed archy_hotspots "
            "and archy_high_risk_modules via `lens`). `lens='fused'` (the "
            "DEFAULT) merges the behavioral lens (cyclomatic complexity x git "
            "churn) and the structural lens (the edit-risk composite: central "
            "and fragile) into one summed priority, so a module flagged by both "
            "generally outranks a single-lens one. `lens='behavioral'` ranks "
            "CC x churn hotspots only (needs git; answers 'where is the "
            "refactoring leverage?'); `lens='structural'` ranks the edit-risk "
            "composite only (git-free; answers 'is this edit dangerous?'). Each "
            "entry says which lenses fired and carries a one-line `rationale`. "
            f"`min_risk` (default {DEFAULT_MIN_RISK}) is the structural floor; "
            "pass 0 to surface every module on the structural lens. `since` "
            "restricts the behavioral (churn) lens to commits after a date "
            "(e.g. 2024-01-01, 2 weeks ago) or a git ref (tag/branch/SHA); a "
            "value that is neither is refused. An empty "
            "`priorities` with a `note` is a real answer: there is genuinely "
            "nothing to prioritize."
        ),
    )
    def archy_what_to_refactor_next(
        path: str,
        lens: str = "fused",
        top_n: int = 10,
        since: str | None = None,
        min_risk: float = DEFAULT_MIN_RISK,
    ) -> WhatToRefactorPayload:
        return run_what_to_refactor_next(
            _project_dir(path), lens=lens, top_n=top_n, since=since, min_risk=min_risk
        )

    @server.tool(
        name="archy_dsm",
        title="Design Structure Matrix",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Design Structure Matrix view of the import graph. "
            "`response_format='summary'` (the DEFAULT) returns a compact "
            "overview -- block structure (group labels + sizes), counts, the "
            "back-edges (source later than target in the ordering = the cycle "
            "signal), and inter-block coupling -- without the full cell list, "
            "so a routine call stays small. `response_format='full'` returns "
            "the full matrix the agent reads positionally: cell (row=source, "
            "col=target) is non-empty when source imports target; it refuses "
            f"matrices with more than {DEFAULT_MAX_DSM_CELLS} cells with a "
            "DSMTooLargePayload (narrow with `focus`/`package`, or read the "
            "summary). Use `group_by='community'` for block-diagonal cohesion, "
            "`group_by='layer'` for layer-violation forensics, or "
            "`group_by='topological'` to localize back-edges. Narrow large "
            "projects with `focus` (qualname + focus_depth-hop neighborhood) or "
            "`package` (qualname prefix). When `baseline_path` (relative to `path`) is provided, "
            "returns a DSMDiff regardless of response_format (`new_back_edges` "
            "flags cycles the edit just introduced)."
        ),
    )
    def archy_dsm(
        path: str,
        response_format: str = "summary",
        group_by: str = "community",
        weight: str = "imports",
        focus: str | None = None,
        focus_depth: int = 1,
        package: str | None = None,
        baseline_path: str | None = None,
    ) -> DSMSummary | DSM | DSMDiff | DSMTooLargePayload | DSMErrorPayload:
        return run_dsm(
            _project_dir(path),
            response_format=response_format,
            group_by=group_by,
            weight=weight,
            focus=focus,
            focus_depth=focus_depth,
            package=package,
            baseline_path=baseline_path,
        )

    @server.tool(
        name="archy_duplicates",
        title="Duplicate-function clusters",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "Cluster functions with an identical normalized body shape "
            "(identifiers/literals folded to placeholders). Returns two tiers: "
            "`duplicates` (the primary 'likely duplicate' list to investigate) "
            "and `variants` (demoted clusters that are likely intentional: "
            "same-class siblings, boilerplate, copies wholly in test suites or "
            "vendoring/isolation dirs, and - with `co_change` (the DEFAULT) - "
            "`independent` copies whose files are actively maintained yet never "
            "co-change in git, i.e. deliberately parallel implementations; each "
            "carries a `variant_reason`). Within the "
            "primary tier, `exact=true` marks byte-identical (Type-1) clusters, "
            "the highest-confidence 'definitely refactor these' subset. "
            f"`min_nodes` (default {DEFAULT_MIN_SIZE}) is the minimum normalized "
            "AST-node count, skipping trivial getters/stubs. This is an advisory "
            "SURFACER, never a score axis: refactorability is a semantic call the "
            "reader (you) makes, so a cluster means 'investigate,' not 'provably "
            "identical' (the co-change demotion lifts primary precision above the "
            "~50% syntactic ceiling by moving benign parallel copies to variants). "
            "`near_miss=true` (opt-in, slower) adds a `near_miss` tier: Type-3 "
            "(gapped) clones - a copy with statements inserted/removed that the "
            "exact shape-hash cannot see - found by token-multiset overlap, each "
            "with a `similarity`. LOWER confidence than the shape tiers; use it "
            "for recall (the exact hash finds ~0% of Type-3). "
            "`response_format='summary'` (the DEFAULT) returns each cluster's "
            "ranking fields plus one sample member; `response_format='full'` "
            "returns the complete member lists. `co_change=false` skips the git "
            "pass. An empty `duplicates` with a `note` is a real answer."
        ),
    )
    def archy_duplicates(
        path: str,
        response_format: str = "summary",
        min_nodes: int = DEFAULT_MIN_SIZE,
        top_n: int = 20,
        members: int = 2,
        co_change: bool = True,
        near_miss: bool = False,
    ) -> DuplicatesPayload:
        return run_duplicates(
            _project_dir(path),
            response_format=response_format,
            min_nodes=min_nodes,
            top_n=top_n,
            members=members,
            co_change=co_change,
            near_miss=near_miss,
        )

    @server.tool(
        name="archy_conventions",
        title="Report the project's house style",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "**Call before adding a new class, field, or command to an "
            "unfamiliar project.** Derives the repository's own conventions "
            "from its source instead of leaving you to infer them by reading. "
            "Returns five censuses: `naming` (class-name suffix families "
            "GROUPED BY HOME MODULE, so the module you are about to edit "
            "shows its own patterns - the answer to 'what do I call a new one "
            "and where does it go'), `surfaces` (mirrored helper sets like "
            "`_x_to_text`/`_x_to_json` and names defined in several modules - "
            "the answer to 'how many places must I wire a new field'), "
            "`gates` (sites where a FAILED FINDING exits non-zero, each with "
            "its literal exit `code` and the lever that controls it: "
            "`flag:--strict`, `config:<attr>`, `param:<name>`, or "
            "`hardcoded` - the answer to 'should my new finding fail the "
            "build or only warn'), `errors` (the separate, usually much "
            "larger set of USER-ERROR exits - bad config, missing file - "
            "which say nothing about gating and are kept apart so the gate "
            "count stays meaningful), `models` (base-class, frozen, and "
            "tuple-vs-list census over the value types), `bases` (kind "
            "families grouped by what they derive from TRANSITIVELY, "
            "counting only bases this project defines - a family reached "
            "through an intermediate class is reported under the base that "
            "names it, and suffix agreement is stated so a family whose name "
            "is not the rule says so), `registries` (`NAME = Ctor(...)` "
            "families with the distribution of every keyword passed - how a "
            "project declares codes, messages, or flags when it does not use "
            "classes), `export_gaps` (family members absent from an "
            "`__all__`, or from a `from .x import Y as Y` block, that "
            "already lists their siblings) and `doc_gaps` (members the "
            "project's own `.rst`/`.md` surface does not name, where it "
            "already documents most of the family). Advisory only; it "
            "never fails and mutates nothing. `min_family` raises the floor "
            "for reporting a family (default 2, raise it on a large project "
            "to cut noise). `include_tests` censuses test modules too "
            "(default false: test fixtures dilute a real family). This "
            "report is RANKED and truncated, so absence from it proves "
            "nothing; for a negative (`does risk import hotspots`) call "
            "`archy_module_view` instead."
        ),
    )
    def archy_conventions(
        path: str, min_family: int = 2, include_tests: bool = False
    ) -> ConventionsReport:
        return run_conventions(
            _project_dir(path), min_family=min_family, include_tests=include_tests
        )

    @server.tool(
        name="archy_module_view",
        title="Everything known about ONE module, complete and unranked",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "**Call when the question is a NEGATIVE.** `archy_conventions` "
            "ranks and truncates, so a module's absence from it means "
            "nothing; every list here is COMPLETE for the one `module` "
            "named, so absence IS the answer, about STATIC edges only: an "
            "empty `imported_by` does not rule out importlib.import_module / "
            "__import__, entry points, plugin registries, INSTALLED_APPS, "
            "pytest plugins or config-driven dispatch, so `rg` for those "
            "before calling a module dead. Use it for `does risk import "
            "hotspots`, `does anything still reach layers`, `is this module "
            "dead`. Returns `imports_internal` (the project modules this one "
            "imports, with relative imports resolved), `imported_by` (every "
            "project module that imports it, tests included even when they "
            "are otherwise set aside, because a module imported only by "
            "tests IS imported), `classes`, `functions`, `exports` (null "
            "when the module declares no `__all__` and no explicit "
            "re-exports, which is different from an empty one), "
            "`suffix_families`, `gates` (its exit sites) and `status` - "
            "which says whether the module was censused or set aside and "
            "why, so an empty result is never mistaken for `nothing to "
            "report`. Raises on an unknown module name rather than "
            "returning an empty view. Advisory: mutates nothing."
        ),
    )
    def archy_module_view(path: str, module: str, include_tests: bool = False) -> ModuleView:
        return run_module_view(_project_dir(path), module, include_tests=include_tests)

    @server.tool(
        name="archy_brief",
        title="One bounded briefing before touching an unfamiliar repository",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "**Call once, before starting on an unfamiliar repository.** "
            "Composes `archy_conventions` (naming, mirrored surfaces, gates, "
            "export and doc gaps), the layer `coverage` of `archy.yaml` and, "
            "with `contracts=True`, the transitive import-linter verdict, into "
            "one answer to 'what do I need to know before I touch this'. "
            "`top_n` (default 8) bounds the ranked sections and `conventions."
            "totals` says how many were dropped; `gates`, `errors`, "
            "`export_gaps` and `doc_gaps` are never truncated. `coverage` is "
            "null when there is no usable archy.yaml. This tool does NOT run "
            "the direct-edge check, so `transitive_checked=false` with a "
            "`transitive_unverified_reason` means the `forbid` rules are "
            "unverified, not that they hold; pass `contracts=True` to settle "
            "it. It is ranked and truncated like `archy_conventions`, so for a "
            "negative call `archy_module_view`. Advisory: mutates nothing."
        ),
    )
    def archy_brief(
        path: str,
        top_n: int = 8,
        contracts: bool = False,
        include_tests: bool = False,
    ) -> BriefPayload:
        return run_brief(
            _project_dir(path), top_n=top_n, with_contracts=contracts, include_tests=include_tests
        )

    # removed v0.41 (#267): archy_status demoted off the agent surface. Index
    # freshness is diagnostic plumbing, not a mid-task decision (every tool
    # syncs on demand, so a result is never stale); the capability now lives in
    # the CLI as `archy index status`.
