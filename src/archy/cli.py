"""Click-based command-line interface for archy.

archy:owns        affected, brief, check, contracts, conventions, coupling, cycles,
                  diff, dsm, duplicates, graph, hotspots, impact, index, index_clear,
                  index_status, index_sync, install, main, mcp, render, score,
                  simulate, snapshot, trend, uninstall, what_to_refactor_next
archy:gates       exit 1 (config:result.strict), exit 1 (flag:--strict),
                  exit 1 (flag:--strict-tolerance), exit 1 (hardcoded),
                  exit 1 (param:check), non-zero exit (hardcoded)
"""

from __future__ import annotations

import difflib
import functools
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import click
import networkx as nx

from archy import __version__
from archy.affected import DEFAULT_DEPTH, Affected, find_affected
from archy.cli_render import (
    ContractsOutcome,
    _affected_to_dict,
    _affected_to_text,
    _brief_to_text,
    _contracts_handoff_to_text,
    _contracts_outcome_to_dict,
    _contracts_outcome_to_text,
    _contracts_to_dict,
    _contracts_to_text,
    _conventions_to_dict,
    _conventions_to_text,
    _coupling_to_dict,
    _coupling_to_text,
    _coverage_to_json,
    _coverage_to_text,
    _cycle_scan_to_dict,
    _cycles_to_json,
    _cycles_to_text,
    _deleted_lines,
    _diff_to_text,
    _duplicates_to_dict,
    _duplicates_to_text,
    _gate_to_dict,
    _gate_to_text,
    _graph_to_text,
    _hotspots_to_dict,
    _hotspots_to_text,
    _impact_to_dict,
    _impact_to_text,
    _module_view_to_text,
    _pattern_hints_to_text,
    _presence_to_text,
    _reach_violations_to_json,
    _reach_violations_to_text,
    _refactor_to_dict,
    _refactor_to_text,
    _score_to_dict,
    _score_to_text,
    _sdp_violations_to_json,
    _sdp_violations_to_text,
    _simulate_to_text,
    _snapshot_to_text,
    _unverified_forbid_rules,
    _violations_to_json,
    _violations_to_text,
)
from archy.contracts import (
    ContractsConfigError,
    ContractsNotAvailable,
    run_contracts,
)
from archy.conventions import (
    ConventionsReport,
    compute_conventions,
    compute_module_view,
)
from archy.coupling import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_SUPPORT,
    compute_coupling,
    git_cochange,
    internal_module_paths,
)
from archy.cycles import scan_cycles
from archy.diff import (
    compute_diff,
    create_baseline_command,
    default_baseline_path,
    missing_baseline_hint,
    read_snapshot,
    strict_baseline_problem,
    strict_verdict,
    take_snapshot,
    write_snapshot,
)
from archy.diff_summary import summarize_diff
from archy.duplicates import (
    classify_variants,
    compute_duplicates,
    compute_near_duplicates,
    demote_independent,
    is_test_path,
)
from archy.gitlog import InvalidSinceError, resolve_repo_root, since_window_empty
from archy.graph import (
    ScanTooLargeError,
    build_graph,
    discover_modules,
    graph_to_dict,
    internal_subgraph,
    parse_project,
    suggest_modules,
)
from archy.headers import apply_header, compute_headers, existing_block, render_header
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.hotspots import NoCommitsError, compute_hotspots, git_churn
from archy.impact import DEFAULT_MAX_CHAINS, find_impact
from archy.layers import (
    LayerConfig,
    LayerConfigError,
    LayerCoverage,
    SdpViolation,
    Violation,
    compute_coverage,
    discover_config,
    find_reach_violations,
    find_sdp_violations,
    find_violations,
    load_config,
)
from archy.refactor import (
    DEFAULT_MIN_RISK,
    compute_refactor_priorities,
)
from archy.render import DEFAULT_MAX_NODES as RENDER_MAX_NODES
from archy.render import render_dsm_html, render_trend_html
from archy.scan_config import (
    graph_kwargs,
    resolve_exclude_type_checking,
    resolve_max_modules,
    scan_kwargs,
)
from archy.score import Score, compute_score
from archy.simulate import find_simulate
from archy.trend import render_text as render_trend

if TYPE_CHECKING:
    # Type-only: archy.install is imported lazily inside `install` to keep startup cheap.
    from archy.install import ConfigPreview


@click.group()
@click.version_option(__version__)
def main() -> None:
    """archy - architectural sensor for Python codebases."""


def _empty_since_window(path: Path, since: str | None, rows: object) -> bool:
    """True when ``since`` selected an empty commit window and ``rows`` is empty.

    Shared by hotspots / coupling / what-to-refactor-next so the
    ``if since and not rows: resolve_repo_root; since_window_empty`` block
    lives in one place.
    """
    if not since or rows:
        return False
    repo = resolve_repo_root(path)
    if repo is None:
        return False
    return since_window_empty(repo, since)


def _require(ok: bool, flag: str, constraint: str, value: object) -> None:
    """Raise a uniform ClickException for a failed CLI option bound-check."""
    if not ok:
        raise click.ClickException(f"--{flag} must be {constraint}; got {value}")


