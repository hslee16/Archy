"""Click-based command-line interface for archy."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import click
import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy import __version__
from archy.affected import DEFAULT_DEPTH, Affected, find_affected
from archy.contracts import (
    ContractsConfigError,
    ContractsNotAvailable,
    ContractsResult,
    run_contracts,
)
from archy.conventions import (
    ConventionsReport,
    Gate,
    ModuleView,
    compute_conventions,
    compute_module_view,
)
from archy.coupling import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_SUPPORT,
    CouplingPair,
    compute_coupling,
    git_cochange,
    internal_module_paths,
)
from archy.cycles import Cycle, find_cycles
from archy.diff import (
    DiffReport,
    DiffSummary,
    Snapshot,
    compute_diff,
    read_snapshot,
    take_snapshot,
    write_snapshot,
)
from archy.diff_summary import summarize_diff
from archy.duplicates import (
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
    internal_subgraph,
    parse_project,
)
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.hotspots import Hotspot, compute_hotspots, git_churn
from archy.impact import DEFAULT_MAX_CHAINS, Impact, find_impact
from archy.layers import (
    LayerConfig,
    LayerConfigError,
    LayerCoverage,
    ReachViolation,
    RequiredRule,
    SdpViolation,
    Violation,
    compute_coverage,
    contracts_unverified,
    discover_config,
    find_reach_violations,
    find_sdp_violations,
    find_violations,
    load_config,
)
from archy.refactor import (
    DEFAULT_MIN_RISK,
    RefactorPriority,
    compute_refactor_priorities,
)
from archy.render import DEFAULT_MAX_NODES as RENDER_MAX_NODES
from archy.render import render_dsm_html, render_trend_html
from archy.score import Score, compute_score
from archy.simulate import SimulateReport, find_simulate
from archy.trend import render_text as render_trend


@click.group()
@click.version_option(__version__)
def main() -> None:
    """archy - architectural sensor for Python codebases."""


def _require(ok: bool, flag: str, constraint: str, value: object) -> None:
    """Raise a uniform ClickException for a failed CLI option bound-check."""
    if not ok:
        raise click.ClickException(f"--{flag} must be {constraint}; got {value}")


@main.command()
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
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
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path))
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
def cycles(path: Path, fmt: str, internal_only: bool, min_size: int, strict: bool) -> None:
    """Find import cycles in a Python project rooted at PATH."""
    _require(min_size >= 1, "min-size", ">= 1", min_size)

    g = _load_graph(path, internal_only=internal_only)

    found = find_cycles(g, min_size=min_size)

    if fmt == "json":
        click.echo(json.dumps(_cycles_to_json(found), indent=2, sort_keys=True))
    else:
        click.echo(_cycles_to_text(found, min_size))

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
        g = build_graph(
            path,
            ignored_dirs=DEFAULT_IGNORED_DIRS | frozenset(config.exclude),
            extra_roots=config.roots,
            max_modules=_effective_max_modules(config),
        )
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
        [path / f if not f.is_absolute() else f for f in files],
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
    "Defaults to pytest conventions: test_*.py, *_test.py, anything under a tests/ directory.",
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
    resolved = [path / f if not f.is_absolute() else f for f in file_list]
    result = find_affected(g, resolved, project_root=path, depth=depth, test_filter=test_filter)

    if as_json:
        click.echo(json.dumps(_affected_to_dict(result), indent=2, sort_keys=True))
    elif quiet:
        for test_path in _affected_test_paths(g, result):
            click.echo(test_path)
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
    help="Restrict churn to commits since this date or refspec (passed to `git log --since`).",
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
    churn = git_churn(path, since=since)
    if churn is None:
        raise click.ClickException(
            f"{path} is not inside a git repository (or git is unavailable); "
            "`archy hotspots` needs git history to compute per-file churn."
        )
    rows = compute_hotspots(g, churn=churn)
    if fmt == "json":
        payload = _hotspots_to_dict(rows, top_n=top_n, since=since)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(_hotspots_to_text(rows, top_n=top_n, since=since))


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
    help="Restrict history to commits since this date or refspec (passed to `git log --since`).",
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
    cochange = git_cochange(path, since=since, keep_paths=keep)
    if cochange is None:
        raise click.ClickException(
            f"{path} is not inside a git repository, has no commit history, or git "
            "is unavailable; `archy coupling` needs git history to compute co-change."
        )
    rows = compute_coupling(g, cochange, min_support=min_support, min_confidence=min_confidence)
    if fmt == "json":
        payload = _coupling_to_dict(rows, top_n=top_n, since=since, min_support=min_support)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(_coupling_to_text(rows, top_n=top_n, since=since, min_support=min_support))


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
            path, **_graph_kwargs(path), max_modules=_resolve_max_modules(path)
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
    help="Restrict churn to commits since this date or refspec (passed to `git log --since`).",
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
    churn = git_churn(path, since=since)
    rows = compute_refactor_priorities(g, churn=churn, min_risk=min_risk)
    if fmt == "json":
        payload = _refactor_to_dict(
            rows,
            top_n=top_n,
            since=since,
            min_risk=min_risk,
            git_available=churn is not None,
        )
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(
            _refactor_to_text(rows, top_n=top_n, min_risk=min_risk, git_available=churn is not None)
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
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write the snapshot. Defaults to PATH/.archy/baseline.json.",
)
def snapshot(path: Path, out_path: Path | None) -> None:
    """Capture score, cycles, and layer violations as a baseline for `archy diff`."""
    g, full = _load_graph_pair(path)
    snap = take_snapshot(g, config_path=discover_config(path), reach_graph=full)
    target = out_path or (path / ".archy" / "baseline.json")
    write_snapshot(snap, target)
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
    type=click.Path(path_type=Path),
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
def diff(path: Path, baseline_path: Path | None, fmt: str, top_n: int) -> None:
    """Compare current state against a baseline snapshot.

    Reports a risk-weighted summary (headline + top regressions / improvements),
    per-component score deltas, newly added cycles/violations, and any
    cycles/violations that have been resolved since the baseline.
    """
    target = baseline_path or (path / ".archy" / "baseline.json")
    baseline = read_snapshot(target)
    if baseline is None:
        raise click.ClickException(f"no baseline at {target}; run `archy snapshot {path}` first.")
    g, full = _load_graph_pair(path)
    current = take_snapshot(g, config_path=discover_config(path), reach_graph=full)
    result = compute_diff(baseline, current)
    result = result.model_copy(update={"summary": summarize_diff(result, g, top_n=top_n)})
    if fmt == "json":
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(_diff_to_text(result))


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
def simulate(
    path: Path, add_specs: tuple[str, ...], remove_specs: tuple[str, ...], fmt: str
) -> None:
    """Predict the structural consequence of an import-edge delta, no files written.

    Apply a hypothetical `--add`/`--remove` of import edges to an in-memory copy
    of the graph and report the new/resolved cycles, new back-edges, new layer
    rules broken, per-axis score delta, and blast-radius change, before you edit.
    """
    g, full = _load_graph_pair(path)
    result = find_simulate(
        g,
        add=[_parse_edge_spec(s) for s in add_specs],
        remove=[_parse_edge_spec(s) for s in remove_specs],
        config_path=discover_config(path),
        project_root=path,
        reach_graph=full,
    )
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
    sys.exit(0 if result.all_kept else 1)


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
    type=click.Path(path_type=Path),
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
        GroupBy,
        Weight,
        build_dsm,
        diff_dsm,
        read_dsm,
        render_ascii,
        render_diff_text,
        render_json,
    )

    g = _load_graph(path, internal_only=False)
    current = build_dsm(
        g,
        group_by=cast(GroupBy, group_by),
        weight=cast(Weight, weight),
        focus=focus,
        focus_depth=focus_depth,
        package=package,
    )

    if diff_path is not None:
        before = read_dsm(diff_path)
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
    type=click.Path(path_type=Path),
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

        from archy.dsm import GroupBy, Weight, build_dsm

        g = _load_graph(path, internal_only=False)
        matrix = build_dsm(
            g,
            group_by=cast(GroupBy, group_by),
            weight=cast(Weight, weight),
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
        modules = discover_modules(path, **_graph_kwargs(path))
        # Apply the same scan-size backstop as the graph commands: `index sync`
        # reparses every changed file, so a stray 40k-file vendored dir would
        # wedge here too even though it never builds the graph (#216).
        limit = _resolve_max_modules(path)
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
    help="Print the config that would be written for one agent, then exit.",
)
def install(
    target: str,
    location: str,
    project_root: Path | None,
    yes: bool,
    no_permissions: bool,
    print_id: str | None,
) -> None:
    """Wire archy's MCP server into your AI coding agents.

    Detects installed clients (Claude Code, Cursor, Codex CLI, opencode,
    Continue), writes each one's MCP config and rules file, and seeds Claude's
    permission allowlist. Re-running is idempotent.
    """
    from archy.install import (
        InstallError,
        Scope,
        print_config,
        resolve_targets,
        run_install,
    )

    scope = Scope.LOCAL if location == "local" else Scope.GLOBAL
    seed_permissions = not no_permissions

    try:
        if print_id is not None:
            files = print_config(
                print_id,
                scope,
                project_root=project_root,
                seed_permissions=seed_permissions,
            )
            for path, content in files:
                click.echo(f"# {path}")
                click.echo(content)
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
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format.",
)
def conventions(
    path: Path, top_n: int, min_family: int, module: str | None, include_tests: bool, fmt: str
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
    if module:
        # 🔴 A lookup, not a ranking. Every list is complete for this module, so
        # absence from one is an answer rather than an artefact of truncation.
        try:
            view = compute_module_view(
                path, module, **_graph_kwargs(path), include_tests=include_tests
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
        path, **_graph_kwargs(path), min_family=min_family, include_tests=include_tests
    )
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

    Advisory, always exits 0. It reports; `check` and `contracts` gate.
    """
    _require(top_n >= 1, "top", ">= 1", top_n)
    kw = _graph_kwargs(path)
    report = compute_conventions(path, **kw, include_tests=include_tests)

    coverage = config = None
    config_path = discover_config(path)
    if config_path is not None:
        try:
            config = load_config(config_path)
            coverage = compute_coverage(build_graph(path, **kw), config)
        except LayerConfigError:
            # A malformed archy.yaml is worth reporting from `check`, which gates
            # on it. Here it would replace the whole briefing with one error, so
            # the section says nothing rather than the command saying nothing.
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


