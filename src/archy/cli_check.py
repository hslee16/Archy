"""Commands that check a project against its rules: `check` and `contracts`.

archy:owns        check, contracts
archy:gates       exit 1 (hardcoded), non-zero exit (hardcoded)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from pydantic import BaseModel, ConfigDict

from archy.cli_app import _layer_config_errors, main
from archy.cli_render import (
    ContractsOutcome,
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
from archy.contracts import (
    ContractsConfigError,
    ContractsNotAvailable,
    run_contracts,
)
from archy.graph import (
    ScanTooLargeError,
    build_graph,
)
from archy.layers import (
    LayerConfig,
    LayerConfigError,
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
from archy.scan_config import (
    scan_kwargs,
)


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
    config_path, config = _check_config(path, config_path)
    run = _check_scan(path, config_path, config)
    if with_contracts:
        run = run.model_copy(update={"contracts": _run_check_contracts(path, contracts_config)})

    if fmt == "json":
        click.echo(json.dumps(_check_payload(run), indent=2, sort_keys=True))
    else:
        _echo_check_text(run, show_unlayered=show_unlayered)
    if _check_fails(run):
        sys.exit(1)


class _CheckRun(BaseModel):
    """Everything `check` found, so the renderers and the exit decision share one value."""

    model_config = ConfigDict(frozen=True)

    config_path: Path
    config: LayerConfig
    coverage: LayerCoverage
    violations: tuple[Violation, ...]
    reach_violations: tuple[ReachViolation, ...]
    sdp_violations: tuple[SdpViolation, ...]
    presence_fails: bool
    contracts: ContractsOutcome | None = None


def _check_config(path: Path, config_path: Path | None) -> tuple[Path, LayerConfig]:
    if config_path is None:
        discovered = discover_config(path)
        if discovered is None:
            raise click.ClickException(
                f"no archy.yaml found near {path}; pass --config or create one at the project root."
            )
        config_path = discovered
    try:
        return config_path, load_config(config_path)
    except LayerConfigError as exc:
        raise click.ClickException(str(exc)) from exc


def _check_scan(path: Path, config_path: Path, config: LayerConfig) -> _CheckRun:
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

    sdp_violations: tuple[SdpViolation, ...] = ()
    if config.sdp.enabled:
        sdp_violations = tuple(find_sdp_violations(g, tolerance=config.sdp.tolerance))

    presence_fails = (
        config.min_layers_present is not None
        and coverage.layers_present < config.min_layers_present
    )
    return _CheckRun(
        config_path=config_path,
        config=config,
        coverage=coverage,
        violations=tuple(violations),
        reach_violations=tuple(reach_violations),
        sdp_violations=sdp_violations,
        presence_fails=presence_fails,
    )


def _check_payload(run: _CheckRun) -> dict:
    checked = _transitive_checked(run.contracts)
    payload = {
        "violations": _violations_to_json(list(run.violations)),
        "required_violations": _reach_violations_to_json(list(run.reach_violations)),
        "sdp_violations": _sdp_violations_to_json(list(run.sdp_violations)),
        "sdp_mode": run.config.sdp.mode,
        "coverage": _coverage_to_json(run.coverage),
        "min_layers_present": run.config.min_layers_present,
        "presence_fails": run.presence_fails,
        # A clean verdict has to say what it looked at. The text output has
        # carried this since v0.46 and the JSON did not, so a machine reader
        # could not tell "checked transitively and clean" from "never
        # looked" (#343).
        "transitive_checked": checked,
        "transitive_unverified_reason": (
            None
            if checked
            else _transitive_unverified_reason(
                run.config,
                run.coverage,
                list(run.violations),
                no_verdict_error=_no_verdict_error(run.contracts),
                unverifiable=_unverifiable_contracts(run.contracts),
            )
        ),
    }
    if run.contracts is not None:
        payload["contracts"] = _contracts_outcome_to_dict(run.contracts)
    return payload


def _echo_check_text(run: _CheckRun, *, show_unlayered: bool) -> None:
    config, coverage = run.config, run.coverage
    click.echo(_violations_to_text(list(run.violations), run.config_path, coverage))
    if config.required:
        click.echo(_reach_violations_to_text(list(run.reach_violations), config.required))
    click.echo(_coverage_to_text(coverage))
    hints = _pattern_hints_to_text(coverage)
    if hints:
        click.echo(hints)
    if run.contracts is None:
        handoff = _contracts_handoff_to_text(config, coverage, list(run.violations))
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
        click.echo(_sdp_violations_to_text(list(run.sdp_violations), config.sdp.tolerance))
        if run.sdp_violations and config.sdp.mode == "warn":
            click.echo("# (sdp.mode=warn; not failing the gate)")
    if run.contracts is not None:
        click.echo("")
        click.echo(_contracts_outcome_to_text(run.contracts))


def _check_fails(run: _CheckRun) -> bool:
    sdp_fails = bool(run.sdp_violations) and run.config.sdp.mode == "error"
    # Required-reach failures gate like forbid violations do. They are opt-in
    # (`required:` is absent from every config that predates the feature), so
    # nothing that passed before can start failing.
    reach_fails = bool(run.reach_violations)
    # A presence shortfall fails the gate like a violation does. Forbidding
    # edges between layers says nothing about whether the layers exist, and a
    # codebase that collapsed them into one module satisfies every forbid rule
    # by having no cross-layer edges at all.
    return bool(run.violations) or reach_fails or sdp_fails or run.presence_fails


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