def _layer_config_errors(command: Callable[..., None]) -> Callable[..., None]:
    """Print a match-time LayerConfigError as the one-line CLI error (#750).

    `match_layer` raises while the graph is being read, not while the config is
    parsed, so the parse-time guards from #524 never see it: `check` reaches it
    through `compute_coverage`, `snapshot`, `simulate` and `diff` through their
    snapshot helpers. Wrapping a command keeps a new match-time call site inside
    it from reintroducing the traceback; a command that can reach `match_layer`
    has to carry this decorator or catch `LayerConfigError` itself, as `brief` does.
    """

    @functools.wraps(command)
    def wrapper(*args: object, **kwargs: object) -> None:
        try:
            command(*args, **kwargs)
        except LayerConfigError as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapper


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["json", "dot", "text"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--internal-only",
    is_flag=True,
    help="Hide edges to external (third-party / stdlib) modules.",
)
def graph(path: Path, fmt: str, internal_only: bool) -> None:
    """Build the import graph for a Python project rooted at PATH."""
    g = _load_graph(path, internal_only=internal_only)

    if fmt == "json":
        click.echo(json.dumps(graph_to_dict(g), indent=2, sort_keys=True))
    elif fmt == "dot":
        click.echo(_graph_to_dot(g))
    else:
        click.echo(_graph_to_text(g))

    if g.graph.get("parse_errors"):
        click.echo(
            f"\n[archy] {len(g.graph['parse_errors'])} file(s) had parse errors "
            "(partial trees were used). Run with --format json to see which.",
            err=True,
        )


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--internal-only/--all",
    default=True,
    help="Restrict cycle detection to internal modules (the default).",
)
@click.option(
    "--min-size",
    type=int,
    default=2,
    show_default=True,
    help="Minimum SCC size to report.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero if any cycles are found.",
)
@click.option(
    "--exclude-type-checking/--include-type-checking",
    "exclude_type_checking",
    default=None,
    help=(
        "Leave out imports that sit under `if TYPE_CHECKING:`, which never run. "
        "Default: archy.yaml's `exclude_type_checking_imports`, else off. "
        "With it on, JSON output becomes an object carrying the excluded-edge count."
    ),
)
@_layer_config_errors
def cycles(
    path: Path,
    fmt: str,
    internal_only: bool,
    min_size: int,
    strict: bool,
    exclude_type_checking: bool | None,
) -> None:
    """Find import cycles in a Python project rooted at PATH."""
    _require(min_size >= 1, "min-size", ">= 1", min_size)

    g = _load_graph(path, internal_only=internal_only)
    exclude = resolve_exclude_type_checking(path, exclude_type_checking)

    scan = scan_cycles(g, min_size=min_size, exclude_type_checking=exclude)
    found = list(scan.cycles)

    if fmt == "json":
        payload = _cycle_scan_to_dict(scan) if exclude else _cycles_to_json(found)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(
            _cycles_to_text(
                found, min_size, excluded=scan.excluded_type_checking_edges if exclude else None
            )
        )

    if strict and found:
        sys.exit(1)


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to archy.yaml. Discovered from PATH upward if omitted.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--show-unlayered",
    is_flag=True,
    default=False,
    help="List every module that matches no declared layer.",
)
@click.option(
    "--contracts",
    "with_contracts",
    is_flag=True,
    default=False,
    help="Also evaluate `forbid` rules transitively via import-linter, which sees paths a "
    "direct-edge check cannot. Reported alongside; it does not change the exit code.",
)
@click.option(
    "--contracts-config",
    "contracts_config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Explicit .importlinter path for --contracts (default: discovered, else derived "
    "from archy.yaml `forbid:`).",
)
@_layer_config_errors
def check(
    path: Path,
    config_path: Path | None,
    fmt: str,
    show_unlayered: bool,
    with_contracts: bool,
    contracts_config: Path | None,
) -> None:
    """Check the project at PATH against layer rules in archy.yaml.

    Exits 0 if there are no violations, 1 otherwise.

    `--contracts` adds the transitive verdict from import-linter. It is
    REPORTED, never gated on: a direct-edge violation and a transitive reach are
    different findings, and silently failing builds on the second because the
    flag was passed would change what a green check has always meant. The MCP
    tool has nested contracts under `archy_check(contracts=True)` since v0.41;
    this is the same capability on the command line, where it was missing.
    """
    if config_path is None:
        discovered = discover_config(path)
        if discovered is None:
            raise click.ClickException(
                f"no archy.yaml found near {path}; pass --config or create one at the project root."
            )
        config_path = discovered

    try:
        config = load_config(config_path)
    except LayerConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        g = build_graph(path, **scan_kwargs(config))
    except ScanTooLargeError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        violations = find_violations(g, config)
        reach_violations = find_reach_violations(g, config)
    except LayerConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    coverage = compute_coverage(g, config)

    sdp_violations: list[SdpViolation] = []
    if config.sdp.enabled:
        sdp_violations = find_sdp_violations(g, tolerance=config.sdp.tolerance)

    presence_fails = (
        config.min_layers_present is not None
        and coverage.layers_present < config.min_layers_present
    )

    contracts_result = _run_check_contracts(path, contracts_config) if with_contracts else None

    if fmt == "json":
        payload = {
            "violations": _violations_to_json(violations),
            "required_violations": _reach_violations_to_json(reach_violations),
            "sdp_violations": _sdp_violations_to_json(sdp_violations),
            "sdp_mode": config.sdp.mode,
            "coverage": _coverage_to_json(coverage),
            "min_layers_present": config.min_layers_present,
            "presence_fails": presence_fails,
            # A clean verdict has to say what it looked at. The text output has
            # carried this since v0.46 and the JSON did not, so a machine reader
            # could not tell "checked transitively and clean" from "never
            # looked" (#343).
            "transitive_checked": _transitive_checked(contracts_result),
            "transitive_unverified_reason": (
                None
                if _transitive_checked(contracts_result)
                else _transitive_unverified_reason(
                    config,
                    coverage,
                    violations,
                    no_verdict_error=_no_verdict_error(contracts_result),
                    unverifiable=_unverifiable_contracts(contracts_result),
                )
            ),
        }
        if contracts_result is not None:
            payload["contracts"] = _contracts_outcome_to_dict(contracts_result)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(_violations_to_text(violations, config_path, coverage))
        if config.required:
            click.echo(_reach_violations_to_text(reach_violations, config.required))
        click.echo(_coverage_to_text(coverage))
        hints = _pattern_hints_to_text(coverage)
        if hints:
            click.echo(hints)
        if not with_contracts:
            handoff = _contracts_handoff_to_text(config, coverage, violations)
            if handoff:
                click.echo(handoff)
        presence = _presence_to_text(coverage, config.min_layers_present)
        if presence:
            click.echo(presence)
        if show_unlayered and coverage.unlayered_modules:
            for module in coverage.unlayered_modules:
                click.echo(f"#     {module}")
        if config.sdp.enabled:
            click.echo("")
            click.echo(_sdp_violations_to_text(sdp_violations, config.sdp.tolerance))
            if sdp_violations and config.sdp.mode == "warn":
                click.echo("# (sdp.mode=warn; not failing the gate)")
        if contracts_result is not None:
            click.echo("")
            click.echo(_contracts_outcome_to_text(contracts_result))

    sdp_fails = bool(sdp_violations) and config.sdp.mode == "error"
    # Required-reach failures gate like forbid violations do. They are opt-in
    # (`required:` is absent from every config that predates the feature), so
    # nothing that passed before can start failing.
    reach_fails = bool(reach_violations)
    # A presence shortfall fails the gate like a violation does. Forbidding
    # edges between layers says nothing about whether the layers exist, and a
    # codebase that collapsed them into one module satisfies every forbid rule
    # by having no cross-layer edges at all.
    if violations or reach_fails or sdp_fails or presence_fails:
        sys.exit(1)


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--internal-only/--all",
    default=True,
    help="Restrict scoring to internal modules (default).",
)
@click.option(
    "--record",
    is_flag=True,
    help="Append this score to .archy/history.jsonl for archy trend.",
)
@click.option(
    "--strict",
    is_flag=True,
    help=(
        "Compare against the most recent recorded score; exit 1 if the overall "
        "score drops by more than --strict-tolerance."
    ),
)
@click.option(
    "--strict-tolerance",
    type=float,
    default=0.02,
    show_default=True,
    help="Maximum allowed drop in overall score before --strict fails.",
)
def score(
    path: Path,
    fmt: str,
    internal_only: bool,
    record: bool,
    strict: bool,
    strict_tolerance: float,
) -> None:
    """Compute the composite architecture quality score for PATH."""
    _require(
        0.0 <= strict_tolerance <= 1.0,
        "strict-tolerance",
        "in [0, 1]",
        strict_tolerance,
    )

    g = _load_graph(path, internal_only=internal_only)
    s = compute_score(g)

    history_path = path / ".archy" / "history.jsonl"
    # Strict reads BEFORE recording so the comparison is against the truly
    # previous run rather than the row we are about to append.
    gate = _gate_against_history(s, history_path) if strict else None

    if fmt == "json":
        payload = _score_to_dict(s)
        if gate is not None:
            payload["gate"] = _gate_to_dict(gate, strict_tolerance)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(_score_to_text(s))
        if gate is not None:
            click.echo("")
            click.echo(_gate_to_text(gate, strict_tolerance))

    if record:
        commit, branch = git_metadata(path)
        row = row_from_score(s, commit=commit, branch=branch)
        append_history(history_path, row)

    if gate is not None and gate["delta"] is not None and gate["delta"] < -strict_tolerance:
        sys.exit(1)


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--last",
    "last_n",
    type=int,
    default=10,
    show_default=True,
    help="Number of most-recent records to display.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def trend(path: Path, last_n: int, fmt: str) -> None:
    """Show the archy score trend for PATH (reads .archy/history.jsonl)."""
    _require(last_n >= 1, "last", ">= 1", last_n)

    rows = read_history(path / ".archy" / "history.jsonl")
    if fmt == "json":
        window = rows[-last_n:]
        click.echo(
            json.dumps(
                [
                    {
                        "timestamp": r.timestamp,
                        "commit": r.commit,
                        "branch": r.branch,
                        "score": {
                            "overall": r.overall,
                            "modularity": r.modularity,
                            "acyclicity": r.acyclicity,
                            "depth": r.depth,
                            "equality": r.equality,
                            # complexity is None on rows written before v0.20.
                            "complexity": r.complexity,
                        },
                    }
                    for r in window
                ],
                indent=2,
                sort_keys=True,
            )
        )
    else:
        click.echo(render_trend(rows, last_n=last_n))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--file",
    "files",
    type=click.Path(path_type=Path),
    multiple=True,
    required=True,
    help="Changed file path. Repeat for multiple files.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--max-chains",
    type=int,
    default=DEFAULT_MAX_CHAINS,
    show_default=True,
    help="Max causal chains (shortest import path to a changed module) to "
    "report, ranked closest-first. Use a negative value for all.",
)
def impact(path: Path, files: tuple[Path, ...], fmt: str, max_chains: int) -> None:
    """List internal modules that depend on the given file(s).

    Resolves each --file to a qualname via the import graph and prints
    every module that transitively imports any of them, with the shortest
    import path back to a changed module (the "because") for the closest
    dependents.
    """
    if max_chains == 0:
        raise click.ClickException(
            "--max-chains must be negative (for all) or positive (for a limit); got 0"
        )

    g = _load_graph(path, internal_only=True)
    result = find_impact(
        g,
        [path / f for f in files],
        max_chains=max_chains,
    )

    if fmt == "json":
        click.echo(json.dumps(_impact_to_dict(result), indent=2, sort_keys=True))
    else:
        click.echo(_impact_to_text(result))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.argument("files", nargs=-1, type=click.Path(path_type=Path))
