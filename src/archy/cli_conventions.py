"""Commands that report a project's house style: `conventions`, `brief` and the headers.

archy:owns        brief, conventions
archy:gates       exit 1 (config:run.stale)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click
from pydantic import BaseModel, ConfigDict

from archy.cli_app import _graph_kwargs_or_exit, _require, main
from archy.cli_check import (
    _no_verdict_error,
    _run_check_contracts,
    _transitive_checked,
    _transitive_unverified_reason,
    _unverifiable_contracts,
)
from archy.cli_render import (
    _brief_to_text,
    _contracts_outcome_to_dict,
    _conventions_to_dict,
    _conventions_to_text,
    _coverage_to_json,
    _module_view_to_text,
)
from archy.conventions import (
    ConventionsReport,
    compute_conventions,
    compute_module_view,
)
from archy.graph import (
    build_graph,
    discover_modules,
)
from archy.headers import (
    ModuleHeader,
    apply_header,
    compute_headers,
    existing_block,
    render_header,
)
from archy.layers import (
    LayerConfigError,
    compute_coverage,
    discover_config,
    load_config,
)

if TYPE_CHECKING:
    # Type-only: archy.install is imported lazily inside `install` to keep startup cheap.
    pass


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
    _require_consistent_header_flags(emit_headers, write, check)
    if module and any((emit_headers, write, check)):
        raise click.UsageError(
            "--module reports on one module; --emit-headers derives blocks for the whole tree. "
            "Run them separately."
        )


def _require_consistent_header_flags(emit_headers: bool, write: bool, check: bool) -> None:
    wants_action = write or check
    if wants_action and not emit_headers:
        raise click.UsageError("--write and --check require --emit-headers.")
    if write and check:
        raise click.UsageError("--write rewrites the tree, --check asserts it is already correct.")


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
    run = _sync_headers(headers, path, write=write, check=check)
    if fmt == "json":
        click.echo(json.dumps(_headers_payload(run), indent=2, sort_keys=True))
    else:
        _echo_headers_text(run, write=write, check=check)
    if check and run.stale:
        sys.exit(1)


class _HeaderRun(BaseModel):
    """What `--emit-headers` did: the derived headers, and which modules were stale or written."""

    model_config = ConfigDict(frozen=True)

    headers: tuple[ModuleHeader, ...]
    stale: tuple[str, ...]
    written: tuple[str, ...]


def _sync_headers(
    headers: tuple[ModuleHeader, ...], path: Path, *, write: bool, check: bool
) -> _HeaderRun:
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
    return _HeaderRun(headers=headers, stale=tuple(stale), written=tuple(written))


def _headers_payload(run: _HeaderRun) -> dict:
    return {
        "headers": [{**h.model_dump(), "rendered": render_header(h)} for h in run.headers],
        "stale": list(run.stale),
        "written": list(run.written),
    }


def _echo_headers_text(run: _HeaderRun, *, write: bool, check: bool) -> None:
    if check:
        if run.stale:
            click.echo(f"# {len(run.stale)} module(s) with a missing or stale archy header:")
            for module in run.stale:
                click.echo(f"#   {module}")
            click.echo("# `archy conventions --emit-headers --write` regenerates them.")
        else:
            click.echo(f"# {len(run.headers)} header(s) match the tree.")
    elif write:
        click.echo(
            f"# wrote {len(run.written)} header(s) into {len(run.headers)} scanned module(s)."
        )
    else:
        for header in run.headers:
            click.echo(f"# {header.path}")
            click.echo(render_header(header))
            click.echo("")
