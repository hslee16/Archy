"""Text and JSON renderers for the CLI.

The renderers live in the `cli_render_*` modules, grouped by the commands they serve;
this module re-exports the names the command modules and tests import, so none of
them had to change when it was split.
"""

from __future__ import annotations

from archy.cli_render_check import (
    ContractsOutcome,
    _brief_to_text,
    _contracts_handoff_to_text,
    _contracts_outcome_to_dict,
    _contracts_outcome_to_text,
    _contracts_to_dict,
    _contracts_to_text,
    _coverage_to_json,
    _coverage_to_text,
    _pattern_hints_to_text,
    _presence_to_text,
    _reach_violations_to_json,
    _reach_violations_to_text,
    _sdp_violations_to_json,
    _sdp_violations_to_text,
    _unverified_forbid_rules,
    _violations_to_json,
    _violations_to_text,
)
from archy.cli_render_common import _gate_to_dict, _gate_to_text, _location
from archy.cli_render_conventions import (
    _conventions_to_dict,
    _conventions_to_text,
    _module_view_to_text,
)
from archy.cli_render_git import (
    _coupling_to_dict,
    _coupling_to_text,
    _duplicates_to_dict,
    _duplicates_to_text,
    _hotspots_to_dict,
    _hotspots_to_text,
    _refactor_to_dict,
    _refactor_to_text,
)
from archy.cli_render_graph import (
    _affected_to_dict,
    _affected_to_text,
    _deleted_lines,
    _graph_to_text,
    _impact_to_dict,
    _impact_to_text,
)
from archy.cli_render_history import (
    _cycle_scan_to_dict,
    _cycles_to_json,
    _cycles_to_text,
    _diff_to_text,
    _namespace_gap_lines,
    _namespace_gaps_to_json,
    _score_to_dict,
    _score_to_text,
    _simulate_to_text,
    _snapshot_to_text,
)

__all__ = [
    "ContractsOutcome",
    "_affected_to_dict",
    "_affected_to_text",
    "_brief_to_text",
    "_contracts_handoff_to_text",
    "_contracts_outcome_to_dict",
    "_contracts_outcome_to_text",
    "_contracts_to_dict",
    "_contracts_to_text",
    "_conventions_to_dict",
    "_conventions_to_text",
    "_coupling_to_dict",
    "_coupling_to_text",
    "_coverage_to_json",
    "_coverage_to_text",
    "_cycle_scan_to_dict",
    "_cycles_to_json",
    "_cycles_to_text",
    "_deleted_lines",
    "_diff_to_text",
    "_duplicates_to_dict",
    "_duplicates_to_text",
    "_gate_to_dict",
    "_gate_to_text",
    "_graph_to_text",
    "_hotspots_to_dict",
    "_hotspots_to_text",
    "_impact_to_dict",
    "_impact_to_text",
    "_location",
    "_module_view_to_text",
    "_namespace_gap_lines",
    "_namespace_gaps_to_json",
    "_pattern_hints_to_text",
    "_presence_to_text",
    "_reach_violations_to_json",
    "_reach_violations_to_text",
    "_refactor_to_dict",
    "_refactor_to_text",
    "_score_to_dict",
    "_score_to_text",
    "_sdp_violations_to_json",
    "_sdp_violations_to_text",
    "_simulate_to_text",
    "_snapshot_to_text",
    "_unverified_forbid_rules",
    "_violations_to_json",
    "_violations_to_text",
]