@click.option(
    "--stdin",
    "from_stdin",
    is_flag=True,
    default=False,
    help=(
        "Read changed file paths from stdin, one per line. These are MERGED "
        "with any FILES passed as positional arguments (both sources combine; "
        "they are not alternatives). Pairs with "
        "`git diff --name-only | archy affected --stdin`."
    ),
)
@click.option(
    "-d",
    "--depth",
    type=int,
    default=DEFAULT_DEPTH,
    show_default=True,
    help="Maximum reverse-dependency hops to traverse.",
)
@click.option(
    "-f",
    "--filter",
    "test_filter",
    type=str,
    default=None,
    help="Recursive glob (matched against project-relative paths) identifying test files. "
    "Defaults to pytest conventions: test_*.py, *_test.py, anything under a tests/ directory. "
    "A glob that matches no file is an error, with a did-you-mean when a directory is close.",
)
@click.option(
    "-j",
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit JSON instead of human-readable text.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    default=False,
    help=(
        "Emit one affected test FILE PATH per line. "
        "Designed for `archy affected -q | xargs pytest`."
    ),
)
def affected(
    path: Path,
    files: tuple[Path, ...],
    from_stdin: bool,
    depth: int,
    test_filter: str | None,
    as_json: bool,
    quiet: bool,
) -> None:
    """Map changed source files to impacted modules and test files.

    Internal-only at launch: third-party and vendored code is not
    traced through. See docs/SPEC_INDEX_AND_INSTALL.md Q3.
    """
    if as_json and quiet:
        raise click.UsageError("--json and --quiet are mutually exclusive.")
    _require(depth >= 1, "depth", ">= 1", depth)

    # Positional FILES and --stdin are intentionally MERGED, not exclusive:
    # a caller can name a few files and pipe the rest (the help text says so).
    file_list = list(files)
    if from_stdin:
        file_list.extend(Path(line.strip()) for line in sys.stdin if line.strip())
    if not file_list:
        raise click.UsageError(
            "No changed files provided. Pass files as arguments or use --stdin "
            "(for example: `git diff --name-only | archy affected --stdin`)."
        )

    g = _load_graph(path, internal_only=True)
    resolved = [path / f for f in file_list]
    try:
        result = find_affected(g, resolved, project_root=path, depth=depth, test_filter=test_filter)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps(_affected_to_dict(result), indent=2, sort_keys=True))
    elif quiet:
        for test_path in _affected_test_paths(g, result):
            click.echo(test_path)
        for line in _deleted_lines(result.deleted):
            click.echo(line, err=True)
    else:
        click.echo(_affected_to_text(result))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=20,
    show_default=True,
    help="Maximum hotspots to show.",
)
@click.option(
    "--since",
    default=None,
    help=(
        "Restrict churn to commits after this date "
        "(e.g. 2024-01-01, 2 weeks ago) or git ref "
        "(tag/branch/SHA); invalid values are refused."
    ),
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def hotspots(path: Path, top_n: int, since: str | None, fmt: str) -> None:
    """Rank files by cyclomatic complexity x git churn.

    Produces a "refactor these first" priority list. CC comes from the
    v0.17 tree-sitter walker (per-module `cc_sum`); churn is per-file
    commit count from a one-pass `git log --name-only`. Files that score
    zero on either axis are dropped.
    """
    # Validate before any graph/git work so a bad --top fails fast and
    # consistently (a non-positive value otherwise silently truncated the
    # list: --top 0 showed nothing, --top -1 dropped the lowest-score row).
    _require(top_n >= 1, "top", ">= 1", top_n)
    g = _load_graph(path, internal_only=True)
    try:
        churn = git_churn(path, since=since)
    except InvalidSinceError as exc:
        raise click.ClickException(str(exc)) from exc
    except NoCommitsError as exc:
        raise click.ClickException(
            f"{path} is a git repository with no commit history yet; "
            "`archy hotspots` needs commits to compute per-file churn."
        ) from exc
    if churn is None:
        raise click.ClickException(
            f"{path} is not inside a git repository (or git is unavailable); "
            "`archy hotspots` needs git history to compute per-file churn."
        )
    rows = compute_hotspots(g, churn=churn)
    empty_window = _empty_since_window(path, since, rows)
    if fmt == "json":
        payload = _hotspots_to_dict(rows, top_n=top_n, since=since, empty_window=empty_window)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(_hotspots_to_text(rows, top_n=top_n, since=since, empty_window=empty_window))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=20,
    show_default=True,
    help="Maximum coupled pairs to show.",
)
@click.option(
    "--since",
    default=None,
    help=(
        "Restrict history to commits after this date "
        "(e.g. 2024-01-01, 2 weeks ago) or git ref "
        "(tag/branch/SHA); invalid values are refused."
    ),
)
@click.option(
    "--min-support",
    type=int,
    default=DEFAULT_MIN_SUPPORT,
    show_default=True,
    help="Minimum focused commits touching both modules for a pair to count.",
)
@click.option(
    "--min-confidence",
    type=float,
    default=DEFAULT_MIN_CONFIDENCE,
    show_default=True,
    help="Minimum coupling strength (co-change commits / the rarer module's commits).",
)
@click.option(
    "--include-tests",
    is_flag=True,
    default=False,
    help="Include test modules (default: source-only; test co-change is mostly noise).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def coupling(
    path: Path,
    top_n: int,
    since: str | None,
    min_support: int,
    min_confidence: float,
    include_tests: bool,
    fmt: str,
) -> None:
    """Rank module pairs that co-change in git history but share no import/call edge.

    Behavioral (temporal) coupling the structural graph can't see: two modules
    that keep changing together yet have no edge between them signal a hidden
    dependency or a missing abstraction. Strength is `confidence = co-change
    commits / the rarer module's commits`; sweeping (bulk) commits are
    normalized away internally so they don't couple everything they touch.
    Test modules are excluded by default (a test co-changing with the module it
    covers is expected, and on test-heavy repos it buries the source-to-source
    couplings that matter); pass `--include-tests` to keep them. Advisory only
    (never changes `archy score`); a pair means "check the other when you touch
    one," not "these must be merged." Needs git history.
    """
    _require(top_n >= 1, "top", ">= 1", top_n)
    _require(min_support >= 1, "min-support", ">= 1", min_support)
    _require(
        0.0 < min_confidence <= 1.0,
        "min-confidence",
        "in (0, 1]",
        min_confidence,
    )
    g = _load_graph(path, internal_only=True)
    module_paths = internal_module_paths(g)
    keep = frozenset(p for p in module_paths if include_tests or not is_test_path(p))
    try:
        cochange = git_cochange(path, since=since, keep_paths=keep)
    except InvalidSinceError as exc:
        raise click.ClickException(str(exc)) from exc
    if cochange is None:
        raise click.ClickException(
            f"{path} is not inside a git repository, has no commit history, or git "
            "is unavailable; `archy coupling` needs git history to compute co-change."
        )
    rows = compute_coupling(g, cochange, min_support=min_support, min_confidence=min_confidence)
    empty_window = _empty_since_window(path, since, rows)
    if fmt == "json":
        payload = _coupling_to_dict(
            rows,
            top_n=top_n,
            since=since,
            min_support=min_support,
            empty_window=empty_window,
        )
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(
            _coupling_to_text(
                rows,
                top_n=top_n,
                since=since,
                min_support=min_support,
                empty_window=empty_window,
            )
        )


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--min-nodes",
    "min_nodes",
    type=int,
    default=30,
    show_default=True,
    help="Minimum normalized AST-node count; smaller functions are ignored as trivial.",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=20,
    show_default=True,
    help="Maximum duplicate clusters to show.",
)
@click.option(
    "--members",
    "min_members",
    type=int,
    default=2,
    show_default=True,
    help="Minimum functions in a cluster for it to count as duplication.",
)
@click.option(
    "--co-change/--no-co-change",
    "co_change",
    default=True,
    show_default=True,
    help="Demote clusters whose copies never co-change in git history (needs git).",
)
@click.option(
    "--near-miss",
    "near_miss",
    is_flag=True,
    default=False,
    help="Also find Type-3 (gapped) near-miss clones via token overlap (slower, lower confidence).",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def duplicates(
    path: Path,
    min_nodes: int,
    top_n: int,
    min_members: int,
    co_change: bool,
    near_miss: bool,
    fmt: str,
) -> None:
    """Surface clusters of functions with identical normalized body shape.

    Folds identifiers and literals to placeholders, hashes the body's AST shape,
    and clusters matches (see `archy.duplicates`). Output is two tiers: "likely
    duplicate(s)" to investigate, and demoted "variant(s)" that are likely
    intentional (same-class siblings, boilerplate, test/vendored copies, and -
    with `--co-change`, the default when git is present - copies whose files are
    actively maintained yet never change together, i.e. deliberately independent
    implementations). `--near-miss` adds a lower-confidence Type-3 tier: gapped
    clones (a copy with statements inserted/removed) that the exact shape-hash
    cannot see, found by token-multiset overlap. Advisory only (never changes
    `archy score`); a cluster means "investigate," not "provably identical."
    Refactorability is a semantic call, so the ~50% precision ceiling is left to
    the reader's judgment. Trivial functions below `--min-nodes` are skipped.
    """
    # Validate before any parse work so bad flags fail fast and consistently.
    _require(min_nodes >= 1, "min-nodes", ">= 1", min_nodes)
    _require(top_n >= 1, "top", ">= 1", top_n)
    _require(min_members >= 2, "members", ">= 2", min_members)
    try:
        modules, parse_results = parse_project(
            path, **_graph_kwargs_or_exit(path), max_modules=resolve_max_modules(path)
        )
    except ScanTooLargeError as exc:
        raise click.ClickException(str(exc)) from exc
    rows = classify_variants(
        compute_duplicates(modules, parse_results, min_size=min_nodes, min_members=min_members)
    )
    if co_change:
        # git-backed precision layer (#242): demote clusters whose copies never
        # co-change. Best-effort - a non-git tree simply leaves the tiers as-is.
        cochange = git_cochange(path, keep_paths=frozenset(str(m.path) for m in modules))
        if cochange is not None:
            rows = demote_independent(
                rows, counts=cochange.counts, pair_support=cochange.pair_support
            )
    if near_miss:
        # Type-3 recall layer (#246): opt-in token-overlap pass over the
        # singletons the exact hash missed, appended as `near_miss` groups the
        # renderers show in their own lower-confidence section.
        rows = rows + compute_near_duplicates(modules, parse_results, min_size=min_nodes)
    if fmt == "json":
        payload = _duplicates_to_dict(rows, top_n=top_n, min_nodes=min_nodes)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(_duplicates_to_text(rows, top_n=top_n, min_nodes=min_nodes))


@main.command(name="what-to-refactor-next")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=10,
    show_default=True,
    help="Maximum priorities to show.",
)
@click.option(
    "--since",
    default=None,
    help=(
        "Restrict churn to commits after this date "
        "(e.g. 2024-01-01, 2 weeks ago) or git ref "
        "(tag/branch/SHA); invalid values are refused."
    ),
)
@click.option(
    "--min-risk",
    type=float,
    default=DEFAULT_MIN_RISK,
    show_default=True,
    help="Structural floor: a module must clear this edit-risk to be surfaced.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def what_to_refactor_next(
    path: Path, top_n: int, since: str | None, min_risk: float, fmt: str
) -> None:
    """Fused refactor-priority list: CC x churn hotspots + edit-risk.

    Merges the behavioral lens (`archy hotspots`) and the structural lens
    (edit-risk) into a summed priority, so a module flagged by both generally
    outranks a comparable single-lens module (a dominant single-lens signal
    can still rank first). Without git the behavioral lens is skipped and the
    ranking is structural-only. An empty list is a real answer: nothing is
    both complex+churned and nothing is central+fragile above --min-risk.
    """
    _require(top_n >= 1, "top", ">= 1", top_n)
    _require(0.0 <= min_risk <= 1.0, "min-risk", "in [0, 1]", min_risk)
    g = _load_graph(path, internal_only=True)
    no_commits = False
    try:
        churn = git_churn(path, since=since)
    except InvalidSinceError as exc:
        raise click.ClickException(str(exc)) from exc
    except NoCommitsError:
        churn = None
        no_commits = True
    rows = compute_refactor_priorities(g, churn=churn, min_risk=min_risk)
    empty_window = churn is not None and _empty_since_window(path, since, rows)
    if fmt == "json":
        payload = _refactor_to_dict(
            rows,
            top_n=top_n,
            since=since,
            min_risk=min_risk,
            git_available=churn is not None,
            no_commits=no_commits,
            empty_window=empty_window,
        )
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(
            _refactor_to_text(
                rows,
                top_n=top_n,
                min_risk=min_risk,
                git_available=churn is not None,
                no_commits=no_commits,
                empty_window=empty_window,
                since=since,
            )
        )


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to write the snapshot. Defaults to PATH/.archy/baseline.json.",
)
@click.option(
    "--findings-only",
    is_flag=True,
    default=False,
    help=(
        "Write only the findings (cycles, violations, module list, rules fingerprint), "
        "sorted, with no score or line numbers: the form to commit as the "
        "`diff --strict` baseline."
    ),
)
@_layer_config_errors
def snapshot(path: Path, out_path: Path | None, findings_only: bool) -> None:
    """Capture score, cycles, and layer violations as a baseline for `archy diff`."""
    g, full = _load_graph_pair(path)
    snap = take_snapshot(g, config_path=discover_config(path), reach_graph=full)
    target = out_path or default_baseline_path(path)
    write_snapshot(snap, target, findings_only=findings_only)
    click.echo(f"# baseline written to {target}")
    click.echo(_snapshot_to_text(snap))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--baseline",
    "baseline_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to a baseline JSON. Defaults to PATH/.archy/baseline.json.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=5,
    help="Max items shown per regression / improvement list in the summary.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help=(
        "Exit 1 on a cycle or layer violation the baseline does not carry, or when "
        "archy.yaml removes or narrows a rule the baseline fingerprint records. "
        "Never on score. Requires --baseline: a committed file, never "
        ".archy/baseline.json."
    ),
)
@_layer_config_errors
def diff(path: Path, baseline_path: Path | None, fmt: str, top_n: int, strict: bool) -> None:
    """Compare current state against a baseline snapshot.

    Reports a risk-weighted summary (headline + top regressions / improvements),
    per-component score deltas, newly added cycles/violations, and any
    cycles/violations that have been resolved since the baseline.

    With --strict, it is a ratchet for a codebase that already has cycles or
    violations: findings the baseline records are carried, new ones fail.
    """
    # Validate before the baseline is read so a bad --top fails fast and
    # consistently (a non-positive value otherwise silently truncated the
    # list: --top 0 dropped the whole section, --top -1 the last regression).
    # Every other --top flag refuses values below 1.
    _require(top_n >= 1, "top", ">= 1", top_n)
    if strict and baseline_path is None:
        # `.archy/baseline.json` is what `archy snapshot` (and the MCP
        # `archy_snapshot` an agent is told to call per task) overwrites. A
        # gate reading it would let any re-snapshot grandfather a new cycle.
        raise click.UsageError(
            "--strict needs --baseline pointing at a committed file (create one with "
            f"`{create_baseline_command(path, Path('archy-baseline.json'))}`); it never "
            "reads .archy/baseline.json, which `archy snapshot` overwrites."
        )
    target = baseline_path or default_baseline_path(path)
    if strict and (
        problem := strict_baseline_problem(path, target, suggested=Path("archy-baseline.json"))
    ):
        raise click.UsageError(problem)
    try:
        baseline = read_snapshot(target)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if baseline is None:
        raise click.ClickException(
            missing_baseline_hint(path, target, committed=baseline_path is not None)
        )
    g, full = _load_graph_pair(path)
    config_path = discover_config(path)
    current = take_snapshot(g, config_path=config_path, reach_graph=full)
    result = compute_diff(baseline, current)
    update: dict[str, object] = {"summary": summarize_diff(result, g, top_n=top_n)}
    if strict:
        update["strict"] = strict_verdict(
            baseline,
            current,
            graph=g,
            root=path,
            target=target,
            label=str(target),
            config_path=config_path,
            location_root=Path.cwd(),
        )
    result = result.model_copy(update=update)
    if fmt == "json":
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(_diff_to_text(result))
    if result.strict is not None and not result.strict.passed:
        sys.exit(1)


