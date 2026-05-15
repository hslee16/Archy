"""Per-file hotspots = cyclomatic complexity x git churn.

Tornhill / CodeScene's "Code Red" formulation: a file is a refactoring
hotspot when it is both complex (high CC) and changes often (high
churn). Neither signal is enough on its own. A complex but stable file
is load-bearing but tolerable; a high-churn but simple file is just an
active part of the codebase. The product surfaces the cells that score
on both axes - the refactor-priority list, not a single number.

Churn is the number of commits that touched each ``.py`` file over a
window (default: full history). Complexity is the per-module ``cc_sum``
already attached to every internal node by the v0.17 tree-sitter walker;
archy uses the file's total CC rather than ``cc_max`` because a file
with twenty branchy functions is a bigger refactoring target than one
with a single ten-CC function.

Implementation note: a single ``git log --name-only --format=`` invocation,
streamed line by line, gives per-file commit counts in one pass - no
per-file ``git log`` commands and no co-change matrix. If the project
isn't inside a git repository (or git is unavailable), ``git_churn``
returns ``None`` rather than raising, and the CLI surfaces the
diagnostic separately. Diagnostic only - not folded into ``archy score``.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict


class Hotspot(BaseModel):
    """A single file's hotspot row: module qualname, file path, the two
    component signals, and their product."""

    model_config = ConfigDict(frozen=True)

    module: str
    path: str
    cc_sum: int
    churn: int
    score: int


def git_churn(root: Path, *, since: str | None = None) -> dict[str, int] | None:
    """Per-``.py``-file commit count from ``git log --name-only``.

    Keys are absolute, resolved paths so callers can match against the
    graph's per-node ``path`` attribute without worrying about
    working-directory or repo-root differences. Returns ``None`` if
    ``root`` isn't inside a git repository or git isn't available;
    returns ``{}`` for an empty (or fully-filtered) history.
    """
    try:
        top_proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    repo_root = Path(top_proc.stdout.strip())

    cmd = ["git", "-C", str(repo_root), "log", "--name-only", "--format="]
    if since:
        cmd.append(f"--since={since}")
    try:
        log_proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError:
        return None

    counts: Counter[str] = Counter()
    for raw in log_proc.stdout.splitlines():
        rel = raw.strip()
        if not rel.endswith(".py"):
            continue
        # `resolve()` here normalizes against symlinks and `..` segments,
        # matching what `parse_file` / graph node `path` produce upstream.
        counts[str((repo_root / rel).resolve())] += 1
    return dict(counts)


def compute_hotspots(graph: nx.DiGraph, *, churn: dict[str, int]) -> list[Hotspot]:
    """Rank internal modules by ``cc_sum * churn``.

    Modules with zero CC or zero churn are dropped: the product only
    flags files that score on both axes, and surfacing rows with a zero
    component would dilute the "refactor these first" signal the metric
    is supposed to produce. Ties break by churn (higher first), then by
    qualname (alphabetical) for stable output.
    """
    out: list[Hotspot] = []
    for node, data in graph.nodes(data=True):
        if data.get("external"):
            continue
        path = data.get("path")
        if not path:
            continue
        cc = int(data.get("cc_sum", 0))
        if cc <= 0:
            continue
        n_commits = churn.get(str(Path(path).resolve()), 0)
        if n_commits <= 0:
            continue
        out.append(
            Hotspot(
                module=node,
                path=path,
                cc_sum=cc,
                churn=n_commits,
                score=cc * n_commits,
            )
        )
    out.sort(key=lambda h: (-h.score, -h.churn, h.module))
    return out
