"""Commands that read git history: `hotspots`, `coupling`, `duplicates`, `what-to-refactor-next`.

archy:owns        coupling, duplicates, hotspots, what_to_refactor_next
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from archy.cli_app import _graph_kwargs_or_exit, _load_graph, _require, main
from archy.cli_render import (
    _coupling_to_dict,
    _coupling_to_text,
    _duplicates_to_dict,
    _duplicates_to_text,
    _hotspots_to_dict,
    _hotspots_to_text,
    _refactor_to_dict,
    _refactor_to_text,
)
from archy.coupling import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_SUPPORT,
    compute_coupling,
    git_cochange,
    internal_module_paths,
)
from archy.duplicates import (
    classify_variants,
    compute_duplicates,
    compute_near_duplicates,
    demote_independent,
    is_test_path,
)
from archy.gitlog import (
    InvalidSinceError,
    resolve_repo_root,
    since_window_empty,
)
from archy.graph import (
    ScanTooLargeError,
    parse_project,
)
from archy.hotspots import NoCommitsError, compute_hotspots, git_churn
from archy.refactor import (
    DEFAULT_MIN_RISK,
    compute_refactor_priorities,
)
from archy.scan_config import (
    resolve_max_modules,
)


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

    The hotspot label requires at least 5% of the project's maximum CC x
    churn score. Lower scores keep their rank but lose the hotspot label
    and rationale; a row below both labeling floors has no lenses.
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