def _parse_edge_spec(spec: str) -> tuple[str, str]:
    """Split a `SRC:DST` CLI edge into `(SRC, DST)`.

    Qualnames use dots, so `:` is an unambiguous separator between them.
    File paths containing `:` (e.g. a Windows drive) are not supported on the
    CLI; use the MCP `{from, to}` form for those.
    """
    parts = spec.split(":")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise click.BadParameter(f"expected SRC:DST, got {spec!r}")
    return (parts[0], parts[1])


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--add",
    "add_specs",
    metavar="SRC:DST",
    multiple=True,
    help="Hypothetical import edge to add (module or file paths). Repeatable.",
)
@click.option(
    "--remove",
    "remove_specs",
    metavar="SRC:DST",
    multiple=True,
    help="Hypothetical import edge to remove. Repeatable.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
@_layer_config_errors
def simulate(
    path: Path, add_specs: tuple[str, ...], remove_specs: tuple[str, ...], fmt: str
) -> None:
    """Predict the structural consequence of an import-edge delta, no files written.

    Apply a hypothetical `--add`/`--remove` of import edges to an in-memory copy
    of the graph and report the new/resolved cycles, new back-edges, new layer
    rules broken, per-axis score delta, and blast-radius change, before you edit.
    """
    g, full = _load_graph_pair(path)
    try:
        result = find_simulate(
            g,
            add=[_parse_edge_spec(s) for s in add_specs],
            remove=[_parse_edge_spec(s) for s in remove_specs],
            config_path=discover_config(path),
            project_root=path,
            reach_graph=full,
        )
    except ValueError as err:
        # Empty delta used to report coverage "complete" / +0.000 (#661).
        raise click.UsageError("pass at least one --add or --remove") from err
    if fmt == "json":
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(_simulate_to_text(result))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--config",
    "config_filename",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to .importlinter or pyproject.toml. Defaults to PATH/.importlinter.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def contracts(path: Path, config_filename: Path | None, fmt: str) -> None:
    """Run import-linter contracts via archy and report violations.

    Wraps import-linter so contract findings flow through the same channel
    as `archy check` / `archy score`. Requires `pip install archy[contracts]`.

    Config resolution: `--config` wins; otherwise prefers `.importlinter`
    in PATH (canonical, supports all five contract types and
    ignore_imports whitelists); falls back to translating `archy.yaml`'s
    `forbid:` rules to Forbidden contracts (best-effort, no whitelists).
    """
    try:
        result = run_contracts(path, config_filename=config_filename)
    except (ContractsNotAvailable, ContractsConfigError) as exc:
        # Exit 1 (not 2) so config/availability errors share the failure code
        # used by `check`/`score` gate failures; CI treats any non-zero
        # uniformly, and POSIX reserves 2 for argument/usage misuse.
        click.echo(str(exc), err=True)
        sys.exit(1)

    if fmt == "json":
        click.echo(json.dumps(_contracts_to_dict(result), indent=2, sort_keys=True))
    else:
        click.echo(_contracts_to_text(result))
    # `verified`, not `all_kept`: exiting 0 on a contract that could not have
    # failed is what let a forbidden transitive path ship green (#435).
    sys.exit(0 if result.verified else 1)


def _dsm_from_path(
    path: Path,
    *,
    group_by: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
):
    """Load the graph and build the DSM, for `dsm` and `render` (#422).

    The six knobs were written out at both call sites, so adding a seventh
    meant remembering to edit two places. The `archy.dsm` import stays deferred
    here, as it was at both sites: it pulls the matrix machinery in only for the
    two commands that need it.
    """
    from archy.dsm import GroupBy, Weight, build_dsm

    try:
        return build_dsm(
            _load_graph(path, internal_only=False),
            group_by=cast(GroupBy, group_by),
            weight=cast(Weight, weight),
            focus=focus,
            focus_depth=focus_depth,
            package=package,
            suggest=suggest_modules,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--group",
    "group_by",
    type=click.Choice(["community", "layer", "topological"]),
    default="community",
    help="How to order rows/columns. Defaults to community-detected blocks.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["ascii", "json"]),
    default="ascii",
    help="Output format. ASCII rejects graphs over --max-nodes.",
)
@click.option(
    "--weight",
    type=click.Choice(["imports", "calls"]),
    default="imports",
    help="Cell value: binary edge presence (imports) or call_count (calls).",
)
@click.option(
    "--focus",
    type=str,
    default=None,
    help="Qualname to focus on; keeps focus + its N-hop neighborhood.",
)
@click.option(
    "--focus-depth",
    type=int,
    default=1,
    help="Hop count for --focus (default 1).",
)
@click.option(
    "--package",
    type=str,
    default=None,
    help="Keep only modules whose qualname starts with this prefix.",
)
@click.option(
    "--max-nodes",
    type=int,
    default=80,
    help="ASCII rendering refuses graphs larger than this.",
)
@click.option(
    "--diff",
    "diff_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Path to a previously written DSM JSON. Renders the diff against current.",
)
def dsm(
    path: Path,
    group_by: str,
    fmt: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
    max_nodes: int,
    diff_path: Path | None,
) -> None:
    """Design Structure Matrix view of the import graph.

    A DSM places modules on both axes in a chosen ordering; cell
    (row=source, col=target) is non-empty when source imports target.
    Use `--group=community` to see block-diagonal cohesion,
    `--group=layer` for layer-violation forensics, or
    `--group=topological` to localize back-edges (cycles appear as
    above-diagonal entries within an SCC block).

    For large projects, narrow with `--focus=<module>` or
    `--package=<prefix>`, or use `--format=json` and let the agent
    consume the structured view.
    """
    _require(focus_depth >= 0, "focus-depth", ">= 0", focus_depth)
    _require(max_nodes >= 1, "max-nodes", ">= 1", max_nodes)

    from archy.dsm import (
        diff_dsm,
        read_dsm,
        render_ascii,
        render_diff_text,
        render_json,
    )

    current = _dsm_from_path(
        path,
        group_by=group_by,
        weight=weight,
        focus=focus,
        focus_depth=focus_depth,
        package=package,
    )

    if diff_path is not None:
        try:
            before = read_dsm(diff_path)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        if before is None:
            raise click.ClickException(f"no DSM snapshot at {diff_path}.")
        diff = diff_dsm(before, current)
        if fmt == "json":
            click.echo(json.dumps(diff.model_dump(), indent=2, sort_keys=True))
        else:
            click.echo(render_diff_text(diff, current, before))
        return

    if fmt == "json":
        click.echo(json.dumps(render_json(current), indent=2, sort_keys=True))
    else:
        click.echo(render_ascii(current, max_nodes=max_nodes))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--view",
    type=click.Choice(["dsm", "trend"]),
    default="dsm",
    show_default=True,
    help="Which view to render.",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Write the HTML here. Defaults to stdout.",
)
@click.option(
    "--group",
    "group_by",
    type=click.Choice(["community", "layer", "topological"]),
    default="community",
    help="dsm view: how to order rows/columns.",
)
@click.option(
    "--weight",
    type=click.Choice(["imports", "calls"]),
    default="imports",
    help="dsm view: cell value, edge presence (imports) or call_count (calls).",
)
@click.option(
    "--focus",
    type=str,
    default=None,
    help="dsm view: qualname to focus on; keeps focus + its N-hop neighborhood.",
)
@click.option(
    "--focus-depth",
    type=int,
    default=1,
    help="dsm view: hop count for --focus.",
)
@click.option(
    "--package",
    type=str,
    default=None,
    help="dsm view: keep only modules whose qualname starts with this prefix.",
)
@click.option(
    "--max-nodes",
    type=int,
    default=RENDER_MAX_NODES,
    show_default=True,
    help="dsm view: refuse to render matrices larger than this.",
)
@click.option(
    "--last",
    "last_n",
    type=int,
    default=10,
    show_default=True,
    help="trend view: number of most-recent records to plot.",
)
def render(
    path: Path,
    view: str,
    out_path: Path | None,
    group_by: str,
    weight: str,
    focus: str | None,
    focus_depth: int,
    package: str | None,
    max_nodes: int,
    last_n: int,
) -> None:
    """Render a view as a self-contained HTML file (offline, no JS, no CDN).

    `--view=dsm` draws the Design Structure Matrix, back-edges in red;
    `--view=trend` plots the five axes over .archy/history.jsonl. Output is
    byte-stable for a fixed input, so two exports diff cleanly.

    Options are per-view (see the `dsm view:` / `trend view:` help text) and
    ignored by the other view. There is no `graph` view: a node-link diagram
    needs a vendored layout engine and carries the least signal of the three,
    so it stays deferred (docs/SPEC_VISUALIZATION.md).
    """
    if view == "trend":
        _require(last_n >= 1, "last", ">= 1", last_n)
        rows = read_history(path / ".archy" / "history.jsonl")
        html = render_trend_html(rows, last_n=last_n)
    else:
        _require(focus_depth >= 0, "focus-depth", ">= 0", focus_depth)
        _require(max_nodes >= 1, "max-nodes", ">= 1", max_nodes)

        matrix = _dsm_from_path(
            path,
            group_by=group_by,
            weight=weight,
            focus=focus,
            focus_depth=focus_depth,
            package=package,
        )
        try:
            html = render_dsm_html(matrix, max_nodes=max_nodes)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

    if out_path is None:
        click.echo(html, nl=False)
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    click.echo(f"Wrote {out_path}", err=True)


