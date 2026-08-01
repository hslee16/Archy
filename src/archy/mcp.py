"""MCP server exposing archy's analysis as tools an AI agent can call.

Built on the official Python `mcp` SDK using its FastMCP API. The 11
tools cover archy's analysis surface (`archy_score`, `archy_cycles`,
`archy_check`, `archy_impact`,
`archy_snapshot`, `archy_diff`, `archy_simulate`, `archy_graph`,
`archy_what_to_refactor_next`, `archy_dsm`,
`archy_duplicates`) so an agent
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

Tool returns are pydantic models; FastMCP serializes them to JSON for
the MCP client. The model shapes are the public wire contract for any
agent calling these tools.

## Structured output (2025-06-18 spec)

FastMCP derives an `outputSchema` (JSON Schema) from each tool's return
annotation and attaches it to the `tools/list` entry, so every archy tool
declares its result shape up front. Each `tools/call` then returns BOTH a
machine-readable `structuredContent` (the JSON object, validated against
that schema) AND a `TextContent` block carrying the same JSON serialized,
which clients may prefer for token-efficient agent UX. archy gets this for
free from the SDK; no per-tool opt-in is needed.

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
`{"result": []}` with no accompanying `TextContent` block (FastMCP emits one
content block per element). The structured form is unambiguous, so this is
not a correctness gap. tests/test_mcp.py locks the whole contract.

## Error model

archy maps failures onto MCP's two error mechanisms (2025-06-18 server spec)
with one convention, so an agent has a single recovery contract:

1. **Protocol error (JSON-RPC, FastMCP-handled):** unknown tool, or a missing
   / mistyped required argument. The framework rejects the call; archy does
   nothing.
2. **Usage error -> `isError:true` (raise):** an invalid argument *value* (e.g.
   `response_format='xml'`, `last_n=0`, `min_risk>1`), a *malformed* `archy.yaml`
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
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import cast

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict

from archy.affected import DEFAULT_DEPTH, Affected, find_affected
from archy.contracts import ContractCheck
from archy.coupling import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_SUPPORT,
    co_changed_with,
    git_cochange,
    internal_module_paths,
)
from archy.cycles import Cycle, find_cycles
from archy.diff import (
    DiffReport,
    compute_diff,
    read_snapshot,
    take_snapshot,
    write_snapshot,
)
from archy.diff_summary import summarize_diff
from archy.dsm import (
    DSM,
    DSMDiff,
    DSMSummary,
    GroupBy,
    Weight,
    build_dsm,
    diff_dsm,
    read_dsm,
    summarize_dsm,
)
from archy.duplicates import (
    DEFAULT_MIN_SIZE,
    DuplicateGroup,
    classify_variants,
    compute_duplicates,
    compute_near_duplicates,
    demote_independent,
    is_test_path,
)
from archy.graph import (
    DEFAULT_IGNORED_DIRS,
    ScanTooLargeError,
    build_graph,
    discover_modules,
    effective_max_modules,
    graph_to_dict,
    parse_project,
    resolve_modules,
)
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.hotspots import git_churn
from archy.impact import DEFAULT_MAX_CHAINS, Impact, find_impact
from archy.instability import compute_instability
from archy.layers import (
    LayerCoverage,
    ReachViolation,
    SdpViolation,
    Violation,
    compute_coverage,
    discover_config,
    find_reach_violations,
    find_sdp_violations,
    find_violations,
    load_config,
)
from archy.reach import compute_propagation_cost
from archy.refactor import DEFAULT_MIN_RISK, compute_refactor_priorities
from archy.risk import compute_edit_risk
from archy.score import Score, ScoreInputs, compute_score
from archy.simulate import EdgeSpec, SimulateReport, find_simulate
from archy.watcher import IndexManager

_AGENT_LOOP_PROMPT = """\
# archy agent loop

archy turns the project's structural health into a number you can act on
between edits. A persistent parse cache, kept warm by a background file
watcher, makes every call cheap (warm graph builds are a few seconds even on
10k+ module repos), so consult archy on *each* edit to keep your working
surface relevant, not just at the start and end of a task. You never need to
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


# --- response models ----------------------------------------------------------


class ScoreComponents(BaseModel):
    model_config = ConfigDict(frozen=True)

    modularity: float
    acyclicity: float
    depth: float
    equality: float
    complexity: float


def _score_components(score: Score) -> ScoreComponents:
    """Project a Score's five axes into the MCP ScoreComponents shape."""
    return ScoreComponents(
        modularity=score.modularity,
        acyclicity=score.acyclicity,
        depth=score.depth,
        equality=score.equality,
        complexity=score.complexity,
    )


class ScoreGate(BaseModel):
    model_config = ConfigDict(frozen=True)

    previous: float | None
    previous_commit: str | None = None
    previous_timestamp: str | None = None
    current: float
    delta: float | None
    tolerance: float
    passed: bool


class ScorePayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: float
    components: ScoreComponents
    inputs: ScoreInputs
    gate: ScoreGate | None = None


class ContractsPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    available: bool
    error: str | None = None
    all_kept: bool | None = None
    kept: int | None = None
    broken: int | None = None
    module_count: int | None = None
    import_count: int | None = None
    contracts: tuple[ContractCheck, ...] = ()


class CheckPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_path: str
    violations: tuple[Violation, ...]
    sdp_violations: tuple[SdpViolation, ...] = ()
    # Unsatisfied `required:` rules: a module that must transitively reach
    # another and does not. Each carries its own `detail`, because the agent
    # receiving this cannot see the config and "passed=false" with an empty
    # `violations` list is indistinguishable from a bug in archy.
    required_violations: tuple[ReachViolation, ...] = ()
    passed: bool
    # How much of the declared roots the rules actually reach. Present on a PASS
    # too, deliberately: `passed=True` is exactly when an agent needs to know
    # whether the config could have said anything (#362). Without it, a config
    # governing 14% of edges is indistinguishable from a clean codebase.
    coverage: LayerCoverage | None = None
    # Why the gate failed, on the wire. `passed=false` with an empty
    # `violations` list is not actionable on its own, and an agent correcting
    # its own output needs the reason, not just the verdict.
    presence_fails: bool = False
    min_layers_present: int | None = None
    # Nested only when archy_check(contracts=True) additionally runs
    # import-linter (#268): the transitive contract results that used to be the
    # separate archy_contracts tool. None when contracts were not requested.
    contracts: ContractsPayload | None = None


