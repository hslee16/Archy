"""The work behind each MCP tool, independent of the MCP framework.

This module is a facade. The ``run_*`` functions live in ``archy.mcp_runs_check``,
``archy.mcp_runs_git`` and ``archy.mcp_runs_graph``, and the warm-index plumbing
(``_manager_for`` and friends) in ``archy.mcp_runs_core``; importing from here keeps
working. Every ``run_*`` function takes plain arguments and returns a payload from
``archy.mcp_payloads``; ``archy.mcp`` only registers them as tools and maps
expected refusals onto ``ToolError``.

archy:owns        contracts_verdict, resolve_against, run_affected, run_brief,
                  run_check, run_contracts, run_conventions, run_cycles, run_diff,
                  run_dsm, run_duplicates, run_graph, run_graph_dump, run_graph_focus,
                  run_graph_summary, run_impact, run_module_view, run_score,
                  run_simulate, run_snapshot, run_trend, run_what_to_refactor_next,
                  score_components, validate_impact_mode
"""

from __future__ import annotations

from archy.mcp_runs_check import (
    contracts_verdict,
    run_brief,
    run_check,
    run_contracts,
    run_conventions,
    run_cycles,
    run_diff,
    run_module_view,
    run_score,
    run_simulate,
    run_snapshot,
    run_trend,
)
from archy.mcp_runs_core import (
    _MANAGERS,
    _load_graph,
    _manager_cache_key,
    _manager_for,
    resolve_against,
    validate_impact_mode,
)
from archy.mcp_runs_git import _duplicates_note, run_duplicates, run_what_to_refactor_next
from archy.mcp_runs_graph import (
    DEFAULT_MAX_DSM_CELLS,
    _pagerank,
    run_affected,
    run_dsm,
    run_graph,
    run_graph_dump,
    run_graph_focus,
    run_graph_summary,
    run_impact,
)

__all__ = [
    "DEFAULT_MAX_DSM_CELLS",
    "_MANAGERS",
    "_duplicates_note",
    "_load_graph",
    "_manager_cache_key",
    "_manager_for",
    "_pagerank",
    "contracts_verdict",
    "resolve_against",
    "run_affected",
    "run_brief",
    "run_check",
    "run_contracts",
    "run_conventions",
    "run_cycles",
    "run_diff",
    "run_dsm",
    "run_duplicates",
    "run_graph",
    "run_graph_dump",
    "run_graph_focus",
    "run_graph_summary",
    "run_impact",
    "run_module_view",
    "run_score",
    "run_simulate",
    "run_snapshot",
    "run_trend",
    "run_what_to_refactor_next",
    "validate_impact_mode",
]