@main.command()
def mcp() -> None:
    """Run archy as an MCP server on stdio for AI agent integration."""
    from archy.mcp import create_server

    create_server().run()


@main.group()
def index() -> None:
    """Manage the persistent parse cache at `.archy/index.db`.

    The cache speeds up repeated graph builds by storing each file's parse
    result keyed by content hash; it is a pure optimization and safe to delete.
    """


@index.command("sync")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
def index_sync(path: Path) -> None:
    """Refresh the cache for PATH and report what changed.

    Re-parses only files whose content changed since the last sync, prunes
    entries for deleted files, and leaves the rest untouched.
    """
    from archy.index import default_db_path, open_index
    from archy.index import sync as sync_index

    conn = open_index(default_db_path(path))
    try:
        modules = discover_modules(path, **_graph_kwargs_or_exit(path))
        # Apply the same scan-size backstop as the graph commands: `index sync`
        # reparses every changed file, so a stray 40k-file vendored dir would
        # wedge here too even though it never builds the graph (#216).
        limit = resolve_max_modules(path)
        if limit and len(modules) > limit:
            raise click.ClickException(str(ScanTooLargeError(len(modules), path.resolve(), limit)))
        _results, stats = sync_index(conn, modules)
    finally:
        conn.close()
    click.echo(
        f"synced {stats.total} module(s): "
        f"{stats.reparsed} reparsed, {stats.unchanged} unchanged, {stats.pruned} pruned."
    )


