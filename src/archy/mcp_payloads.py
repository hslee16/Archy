"""Wire contract of the MCP tools: the response models each tool returns.

Split out of `archy.mcp` (#395) because they change for a different reason than
the tool bodies do: a field here is a change to what agents receive. The
protocol suite (`tests/test_mcp_protocol.py`) pins their serialised schemas.

archy:owns        BriefConventions, BriefLayer, BriefPayload, CheckErrorPayload,
                  CheckPayload, ContractsPayload, DSMErrorPayload, DSMTooLargePayload,
                  DiffErrorPayload, DuplicateGroupSummary, DuplicatesPayload,
                  ForbiddenEdge, GraphEdge, GraphNode, GraphPayload, GraphSummaryEntry,
                  GraphSummaryPayload, GraphTooLargePayload, InvariantBrief,
                  LoadBearingModule, RefactorPriorityEntry, RequiredReach,
                  ScoreComponents, ScoreGate, ScorePayload, SnapshotPayload, TrendRow,
                  TrendRowInputs, TrendRowScore, WhatToRefactorPayload
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from archy.contracts import ContractCheck
from archy.conventions import ConventionsReport
from archy.cycles import Cycle
from archy.duplicates import DuplicateGroup
from archy.graph import UnresolvedHint
from archy.layers import (
    LayerCoverage,
    ReachViolation,
    SdpViolation,
    Violation,
)
from archy.score import Score, ScoreInputs


class ScoreComponents(BaseModel):
    model_config = ConfigDict(frozen=True)

    modularity: float
    acyclicity: float
    depth: float
    equality: float
    complexity: float


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
    # `all_kept` is true of a contract that matched no module. `verified` is
    # the field an agent should read: it is False when any contract could not
    # have failed, which `all_kept` cannot express (#435).
    verified: bool | None = None
    unverifiable: int | None = None
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
    # 🔴 WHAT THIS RUN ACTUALLY LOOKED AT, because `passed=true` alone does not
    # say (#343). `archy check` sees DIRECT edges; a `store -> common -> api`
    # chain passes it and fails `contracts`. An agent receiving passed=true
    # could not distinguish "checked transitively and clean" from "never
    # looked", on exactly the case archy claims as its differentiator. The CLI
    # got the qualified verdict in v0.45/v0.46 and this surface did not, which
    # is the three-surfaces defect AGENTS.md warns about, on the surface agents
    # actually call.
    transitive_checked: bool = False
    # Why passed=true does not prove the `forbid` rules hold, phrased for a
    # caller of this tool rather than a reader of the CLI. None when the
    # question does not arise: no forbid rules, a concrete violation already
    # found, or coverage complete enough to prove the absence.
    transitive_unverified_reason: str | None = None


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


class BriefConventions(ConventionsReport):
    """`ConventionsReport` bounded to `top_n` rows in its ranked sections.

    Says how much it dropped (`totals`) rather than handing back a short list
    that looks complete, which is what the CLI's `brief --format json` does.
    `gates`, `errors`, `export_gaps` and `doc_gaps` are never truncated: a
    partial inventory of "things you forgot to wire up" reads as a finished
    checklist.
    """

    top_n: int
    totals: dict[str, int]


class BriefPayload(BaseModel):
    """What to know before touching an unfamiliar repository, in one call.

    Top-level field names match `archy brief --format json`, so the two surfaces
    cannot drift apart on the handoff (#427). Nested shapes follow the MCP models,
    as they already do between `archy check` and `archy_check`.
    """

    model_config = ConfigDict(frozen=True)

    conventions: BriefConventions
    # None when there is no usable archy.yaml: brief tolerates a missing or
    # malformed config and still reports, where `archy_check` cannot.
    coverage: LayerCoverage | None = None
    # Nested only when archy_brief(contracts=True) ran import-linter.
    contracts: ContractsPayload | None = None
    # What this run actually looked at; `passed`-style silence would hide that
    # brief never runs the direct pass (#343).
    transitive_checked: bool = False
    transitive_unverified_reason: str | None = None


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
    # Set only when `source` is "@entrypoints": the pattern the derived
    # entrypoints are narrowed to.
    within: str | None = None


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
    unresolved_hints: tuple[UnresolvedHint, ...] = ()


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
    the project is not under git (or has no commits yet) - the behavioral
    lens is then skipped and the ranking is structural-only."""

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