def _load_graph(path: Path, *, internal_only: bool) -> nx.DiGraph:
    try:
        g = build_graph(path, **_graph_kwargs(path), max_modules=_resolve_max_modules(path))
    except ScanTooLargeError as exc:
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


def _effective_max_modules(config: LayerConfig | None) -> int | None:
    return effective_max_modules(config.max_modules if config is not None else None)


def _resolve_max_modules(path: Path) -> int | None:
    config_path = discover_config(path)
    config = load_config(config_path) if config_path is not None else None
    return _effective_max_modules(config)


def _graph_kwargs(path: Path) -> dict:
    # Best-effort: pick up `exclude:` and `roots:` from a discovered archy.yaml
    # so every analysis (graph, cycles, score, check) sees the same surface.
    # Missing config is fine; malformed config is a real bug the user wants to
    # see, so we let LayerConfigError propagate.
    config_path = discover_config(path)
    if config_path is None:
        return {}
    config = load_config(config_path)
    return {
        "ignored_dirs": DEFAULT_IGNORED_DIRS | frozenset(config.exclude),
        "extra_roots": config.roots,
    }


def _format_lines(lines: tuple[int, ...]) -> str:
    label = "lines" if len(lines) > 1 else "line"
    text = ", ".join(str(n) for n in lines) or "?"
    return f"({label}: {text})"


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


def _gate_to_dict(gate: dict, tolerance: float) -> dict:
    delta = gate["delta"]
    return {
        "previous": gate["previous"],
        "current": gate["current"],
        "delta": delta,
        "tolerance": tolerance,
        "passed": delta is None or delta >= -tolerance,
    }


def _gate_to_text(gate: dict, tolerance: float) -> str:
    if gate["delta"] is None:
        return (
            "# strict: no prior score recorded; nothing to compare. "
            "Pass `--record` to seed the baseline."
        )
    delta = gate["delta"]
    direction = "improved" if delta >= 0 else "dropped"
    verdict = "PASS" if delta >= -tolerance else "FAIL"
    prev_label = (gate.get("previous_commit") or "?")[:7]
    return (
        f"# strict: {verdict}  "
        f"{gate['previous']:.3f} ({prev_label}) -> {gate['current']:.3f}  "
        f"({direction} {delta:+.3f}, tolerance {tolerance:.3f})"
    )


def _call_weighted_modularity_prose(s: Score) -> str:
    """One-line direction summary for the call-weighted Q diagnostic.

    The gap (weighted - unweighted) is what carries the meaning; thresholds
    chosen empirically from the 27-project bench so common projects land
    in one of the three buckets without false precision in the prose.
    """
    delta = s.inputs.raw_modularity_weighted - s.inputs.raw_modularity
    if delta > 0.05:
        return "calls amplify community structure"
    if delta < -0.05:
        return "calls cross community boundaries"
    return "calls roughly track community structure"


def _score_to_dict(s: Score) -> dict:
    return {
        "overall": s.overall,
        "components": {
            "modularity": s.modularity,
            "acyclicity": s.acyclicity,
            "depth": s.depth,
            "equality": s.equality,
            "complexity": s.complexity,
        },
        "inputs": {
            "module_count": s.inputs.module_count,
            "edge_count": s.inputs.edge_count,
            "cycle_count": s.inputs.cycle_count,
            "tangle_ratio": s.inputs.tangle_ratio,
            "max_depth": s.inputs.max_depth,
            "community_count": s.inputs.community_count,
            "raw_modularity": s.inputs.raw_modularity,
            "raw_gini": s.inputs.raw_gini,
            "propagation_cost": s.inputs.propagation_cost,
            "call_edge_count": s.inputs.call_edge_count,
            "total_calls": s.inputs.total_calls,
            "calls_per_edge": s.inputs.calls_per_edge,
            "function_count": s.inputs.function_count,
            "cc_total": s.inputs.cc_total,
            "cc_max": s.inputs.cc_max,
            "cc_mean": s.inputs.cc_mean,
            "raw_modularity_weighted": s.inputs.raw_modularity_weighted,
            "modularity_weighted_community_count": s.inputs.modularity_weighted_community_count,
        },
    }


def _score_to_text(s: Score) -> str:
    lines = [
        f"# archy score: {s.overall:.3f}",
        f"modularity:  {s.modularity:.3f}  "
        f"({s.inputs.community_count} communities, raw Q={s.inputs.raw_modularity:.3f})",
        f"  call-weighted Q={s.inputs.raw_modularity_weighted:.3f}  "
        f"({_call_weighted_modularity_prose(s)})",
        f"acyclicity:  {s.acyclicity:.3f}  "
        f"({s.inputs.cycle_count} cycles, tangle={s.inputs.tangle_ratio:.3f})",
        f"depth:       {s.depth:.3f}  (max depth {s.inputs.max_depth})",
        f"equality:    {s.equality:.3f}  (Gini={s.inputs.raw_gini:.3f})",
        f"complexity:  {s.complexity:.3f}  "
        f"({s.inputs.function_count} functions, cc_mean={s.inputs.cc_mean:.2f}, "
        f"cc_max={s.inputs.cc_max})",
        f"# graph: {s.inputs.module_count} modules, {s.inputs.edge_count} edges",
        f"# propagation_cost: {s.inputs.propagation_cost:.4f}  "
        f"(diagnostic, not in score; MacCormack reverse-reach fraction)",
        f"# calls: {s.inputs.total_calls} resolved across {s.inputs.call_edge_count} edge(s), "
        f"{s.inputs.calls_per_edge:.2f}/edge  (diagnostic, not in score)",
    ]
    return "\n".join(lines)


def _violations_to_json(violations: list[Violation]) -> list[dict]:
    return [
        {
            "rule": {"from": v.rule.from_layer, "to": v.rule.to_layer},
            "source": v.source,
            "target": v.target,
            "lines": list(v.lines),
        }
        for v in violations
    ]


def _reach_violations_to_json(violations: list[ReachViolation]) -> list[dict]:
    return [
        {
            "rule": {
                "source": v.rule.source,
                "must_reach": v.rule.must_reach,
                "reason": v.rule.reason,
            },
            "module": v.module,
            "detail": v.detail,
        }
        for v in violations
    ]