@index.command("clear")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
def index_clear(path: Path) -> None:
    """Delete the cache database for PATH (the next build cold-rebuilds it)."""
    from archy.index import default_db_path

    db = default_db_path(path)
    if db.exists():
        db.unlink()
        click.echo(f"removed {db}")
    else:
        click.echo("no cache to remove.")


@index.command("status")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
def index_status(path: Path) -> None:
    """Report the persistent cache's freshness for PATH.

    Diagnostic plumbing demoted off the MCP surface in v0.41 (#267): every
    analysis tool syncs on demand, so a result is never stale and an agent
    rarely needs this mid-task. Reports the on-disk cache state (db location
    and cached file count); `watching` is an MCP-server-only concept and does
    not apply to a one-shot CLI call.
    """
    from archy.index import default_db_path, open_index

    db = default_db_path(path)
    if not db.exists():
        click.echo(f"no cache at {db} (a build or `archy index sync` will create it).")
        return
    conn = open_index(db)
    try:
        row = conn.execute("SELECT COUNT(*) FROM files").fetchone()
    finally:
        conn.close()
    cached = int(row[0]) if row else 0
    click.echo(f"cache: {db}\ncached_files: {cached}")


def _confirm_targets(adapters, location: str, action: str) -> bool:
    """Preview the affected adapters and prompt before install/uninstall writes.

    Returns False if the user declines (caller should abort). `action` is the
    verb phrase spliced into "archy will {action} ({location}):" -- "configure"
    for install, "be removed from" for uninstall.
    """
    from archy.install import detect_all

    detected = {d.adapter.id for d in detect_all() if d.detected}
    click.echo(f"archy will {action} ({location}):")
    for adapter in adapters:
        mark = "detected" if adapter.id in detected else "not detected"
        click.echo(f"  - {adapter.name} ({adapter.id}, {mark})")
    if not click.confirm("Proceed?", default=True):
        click.echo("Aborted.")
        return False
    return True


def _config_change(preview: ConfigPreview) -> str:
    """The lines archy writes into one file, and a count of what else changes.

    Shared files such as ~/.claude.json hold the user's whole client config,
    other servers' secrets included (#519). Diffing the on-disk file would print
    every line the writer reformats (re-indented JSON, `\\u` escapes, TOML inline
    tables), so archy's lines come from comparing against `without_archy`, the
    user's content in the writer's own format. Only those lines print; every
    other changed line is counted, never shown. A file that does not exist yet
    is archy's own, so it prints whole.
    """
    if preview.existing is None:
        return preview.rendered
    if preview.existing == preview.rendered:
        return "(no changes)"
    rendered = preview.rendered.split("\n")
    out = ["archy writes these lines (line numbers are in the result):"]
    for start, end in _inserted(preview.without_archy.split("\n"), rendered):
        out.append(f"@@ +{start + 1},{end - start} @@")
        out += [f"+{line}" for line in rendered[start:end]]
    changed = _removed_count(preview.existing.split("\n"), rendered)
    if changed:
        out.append(
            f"# install also changes {changed} other existing line(s) here: formatting, "
            "or an older archy entry it replaces, custom args or env included. "
            "--full prints the whole result."
        )
    return "\n".join(out)


def _matcher(before: list[str], after: list[str]) -> difflib.SequenceMatcher[str]:
    """Match lines ignoring one trailing comma.

    Appending an entry to a JSON object or list adds a comma to the user's
    previous last line; ignoring it keeps that line matched, so it neither
    prints nor pairs off with archy's own closing brace. Lines are split on
    "\\n" alone, so a CRLF file still counts as changed, which it is.
    """
    return difflib.SequenceMatcher(
        None,
        [line.removesuffix(",") for line in before],
        [line.removesuffix(",") for line in after],
        autojunk=False,
    )


def _inserted(before: list[str], after: list[str]) -> list[tuple[int, int]]:
    """Line ranges of `after` that are new relative to `before`."""
    return [
        (j1, j2)
        for tag, _, _, j1, j2 in _matcher(before, after).get_opcodes()
        if tag in ("insert", "replace")
    ]


def _removed_count(before: list[str], after: list[str]) -> int:
    """How many lines of `before` are deleted or rewritten in `after`."""
    return sum(
        i2 - i1
        for tag, i1, i2, _, _ in _matcher(before, after).get_opcodes()
        if tag in ("delete", "replace")
    )


@main.command()
@click.option(
    "--target",
    default="auto",
    show_default=True,
    metavar="auto|all|<id>[,<id>...]",
    help="Which agents to configure: auto-detected, all, or a comma list of ids.",
)
@click.option(
    "--location",
    type=click.Choice(["global", "local"]),
    default="global",
    show_default=True,
    help="Configure for every project (global) or just this one (local).",
)
@click.option(
    "--project-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Project root for --location=local writes (defaults to the cwd).",
)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.option(
    "--no-permissions",
    is_flag=True,
    help="Do not seed Claude's permission allowlist for the archy tools.",
)
@click.option(
    "--print-config",
    "print_id",
    default=None,
    metavar="<id>",
    help="Print the lines install would write for one agent, then exit.",
)
@click.option(
    "--full",
    is_flag=True,
    help="With --print-config, print each whole file instead of the diff.",
)
def install(
    target: str,
    location: str,
    project_root: Path | None,
    yes: bool,
    no_permissions: bool,
    print_id: str | None,
    full: bool,
) -> None:
    """Wire archy's MCP server into your AI coding agents.

    Detects installed clients (Claude Code, Cursor, Codex CLI, opencode,
    Continue), writes each one's MCP config and rules file, and seeds Claude's
    permission allowlist. Re-running is idempotent.
    """
    from archy.install import (
        InstallError,
        Scope,
        preview_config,
        resolve_targets,
        run_install,
    )

    scope = Scope.LOCAL if location == "local" else Scope.GLOBAL
    seed_permissions = not no_permissions

    if full and print_id is None:
        raise click.UsageError("--full only applies with --print-config.")

    try:
        if print_id is not None:
            previews = preview_config(
                print_id,
                scope,
                project_root=project_root,
                seed_permissions=seed_permissions,
            )
            for preview in previews:
                click.echo(f"# {preview.path}")
                click.echo(preview.rendered if full else _config_change(preview))
            return

        adapters = resolve_targets(target)

        if not yes and not _confirm_targets(adapters, location, "configure"):
            return

        result = run_install(
            adapters,
            scope,
            project_root=project_root,
            seed_permissions=seed_permissions,
        )
    except InstallError as exc:
        raise click.ClickException(str(exc)) from exc

    paths_written = result.all_paths()
    click.echo(f"Wrote {len(paths_written)} file(s):")
    for path in paths_written:
        click.echo(f"  {path}")
    for note in result.all_notes():
        click.echo(note)
    click.echo("Restart your agent client(s) to pick up the archy MCP server.")


