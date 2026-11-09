"""Layer-rule governance over an import graph.

A YAML config declares named layers (groups of modules matched by dotted-name
globs) and forbidden inter-layer edges. `find_violations` walks the import
graph and returns every edge that crosses a forbidden boundary.

archy:owns        ExactPatternHint, ForbidRule, LayerConfig, LayerConfigError,
                  LayerCoverage, LayerSpec, ReachViolation, RequiredRule, SdpConfig,
                  SdpViolation, Violation, check_external_patterns, compute_coverage,
                  contracts_unverified, discover_config, find_reach_violations,
                  find_sdp_violations, find_violations, governed_roots,
                  is_floating_pattern, load_config, match_layer
archy:mirrored-by LayerCoverage -> archy.cli_check, archy.cli_render_check,
                  archy.mcp_payloads, archy.mcp_runs,
                  ReachViolation -> archy.cli_check, archy.cli_render_check,
                  archy.diff, archy.mcp_payloads,
                  RequiredRule -> archy.cli_render_check, archy.diff,
                  SdpViolation -> archy.cli_check, archy.cli_render_check, archy.diff,
                  archy.mcp_payloads, archy.mcp_runs, Violation -> archy.cli_check,
                  archy.cli_render_check, archy.diff, archy.mcp_payloads,
                  archy.ratchet, compute_coverage -> archy.cli_check,
                  archy.cli_conventions, archy.mcp_runs, bench.greenfield_eval,
                  contracts_unverified -> archy.cli_render_check, archy.mcp_runs,
                  discover_config -> archy.cli_check, archy.cli_conventions,
                  archy.cli_history, archy.mcp_runs, archy.scan_config,
                  find_reach_violations -> archy.cli_check, archy.diff, archy.mcp_runs,
                  find_sdp_violations -> archy.cli_check, archy.diff, archy.mcp_runs,
                  find_violations -> archy.cli_check, archy.diff, archy.mcp_runs,
                  bench.greenfield_eval, bench.q1b_run
"""

from __future__ import annotations

from archy.layers_check import (
    check_external_patterns,
    compute_coverage,
    contracts_unverified,
    find_reach_violations,
    find_sdp_violations,
    find_violations,
    governed_roots,
)
from archy.layers_config import discover_config, load_config
from archy.layers_match import _compile_pattern, is_floating_pattern, match_layer
from archy.layers_models import (
    ENTRYPOINTS_SOURCE,
    ExactPatternHint,
    ForbidRule,
    LayerConfig,
    LayerConfigError,
    LayerCoverage,
    LayerSpec,
    ReachViolation,
    RequiredRule,
    SdpViolation,
    Violation,
)

__all__ = [
    "ENTRYPOINTS_SOURCE",
    "ExactPatternHint",
    "ForbidRule",
    "LayerConfig",
    "LayerConfigError",
    "LayerCoverage",
    "LayerSpec",
    "ReachViolation",
    "RequiredRule",
    "SdpViolation",
    "Violation",
    "_compile_pattern",
    "check_external_patterns",
    "compute_coverage",
    "contracts_unverified",
    "discover_config",
    "find_reach_violations",
    "find_sdp_violations",
    "find_violations",
    "governed_roots",
    "is_floating_pattern",
    "load_config",
    "match_layer",
]
