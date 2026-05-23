"""Click-based command-line interface for archy."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import click
import networkx as nx

from archy import __version__
from archy.affected import DEFAULT_DEPTH, Affected, find_affected
from archy.contracts import (
    ContractsConfigError,
    ContractsNotAvailable,
    ContractsResult,
    run_contracts,
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
from archy.graph import DEFAULT_IGNORED_DIRS, build_graph, discover_modules, graph_to_dict
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.hotspots import Hotspot, compute_hotspots, git_churn
from archy.impact import Impact, find_impact
from archy.layers import (
    LayerConfigError,
    SdpViolation,
    Violation,
    discover_config,
    find_sdp_violations,
    find_violations,
    load_config,
)
from archy.score import Score, compute_score
from archy.trend import render_text as render_trend


@click.group()
@click.version_option(__version__)
def main() -> None:
    """archy - architectural sensor for Python codebases."""


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
def check(path: Path, config_path: Path | None, fmt: str) -> None:
    """Check the project at PATH against layer rules in archy.yaml.

    Exits 0 if there are no violations, 1 otherwise.
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

    g = build_graph(
        path,
        ignored_dirs=DEFAULT_IGNORED_DIRS | frozenset(config.exclude),
        extra_roots=config.roots,
    )
    try:
        violations = find_violations(g, config)
    except LayerConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    sdp_violations: list[SdpViolation] = []
    if config.sdp.enabled:
        sdp_violations = find_sdp_violations(g, tolerance=config.sdp.tolerance)

    if fmt == "json":
        payload = {
            "violations": _violations_to_json(violations),
            "sdp_violations": _sdp_violations_to_json(sdp_violations),
            "sdp_mode": config.sdp.mode,
        }
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
    else:
        click.echo(_violations_to_text(violations, config_path))
        if config.sdp.enabled:
            click.echo("")
            click.echo(_sdp_violations_to_text(sdp_violations, config.sdp.tolerance))
            if sdp_violations and config.sdp.mode == "warn":
                click.echo("# (sdp.mode=warn; not failing the gate)")

    sdp_fails = bool(sdp_violations) and config.sdp.mode == "error"
    if violations or sdp_fails:
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
    rows = read_history(path / ".archy" / "history.jsonl")
    if fmt == "json":
        window = rows[-last_n:] if last_n > 0 else rows
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
def impact(path: Path, files: tuple[Path, ...], fmt: str) -> None:
    """List internal modules that depend on the given file(s).

    Resolves each --file to a qualname via the import graph and prints
    every module that transitively imports any of them.
    """
    g = _load_graph(path, internal_only=True)
    result = find_impact(g, [path / f if not f.is_absolute() else f for f in files])

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
        "Read changed file paths from stdin, one per line. "
        "Pairs with `git diff --name-only | archy affected --stdin`."
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
    "--out",
    "out_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Where to write the snapshot. Defaults to PATH/.archy/baseline.json.",
)
def snapshot(path: Path, out_path: Path | None) -> None:
    """Capture score, cycles, and layer violations as a baseline for `archy diff`."""
    g = _load_graph(path, internal_only=True)
    snap = take_snapshot(g, config_path=discover_config(path))
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
    g = _load_graph(path, internal_only=True)
    current = take_snapshot(g, config_path=discover_config(path))
    result = compute_diff(baseline, current)
    result = result.model_copy(update={"summary": summarize_diff(result, g, top_n=top_n)})
    if fmt == "json":
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(_diff_to_text(result))


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
        click.echo(str(exc), err=True)
        sys.exit(2)

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
            click.echo(render_diff_text(diff, current))
        return

    if fmt == "json":
        click.echo(json.dumps(render_json(current), indent=2, sort_keys=True))
    else:
        click.echo(render_ascii(current, max_nodes=max_nodes))


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
        detect_all,
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

        if not yes:
            detected = {d.adapter.id for d in detect_all() if d.detected}
            click.echo(f"archy will configure ({location}):")
            for adapter in adapters:
                mark = "detected" if adapter.id in detected else "not detected"
                click.echo(f"  - {adapter.name} ({adapter.id}, {mark})")
            if not click.confirm("Proceed?", default=True):
                click.echo("Aborted.")
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
        detect_all,
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

        if not yes:
            detected = {d.adapter.id for d in detect_all() if d.detected}
            click.echo(f"archy will be removed from ({location}):")
            for adapter in adapters:
                mark = "detected" if adapter.id in detected else "not detected"
                click.echo(f"  - {adapter.name} ({adapter.id}, {mark})")
            if not click.confirm("Proceed?", default=True):
                click.echo("Aborted.")
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


def _load_graph(path: Path, *, internal_only: bool) -> nx.DiGraph:
    g = build_graph(path, **_graph_kwargs(path))
    if internal_only:
        external = {n for n, d in g.nodes(data=True) if d.get("external")}
        g.remove_nodes_from(external)
    return g


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


def _violations_to_text(violations: list[Violation], config_path: Path) -> str:
    if not violations:
        return f"# No layer violations (config: {config_path})."
    lines = [f"# {len(violations)} layer violation(s) (config: {config_path})"]
    current_rule: tuple[str, str] | None = None
    for v in violations:
        rule_pair = (v.rule.from_layer, v.rule.to_layer)
        if rule_pair != current_rule:
            lines.append(f"\n{v.rule.from_layer} -> {v.rule.to_layer} (forbidden):")
            current_rule = rule_pair
        lines.append(f"  {v.source} -> {v.target}  {_format_lines(v.lines)}")
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
    return "\n".join(lines)


def _summary_to_text(summary: DiffSummary) -> list[str]:
    lines = [f"# summary: {summary.headline}"]
    if summary.top_regressions:
        lines.append("")
        lines.append("## top regressions (risk-weighted):")
        for item in summary.top_regressions:
            lines.append(f"  risk={item.risk:.2f}  {item.description}")
    if summary.top_improvements:
        lines.append("")
        lines.append("## top improvements (risk-weighted):")
        for item in summary.top_improvements:
            lines.append(f"  risk={item.risk:.2f}  {item.description}")
    return lines


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