@main.command()
@click.option(
    "--target",
    default="auto",
    show_default=True,
    metavar="auto|all|<id>[,<id>...]",
    help="Which agents to clean up: auto-detected, all, or a comma list of ids.",
)
@click.option(
    "--location",
    type=click.Choice(["global", "local"]),
    default="global",
    show_default=True,
    help="Remove from every project (global) or just this one (local).",
)
@click.option(
    "--project-root",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Project root for --location=local removals (defaults to the cwd).",
)
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.option(
    "--no-permissions",
    is_flag=True,
    help="Leave Claude's permission allowlist untouched.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be removed or stripped, then exit without changing anything.",
)
def uninstall(
    target: str,
    location: str,
    project_root: Path | None,
    yes: bool,
    no_permissions: bool,
    dry_run: bool,
) -> None:
    """Remove archy from your AI coding agents.

    The inverse of `archy install`: strips archy's MCP stanza, permission
    entries, and instruction block from each client's config (leaving the rest
    untouched) and deletes the files archy owns outright. Idempotent.
    """
    from archy.install import (
        InstallError,
        Scope,
        resolve_targets,
        run_uninstall,
    )
    from archy.install.writer import DryRunWriteSystem

    scope = Scope.LOCAL if location == "local" else Scope.GLOBAL
    seed_permissions = not no_permissions

    try:
        adapters = resolve_targets(target)

        if dry_run:
            ws = DryRunWriteSystem()
            run_uninstall(
                adapters,
                scope,
                project_root=project_root,
                seed_permissions=seed_permissions,
                write_system=ws,
            )
            for path in ws.removed:
                click.echo(f"delete {path}")
            for record in ws.records:
                click.echo(f"strip  {record.path}")
            if not ws.removed and not ws.records:
                click.echo("Nothing to remove; archy is not installed for these targets.")
            return

        if not yes and not _confirm_targets(adapters, location, "be removed from"):
            return

        result = run_uninstall(
            adapters,
            scope,
            project_root=project_root,
            seed_permissions=seed_permissions,
        )
    except InstallError as exc:
        raise click.ClickException(str(exc)) from exc

    touched = result.all_paths()
    click.echo(f"Cleaned up {len(touched)} file(s):")
    for path in touched:
        click.echo(f"  {path}")


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--top",
    "top_n",
    type=int,
    default=12,
    show_default=True,
    help="Maximum rows per section.",
)
@click.option(
    "--min-family",
    "min_family",
    type=int,
    default=2,
    show_default=True,
    help="Minimum members before a naming family or mirrored set is reported.",
)
@click.option(
    "--module",
    "module",
    default=None,
    help="Report everything known about ONE module, complete and unranked: what it "
    "imports, what imports it, and whether it was set aside. Answers a negative, "
    "which the ranked report cannot.",
)
@click.option(
    "--include-tests",
    "include_tests",
    is_flag=True,
    default=False,
    help="Census test modules too. Off by default: test fixtures outnumber the "
    "code they exercise and win any count they are entered in.",
)
@click.option(
    "--emit-headers",
    "emit_headers",
    is_flag=True,
    default=False,
    help="Derive a per-module header block (what it owns, what mirrors it, whether a "
    "finding here gates) and print it. Prints only; add --write to put it in the files.",
)
@click.option(
    "--write",
    "write",
    is_flag=True,
    default=False,
    help="With --emit-headers, write each block into its module docstring. The only "
    "archy command that modifies your source, so it never happens implicitly.",
)
@click.option(
    "--check",
    "check_headers",
    is_flag=True,
    default=False,
    help="With --emit-headers, exit 1 if any header is missing or stale. For CI, so a "
    "derived block cannot rot into a lie the way hand-written docs do.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def conventions(
    path: Path,
    top_n: int,
    min_family: int,
    module: str | None,
    include_tests: bool,
    emit_headers: bool,
    write: bool,
    check_headers: bool,
    fmt: str,
) -> None:
    """Report the project's own house style, derived from its source.

    Answers the four questions an agent otherwise re-derives by reading:
    what is a new thing a kind of and where does it live (kinds, naming,
    registries), how many parallel surfaces must it be wired through
    (surfaces, export gaps, doc gaps), does a new finding fail the build or only
    warn (gates, plus the constants and keyword defaults a family
    declares), and what shape are the value types (models).

    Test modules are set aside by default -- fixtures outnumber the code
    they exercise -- as are subtrees that duplicate their own parent, such
    as a vendored copy of a previous major version. The header says what
    was set aside and why. Pass `--include-tests` to census them too.

    Advisory, never a gate: it reports and always exits 0. Only `check`,
    `contracts` and the `--strict` variants fail a build.
    """
    _require(top_n >= 1, "top", ">= 1", top_n)
    _require(min_family >= 2, "min-family", ">= 2", min_family)
    # Before the `--module` early return, or the flags it cannot honour are
    # parsed and dropped on the floor.
    _require_headers_flags(emit_headers, write, check_headers, module)
    if module:
        # 🔴 A lookup, not a ranking. Every list is complete for this module, so
        # absence from one is an answer rather than an artefact of truncation.
        try:
            view = compute_module_view(
                path, module, **_graph_kwargs_or_exit(path), include_tests=include_tests
            )
        except LookupError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            json.dumps(view.model_dump(), indent=2, sort_keys=True)
            if fmt == "json"
            else _module_view_to_text(view)
        )
        return
    report = compute_conventions(
        path, **_graph_kwargs_or_exit(path), min_family=min_family, include_tests=include_tests
    )
    if emit_headers:
        _emit_headers(
            report, path, write=write, check=check_headers, fmt=fmt, include_tests=include_tests
        )
        return
    if fmt == "json":
        click.echo(json.dumps(_conventions_to_dict(report, top_n=top_n), indent=2, sort_keys=True))
    else:
        click.echo(_conventions_to_text(report, top_n=top_n))


@main.command()
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--top", "top_n", type=int, default=8, show_default=True, help="Maximum rows per section."
)
@click.option(
    "--contracts",
    "with_contracts",
    is_flag=True,
    default=False,
    help="Include the transitive import-linter verdict.",
)
@click.option(
    "--include-tests", "include_tests", is_flag=True, default=False, help="Census test modules too."
)
@click.option(
    "--format", "fmt", type=click.Choice(["text", "json"]), default="text", help="Output format."
)
def brief(path: Path, top_n: int, with_contracts: bool, include_tests: bool, fmt: str) -> None:
    """One screen to hand an agent BEFORE it starts on an unfamiliar repository.

    Composes what is already computed -- `conventions`, the co-update sets,
    the gate inventory, and `check`'s coverage -- into a single bounded answer
    to "what do I need to know before I touch this?".

    \b
    Why a command rather than a note telling you to run six:
    reading is far cheaper than writing on an inference box, so a briefing
    costs seconds of prefill while the model working the same facts out for
    itself costs tens of seconds of decode per block. That trade only pays if
    the briefing arrives without being asked for, which means one invocation a
    hook or a harness can make.

    Advisory on content (always exits 0 when the config parses). A malformed
    archy.yaml is a one-line Error and exit 1, same as the other graph
    commands (#524); `check` and `contracts` remain the gates.

    \b
    The same briefing is the `archy_brief` MCP tool (#427), with the same
    field names in its payload as `--format json` here.
    """
    _require(top_n >= 1, "top", ">= 1", top_n)
    kw = _graph_kwargs_or_exit(path)
    report = compute_conventions(path, **kw, include_tests=include_tests)

    coverage = config = None
    config_path = discover_config(path)
    if config_path is not None:
        try:
            config = load_config(config_path)
            coverage = compute_coverage(build_graph(path, **kw), config)
        except LayerConfigError:
            # Parse failures already exit via `_graph_kwargs_or_exit` above. This
            # catch only blanks coverage if a late load fails after kwargs succeeded.
            coverage = config = None

    contracts = _run_check_contracts(path, None) if with_contracts else None

    if fmt == "json":
        payload = {
            "conventions": _conventions_to_dict(report, top_n=top_n),
            "coverage": _coverage_to_json(coverage) if coverage is not None else None,
            "contracts": (_contracts_outcome_to_dict(contracts) if contracts is not None else None),
            # brief's TEXT output has rendered this handoff from the start; its
            # JSON had not, which left the same clean-pass-says-nothing gap #343
            # closed on `check` open one command over. `violations` is empty
            # because brief does not run the direct pass: it reports what the
            # config cannot see, not what it caught.
            "transitive_checked": _transitive_checked(contracts),
            "transitive_unverified_reason": (
                _transitive_unverified_reason(
                    config,
                    coverage,
                    [],
                    no_verdict_error=_no_verdict_error(contracts),
                    unverifiable=_unverifiable_contracts(contracts),
                )
                # brief tolerates a missing or malformed archy.yaml and still
                # reports; `check` cannot get here without one.
                if config is not None
                and coverage is not None
                and not _transitive_checked(contracts)
                else None
            ),
        }
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(_brief_to_text(report, coverage, config, contracts, top_n=top_n))


def _graph_kwargs_or_exit(path: Path) -> dict:
    """`graph_kwargs` for CLI callers: turn LayerConfigError into ClickException.

    `graph_kwargs` itself stays raising so MCP can map LayerConfigError to isError.
    Every CLI site that used to call `graph_kwargs(path)` (and `_load_graph`) goes
    through this helper or the matching catch in `_load_graph`, so a malformed
    archy.yaml prints a one-line Error instead of a traceback (#524, same class as #207).
    """
    try:
        return graph_kwargs(path)
    except LayerConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _load_graph(path: Path, *, internal_only: bool) -> nx.DiGraph:
    try:
        g = build_graph(
            path,
            **_graph_kwargs_or_exit(path),
            max_modules=resolve_max_modules(path),
        )
    except ScanTooLargeError as exc:
        raise click.ClickException(str(exc)) from exc
    except LayerConfigError as exc:
        # resolve_max_modules also loads the config; keep the same one-line Error.
        raise click.ClickException(str(exc)) from exc
    if internal_only:
        external = {n for n, d in g.nodes(data=True) if d.get("external")}
        g.remove_nodes_from(external)
    return g


