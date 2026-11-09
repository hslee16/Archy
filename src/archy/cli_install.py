"""Commands that manage archy itself: `install`, `uninstall`, `mcp` and `index`.

archy:owns        index, index_clear, index_status, index_sync, install, mcp, uninstall
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import TYPE_CHECKING

import click

from archy.cli_app import _graph_kwargs_or_exit, main
from archy.graph import (
    ScanTooLargeError,
    discover_modules,
)
from archy.scan_config import (
    resolve_max_modules,
)

if TYPE_CHECKING:
    # Type-only: archy.install is imported lazily inside `install` to keep startup cheap.
    from archy.install import ConfigPreview


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
