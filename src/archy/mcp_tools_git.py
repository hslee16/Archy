"""MCP tool registrations for what-to-refactor-next and duplicates.

archy:owns        register_archy_duplicates, register_archy_what_to_refactor_next
"""

from __future__ import annotations

from archy.duplicates import (
    DEFAULT_MIN_SIZE,
)
from archy.mcp_compat import Server
from archy.mcp_payloads import (
    DuplicatesPayload,
    WhatToRefactorPayload,
)
from archy.mcp_runs import (
    run_duplicates,
    run_what_to_refactor_next,
)
from archy.mcp_tools_common import _READ_ONLY_ANNOTATIONS, _project_dir
from archy.refactor import DEFAULT_MIN_RISK


def register_archy_what_to_refactor_next(server: Server) -> None:
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


def register_archy_duplicates(server: Server) -> None:
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