def _load_graph_pair(path: Path) -> tuple[nx.DiGraph, nx.DiGraph]:
    """Return `(internal_only, full)` from ONE scan, for the snapshot paths.

    Score and cycles want the internal-only graph; required-reach rules need the
    full one, because `must_reach` may name an external package. Built once and
    copied rather than scanned twice.
    """
    full = _load_graph(path, internal_only=False)
    return internal_subgraph(full), full


def _gate_against_history(current: Score, history_path: Path) -> dict:
    rows = read_history(history_path)
    if not rows:
        return {"previous": None, "current": current.overall, "delta": None}
    previous = rows[-1]
    return {
        "previous": previous.overall,
        "previous_commit": previous.commit,
        "previous_timestamp": previous.timestamp,
        "current": current.overall,
        "delta": current.overall - previous.overall,
    }


def _run_check_contracts(path: Path, config_filename: Path | None) -> ContractsOutcome:
    """Run contracts for `check --contracts`, and never let it fail the check.

    A missing `import-linter` or an unreadable contracts config is a reason the
    EXTRA verdict is unavailable, not a reason the layer check did not happen.
    The standalone `contracts` command exits 1 on both, which is right when the
    verdict is the whole point and wrong when it is an addendum: a flag that can
    turn a passing check into a failing one because an optional dependency is
    absent would make `--contracts` unsafe to leave on in CI.

    The two failures are distinguished the way `mcp_runs.run_contracts` distinguishes
    them: a missing dependency is `available=False`, while a config archy could
    not read is `available=True` with the reason attached.
    """
    # Function-local on purpose: it resolves `run_contracts` through the module
    # at CALL time, which is what lets a test substitute a raising stub to
    # exercise the no-verdict branch. (The module-level import already binds the
    # name early, so it would not see the stub.)
    from archy.contracts import run_contracts

    try:
        return ContractsOutcome(result=run_contracts(path, config_filename=config_filename))
    except ContractsNotAvailable as exc:
        click.echo(f"# --contracts unavailable: {exc}", err=True)
        return ContractsOutcome(available=False, error=str(exc))
    except ContractsConfigError as exc:
        click.echo(f"# --contracts unavailable: {exc}", err=True)
        return ContractsOutcome(available=True, error=str(exc))


def _transitive_checked(outcome: ContractsOutcome | None) -> bool:
    """Did this run actually reach a transitive verdict?

    Not "were contracts requested": a request that could not run leaves the
    rules exactly as unverified as never asking, and conflating the two is the
    bug #343 closed. `check` and `brief` both answer it from here so the two
    JSON payloads cannot drift apart on it.

    A contract that could not have failed is not a verdict either, so ANY
    unverifiable contract makes this false (#457). That matches `verified` on
    the result itself, so the two fields tell one story rather than two; the
    reason field carries how many, which is where the precision goes.
    """
    if outcome is None or outcome.result is None:
        return False
    return outcome.result.unverifiable == 0


def _no_verdict_error(outcome: ContractsOutcome | None) -> str | None:
    """The reason contracts produced nothing, or None if they were never asked.

    Distinguishes "not requested" (no error to report; the handoff names the
    flag) from "requested and failed" (name the actual failure, because naming
    the flag to someone who just passed it sends them round a loop).
    """
    return outcome.error if outcome is not None and outcome.result is None else None


def _unverifiable_contracts(outcome: ContractsOutcome | None) -> int:
    """How many contracts this run could not have failed, 0 if none ran.

    Shared so `check` and `brief` cannot disagree about it, the same reason
    `_transitive_checked` is a function rather than an inline expression.
    """
    return outcome.result.unverifiable if outcome is not None and outcome.result else 0


def _transitive_unverified_reason(
    config: LayerConfig,
    coverage: LayerCoverage,
    violations: list[Violation],
    *,
    no_verdict_error: str | None = None,
    unverifiable: int = 0,
) -> str | None:
    """The text handoff's reason, for a reader that cannot parse prose.

    A bare `transitive_checked: false` is a verdict without a reason, which
    AGENTS.md rules out on every surface: the consumer needs to know whether the
    rules were merely not requested or could not be proven, and what settles it.

    `no_verdict_error` distinguishes the ways this run can fail to verify.
    Naming `--contracts` to a caller who just passed it and watched it fail is
    worse than saying nothing: it sends them round a loop they have already been
    through, so that case reports the actual cause instead.

    `unverifiable` is the third way, and unlike the others it does not depend on
    thin coverage: contracts ran, and some of them could not have failed. It is
    checked before the coverage stem for that reason, or a project whose direct
    edges are fully governed would report `transitive_checked: false` with no
    reason at all.
    """
    if unverifiable:
        return (
            f"{unverifiable} contract(s) could not have failed: their module patterns "
            "matched nothing, so the rule holds whatever the code does. Fix the patterns "
            "or drop the rule; `archy contracts` names the expressions that matched no module."
        )
    stem = _unverified_forbid_rules(config, coverage, violations)
    if stem is None:
        return None
    if no_verdict_error is not None:
        return (
            f"{stem} and still unverified: `--contracts` produced no transitive verdict "
            f"({no_verdict_error})."
        )
    return (
        f"{stem}, but this check governs too little of the graph to verify them; "
        "`--contracts` evaluates them transitively via import-linter."
    )


def _affected_test_paths(graph: nx.DiGraph, result: Affected) -> list[str]:
    """File paths for the impacted tests, suitable for `xargs pytest`.

    Falls back to the qualname if a node has no resolvable path (which
    shouldn't happen for internal nodes, but degrades safely).
    """
    out: list[str] = []
    for qualname in result.impacted_tests:
        path = graph.nodes[qualname].get("path")
        out.append(str(path) if path else qualname)
    return out


def _graph_to_dot(g: nx.DiGraph) -> str:
    lines = ["digraph imports {", '  rankdir="LR";']
    for n, d in sorted(g.nodes(data=True)):
        style = ' style="dashed" color="gray"' if d.get("external") else ""
        lines.append(f'  "{n}"[{style.strip()}];')
    for u, v in sorted(g.edges()):
        lines.append(f'  "{u}" -> "{v}";')
    lines.append("}")
    return "\n".join(lines)


def _require_headers_flags(
    emit_headers: bool, write: bool, check: bool, module: str | None
) -> None:
    """`--write` and `--check` mean nothing without `--emit-headers`, and mean opposite
    things to each other. Silently ignoring a flag someone typed is how a user comes to
    believe their source was rewritten when it was not.

    `--module` is the same failure and was live: it returns a single-module view
    early, so `--module X --write` parsed the flag, did nothing, and exited 0.
    Someone running that in a script would believe a header had been written.
    """
    if (write or check) and not emit_headers:
        raise click.UsageError("--write and --check require --emit-headers.")
    if write and check:
        raise click.UsageError("--write rewrites the tree, --check asserts it is already correct.")
    if module and (emit_headers or write or check):
        raise click.UsageError(
            "--module reports on one module; --emit-headers derives blocks for the whole tree. "
            "Run them separately."
        )


def _emit_headers(
    report: ConventionsReport,
    path: Path,
    *,
    write: bool,
    check: bool,
    fmt: str,
    include_tests: bool,
) -> None:
    """Print, write or verify the derived header blocks.

    `--check` exits 1 on a stale header and says which module, because a CI
    failure whose only content is "something drifted" costs a reader the same
    investigation the check was supposed to save.
    """
    headers = compute_headers(
        report,
        path,
        discover_modules(path, **_graph_kwargs_or_exit(path)),
        include_tests=include_tests,
    )
    stale: list[str] = []
    written: list[str] = []

    for header in headers:
        target = path / header.path
        source = target.read_text(encoding="utf-8")
        block = render_header(header)
        if check:
            if existing_block(source) != block:
                stale.append(header.module)
            continue
        if write:
            updated = apply_header(source, block)
            if updated != source:
                target.write_text(updated, encoding="utf-8")
                written.append(header.module)

    if fmt == "json":
        click.echo(
            json.dumps(
                {
                    "headers": [{**h.model_dump(), "rendered": render_header(h)} for h in headers],
                    "stale": stale,
                    "written": written,
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif check:
        if stale:
            click.echo(f"# {len(stale)} module(s) with a missing or stale archy header:")
            for module in stale:
                click.echo(f"#   {module}")
            click.echo("# `archy conventions --emit-headers --write` regenerates them.")
        else:
            click.echo(f"# {len(headers)} header(s) match the tree.")
    elif write:
        click.echo(f"# wrote {len(written)} header(s) into {len(headers)} scanned module(s).")
    else:
        for header in headers:
            click.echo(f"# {header.path}")
            click.echo(render_header(header))
            click.echo("")

    if check and stale:
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