def _reach_violations_to_text(
    violations: list[ReachViolation], rules: tuple[RequiredRule, ...]
) -> str:
    """Render `required:` results. Called only when the config declares rules.

    The clean line names the rule count on purpose: "no required-reach
    violations" without it reads the same whether one rule passed or twenty did.
    """
    if not violations:
        return f"# No required-reach violations ({len(rules)} rule(s) checked, transitively)."
    lines = [f"# {len(violations)} required-reach violation(s)"]
    current: tuple[str, str] | None = None
    for v in violations:
        pair = (v.rule.source, v.rule.must_reach)
        if pair != current:
            header = f"\n{v.rule.source} must reach {v.rule.must_reach}:"
            if v.rule.reason:
                header += f"\n  reason: {v.rule.reason}"
            lines.append(header)
            current = pair
        lines.append(f"  {v.detail}")
    return "\n".join(lines)


def _violations_to_text(
    violations: list[Violation], config_path: Path, coverage: LayerCoverage
) -> str:
    """The headline verdict, qualified by coverage when coverage is degenerate.

    A clean pass under a config that governs no edges is true and useless: it
    says nothing was found without saying nothing was looked at. The numbers
    were already printed one line below, where a reader who has just read
    "No layer violations" has already stopped reading. Promoting the fact into
    the verdict itself is the whole point (#362).

    The substring "No layer violations" is preserved verbatim: `bench/walkthrough.py`
    and the CLI tests assert on it, and the qualification is a clause, not a
    replacement. The exit code is untouched -- see `check`.
    """
    if not violations:
        verdict = f"# No layer violations (config: {config_path})."
        if coverage.governs_nothing:
            return (
                f"# No layer violations, but no module in the tree falls under any root "
                f"this config names, so no rule here can fire (config: {config_path})."
            )
        if coverage.governs_no_edges:
            return (
                f"# No layer violations, but this config governs "
                f"{coverage.edges_governed} of {coverage.edges_total} internal edges "
                f"(0%), so no forbid rule can fire (config: {config_path})."
            )
        return verdict
    lines = [f"# {len(violations)} layer violation(s) (config: {config_path})"]
    current_rule: tuple[str, str] | None = None
    for v in violations:
        rule_pair = (v.rule.from_layer, v.rule.to_layer)
        if rule_pair != current_rule:
            lines.append(f"\n{v.rule.from_layer} -> {v.rule.to_layer} (forbidden):")
            current_rule = rule_pair
        lines.append(f"  {v.source} -> {v.target}  {_format_lines(v.lines)}")
    return "\n".join(lines)


def _coverage_to_json(coverage: LayerCoverage) -> dict:
    return {
        "modules_total": coverage.modules_total,
        "modules_matched": coverage.modules_matched,
        "modules_in_ruled_layer": coverage.modules_in_ruled_layer,
        "module_ratio": round(coverage.module_ratio, 4),
        "edges_total": coverage.edges_total,
        "edges_governed": coverage.edges_governed,
        "edge_ratio": round(coverage.edge_ratio, 4),
        "unlayered_modules": list(coverage.unlayered_modules),
        "modules_outside_declared_roots": coverage.modules_outside_declared_roots,
        # Presence, in the payload as well as the text output. Without these a
        # JSON consumer sees exit 1 with an empty `violations` list and nothing
        # explaining why, which is indistinguishable from a bug in archy.
        "layer_sizes": dict(coverage.layer_sizes),
        "layers_present": coverage.layers_present,
        "empty_layers": list(coverage.empty_layers),
        # The degenerate-coverage verdict and its most common cause, on the
        # wire as well as in the text output. A JSON consumer seeing an empty
        # `violations` list otherwise has to re-derive "could this config have
        # said anything?" from the raw counts.
        "governs_no_edges": coverage.governs_no_edges,
        "exact_pattern_hints": [
            {
                "layer": hint.layer,
                "pattern": hint.pattern,
                "unlayered_descendants": list(hint.unlayered_descendants),
                "suggestion": hint.suggestion,
            }
            for hint in coverage.exact_pattern_hints
        ],
    }


def _presence_to_text(coverage: LayerCoverage, minimum: int | None) -> str:
    """Report empty declared layers, and whether they breach a presence floor.

    Printed even without a floor configured: a layer matching nothing is worth
    saying out loud regardless, because every rule naming it is dead.
    """
    empty = coverage.empty_layers
    if not empty and minimum is None:
        return ""
    total = len(coverage.layer_sizes)
    lines = []
    if empty:
        lines.append(
            f"#   layers present: {coverage.layers_present} of {total} declared; "
            f"empty: {', '.join(empty)}"
        )
    if minimum is not None and coverage.layers_present < minimum:
        lines.append(
            f"#   FAIL: {coverage.layers_present} layer(s) present, min_layers_present is {minimum}"
        )
    return "\n".join(lines)


def _coverage_to_text(coverage: LayerCoverage) -> str:
    """One line, always printed, including on a clean pass.

    Printed on a PASS specifically: a clean result is exactly when a reader is
    entitled to know whether the rules could have said anything at all (#362).
    """
    if coverage.governs_nothing:
        return (
            "#   layer coverage: NO modules under the declared root packages, so no rule "
            "here can ever fire"
        )
    line = (
        f"#   layer coverage: {coverage.modules_matched} of {coverage.modules_total} modules "
        f"({coverage.module_ratio:.0%}), {coverage.edges_governed} of {coverage.edges_total} "
        f"internal edges ({coverage.edge_ratio:.0%})"
    )
    unlayered = len(coverage.unlayered_modules)
    if unlayered:
        line += f"; {unlayered} module(s) match no layer (`archy check --show-unlayered`)"
    if coverage.modules_outside_declared_roots:
        line += (
            f"\n#   {coverage.modules_outside_declared_roots} scanned module(s) sit outside "
            "every declared root package and are not counted above"
        )
    return line


def _brief_to_text(report, coverage, config, contracts, *, top_n: int) -> str:
    """One screen an agent can be handed BEFORE it starts, not a report to browse.

    🔴 SIZED FOR INJECTION, WHICH IS AN ECONOMIC CLAIM, NOT AN AESTHETIC ONE.
    On the hardware this was measured on, reading runs at ~796 tok/s and writing
    at ~12 -- 66x apart. A few thousand tokens of briefing costs seconds of
    prefill; one block of the model working the same fact out for itself costs
    ~32 seconds of decode. A brief only has to prevent one block in a handful of
    runs to pay for itself, which is why this is a command rather than advice to
    run six others.

    Ordered by what a reader needs FIRST, not by what is cheapest to compute:
    what kind of thing am I adding, what do I call it, what has to change with
    it, does it gate, and what can this configuration not see. The last is the
    one an agent skips and then re-derives at length.
    """
    L = [f"# archy brief: {report.root}"]
    p = report.partition
    if p:
        aside = ", ".join(
            x
            for x in (
                f"{p.tests} test" if p.tests else "",
                f"{p.nonsource} non-source" if p.nonsource else "",
                f"{p.shadowed} duplicating a parent" if p.shadowed else "",
            )
            if x
        )
        L.append(
            f"#   {report.modules_scanned} module(s)"
            + (f", {report.docs_scanned} doc file(s)" if report.docs_scanned else "")
            + (f"; set aside: {aside}" if aside else "")
        )

    L.append("")
    L.append("## what kind of thing am I adding, and where does it go")
    kinds = [b for b in report.bases if b.count >= 3][:top_n]
    if not kinds:
        L.append("  (no base class in this project has enough subclasses to be a convention)")
    for b in kinds:
        note = (
            ""
            if b.suffix_agreement >= 0.8
            else f"  <- name is NOT the rule ({b.suffix_agreement:.0%})"
        )
        L.append(f"  {b.base:<26} {b.count:>3} @ {b.home_module}{note}")
        for c in b.shared_constants[:2]:
            dist = ", ".join(f"{v}x{n}" for v, n in c.distribution)
            L.append(f"  {'':<26}     {c.name} = {dist}  ({c.setters} of {b.count} set it)")

    L.append("")
    L.append("## what must change WITH it")
    # 🔴 Cross-module first and never truncated below the fold: a co-update set is
    # the one thing here that is actionable rather than descriptive, and a
    # half-wired feature is this project's most-replicated defect.
    co = [x for x in report.surfaces if x.kind == "consumer"][:top_n]
    if not co:
        L.append("  (no symbol is consumed by 2-5 internal modules)")
    for x in co:
        L.append(f"  {x.stem:<26} {x.module} -> {', '.join(x.surfaces)}")
    for g in report.export_gaps[:top_n]:
        L.append(
            f"  🔴 {g.export_module}: {g.family} "
            f"{g.exported}/{g.defined}, missing {', '.join(g.missing)}"
        )
    for g in report.doc_gaps[:top_n]:
        L.append(
            f"  🔴 {g.doc_root}/: {g.family} "
            f"{g.documented}/{g.defined}, missing {', '.join(g.missing)}"
        )

    L.append("")
    codes = ", ".join(str(c) for c in report.gate_codes) or "none literal"
    L.append(
        f"## does a new finding gate ({len(report.gates)} "
        f"finding-failure exit(s); code(s): {codes})"
    )
    L += [_gate_row(g) for g in report.gates[:top_n]] or [
        "  (nothing here fails a build on a finding)"
    ]

    L.append("")
    L.append("## what this configuration cannot see")

    def indent(block: str) -> list[str]:
        # Per LINE, not per block: these renderers emit multi-line text and a
        # single strip() left every continuation flush against the margin,
        # which reads as a new section rather than the rest of a sentence.
        return ["  " + ln.lstrip("# ").rstrip() for ln in block.split("\n") if ln.strip()]

    if coverage is None:
        L.append("  (no archy.yaml discovered, so no layer rule governs anything)")
    else:
        L += indent(_coverage_to_text(coverage))
        hints = _pattern_hints_to_text(coverage)
        if hints:
            L += indent(hints)
        if config is not None:
            handoff = _contracts_handoff_to_text(config, coverage, [])
            if handoff:
                L += indent(handoff)
    if contracts is not None:
        L.append("")
        L.append(_contracts_outcome_to_text(contracts))
    return "\n".join(L)


