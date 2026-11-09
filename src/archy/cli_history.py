"""Commands over a project's score and findings: score, trend, snapshot, diff, cycles, simulate.

archy:owns        cycles, diff, score, simulate, snapshot, trend
archy:gates       exit 1 (config:result.strict), exit 1 (flag:--strict),
                  exit 1 (flag:--strict-tolerance)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from archy.cli_app import (
    _gate_against_history,
    _layer_config_errors,
    _load_graph,
    _load_graph_pair,
    _require,
    main,
)
from archy.cli_render import (
    _cycle_scan_to_dict,
    _cycles_to_json,
    _cycles_to_text,
    _diff_to_text,
    _gate_to_dict,
    _gate_to_text,
    _score_to_dict,
    _score_to_text,
    _simulate_to_text,
    _snapshot_to_text,
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
from archy.graph import (
    internal_module_count,
    zero_modules_reason,
)
from archy.history import append as append_history
from archy.history import git_metadata, row_from_score
from archy.history import read as read_history
from archy.layers import (
    discover_config,
)
from archy.scan_config import (
    resolve_exclude_type_checking,
)
from archy.score import compute_score
from archy.simulate import find_simulate
from archy.trend import render_text as render_trend


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
    _refuse_vacuous_score(path, s, record=record, strict=strict)

    history_path = path / ".archy" / "history.jsonl"
    # Strict reads BEFORE recording so the comparison is against the truly
    # previous run rather than the row we are about to append.
    gate = _gate_against_history(s, history_path) if strict else None

    _echo_score(s, gate, fmt=fmt, strict_tolerance=strict_tolerance)
    if record:
        _record_score(path, s, history_path)
    if gate is not None and gate["delta"] is not None and gate["delta"] < -strict_tolerance:
        sys.exit(1)


def _refuse_vacuous_score(path: Path, s, *, record: bool, strict: bool) -> None:
    if s.inputs.module_count == 0 and (record or strict):
        # A vacuous 1.000 recorded to history reads as an improvement to every
        # later `--strict` run, and `--strict` over it passes by construction (#523).
        raise click.ClickException(
            f"{zero_modules_reason(path, 'scored')}; refusing to "
            f"{'record' if record else 'gate against history'}"
        )


def _echo_score(s, gate: dict | None, *, fmt: str, strict_tolerance: float) -> None:
    if fmt == "json":
        payload = _score_to_dict(s)
        if gate is not None:
            payload["gate"] = _gate_to_dict(gate, strict_tolerance)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    click.echo(_score_to_text(s))
    if gate is not None:
        click.echo("")
        click.echo(_gate_to_text(gate, strict_tolerance))


def _record_score(path: Path, s, history_path: Path) -> None:
    commit, branch = git_metadata(path)
    append_history(history_path, row_from_score(s, commit=commit, branch=branch))


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
    # A scan that found no internal module checked nothing (#523). The list stays
    # `[]` on stdout so JSON consumers see no shape change; the reason goes to
    # stderr, and `--strict` refuses rather than pass over nothing.
    no_modules = zero_modules_reason(path, "checked") if internal_module_count(g) == 0 else None

    if fmt == "json":
        payload = _cycle_scan_to_dict(scan) if exclude else _cycles_to_json(found)
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        if no_modules is not None and not strict:
            click.echo(f"warning: {no_modules}", err=True)
    else:
        click.echo(
            _cycles_to_text(
                found,
                min_size,
                excluded=scan.excluded_type_checking_edges if exclude else None,
                no_modules=no_modules,
            )
        )

    if strict and no_modules is not None:
        raise click.ClickException(no_modules)
    if strict and found:
        sys.exit(1)


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