class CheckErrorPayload(BaseModel):
    """Tier-3 in-band advisory for archy_check: no `archy.yaml` was found.

    A recoverable precondition (the agent can create a config or pass
    config_path), not a usage error -- so it returns in-band (`isError:false`)
    like DiffErrorPayload / DSMErrorPayload rather than raising. A *malformed*
    config is different: that raises (tier-2 `isError:true`) because the config
    is broken and the agent cannot recover by retrying. See the module
    docstring's "Error model" section.
    """

    model_config = ConfigDict(frozen=True)

    error: str


class BriefLayer(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    patterns: tuple[str, ...]


class ForbiddenEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_layer: str
    to_layer: str


class RequiredReach(BaseModel):
    """A declared `required:` rule, as stated in archy.yaml.

    `reason` is carried because the rule is a runtime fact the graph cannot
    explain: "commands.** must reach the model registry" is enforceable, but
    only the author can say it is about SQLAlchemy mapper configuration. An
    agent told the rule without the reason will satisfy it by the cheapest edge
    it can find, which may not be the one the constraint was about.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    must_reach: str
    reason: str = ""


class LoadBearingModule(BaseModel):
    model_config = ConfigDict(frozen=True)

    module: str
    edit_risk: float


class InvariantBrief(BaseModel):
    """Constraints to hand a stateless agent up front, before its first edit.

    A recombination of data the snapshot already computes, surfaced at
    session start so the agent is *told the rules in advance* (prevention)
    instead of only after a check fails (correction): the declared layers
    and the forbidden edges between them, whether the graph is currently
    acyclic, the baseline score per axis, and the top load-bearing modules
    (highest `edit_risk`) to treat as high-blast-radius. No new analysis.
    """

    model_config = ConfigDict(frozen=True)

    layers: tuple[BriefLayer, ...]
    forbidden_edges: tuple[ForbiddenEdge, ...]
    # Declared required-reach rules. Prevention is the whole point of this
    # object, and this is the one constraint class an agent cannot infer by
    # reading the code: a missing import looks like nothing at all.
    required_reach: tuple[RequiredReach, ...] = ()
    acyclic: bool
    overall: float
    components: ScoreComponents
    load_bearing: tuple[LoadBearingModule, ...]


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: Score
    cycles: tuple[Cycle, ...]
    violations: tuple[Violation, ...]
    sdp_violations: tuple[SdpViolation, ...] = ()
    required_violations: tuple[ReachViolation, ...] = ()
    baseline_path: str
    invariant_brief: InvariantBrief


class DiffErrorPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: str


class TrendRowScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: float
    modularity: float
    acyclicity: float
    depth: float
    equality: float
    # Optional: rows written by archy < 0.20 don't have a complexity axis.
    complexity: float | None = None


class TrendRowInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    module_count: int
    edge_count: int
    cycle_count: int
    tangle_ratio: float
    max_depth: int
    community_count: int


class TrendRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: str
    commit: str | None
    branch: str | None
    score: TrendRowScore
    inputs: TrendRowInputs


class GraphNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    external: bool
    path: str | None = None
    is_package: bool | None = None
    instability: float | None = None
    propagation_cost: float | None = None
    edit_risk: float | None = None


class GraphEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    is_relative: bool
    lines: tuple[int, ...]


class GraphPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: str | None
    parse_errors: tuple[str, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    unresolved: tuple[str, ...] = ()


class GraphTooLargePayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: str
    node_count: int
    max_nodes: int


class GraphSummaryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    module: str
    value: float
    instability: float | None = None
    propagation_cost: float | None = None
    edit_risk: float | None = None


class GraphSummaryPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    module_count: int
    internal_edge_count: int
    external_edge_count: int
    parse_errors: tuple[str, ...]
    top_fan_in: tuple[GraphSummaryEntry, ...]
    top_fan_out: tuple[GraphSummaryEntry, ...]
    top_pagerank: tuple[GraphSummaryEntry, ...]
    top_edit_risk: tuple[GraphSummaryEntry, ...]
    external_deps: tuple[GraphSummaryEntry, ...]


class RefactorPriorityEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    module: str
    path: str | None
    lenses: tuple[str, ...]
    priority: float
    cc_sum: int
    churn: int
    hotspot_score: int
    edit_risk: float
    propagation_cost: float
    instability: float
    fan_in: int
    rationale: str


class WhatToRefactorPayload(BaseModel):
    """Fused refactor-priority ranking (CC x churn hotspots + edit-risk).

    `total` is the full candidate count; `shown` is the returned slice. An
    empty `priorities` with a `note` is a meaningful answer: nothing is both
    complex and churned and nothing is central+fragile above `min_risk`, so
    there is genuinely nothing to prioritize. `git_available` is False when
    the project is not under git - the behavioral lens is then skipped and the
    ranking is structural-only."""

    model_config = ConfigDict(frozen=True)

    since: str | None
    min_risk: float
    git_available: bool
    total: int
    shown: int
    priorities: tuple[RefactorPriorityEntry, ...]
    note: str | None = None


class DSMErrorPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: str


# Cell-count ceiling for a full-mode DSM dump. Mirrors archy_graph's
# max_nodes guard: above this, the full matrix is refused with a pointer to
# the concise summary / focus / package escape hatches rather than dumping
# thousands of cells into the agent's context.
DEFAULT_MAX_DSM_CELLS = 2000


class DSMTooLargePayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    error: str
    cell_count: int
    max_cells: int


class DuplicateGroupSummary(BaseModel):
    """Concise (response_format='summary') form of a duplicate cluster: the
    ranking fields plus one sample member, without the full member list."""

    model_config = ConfigDict(frozen=True)

    shape_hash: str
    size: int
    member_count: int
    redundancy: int
    category: str
    variant_reason: str | None
    exact: bool
    similarity: float | None
    sample: str


class DuplicatesPayload(BaseModel):
    """Two-tier duplicate-cluster result (issue #133/#242), plus an optional
    Type-3 tier (#246).

    `duplicates` is the primary "likely duplicate" tier (investigate these);
    `variants` is the demoted "likely-intentional" tier (same-class siblings,
    boilerplate, and test/vendored/independent copies - see `variant_reason`).
    `near_miss` is the opt-in (`co_change` unrelated; `near_miss=True`) Type-3
    tier: gapped clones found by token overlap, each with a `similarity`, LOWER
    confidence. `total` / `variant_total` / `near_total` count each tier; `shown`
    is how many of the primary tier are returned (capped by `top_n`). An empty
    `duplicates` with a `note` is a real answer. Advisory only, never a score
    axis; refactorability is a semantic call (see the tool description).
    """

    model_config = ConfigDict(frozen=True)

    min_nodes: int
    total: int
    exact_total: int
    variant_total: int
    near_total: int
    shown: int
    duplicated_functions: int
    duplicates: tuple[DuplicateGroupSummary | DuplicateGroup, ...]
    variants: tuple[DuplicateGroupSummary | DuplicateGroup, ...]
    near_miss: tuple[DuplicateGroupSummary | DuplicateGroup, ...]
    note: str | None


def create_server() -> FastMCP:
    server: FastMCP = FastMCP("archy")
    _register_tools(server)
    _register_prompts(server)
    return server


def _register_prompts(server: FastMCP) -> None:
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
"""All 11 archy tools are read-only structural analysis: they compute over a
project and mutate nothing observable on the wire, are closed-domain (no
network/external world), and are idempotent for a fixed source tree. Declaring
this explicitly lets trusted clients auto-approve archy's calls instead of
prompting on every read (MCP tool-annotations, 2025-03-26 spec). The tools that
write a dotfile under .archy/ (snapshot, and score when record=True) are still
read-only *for the model's purposes*: the file is a cache/baseline the next
call reads, not an external side effect, and re-running them is idempotent."""


def _register_tools(server: FastMCP) -> None:
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
            "a regression gate (strict=True). Pass record=True to record a "
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
        if view == "history":
            return _run_trend(Path(path), last_n=last_n)
        if view != "current":
            raise ValueError(f"view must be 'current' or 'history'; got {view!r}")
        return _run_score(
            Path(path),
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
            "in a Python project. Returns cycles sorted largest-first."
        ),
    )
    def archy_cycles(
        path: str,
        min_size: int = 2,
        internal_only: bool = True,
    ) -> list[Cycle]:
        return _run_cycles(Path(path), min_size=min_size, internal_only=internal_only)

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
            "(or pyproject.toml, override with `contracts_config_path`) and needs "
            "`pip install archy[contracts]`; if the extra is absent, `contracts` "
            "comes back with `available=false` and an advisory rather than "
            "raising (replaces the removed archy_contracts tool). If no archy.yaml "
            "is found, returns an in-band CheckErrorPayload (an `error` field, not "
            "a raised error) so you can create one or pass `config_path`, and "
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
        result = _run_check(Path(path), config_path=Path(config_path) if config_path else None)
        if contracts and isinstance(result, CheckPayload):
            result = result.model_copy(
                update={
                    "contracts": _run_contracts(
                        Path(path),
                        config_filename=(
                            Path(contracts_config_path) if contracts_config_path else None
                        ),
                    )
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
            "`mode='blast'` (the DEFAULT) returns the internal modules that "
            "transitively import any of them (the blast radius): use before "
            "refactoring or removing a module to see what would break. "
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
            "thousands of nodes. Test detection uses pytest conventions "
            "(test_*.py, *_test.py, files under tests/) unless `test_filter` "
            "overrides with a recursive glob. Files that resolve to no module "
            "are returned in `unresolved`; internal modules only. "
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
        _validate_impact_mode(mode)
        resolved = [Path(f) for f in files]
        if mode == "affected":
            return _run_affected(
                Path(path),
                files=resolved,
                depth=depth,
                test_filter=test_filter,
            )
        return _run_impact(Path(path), files=resolved, max_chains=max_chains, co_change=co_change)

    # removed v0.36 (#227): archy_affected folded into archy_impact(mode='affected').

    @server.tool(
        name="archy_snapshot",
        title="Snapshot baseline",
        annotations=_READ_ONLY_ANNOTATIONS,
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
        return _run_snapshot(Path(path))

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
            "prompt."
        ),
    )
    def archy_diff(path: str) -> DiffReport | DiffErrorPayload:
        return _run_diff(Path(path))

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
            "`applied.unresolved`. Caveat: it models the graph delta you "
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
        return _run_simulate(Path(path), add=add or [], remove=remove or [])

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
        return _run_graph(
            Path(path),
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
            "pass 0 to surface every module on the structural lens. An empty "
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
        return _run_what_to_refactor_next(
            Path(path), lens=lens, top_n=top_n, since=since, min_risk=min_risk
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
            "`package` (qualname prefix). When `baseline_path` is provided, "
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
        return _run_dsm(
            Path(path),
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
        return _run_duplicates(
            Path(path),
            response_format=response_format,
            min_nodes=min_nodes,
            top_n=top_n,
            members=members,
            co_change=co_change,
            near_miss=near_miss,
        )

    # removed v0.41 (#267): archy_status demoted off the agent surface. Index
    # freshness is diagnostic plumbing, not a mid-task decision (every tool
    # syncs on demand, so a result is never stale); the capability now lives in
    # the CLI as `archy index status`.


# --- thin internals ----------------------------------------------------------


def _run_score(
    path: Path,
    *,
    internal_only: bool,
    record: bool,
    strict: bool,
    strict_tolerance: float,
) -> ScorePayload:
    graph = _load_graph(path, internal_only=internal_only)
    score = compute_score(graph)
    history_path = path / ".archy" / "history.jsonl"

    gate: ScoreGate | None = None
    if strict:
        rows = read_history(history_path)
        if rows:
            previous = rows[-1]
            delta = score.overall - previous.overall
            gate = ScoreGate(
                previous=previous.overall,
                previous_commit=previous.commit,
                previous_timestamp=previous.timestamp,
                current=score.overall,
                delta=delta,
                tolerance=strict_tolerance,
                passed=delta >= -strict_tolerance,
            )
        else:
            gate = ScoreGate(
                previous=None,
                current=score.overall,
                delta=None,
                tolerance=strict_tolerance,
                passed=True,
            )

    if record:
        commit, branch = git_metadata(path)
        append_history(history_path, row_from_score(score, commit=commit, branch=branch))

    return ScorePayload(
        overall=score.overall,
        components=_score_components(score),
        inputs=score.inputs,
        gate=gate,
    )


def _run_cycles(path: Path, *, min_size: int, internal_only: bool) -> list[Cycle]:
    graph = _load_graph(path, internal_only=internal_only)
    return list(find_cycles(graph, min_size=min_size))


def _run_check(path: Path, *, config_path: Path | None) -> CheckPayload | CheckErrorPayload:
    if config_path is None:
        discovered = discover_config(path)
        if discovered is None:
            # Tier-3 recoverable precondition: no config to check against. In-band
            # (isError:false) so the agent can branch and create/point at a config,
            # not a raise. A *malformed* config below still raises (tier 2).
            return CheckErrorPayload(
                error=f"no archy.yaml found near {path}; pass config_path or create one."
            )
        config_path = discovered
    config = load_config(config_path)
    graph = _build_graph(
        path,
        ignored_dirs=DEFAULT_IGNORED_DIRS | frozenset(config.exclude),
        extra_roots=config.roots,
        max_modules=effective_max_modules(config.max_modules),
    )
    violations = find_violations(graph, config)
    reach_violations = find_reach_violations(graph, config)
    sdp_violations: list[SdpViolation] = []
    if config.sdp.enabled:
        sdp_violations = find_sdp_violations(graph, tolerance=config.sdp.tolerance)
    sdp_fails_gate = bool(sdp_violations) and config.sdp.mode == "error"
    coverage = compute_coverage(graph, config)
    # The presence floor gates `passed` here exactly as it gates the CLI's exit
    # code. Wiring it into one surface and not the other was the first cut, and
    # it left the AGENT-FACING surface reporting passed=true on the degenerate
    # single-module solution the check exists to catch: the case an agent in a
    # course-correction loop most needs to be told about.
    presence_fails = (
        config.min_layers_present is not None
        and coverage.layers_present < config.min_layers_present
    )
    return CheckPayload(
        config_path=str(config_path),
        violations=tuple(violations),
        sdp_violations=tuple(sdp_violations),
        required_violations=tuple(reach_violations),
        passed=(
            not violations and not reach_violations and not sdp_fails_gate and not presence_fails
        ),
        coverage=coverage,
        presence_fails=presence_fails,
        min_layers_present=config.min_layers_present,
    )


def _run_contracts(path: Path, *, config_filename: Path | None) -> ContractsPayload:
    from archy.contracts import (
        ContractsConfigError,
        ContractsNotAvailable,
        run_contracts,
    )

    try:
        result = run_contracts(path, config_filename=config_filename)
    except ContractsNotAvailable as exc:
        return ContractsPayload(available=False, error=str(exc))
    except ContractsConfigError as exc:
        return ContractsPayload(available=True, error=str(exc))

    return ContractsPayload(
        available=True,
        all_kept=result.all_kept,
        kept=result.kept,
        broken=result.broken,
        module_count=result.module_count,
        import_count=result.import_count,
        contracts=result.contracts,
    )


_BRIEF_LOAD_BEARING_N = 5


def _build_invariant_brief(
    graph, config_path: Path | None, score: Score, cycles: tuple[Cycle, ...]
) -> InvariantBrief:
    """Recombine the snapshot's own data into the session-start brief.

    Layers/forbidden edges come from the same archy.yaml `take_snapshot`
    already loaded (so a malformed config has failed before reaching here);
    load-bearing modules are the top `edit_risk` nodes, the same ranking
    `archy_what_to_refactor_next(lens='structural')` reports.
    """
    layers: tuple[BriefLayer, ...] = ()
    forbidden: tuple[ForbiddenEdge, ...] = ()
    required: tuple[RequiredReach, ...] = ()
    if config_path is not None:
        config = load_config(config_path)
        layers = tuple(
            BriefLayer(name=layer.name, patterns=layer.patterns) for layer in config.layers
        )
        forbidden = tuple(
            ForbiddenEdge(from_layer=rule.from_layer, to_layer=rule.to_layer)
            for rule in config.forbid
        )
        required = tuple(
            RequiredReach(source=rule.source, must_reach=rule.must_reach, reason=rule.reason)
            for rule in config.required
        )

    ranked = sorted(compute_edit_risk(graph).items(), key=lambda t: (-t[1], t[0]))
    load_bearing = tuple(
        LoadBearingModule(module=name, edit_risk=risk)
        for name, risk in ranked[:_BRIEF_LOAD_BEARING_N]
    )

    return InvariantBrief(
        layers=layers,
        forbidden_edges=forbidden,
        required_reach=required,
        acyclic=not cycles,
        overall=score.overall,
        components=_score_components(score),
        load_bearing=load_bearing,
    )


def _run_snapshot(path: Path) -> SnapshotPayload:
    graph, full = _load_graph_pair(path)
    config_path = discover_config(path)
    snap = take_snapshot(graph, config_path=config_path, reach_graph=full)
    target = path / ".archy" / "baseline.json"
    write_snapshot(snap, target)
    return SnapshotPayload(
        score=snap.score,
        cycles=snap.cycles,
        violations=snap.violations,
        sdp_violations=snap.sdp_violations,
        required_violations=snap.required_violations,
        baseline_path=str(target),
        invariant_brief=_build_invariant_brief(graph, config_path, snap.score, snap.cycles),
    )


def _run_diff(path: Path) -> DiffReport | DiffErrorPayload:
    target = path / ".archy" / "baseline.json"
    baseline = read_snapshot(target)
    if baseline is None:
        return DiffErrorPayload(
            error=f"no baseline at {target}; call archy_snapshot first to capture one."
        )
    graph, full = _load_graph_pair(path)
    current = take_snapshot(graph, config_path=discover_config(path), reach_graph=full)
    report = compute_diff(baseline, current)
    return report.model_copy(update={"summary": summarize_diff(report, graph)})


def _run_simulate(path: Path, *, add: list[EdgeSpec], remove: list[EdgeSpec]) -> SimulateReport:
    graph, full = _load_graph_pair(path)
    return find_simulate(
        graph,
        add=[e.as_pair() for e in add],
        remove=[e.as_pair() for e in remove],
        config_path=discover_config(path),
        project_root=path,
        reach_graph=full,
    )


def _resolve_against(path: Path, files: list[Path]) -> list[Path]:
    """Anchor relative file args against the project root; leave absolutes alone."""
    return [path / f if not f.is_absolute() else f for f in files]


def _run_impact(
    path: Path,
    *,
    files: list[Path],
    max_chains: int = DEFAULT_MAX_CHAINS,
    co_change: bool = False,
) -> Impact:
    graph = _load_graph(path, internal_only=True)
    result = find_impact(graph, _resolve_against(path, files), max_chains=max_chains)
    if not co_change or not result.changed:
        return result
    # Opt-in behavioral overlay: surface modules that co-change with the edited
    # set but have no structural edge (so the blast radius above missed them).
    # Source-only (test co-change is mostly the module's own test) and best-effort
    # (no git -> empty). Exclude modules already in `impacted` - the point is the
    # HIDDEN dependents. This is the only path that makes archy_impact touch git.
    source_paths = frozenset(p for p in internal_module_paths(graph) if not is_test_path(p))
    cochange = git_cochange(path, keep_paths=source_paths)
    if cochange is None:
        return result
    hints = co_changed_with(
        graph,
        cochange,
        targets=frozenset(result.changed),
        exclude=frozenset(result.impacted),
        min_support=DEFAULT_MIN_SUPPORT,
        min_confidence=DEFAULT_MIN_CONFIDENCE,
    )
    return result.model_copy(update={"co_changed": tuple(hints)})


def _run_affected(
    path: Path,
    *,
    files: list[Path],
    depth: int,
    test_filter: str | None,
) -> Affected:
    graph = _load_graph(path, internal_only=True)
    resolved = _resolve_against(path, files)
    return find_affected(
        graph,
        resolved,
        project_root=path,
        depth=depth,
        test_filter=test_filter,
    )


def _run_trend(path: Path, *, last_n: int) -> list[TrendRow]:
    if last_n < 1:
        raise ValueError(f"last_n must be >= 1; got {last_n}")
    rows = read_history(path / ".archy" / "history.jsonl")
    window = rows[-last_n:]
    return [
        TrendRow(
            timestamp=r.timestamp,
            commit=r.commit,
            branch=r.branch,
            score=TrendRowScore(
                overall=r.overall,
                modularity=r.modularity,
                acyclicity=r.acyclicity,
                depth=r.depth,
                equality=r.equality,
                complexity=r.complexity,
            ),
            inputs=TrendRowInputs(
                module_count=r.module_count,
                edge_count=r.edge_count,
                cycle_count=r.cycle_count,
                tangle_ratio=r.tangle_ratio,
                max_depth=r.max_depth,
                community_count=r.community_count,
            ),
        )
        for r in window
    ]


def _run_graph_focus(
    path: Path,
    *,
    modules: list[str],
    depth: int,
    direction: str,
    internal_only: bool,
) -> GraphPayload:
    import networkx as nx

    if direction not in ("in", "out", "both"):
        raise ValueError(f"direction must be 'in', 'out', or 'both'; got {direction!r}")
    if depth < 0:
        raise ValueError(f"depth must be >= 0; got {depth}")

    graph = _load_graph(path, internal_only=internal_only)
    resolved, unresolved = resolve_modules(graph, modules, project_root=path)
    if not resolved:
        return _graph_payload_from(_empty_subgraph(graph), unresolved=tuple(unresolved))

    reachable: set[str] = set(resolved)
    if direction in ("out", "both"):
        for seed in resolved:
            reachable |= set(nx.ego_graph(graph, seed, radius=depth).nodes())
    if direction in ("in", "both"):
        reverse = graph.reverse(copy=False)
        for seed in resolved:
            reachable |= set(nx.ego_graph(reverse, seed, radius=depth).nodes())

    sub = graph.subgraph(reachable).copy()
    sub.graph["root"] = graph.graph.get("root")
    sub.graph["parse_errors"] = graph.graph.get("parse_errors", ())
    return _graph_payload_from(sub, unresolved=tuple(unresolved))


def _pagerank(graph, *, damping: float = 0.85, iterations: int = 50, tol: float = 1e-6) -> dict:
    # NetworkX 3.x's pagerank requires numpy/scipy. archy stays dependency-light,
    # so we hand-roll the power iteration. Identical formulation to the standard
    # damped random-walk PageRank with dangling-node redistribution.
    nodes = list(graph.nodes())
    n = len(nodes)
    if n == 0:
        return {}
    out_degree = {v: graph.out_degree(v) for v in nodes}
    pr = dict.fromkeys(nodes, 1.0 / n)
    teleport = (1.0 - damping) / n
    for _ in range(iterations):
        dangling_mass = damping * sum(pr[v] for v in nodes if out_degree[v] == 0) / n
        new_pr = {v: teleport + dangling_mass for v in nodes}
        for u in nodes:
            if out_degree[u]:
                share = damping * pr[u] / out_degree[u]
                for v in graph.successors(u):
                    new_pr[v] += share
        if sum(abs(new_pr[v] - pr[v]) for v in nodes) < tol:
            return new_pr
        pr = new_pr
    return pr


def _run_graph_summary(path: Path, *, top_n: int) -> GraphSummaryPayload:
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1; got {top_n}")

    graph = _load_graph(path, internal_only=False)
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]
    internal_set = set(internal)

    internal_subgraph = graph.subgraph(internal)
    instability = compute_instability(internal_subgraph)
    _, propagation_cost = compute_propagation_cost(internal_subgraph)
    edit_risk = compute_edit_risk(internal_subgraph)

    internal_edge_count = internal_subgraph.number_of_edges()
    external_edge_count = sum(
        1 for u, v in graph.edges() if u in internal_set and v not in internal_set
    )

    fan_in = sorted(
        ((n, internal_subgraph.in_degree(n)) for n in internal),
        key=lambda t: (-t[1], t[0]),
    )
    fan_out = sorted(
        ((n, internal_subgraph.out_degree(n)) for n in internal),
        key=lambda t: (-t[1], t[0]),
    )

    pagerank = _pagerank(internal_subgraph)
    pr_sorted = sorted(pagerank.items(), key=lambda t: (-t[1], t[0]))

    risk_sorted = sorted(edit_risk.items(), key=lambda t: (-t[1], t[0]))

    external_counts: dict[str, int] = {}
    for _, v in graph.edges():
        if v not in internal_set and graph.nodes[v].get("external"):
            external_counts[v] = external_counts.get(v, 0) + 1
    ext_sorted = sorted(external_counts.items(), key=lambda t: (-t[1], t[0]))

    def _entries(
        pairs: list[tuple[str, float | int]],
        *,
        with_internal_metrics: bool,
    ) -> tuple[GraphSummaryEntry, ...]:
        return tuple(
            GraphSummaryEntry(
                module=name,
                value=float(value),
                instability=instability.get(name) if with_internal_metrics else None,
                propagation_cost=propagation_cost.get(name) if with_internal_metrics else None,
                edit_risk=edit_risk.get(name) if with_internal_metrics else None,
            )
            for name, value in pairs[:top_n]
        )

    return GraphSummaryPayload(
        module_count=len(internal),
        internal_edge_count=internal_edge_count,
        external_edge_count=external_edge_count,
        parse_errors=tuple(graph.graph.get("parse_errors", ())),
        top_fan_in=_entries(list(fan_in), with_internal_metrics=True),
        top_fan_out=_entries(list(fan_out), with_internal_metrics=True),
        top_pagerank=_entries(list(pr_sorted), with_internal_metrics=True),
        top_edit_risk=_entries(list(risk_sorted), with_internal_metrics=True),
        external_deps=_entries(list(ext_sorted), with_internal_metrics=False),
    )


def _run_graph_dump(
    path: Path,
    *,
    internal_only: bool,
    max_nodes: int,
) -> GraphPayload | GraphTooLargePayload:
    if max_nodes < 1:
        raise ValueError(f"max_nodes must be >= 1; got {max_nodes}")
    graph = _load_graph(path, internal_only=internal_only)
    node_count = graph.number_of_nodes()
    if node_count > max_nodes:
        return GraphTooLargePayload(
            error=(
                f"graph has {node_count} nodes (> max_nodes={max_nodes}). "
                "Use archy_graph(focus=[...]) for a local slice or "
                "archy_graph(response_format='summary') for a top-N overview, or "
                "call archy_graph again with a higher max_nodes if you really "
                "want the full dump."
            ),
            node_count=node_count,
            max_nodes=max_nodes,
        )
    return _graph_payload_from(graph)


def _validate_response_format(response_format: str) -> None:
    if response_format not in ("summary", "full"):
        raise ValueError(f"response_format must be 'summary' or 'full'; got {response_format!r}")


def _validate_impact_mode(mode: str) -> None:
    if mode not in ("blast", "affected"):
        raise ValueError(f"mode must be 'blast' or 'affected'; got {mode!r}")


def _validate_refactor_lens(lens: str) -> None:
    if lens not in ("fused", "behavioral", "structural"):
        raise ValueError(f"lens must be 'fused', 'behavioral', or 'structural'; got {lens!r}")


def _run_graph(
    path: Path,
    *,
    response_format: str,
    focus: list[str] | None = None,
    depth: int = 2,
    direction: str = "both",
    internal_only: bool,
    max_nodes: int,
    top_n: int,
) -> GraphSummaryPayload | GraphPayload | GraphTooLargePayload:
    """Route archy_graph between a focused subgraph, the summary, and the dump.

    `focus` (absorbing the old archy_graph_focus) takes precedence: a focused
    neighborhood is already bounded, so response_format/max_nodes/top_n do not
    apply to it. Otherwise summary is the default (top-N overview, identical to
    the old archy_graph_summary) and full is the opt-in dump, guarded by
    max_nodes. response_format is validated on every path so an invalid value
    raises even when focus is set.
    """
    _validate_response_format(response_format)
    if focus:
        return _run_graph_focus(
            path,
            modules=focus,
            depth=depth,
            direction=direction,
            internal_only=internal_only,
        )
    if response_format == "summary":
        return _run_graph_summary(path, top_n=top_n)
    return _run_graph_dump(path, internal_only=internal_only, max_nodes=max_nodes)


def _run_dsm(
    path: Path,
    *,
    response_format: str,
    group_by: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
    baseline_path: str | None,
) -> DSMSummary | DSM | DSMDiff | DSMTooLargePayload | DSMErrorPayload:
    """Route archy_dsm between the concise summary, the full matrix, and a diff.

    A diff (baseline_path set) is a deliberate, already-compact comparison
    (new_back_edges is the signal), not a dump, so response_format does not
    apply to it. The full matrix is capped at DEFAULT_MAX_DSM_CELLS cells.
    """
    _validate_response_format(response_format)
    graph = _load_graph(path, internal_only=False)
    current = build_dsm(
        graph,
        group_by=cast(GroupBy, group_by),
        weight=cast(Weight, weight),
        focus=focus,
        focus_depth=focus_depth,
        package=package,
    )
    if baseline_path is not None:
        before = read_dsm(Path(baseline_path))
        if before is None:
            return DSMErrorPayload(error=f"no DSM snapshot at {baseline_path}")
        return diff_dsm(before, current)
    if response_format == "summary":
        return summarize_dsm(current)
    if len(current.cells) > DEFAULT_MAX_DSM_CELLS:
        return DSMTooLargePayload(
            error=(
                f"DSM has {len(current.cells)} cells (> max_cells="
                f"{DEFAULT_MAX_DSM_CELLS}). Read response_format='summary' for a "
                "compact overview, or narrow with focus=<qualname> or "
                "package=<prefix>."
            ),
            cell_count=len(current.cells),
            max_cells=DEFAULT_MAX_DSM_CELLS,
        )
    return current


def _summarize_duplicate_group(group: DuplicateGroup) -> DuplicateGroupSummary:
    first = group.members[0]
    return DuplicateGroupSummary(
        shape_hash=group.shape_hash,
        size=group.size,
        member_count=group.member_count,
        redundancy=group.redundancy,
        category=group.category,
        variant_reason=group.variant_reason,
        exact=group.exact,
        similarity=group.similarity,
        sample=f"{first.module}:{first.line} {first.qualified_name}",
    )


def _run_duplicates(
    path: Path,
    *,
    response_format: str,
    min_nodes: int,
    top_n: int,
    members: int,
    co_change: bool = True,
    near_miss: bool = False,
) -> DuplicatesPayload:
    """Two-tier duplicate clusters for archy_duplicates.

    Reads per-function rows via the function-grained warm path (`parse_map`),
    classifies clusters into the primary/variant tiers, and shapes each tier by
    `response_format` (a per-cluster summary vs the full member lists). When
    `co_change` and git history is available, demotes primary clusters whose
    copies never co-change (issue #242); best-effort (no git -> unchanged). When
    `near_miss`, adds a lower-confidence Type-3 tier via token overlap (#246).
    """
    _validate_response_format(response_format)
    if min_nodes < 1:
        raise ValueError(f"min_nodes must be >= 1; got {min_nodes}")
    if top_n < 1:
        raise ValueError(f"top_n must be >= 1; got {top_n}")
    # members < 2 is validated by compute_duplicates (raises ValueError).
    modules, results = _parse_project(
        path, max_modules=_resolve_max_modules(path), **_graph_kwargs(path)
    )
    rows = classify_variants(
        compute_duplicates(modules, results, min_size=min_nodes, min_members=members)
    )
    if co_change:
        cochange = git_cochange(path, keep_paths=frozenset(str(m.path) for m in modules))
        if cochange is not None:
            rows = demote_independent(
                rows, counts=cochange.counts, pair_support=cochange.pair_support
            )
    near = compute_near_duplicates(modules, results, min_size=min_nodes) if near_miss else []
    dups = [g for g in rows if g.category == "duplicate"]
    variants = [g for g in rows if g.category == "variant"]

    def shape(groups: list[DuplicateGroup]):
        top = groups[:top_n]
        if response_format == "full":
            return tuple(top)
        return tuple(_summarize_duplicate_group(g) for g in top)

    note: str | None = None
    if not dups:
        note = (
            f"No likely-duplicate function bodies of >= {min_nodes} normalized "
            "nodes found; lower min_nodes to widen the search."
        )
        if variants:
            note += (
                f" ({len(variants)} likely-intentional variant(s) were found but demoted: "
                "same-class / boilerplate / test / vendored / independent.)"
            )
    return DuplicatesPayload(
        min_nodes=min_nodes,
        total=len(dups),
        exact_total=sum(1 for g in dups if g.exact),
        variant_total=len(variants),
        near_total=len(near),
        shown=min(top_n, len(dups)),
        duplicated_functions=sum(g.member_count for g in dups),
        duplicates=shape(dups),
        variants=shape(variants),
        near_miss=shape(near),
        note=note,
    )


def _empty_subgraph(graph):
    import networkx as nx

    empty: nx.DiGraph = nx.DiGraph()
    empty.graph["root"] = graph.graph.get("root")
    empty.graph["parse_errors"] = graph.graph.get("parse_errors", ())
    return empty


def _graph_payload_from(graph, *, unresolved: tuple[str, ...] = ()) -> GraphPayload:
    data = graph_to_dict(graph)
    nodes = tuple(
        GraphNode(
            id=n["id"],
            external=bool(n.get("external", False)),
            path=n.get("path"),
            is_package=n.get("is_package"),
            instability=n.get("instability"),
            propagation_cost=n.get("propagation_cost"),
            edit_risk=n.get("edit_risk"),
        )
        for n in data["nodes"]
    )
    edges = tuple(
        GraphEdge(
            source=e["source"],
            target=e["target"],
            is_relative=bool(e.get("is_relative", False)),
            lines=tuple(e.get("lines", ())),
        )
        for e in data["edges"]
    )
    return GraphPayload(
        root=data["root"],
        parse_errors=tuple(data["parse_errors"]),
        nodes=nodes,
        edges=edges,
        unresolved=unresolved,
    )


def _refactor_note(
    *,
    lens: str,
    rows: list,
    git_available: bool,
    path: Path,
    min_risk: float,
) -> str | None:
    """Lens-aware empty/degraded note for archy_what_to_refactor_next.

    behavioral needs git; structural never does; fused runs both and degrades
    to structural-only off git. A non-empty single-lens result needs no note.
    """
    if rows:
        if lens == "fused" and not git_available:
            return (
                f"{path} is not inside a git repository (or git is unavailable); "
                "the CC x churn behavioral lens was skipped and this ranking is "
                "structural-only (edit-risk). Run inside git for the fused view."
            )
        return None
    if lens == "behavioral":
        if not git_available:
            return (
                f"{path} is not inside a git repository (or git is unavailable); "
                "the behavioral lens needs git history to compute per-file churn. "
                "Use lens='structural' for a git-free 'is this edit dangerous?' "
                "signal instead."
            )
        return (
            "No behavioral hotspots surfaced: no file is both complex and "
            "frequently changed (CC x churn). The project is likely small, "
            "young, or structurally clean."
        )
    if lens == "structural":
        return (
            f"No module is central and fragile above min_risk={min_risk}. Lower "
            "min_risk (pass 0 to surface every module) to widen the lens."
        )
    # fused
    if git_available:
        return (
            "No refactoring priorities surfaced: no file is both complex and "
            "frequently changed (CC x churn hotspots: 0), and no module is "
            f"central and fragile above min_risk={min_risk} (0). The project is "
            "likely small, young, or structurally clean - there is genuinely "
            "nothing to prioritize right now. Lower min_risk to widen the "
            "structural lens."
        )
    return (
        f"{path} is not inside a git repository (or git is unavailable), so the "
        "CC x churn behavioral lens was skipped and only the structural "
        f"edit-risk lens ran. No module is central and fragile above "
        f"min_risk={min_risk}, so nothing was surfaced. Lower min_risk to widen "
        "the structural lens."
    )


def _run_what_to_refactor_next(
    path: Path, *, lens: str = "fused", top_n: int, since: str | None, min_risk: float
) -> WhatToRefactorPayload:
    _validate_refactor_lens(lens)
    if top_n <= 0:
        raise ValueError(f"top_n must be >= 1; got {top_n}")
    if not 0.0 <= min_risk <= 1.0:
        raise ValueError(f"min_risk must be in [0, 1]; got {min_risk}")

    graph = _load_graph(path, internal_only=True)
    # The behavioral lens needs git; the structural lens never does, so skip the
    # git call entirely for structural (since is irrelevant there).
    churn = None if lens == "structural" else git_churn(path, since=since)
    git_available = churn is not None

    rows = compute_refactor_priorities(graph, churn=churn, min_risk=min_risk)
    if lens == "behavioral":
        # Keep only rows the behavioral lens fired on and restore the pure
        # CC x churn ranking (the fused `priority` would let a both-lens module
        # outrank a bigger pure hotspot).
        rows = sorted(
            (r for r in rows if "hotspot" in r.lenses),
            key=lambda r: (-r.hotspot_score, r.module),
        )
    # structural: churn=None already restricts rows to the edit_risk lens, sorted
    # by priority (== normalized edit_risk), matching the old high-risk ranking.

    shown = rows[:top_n]
    entries = tuple(
        RefactorPriorityEntry(
            module=r.module,
            path=r.path,
            lenses=r.lenses,
            priority=r.priority,
            cc_sum=r.cc_sum,
            churn=r.churn,
            hotspot_score=r.hotspot_score,
            edit_risk=r.edit_risk,
            propagation_cost=r.propagation_cost,
            instability=r.instability,
            fan_in=r.fan_in,
            rationale=r.rationale,
        )
        for r in shown
    )

    note = _refactor_note(
        lens=lens,
        rows=rows,
        git_available=git_available,
        path=path,
        min_risk=min_risk,
    )

    return WhatToRefactorPayload(
        since=since,
        min_risk=min_risk,
        git_available=git_available,
        total=len(rows),
        shown=len(entries),
        priorities=entries,
        note=note,
    )


_ManagerKey = tuple[str, frozenset[str], tuple[str, ...]]
_MANAGERS: dict[_ManagerKey, IndexManager] = {}
_MANAGERS_LOCK = threading.Lock()


def _manager_cache_key(root: Path, kwargs: dict) -> _ManagerKey:
    """Config-aware cache key for ``_manager_for``.

    The key folds in the graph-building kwargs (``ignored_dirs`` /
    ``extra_roots``) normalized exactly as ``IndexManager.__init__`` normalizes
    them, so two calls with the same effective config share a manager while a
    call with a *different* config gets its own. Keying on ``root`` alone would
    pin the first config seen and silently ignore later kwargs.
    """
    ignored = frozenset(kwargs.get("ignored_dirs", DEFAULT_IGNORED_DIRS))
    extra = tuple(kwargs.get("extra_roots", ()))
    return (str(root), ignored, extra)


def _manager_for(path: Path, *, max_modules: int | None = None, **kwargs) -> IndexManager:
    """Get-or-create the per-(root, config) IndexManager, starting its watcher once.

    Managers live for the server's lifetime: one persistent cache connection
    and one debounced watcher per project, so repeated tool calls reuse a warm,
    background-synced index. ``kwargs`` (ignored_dirs / extra_roots) are part of
    the cache key, so changing the discovered config produces a fresh manager
    rather than reusing one built with stale kwargs.

    ``max_modules`` is the scan-size backstop (see graph.DEFAULT_MAX_MODULES). It
    is enforced here, on first creation, via a cheap discovery walk BEFORE the
    recursive watcher is scheduled -- scheduling a watcher over a 40k-file
    vendored tree is itself costly, so a `ScanTooLargeError` must short-circuit
    before `start_watching`. It is intentionally NOT part of the cache key (it is
    a guard, not part of the manager's identity).
    """
    root = path.resolve()
    key = _manager_cache_key(root, kwargs)
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            if max_modules and max_modules > 0:
                count = len(
                    discover_modules(
                        root,
                        ignored_dirs=kwargs.get("ignored_dirs", DEFAULT_IGNORED_DIRS),
                        extra_roots=kwargs.get("extra_roots", ()),
                    )
                )
                if count > max_modules:
                    raise ScanTooLargeError(count, root, max_modules)
            # The config for this root changed (different key, same path): retire
            # any manager built with the superseded config so we don't leak its
            # watcher thread + cache connection on the same directory. A root has
            # exactly one live config at a time, so at most one manager per root.
            for stale_key in [k for k in _MANAGERS if k[0] == key[0]]:
                _MANAGERS.pop(stale_key).stop()
            manager = IndexManager(root, **kwargs)
            manager.start_watching()  # best-effort; on-demand sync works regardless
            _MANAGERS[key] = manager
        return manager


def _build_graph(path: Path, *, max_modules: int | None = None, **kwargs):
    """Cache-backed build for the long-lived MCP server (its hot path).

    Routes through a per-root IndexManager (persistent connection + background
    watcher). Falls back to a cold `build_graph` if the cache cannot be used
    (read-only filesystem, permission error): the index is an optimization,
    never a dependency, so a tool call must still succeed without it.

    A `ScanTooLargeError` from the guard is NOT a cache failure, so it propagates
    rather than being retried by the cold path (which would re-raise anyway).
    """
    try:
        return _manager_for(path, max_modules=max_modules, **kwargs).build_graph()
    except (sqlite3.Error, OSError):
        return build_graph(path, max_modules=max_modules, **kwargs)


def _parse_project(path: Path, *, max_modules: int | None = None, **kwargs):
    """Function-grained warm path: `(modules, parse_results)` for archy_duplicates.

    Mirrors `_build_graph` -- routes through the per-root IndexManager's
    `parse_map` (persistent cache + background watcher) and falls back to a cold
    `graph.parse_project` if the cache cannot be used. A `ScanTooLargeError` from
    the guard propagates rather than being retried by the cold path.
    """
    try:
        return _manager_for(path, max_modules=max_modules, **kwargs).parse_map()
    except (sqlite3.Error, OSError):
        return parse_project(path, max_modules=max_modules, **kwargs)


def _resolve_max_modules(path: Path) -> int | None:
    config_path = discover_config(path)
    configured = load_config(config_path).max_modules if config_path is not None else None
    return effective_max_modules(configured)


def _load_graph(path: Path, *, internal_only: bool):
    graph = _build_graph(path, max_modules=_resolve_max_modules(path), **_graph_kwargs(path))
    if internal_only:
        external = {n for n, d in graph.nodes(data=True) if d.get("external")}
        graph.remove_nodes_from(external)
    return graph


def _load_graph_pair(path: Path):
    """Return `(internal_only, full)` from ONE scan. Mirrors cli._load_graph_pair.

    Score and cycles want the internal-only graph; required-reach rules need the
    full one, because `must_reach` may name an external package.
    """
    full = _load_graph(path, internal_only=False)
    internal = full.copy()
    internal.remove_nodes_from([n for n, d in full.nodes(data=True) if d.get("external")])
    return internal, full


def _graph_kwargs(path: Path) -> dict:
    # Best-effort archy.yaml discovery so MCP tools honor `exclude:` and
    # `roots:` the same way the CLI does. See cli._graph_kwargs.
    config_path = discover_config(path)
    if config_path is None:
        return {}
    config = load_config(config_path)
    return {
        "ignored_dirs": DEFAULT_IGNORED_DIRS | frozenset(config.exclude),
        "extra_roots": config.roots,
    }