class ContractsOutcome(BaseModel):
    """The `--contracts` addendum, including the case where it produced no verdict.

    Carries `available` and `error` so a run that could not reach a verdict still
    says why on every surface. Returning `None` instead left `check --format
    json` with no `contracts` key at all and `brief --format json` with a bare
    `null`, which is indistinguishable from a bug in archy; the reason went only
    to stderr, which neither structured stream carries. The MCP surface has
    reported `available`/`error` from the start, so this is also what keeps the
    two telling the same story.
    """

    model_config = ConfigDict(frozen=True)

    available: bool = True
    error: str | None = None
    result: ContractsResult | None = None


def _run_check_contracts(path: Path, config_filename: Path | None) -> ContractsOutcome:
    """Run contracts for `check --contracts`, and never let it fail the check.

    A missing `import-linter` or an unreadable contracts config is a reason the
    EXTRA verdict is unavailable, not a reason the layer check did not happen.
    The standalone `contracts` command exits 1 on both, which is right when the
    verdict is the whole point and wrong when it is an addendum: a flag that can
    turn a passing check into a failing one because an optional dependency is
    absent would make `--contracts` unsafe to leave on in CI.

    The two failures are distinguished the way `mcp._run_contracts` distinguishes
    them: a missing dependency is `available=False`, while a config archy could
    not read is `available=True` with the reason attached.
    """
    # Function-local on purpose, and load-bearing twice over: it keeps the
    # optional import-linter dependency off the module-import path, and it
    # resolves `run_contracts` through the module at CALL time, which is what
    # lets a test substitute a raising stub to exercise the no-verdict branch.
    from archy.contracts import ContractsConfigError, ContractsNotAvailable, run_contracts

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
    """
    return outcome is not None and outcome.result is not None


def _no_verdict_error(outcome: ContractsOutcome | None) -> str | None:
    """The reason contracts produced nothing, or None if they were never asked.

    Distinguishes "not requested" (no error to report; the handoff names the
    flag) from "requested and failed" (name the actual failure, because naming
    the flag to someone who just passed it sends them round a loop).
    """
    return outcome.error if outcome is not None and outcome.result is None else None


def _transitive_unverified_reason(
    config: LayerConfig,
    coverage: LayerCoverage,
    violations: list[Violation],
    *,
    no_verdict_error: str | None = None,
) -> str | None:
    """The text handoff's reason, for a reader that cannot parse prose.

    A bare `transitive_checked: false` is a verdict without a reason, which
    AGENTS.md rules out on every surface: the consumer needs to know whether the
    rules were merely not requested or could not be proven, and what settles it.

    `no_verdict_error` distinguishes the two ways this run can fail to verify.
    Naming `--contracts` to a caller who just passed it and watched it fail is
    worse than saying nothing: it sends them round a loop they have already been
    through, so that case reports the actual cause instead.
    """
    if not contracts_unverified(config, coverage, violations):
        return None
    n = len(config.forbid)
    rules = "rule" if n == 1 else "rules"
    if no_verdict_error is not None:
        return (
            f"{n} forbid {rules} declared and still unverified: `--contracts` produced no "
            f"transitive verdict ({no_verdict_error})."
        )
    return (
        f"{n} forbid {rules} declared, but this check governs too little of the graph to "
        "verify them; `--contracts` evaluates them transitively via import-linter."
    )


def _contracts_handoff_to_text(
    config: LayerConfig, coverage: LayerCoverage, violations: list[Violation]
) -> str:
    """Name the flag that settles a `forbid` rule this run could not verify.

    🔴 THIS EXISTS BECAUSE OF A MEASUREMENT, NOT A HUNCH. Over a five-hour agent
    run on this repository, `archy check` was invoked TWENTY times and
    `archy contracts` ZERO times -- while the model named `contracts` thirty-one
    times in its own reasoning and the task statement named it too. Awareness was
    never the deficit. The model navigates by re-running the command it is
    changing, so the only handoff that lands is one printed by that command.

    Deliberately points at `--contracts` on `check` rather than at the separate
    `contracts` command: a flag on the invocation already being typed is the
    smallest discovery step available, and the same run then answers the
    question instead of ending with a suggestion.

    Silent unless it would be useful: a `forbid` rule must exist (nothing to
    verify otherwise), the direct pass must have found nothing (a reader with a
    concrete violation already has somewhere to go), and coverage must be
    incomplete enough that a path could hide in the ungoverned part. On a fully
    layered repository with every edge governed it stays quiet.
    """
    if not contracts_unverified(config, coverage, violations):
        return ""
    n = len(config.forbid)
    rules = "rule" if n == 1 else "rules"
    return (
        f"#   {n} forbid {rules} declared, but this check governs too little of the graph to "
        "verify them.\n"
        "#   `archy check --contracts` evaluates them transitively via import-linter, which "
        "sees paths a direct-edge check cannot."
    )


def _pattern_hints_to_text(coverage: LayerCoverage) -> str:
    """Name the cause of degenerate coverage, not just the number.

    Empty when nothing was detected, so the caller can skip the echo entirely.
    """
    lines = []
    for hint in coverage.exact_pattern_hints:
        count = len(hint.unlayered_descendants)
        shown = ", ".join(hint.unlayered_descendants[:3])
        if count > 3:
            shown += f", ... (+{count - 3} more)"
        noun = "module is" if count == 1 else "modules are"
        lines.append(
            f"#   layer {hint.layer!r} matches {hint.pattern} exactly; "
            f"{count} descendant {noun} unlayered ({shown}). "
            f'Did you mean "{hint.suggestion}"?'
        )
    return "\n".join(lines)


def _sdp_violations_to_json(violations: list[SdpViolation]) -> list[dict]:
    return [
        {
            "source": v.source,
            "target": v.target,
            "source_instability": v.source_instability,
            "target_instability": v.target_instability,
            "lines": list(v.lines),
        }
        for v in violations
    ]


def _sdp_violations_to_text(violations: list[SdpViolation], tolerance: float) -> str:
    if not violations:
        return f"# No SDP violations (tolerance: {tolerance})."
    lines = [f"# {len(violations)} SDP violation(s) (tolerance: {tolerance})"]
    for v in violations:
        gap = v.target_instability - v.source_instability
        lines.append(
            f"  {v.source} (I={v.source_instability:.2f}) -> "
            f"{v.target} (I={v.target_instability:.2f}, gap={gap:+.2f})  "
            f"{_format_lines(v.lines)}"
        )
    return "\n".join(lines)


def _cycles_to_json(cycles: list[Cycle]) -> list[dict]:
    return [
        {
            "modules": list(c.modules),
            "edges": [
                {"source": e.source, "target": e.target, "lines": list(e.lines)} for e in c.edges
            ],
        }
        for c in cycles
    ]


def _snapshot_to_text(snap: Snapshot) -> str:
    return (
        f"# score: {snap.score.overall:.3f}  "
        f"cycles: {len(snap.cycles)}  violations: {len(snap.violations)}"
    )


def _diff_to_text(result: DiffReport) -> str:
    lines: list[str] = []
    if result.summary is not None:
        lines.extend(_summary_to_text(result.summary))
        lines.append("")
    deltas = result.score_delta
    lines.append("# score deltas (current - baseline):")
    for name in ("overall", "modularity", "acyclicity", "depth", "equality", "complexity"):
        lines.append(f"  {name:11s} {getattr(deltas, name):+.3f}")
    cycles = result.cycles
    lines.append("")
    lines.append(f"# cycles: +{len(cycles.added)} added, -{len(cycles.resolved)} resolved")
    for c in cycles.added:
        lines.append(f"  + cycle: {', '.join(c.modules)}")
    for c in cycles.resolved:
        lines.append(f"  - cycle (resolved): {', '.join(c.modules)}")
    violations = result.violations
    lines.append("")
    lines.append(
        f"# violations: +{len(violations.added)} added, -{len(violations.resolved)} resolved"
    )
    for v in violations.added:
        lines.append(f"  + {v.source} -> {v.target}  ({v.rule.from_layer} -> {v.rule.to_layer})")
    for v in violations.resolved:
        rule = f"{v.rule.from_layer} -> {v.rule.to_layer}"
        lines.append(f"  - {v.source} -> {v.target}  resolved ({rule})")
    reach = result.required_violations
    # Only printed when there is something to say: a diff on a project with no
    # `required:` rules should not grow a permanent zero line.
    if reach.added or reach.resolved:
        lines.append("")
        lines.append(
            f"# required-reach: +{len(reach.added)} added, -{len(reach.resolved)} resolved"
        )
        for v in reach.added:
            lines.append(f"  + {v.detail}")
        for v in reach.resolved:
            lines.append(f"  - {v.module or v.rule.source} now reaches {v.rule.must_reach}")
    return "\n".join(lines)


def _summary_to_text(summary: DiffSummary) -> list[str]:
    lines = [f"# summary: {summary.headline}"]
    if summary.top_regressions:
        lines.append("")
        lines.append("## top regressions (risk-weighted):")
        for item in summary.top_regressions:
            lines.append(f"  risk={item.risk:.2f}  {item.description}")
            lines.append(f"      ? {item.prompt}")
    if summary.top_improvements:
        lines.append("")
        lines.append("## top improvements (risk-weighted):")
        for item in summary.top_improvements:
            lines.append(f"  risk={item.risk:.2f}  {item.description}")
    return lines


def _simulate_to_text(result: SimulateReport) -> str:
    a = result.applied
    lines = [
        f"# simulation (no files written): +{len(a.added_edges)} / -{len(a.removed_edges)} edge(s)"
    ]
    for note, items in (
        ("unresolved endpoint", a.unresolved),
        ("rejected", a.rejected),
    ):
        for item in items:
            lines.append(f"  ! {note}: {item}")
    for note, edges in (
        ("no-op add (edge already exists)", a.no_op_adds),
        ("no-op remove (edge absent)", a.no_op_removes),
    ):
        for e in edges:
            lines.append(f"  ~ {note}: {e.source} -> {e.target}")

    lines.append("")
    lines.extend(_summary_to_text(result.summary))

    lines.append("")
    lines.append("# would-be score deltas:")
    for name in ("overall", "modularity", "acyclicity", "depth", "equality", "complexity"):
        lines.append(f"  {name:11s} {getattr(result.score_delta, name):+.3f}")
    p = result.propagation_cost
    lines.append(f"  {'blast radius':11s} {p.before:.3f} -> {p.after:.3f} ({p.delta:+.3f})")

    if result.cycles.added:
        lines.append("")
        lines.append(f"# new cycles (+{len(result.cycles.added)}):")
        for c in result.cycles.added:
            lines.append(f"  + {', '.join(c.modules)}")
    if result.new_back_edges:
        lines.append("")
        lines.append(f"# new back-edges (+{len(result.new_back_edges)}):")
        for e in result.new_back_edges:
            lines.append(f"  + {e.source} -> {e.target}")
    if result.violations.added:
        lines.append("")
        lines.append(f"# new layer violations (+{len(result.violations.added)}):")
        for v in result.violations.added:
            lines.append(
                f"  + {v.source} -> {v.target}  ({v.rule.from_layer} -> {v.rule.to_layer})"
            )
    # Listed unconditionally, like the block above, NOT left to the ranked
    # summary. `simulate` has no --top-n and takes the default cap of 5, and a
    # whole-rule reach item carries risk 0.0 (it names no module to weight), so
    # it is the first thing evicted from the top-5 exactly when a simulation has
    # a lot going on. "Would removing this import break a required-reach rule?"
    # is a question simulate exists to answer; it must not depend on ranking.
    if result.required_violations.added:
        lines.append("")
        lines.append(f"# new required-reach violations (+{len(result.required_violations.added)}):")
        for v in result.required_violations.added:
            lines.append(f"  + {v.detail}")
    return "\n".join(lines)


def _contracts_to_dict(result: ContractsResult) -> dict:
    return {
        "kept": result.kept,
        "broken": result.broken,
        "module_count": result.module_count,
        "import_count": result.import_count,
        "all_kept": result.all_kept,
        "contracts": [
            {
                "name": c.name,
                "type": c.contract_type,
                "kept": c.kept,
                "metadata": c.metadata,
                "warnings": list(c.warnings),
            }
            for c in result.contracts
        ],
    }


def _contracts_outcome_to_dict(outcome: ContractsOutcome) -> dict:
    payload: dict = {"available": outcome.available, "error": outcome.error}
    if outcome.result is not None:
        payload.update(_contracts_to_dict(outcome.result))
    return payload


def _contracts_outcome_to_text(outcome: ContractsOutcome) -> str:
    if outcome.result is None:
        return f"# contracts: no verdict ({outcome.error})"
    return _contracts_to_text(outcome.result)


def _contracts_to_text(result: ContractsResult) -> str:
    lines = [
        f"# contracts: {result.kept} kept, {result.broken} broken "
        f"({result.module_count} modules, {result.import_count} imports)"
    ]
    for c in result.contracts:
        marker = "OK " if c.kept else "X  "
        lines.append(f"{marker}{c.name}  [{c.contract_type}]")
        for w in c.warnings:
            lines.append(f"    ! {w}")
        if not c.kept:
            # `metadata` is `dict[str, object]` (import-linter's per-contract-
            # type shape); cast each level explicitly at the read sites rather
            # than typing the import-linter wire format.
            chains = cast(list[dict[str, object]], c.metadata.get("invalid_chains") or [])
            for chain in chains:
                upstream = chain.get("upstream_module", "?")
                downstream = chain.get("downstream_module", "?")
                lines.append(f"    {downstream} -> {upstream}")
                paths = cast(list[list[dict[str, object]]], chain.get("chains") or [])
                for path in paths:
                    # Multi-step paths are worth showing; single-step paths
                    # are already in the line above.
                    if len(path) <= 1:
                        continue
                    nodes = [str(path[0].get("importer", "?"))]
                    nodes.extend(str(step.get("imported", "?")) for step in path)
                    lines.append(f"      via {' -> '.join(nodes)}")
    return "\n".join(lines)


def _affected_to_dict(result: Affected) -> dict:
    return {
        "changed": list(result.changed),
        "unresolved": list(result.unresolved),
        "impacted_modules": list(result.impacted_modules),
        "impacted_tests": list(result.impacted_tests),
        "depth": result.depth,
        "test_filter": result.test_filter,
    }


def _affected_to_text(result: Affected) -> str:
    lines = [
        f"# depth={result.depth}, "
        f"{len(result.impacted_tests)} test(s), "
        f"{len(result.impacted_modules)} module(s) "
        f"affected by {len(result.changed)} changed module(s)"
    ]
    if result.test_filter:
        lines.append(f"# test filter: {result.test_filter}")
    if result.unresolved:
        lines.append(
            f"# {len(result.unresolved)} file(s) did not resolve to a module "
            "(non-Python, excluded, or outside any package):"
        )
        for f in result.unresolved:
            lines.append(f"  ? {f}")
    if result.changed:
        lines.append("")
        lines.append("Changed:")
        for q in result.changed:
            lines.append(f"  - {q}")
    if result.impacted_tests:
        lines.append("")
        lines.append("Tests to run:")
        for q in result.impacted_tests:
            lines.append(f"  - {q}")
    if result.impacted_modules:
        lines.append("")
        lines.append("Other modules touched:")
        for q in result.impacted_modules:
            lines.append(f"  - {q}")
    return "\n".join(lines)


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


def _impact_to_dict(result: Impact) -> dict:
    return {
        "changed": list(result.changed),
        "unresolved": list(result.unresolved),
        "impacted": list(result.impacted),
        "chains": [
            {
                "impacted": c.impacted,
                "changed": c.changed,
                "via": list(c.via),
                "hops": [
                    {"source": h.source, "target": h.target, "lines": list(h.lines)} for h in c.hops
                ],
            }
            for c in result.chains
        ],
        "chains_omitted": result.chains_omitted,
    }


def _hotspots_to_dict(rows: list[Hotspot], *, top_n: int, since: str | None) -> dict:
    return {
        "since": since,
        "total": len(rows),
        "shown": min(top_n, len(rows)),
        "hotspots": [r.model_dump() for r in rows[:top_n]],
    }


def _hotspots_to_text(rows: list[Hotspot], *, top_n: int, since: str | None) -> str:
    if not rows:
        suffix = f" since {since}" if since else ""
        return f"# No hotspots found{suffix} (need both cc_sum > 0 and churn > 0)."
    shown = rows[:top_n]
    header = f"# {len(rows)} hotspot(s); showing top {len(shown)}"
    if since:
        header += f" since {since}"
    lines = [header, "", "  score  churn  cc_sum  module"]
    for r in shown:
        lines.append(f"  {r.score:>5}  {r.churn:>5}  {r.cc_sum:>6}  {r.module}")
    return "\n".join(lines)


def _coupling_note(rows: list[CouplingPair], *, min_support: int, since: str | None) -> str | None:
    """Machine-readable explanation for the empty case (mirrors _refactor_note).

    An empty list is a real answer: either the history is too short to clear
    `--min-support`, or every co-changing pair already has a structural edge (so
    the graph already captures it). Say which lever to relax rather than imply
    the project has no coupling."""
    if rows:
        return None
    suffix = f" since {since}" if since else ""
    return (
        f"No structurally-unconnected module pairs co-change above the thresholds{suffix}. "
        "Lower --min-support / --min-confidence to widen, or the graph already "
        "captures the coupling as import/call edges."
    )


def _coupling_to_dict(
    rows: list[CouplingPair], *, top_n: int, since: str | None, min_support: int
) -> dict:
    return {
        "since": since,
        "min_support": min_support,
        "total": len(rows),
        "shown": min(top_n, len(rows)),
        "pairs": [r.model_dump() for r in rows[:top_n]],
        "note": _coupling_note(rows, min_support=min_support, since=since),
    }


def _coupling_to_text(
    rows: list[CouplingPair], *, top_n: int, since: str | None, min_support: int
) -> str:
    if not rows:
        return f"# {_coupling_note(rows, min_support=min_support, since=since)}"
    shown = rows[:top_n]
    header = f"# {len(rows)} hidden-coupling pair(s); showing top {len(shown)}"
    if since:
        header += f" since {since}"
    lines = [header, "", "  conf  commits  modules (no import/call edge)"]
    for r in shown:
        lines.append(f"  {r.confidence:>4.2f}  {r.support:>7}  {r.module_a} <-> {r.module_b}")
    return "\n".join(lines)


def _duplicates_note(
    dups: list[DuplicateGroup], *, min_nodes: int, variant_count: int = 0
) -> str | None:
    """Explain an empty primary tier (mirrors the CLI/MCP note pattern).

    Speaks to the primary "likely duplicate" tier only; if the semantic de-noiser
    demoted everything to the variant tier, say so rather than imply nothing was
    found at all."""
    if dups:
        return None
    note = (
        f"No likely-duplicate function bodies of >= {min_nodes} normalized nodes found; "
        "lower --min-nodes to widen the search."
    )
    if variant_count:
        note += (
            f" ({variant_count} likely-intentional variant(s) were found but demoted: "
            "same-class siblings, boilerplate, test/vendored copies, or independent "
            "copies that never co-change.)"
        )
    return note


def _duplicate_group_lines(group: DuplicateGroup, *, with_reason: bool) -> list[str]:
    """Render one cluster: a header row for the first member, then one row per other."""
    reason = f"  {(group.variant_reason or ''):<10}" if with_reason else ""
    pad = f"  {'':<10}" if with_reason else ""
    first, *rest = group.members
    lines = [
        f"  {group.redundancy:>6}  {group.size:>4}  {group.member_count:>5}{reason}  "
        f"{first.module}:{first.line} {first.qualified_name}"
    ]
    lines.extend(
        f"  {'':>6}  {'':>4}  {'':>5}{pad}  {m.module}:{m.line} {m.qualified_name}" for m in rest
    )
    return lines


def _duplicates_to_dict(rows: list[DuplicateGroup], *, top_n: int, min_nodes: int) -> dict:
    dups = [g for g in rows if g.category == "duplicate"]
    variants = [g for g in rows if g.category == "variant"]
    near = [g for g in rows if g.category == "near_miss"]
    return {
        "min_nodes": min_nodes,
        "total": len(dups),
        "exact_total": sum(1 for g in dups if g.exact),
        "variant_total": len(variants),
        "near_total": len(near),
        "shown": min(top_n, len(dups)),
        "duplicated_functions": sum(g.member_count for g in dups),
        "duplicates": [g.model_dump() for g in dups[:top_n]],
        "variants": [g.model_dump() for g in variants[:top_n]],
        "near_miss": [g.model_dump() for g in near[:top_n]],
        "note": _duplicates_note(dups, min_nodes=min_nodes, variant_count=len(variants)),
    }


def _near_miss_section(out: list[str], header: str, groups: list[DuplicateGroup]) -> None:
    """Render the Type-3 near-miss tier: a `sim` column instead of redund/size."""
    if out:
        out.append("")
    out.append(header)
    out.append("")
    out.append("   sim  count  members")
    for g in groups:
        first, *rest = g.members
        out.append(
            f"  {g.similarity or 0:>4.2f}  {g.member_count:>5}  {first.module}:{first.line} "
            f"{first.qualified_name}"
        )
        out.extend(f"  {'':>4}  {'':>5}  {m.module}:{m.line} {m.qualified_name}" for m in rest)


def _duplicates_section(
    out: list[str], header: str, groups: list[DuplicateGroup], *, with_reason: bool
) -> None:
    if out:
        out.append("")
    out.append(header)
    out.append("")
    out.append(f"  redund  size  count  {'reason      ' if with_reason else ''}members")
    for g in groups:
        out.extend(_duplicate_group_lines(g, with_reason=with_reason))


def _duplicates_to_text(rows: list[DuplicateGroup], *, top_n: int, min_nodes: int) -> str:
    exact = [g for g in rows if g.category == "duplicate" and g.exact]
    near = [g for g in rows if g.category == "duplicate" and not g.exact]
    variants = [g for g in rows if g.category == "variant"]
    out: list[str] = []
    if exact:
        _duplicates_section(
            out,
            f"# {len(exact)} exact duplicate(s) (byte-identical; high confidence); "
            f"showing top {min(top_n, len(exact))} (min-nodes {min_nodes})",
            exact[:top_n],
            with_reason=False,
        )
    if near:
        _duplicates_section(
            out,
            f"# {len(near)} likely duplicate(s) (same shape, differ by names/values); "
            f"showing top {min(top_n, len(near))}",
            near[:top_n],
            with_reason=False,
        )
    if not exact and not near:
        out.append(f"# {_duplicates_note([], min_nodes=min_nodes, variant_count=len(variants))}")
    if variants:
        _duplicates_section(
            out,
            f"# {len(variants)} likely-intentional variant(s) (same-class / boilerplate / "
            f"test / vendored / independent; showing top {min(top_n, len(variants))})",
            variants[:top_n],
            with_reason=True,
        )
    near = [g for g in rows if g.category == "near_miss"]
    if near:
        _near_miss_section(
            out,
            f"# {len(near)} possible near-miss (Type-3, gapped) cluster(s) (LOWER confidence; "
            f"showing top {min(top_n, len(near))})",
            near[:top_n],
        )
    return "\n".join(out)


def _refactor_note(
    rows: list[RefactorPriority], *, min_risk: float, git_available: bool
) -> str | None:
    """Machine-readable explanation for the empty / git-absent cases.

    Mirrors the `note` the MCP tool returns so the CLI JSON surface carries
    the same "nothing to prioritize" / "structural-only" signal rather than
    leaving the caller to infer it from an empty list."""
    if not rows:
        if git_available:
            return (
                "No refactoring priorities surfaced: no file is both complex "
                "and frequently changed, and no module is central and fragile "
                f"above min_risk={min_risk}. Lower min_risk to widen the "
                "structural lens."
            )
        return (
            "Not a git repository, so the CC x churn lens was skipped and only "
            f"the structural lens ran; no module is central and fragile above "
            f"min_risk={min_risk}. Lower min_risk to widen the structural lens."
        )
    if not git_available:
        return (
            "Not a git repository; the CC x churn lens was skipped and this "
            "ranking is structural-only (edit-risk)."
        )
    return None


def _refactor_to_dict(
    rows: list[RefactorPriority],
    *,
    top_n: int,
    since: str | None,
    min_risk: float,
    git_available: bool,
) -> dict:
    return {
        "since": since,
        "min_risk": min_risk,
        "git_available": git_available,
        "total": len(rows),
        "shown": min(top_n, len(rows)),
        "priorities": [r.model_dump() for r in rows[:top_n]],
        "note": _refactor_note(rows, min_risk=min_risk, git_available=git_available),
    }


def _refactor_to_text(
    rows: list[RefactorPriority],
    *,
    top_n: int,
    min_risk: float,
    git_available: bool,
) -> str:
    if not rows:
        if git_available:
            return (
                "# Nothing to refactor: no file is both complex and churned, and "
                f"no module is central+fragile above min_risk={min_risk}."
            )
        return (
            "# Not a git repo, so only the structural lens ran; no module is "
            f"central+fragile above min_risk={min_risk}. Nothing surfaced."
        )
    shown = rows[:top_n]
    header = f"# {len(rows)} refactor candidate(s); showing top {len(shown)}"
    if not git_available:
        header += " (structural-only: not a git repo)"
    lines = [header, ""]
    for i, r in enumerate(shown, 1):
        lenses = "+".join(r.lenses)
        lines.append(f"{i}. {r.module}  [{lenses}]  priority={r.priority:.3f}")
        lines.append(f"   {r.rationale}")
    return "\n".join(lines)


def _impact_to_text(result: Impact) -> str:
    lines = [
        f"# {len(result.impacted)} module(s) depend on {len(result.changed)} changed module(s)"
    ]
    if result.unresolved:
        lines.append(
            f"# {len(result.unresolved)} file(s) did not resolve to a module "
            "(non-Python, excluded, or outside any package):"
        )
        for f in result.unresolved:
            lines.append(f"  ? {f}")
    if result.changed:
        lines.append("")
        lines.append("Changed:")
        for q in result.changed:
            lines.append(f"  - {q}")
    if result.impacted:
        lines.append("")
        lines.append("Impacted (transitive dependents):")
        for q in result.impacted:
            lines.append(f"  - {q}")
    if result.chains:
        lines.append("")
        lines.append("Why (shortest import path to a changed module):")
        for c in result.chains:
            lines.append(f"  - {' -> '.join(c.via)}")
            for h in c.hops:
                lines.append(f"      {h.source} imports {h.target}  {_format_lines(h.lines)}")
        if result.chains_omitted:
            lines.append(
                f"  # {result.chains_omitted} more impacted module(s) not shown "
                "(raise --max-chains)."
            )
    return "\n".join(lines)


def _cycles_to_text(cycles: list[Cycle], min_size: int) -> str:
    if not cycles:
        return f"# No cycles found (min_size={min_size})."
    lines = [f"# {len(cycles)} cycle(s) found"]
    for c in cycles:
        lines.append(f"\nCycle of {len(c.modules)} module(s):")
        for m in c.modules:
            lines.append(f"  - {m}")
        lines.append("Edges:")
        for e in c.edges:
            lines.append(f"  {e.source} -> {e.target}  {_format_lines(e.lines)}")
    return "\n".join(lines)


def _graph_to_dot(g: nx.DiGraph) -> str:
    lines = ["digraph imports {", '  rankdir="LR";']
    for n, d in sorted(g.nodes(data=True)):
        style = ' style="dashed" color="gray"' if d.get("external") else ""
        lines.append(f'  "{n}"[{style.strip()}];')
    for u, v in sorted(g.edges()):
        lines.append(f'  "{u}" -> "{v}";')
    lines.append("}")
    return "\n".join(lines)


def _graph_to_text(g: nx.DiGraph) -> str:
    internal = sorted(n for n, d in g.nodes(data=True) if not d.get("external"))
    lines = [f"# {len(internal)} internal module(s), {g.number_of_edges()} import edge(s)"]
    for n in internal:
        lines.append(f"{n}")
        for t in sorted(g.successors(n)):
            marker = "ext" if g.nodes[t].get("external") else "int"
            lines.append(f"  -> [{marker}] {t}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


def _conventions_to_dict(report: ConventionsReport, *, top_n: int) -> dict:
    """Hand-rolled payload, per this module's convention: the CLI decides what
    to truncate, so the JSON says how much it dropped rather than silently
    handing back a short list that looks complete. Gates and errors are never
    truncated -- the whole point of the section is the complete inventory."""
    payload = report.model_dump()
    payload["top_n"] = top_n
    payload["naming"] = [h.model_dump() for h in report.naming[:top_n]]
    payload["surfaces"] = [s.model_dump() for s in report.surfaces[:top_n]]
    payload["gates"] = [g.model_dump() for g in report.gates]
    payload["errors"] = [g.model_dump() for g in report.errors]
    payload["bases"] = [b.model_dump() for b in report.bases[:top_n]]
    payload["registries"] = [
        {**r.model_dump(), "literal_names": list(r.literal_names[:6])}
        for r in report.registries[:top_n]
    ]
    # Export gaps are never truncated: the section exists to be complete, for
    # the same reason gates are. A truncated list of "things you forgot to
    # wire up" is worse than none, because it reads as a finished checklist.
    payload["export_gaps"] = [g.model_dump() for g in report.export_gaps]
    payload["doc_gaps"] = [g.model_dump() for g in report.doc_gaps]
    payload["totals"] = {
        "naming": len(report.naming),
        "surfaces": len(report.surfaces),
        "gates": len(report.gates),
        "errors": len(report.errors),
        "bases": len(report.bases),
        "registries": len(report.registries),
        "export_gaps": len(report.export_gaps),
        "doc_gaps": len(report.doc_gaps),
    }
    return payload


def _gate_row(gate: Gate) -> str:
    code = "?" if gate.code is None else str(gate.code)
    where = f"{gate.module}:{gate.function}"
    suffix = "  [command]" if gate.is_command else ""
    call = f"{gate.kind}({code})"
    return f"  {where:<34} {call:<18} {gate.control}{suffix}"


def _module_view_to_text(view: ModuleView) -> str:
    """Terse and COMPLETE. Every list is the whole list; see ModuleView."""
    lines = [f"# {view.module}", f"  status        {view.status}"]
    # Classes and functions separately, each with a count. Completeness is the
    # point of this view, so nothing is dropped -- but a test module defines a
    # hundred `test_*` functions, and one undifferentiated wall is the same
    # delivery failure in a new place. A count lets a reader see the shape
    # before deciding to read the list.
    for label, items in (
        ("classes", view.classes),
        ("functions", view.functions),
        ("imports", view.imports_internal),
        ("imported by", view.imported_by),
        ("exports", view.exports if view.exports is not None else ()),
        ("name families", view.suffix_families),
    ):
        if label == "exports" and view.exports is None:
            lines.append(f"  {label:<13} (no __all__ and no explicit re-exports)")
            continue
        # "(none)" is a real answer here and must not be silence: it is what
        # makes "does this import that" answerable in the negative.
        count = f" ({len(items)})" if len(items) > 6 else ""
        lines.append(f"  {label + count:<13} {', '.join(items) if items else '(none)'}")
    if view.gates:
        lines.append("  exits")
        lines += [_gate_row(g) for g in view.gates]
    lines.append("  🔴 every list above is COMPLETE for this module; absence is an answer")
    return "\n".join(lines)


def _shown(total: int, top_n: int) -> str:
    """`(150; showing 12, --top 150 for the rest)`, or just `(12)` when nothing is hidden.

    🔴 A reader who is told "showing 12" and not told how to see the other 138
    cannot tell a missing fact from an unranked one, so absence from the list
    proves nothing to them. Measured: 24 pieces of real agent reasoning, all
    chosen because this command could in principle answer them, scored 0 -- and
    both blind readers named truncation, not the analysis, as the reason. The
    number was computed and then withheld. Printing the way to ask for it costs
    one clause.
    """
    if total <= top_n:
        return f"({total})"
    return f"({total}; showing {top_n}, --top {total} for the rest)"


def _conventions_to_text(report: ConventionsReport, *, top_n: int) -> str:
    """Terse on purpose: an agent pays per token to read this."""
    m = report.models
    lines = [
        f"# {report.root}: {report.modules_scanned} module(s) parsed"
        + (f", {report.modules_unparsed} unparsed" if report.modules_unparsed else "")
        + (f", {report.docs_scanned} doc file(s)" if report.docs_scanned else ""),
        *(
            [
                "# set aside: "
                + ", ".join(
                    part
                    for part in (
                        f"{report.partition.tests} test" if report.partition.tests else "",
                        f"{report.partition.nonsource} non-source"
                        if report.partition.nonsource
                        else "",
                        f"{report.partition.shadowed} in {', '.join(report.partition.shadow_roots)}"
                        " (duplicates its parent)"
                        if report.partition.shadowed
                        else "",
                    )
                    if part
                )
                # 🔴 Say how to get them back. Setting tests aside is right when a
                # vendored copy or a fixture pile would otherwise win every count,
                # and wrong when the question IS about tests -- "where do helpers
                # live in this test file, and what are they called" is a real
                # convention this command silently cannot answer. One of the 24
                # scored derivations asked exactly that.
                + ("  (--include-tests to census them)" if report.partition.tests else "")
            ]
            if report.partition
            and (report.partition.tests or report.partition.shadowed or report.partition.nonsource)
            else []
        ),
        "",
        f"## naming - by home module {_shown(len(report.naming), top_n)}",
    ]
    if not report.naming:
        lines.append("  (none: no class-name suffix is shared by enough definitions)")
    for home in report.naming[:top_n]:
        families = " ".join(f"*{f.suffix}({f.count})" for f in home.families)
        lines.append(f"  {home.module:<34} {home.total:>3}  {families}")
        top = home.families[0]
        lines.append(f"  {'':<34}      e.g. {', '.join(top.examples)}")

    # Kinds before names. A base family answers "what is it a kind of", which is
    # the question a suffix census silently fails when the two disagree -- and
    # they disagree in most projects, so this section leads.
    lines += [
        "",
        f"## kinds - classes grouped by what they derive from {_shown(len(report.bases), top_n)}",
    ]
    if not report.bases:
        lines.append("  (none: no base defined in this project has enough subclasses)")
    for b in report.bases[:top_n]:
        agree = f"{b.suffix_agreement:.0%}"
        note = (
            ""
            if b.suffix_agreement >= 0.8
            else f"  <- name is NOT the rule ({agree} share a suffix)"
        )
        lines.append(f"  {b.base:<28} {b.count:>3} @ {b.home_module}{note}")
        lines.append(f"  {'':<28}     e.g. {', '.join(b.members[:5])}")
        for c in b.shared_constants[:3]:
            dist = ", ".join(f"{v}x{n}" for v, n in c.distribution)
            lines.append(f"  {'':<28}     {c.name} = {dist}  ({c.setters} of {b.count} set it)")

    lines += [
        "",
        "## registries - values declared by repeated construction "
        + _shown(len(report.registries), top_n),
    ]
    if not report.registries:
        lines.append("  (none)")
    for r in report.registries[:top_n]:
        lines.append(f"  {r.constructor:<28} {r.count:>3} @ {r.home_module} ({r.home_count})")
        if r.literal_names:
            shown = ", ".join(r.literal_names[:6])
            more = f" (+{len(r.literal_names) - 6})" if len(r.literal_names) > 6 else ""
            lines.append(f"  {'':<28}     names: {shown}{more}")
        for c in r.keyword_defaults[:3]:
            dist = ", ".join(f"{v}x{n}" for v, n in c.distribution)
            # "13 of 80 pass it" is the fact; a bare distribution reads as if the
            # whole family were declared that way, which inverts the meaning of
            # an argument that is usually left at its default.
            lines.append(f"  {'':<28}     {c.name} = {dist}  ({c.setters} of {r.count} pass it)")

    if report.export_gaps:
        lines += [
            "",
            f"## export gaps ({len(report.export_gaps)}) - defined but not re-exported "
            "beside their siblings",
        ]
        for g in report.export_gaps:
            lines.append(
                f"  {g.export_module:<28} {g.family}: {g.exported}/{g.defined}"
                f"  missing {', '.join(g.missing)}"
            )

    if report.doc_gaps:
        lines += [
            "",
            f"## doc gaps ({len(report.doc_gaps)}) - public members their own docs do not name",
        ]
        for g in report.doc_gaps:
            lines.append(
                f"  {g.doc_root + '/':<28} {g.family}: {g.documented}/{g.defined}"
                f"  missing {', '.join(g.missing)}"
            )

    lines += [
        "",
        f"## surfaces {_shown(len(report.surfaces), top_n)} - wire all of these together",
    ]
    if not report.surfaces:
        lines.append("  (none)")
    for s in report.surfaces[:top_n]:
        where = s.module or "across modules"
        lines.append(
            f"  {s.kind:<8} {s.stem:<28} {s.surface_count} @ {where}: {', '.join(s.surfaces)}"
        )

    # The question this section exists for is "should MY NEW FINDING gate?", so
    # a finding-failure exit and a bad-input exit must not share a count.
    optional = [g for g in report.gates if g.optional]
    codes = ", ".join(str(c) for c in report.gate_codes) or "none literal"
    lines += [
        "",
        f"## gates ({len(report.gates)} finding-failure exit(s), "
        f"{len(optional)} caller-controlled; exit code(s): {codes})",
    ]
    if not report.gates:
        lines.append("  (none: no finding in this project fails the build)")
    lines += [_gate_row(g) for g in report.gates]

    kinds = ", ".join(
        f"{kind}={sum(1 for g in report.errors if g.kind == kind)}"
        for kind in sorted({g.kind for g in report.errors})
    )
    where = ", ".join(sorted({g.module for g in report.errors}))
    lines += [
        "",
        f"## errors ({len(report.errors)} user-error exit(s) - bad input, not a finding)",
    ]
    lines.append(f"  {kinds} @ {where}" if report.errors else "  (none)")

    bases = ", ".join(f"{b}={n}" for b, n in m.base_counts[:5])
    lines += [
        "",
        "## models",
        f"  {m.value_classes} value class(es) of {m.total_classes} class(es); "
        f"{m.frozen_classes} frozen ({m.frozen_ratio:.0%})",
        f"  dominant base: {m.dominant_base or '(none)'}" + (f"  bases: {bases}" if bases else ""),
        f"  config: {', '.join(f'{k}={n}' for k, n in m.config_flags) or '(none)'}",
        f"  collection fields: {m.tuple_fields} tuple / {m.list_fields} list "
        f"({m.tuple_ratio:.0%} tuple)",
    ]
    return "\n".join(lines)
