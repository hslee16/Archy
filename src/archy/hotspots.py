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

Implementation note: a single ``git log --name-status -M --format=``
invocation, streamed line by line, gives per-file commit counts in one
pass - no per-file ``git log`` commands and no co-change matrix. Rename
lines (``R`` status) fold the old path's history onto the current path so
a renamed file keeps its full churn instead of splitting it across a
phantom old-path entry that matches no live graph node. If the project
isn't inside a git repository (or git is unavailable), ``git_churn``
returns ``None`` rather than raising, and the CLI surfaces the
diagnostic separately. Diagnostic only - not folded into ``archy score``.

The default churn window is full history. The 27-project bench sweep
in ``bench/hotspots_results.md`` (median Jaccard(full, 12mo) = 0.60,
median stale-fraction = 0.25) shows full history carries about 25%
recency contamination but is the only window that produces a stable
top-K on low-activity projects, where a 12-month cap can collapse the
result set to fewer than 20 items. Use ``--since`` (a CLI passthrough
to ``git log --since``) when you want the "what should I refactor
right now" lens instead of the historical view.
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
    """Per-``.py``-file commit count from ``git log --name-status -M``.

    Keys are absolute, resolved paths so callers can match against the
    graph's per-node ``path`` attribute without worrying about
    working-directory or repo-root differences. Returns ``None`` if
    ``root`` isn't inside a git repository or git isn't available;
    returns ``{}`` for an empty (or fully-filtered) history.

    Renamed files keep their full history: a ``git mv`` splits a file's
    commits across the old and new path, so rename (``R``) status lines
    fold the pre-rename count onto the current path. Without this a moved
    file is undercounted and its old-path history becomes a phantom entry
    that matches no live graph node, systematically under-ranking exactly
    the files that have been reorganized.
    """
    try:
        top_proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            # `surrogateescape` round-trips non-UTF8 path bytes instead of
            # raising: git's default `core.quotePath` already ASCII-escapes
            # them, but a repo with `core.quotePath=false` would otherwise
            # crash the decode and break the documented "return None" contract.
            errors="surrogateescape",
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    repo_root = Path(top_proc.stdout.strip())

    # `-M` forces rename detection regardless of the user's `diff.renames`
    # config so the `R` lines we rely on are always present.
    cmd = ["git", "-C", str(repo_root), "log", "-M", "--name-status", "--format="]
    if since:
        cmd.append(f"--since={since}")
    try:
        log_proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="surrogateescape", check=True
        )
    except subprocess.CalledProcessError:
        return None

    raw_counts: Counter[str] = Counter()
    renames: dict[str, str] = {}
    for line in log_proc.stdout.splitlines():
        if not line:
            continue  # `--format=` leaves a blank line between commits
        parts = line.split("\t")
        status = parts[0]
        if status[:1] in ("R", "C") and len(parts) == 3:
            # `R<sim>\told\tnew` / `C<sim>\told\tnew`: the rename/copy commit
            # itself touched the file, so count the current path. For a rename
            # (not a copy, where the old path still exists) remember the link
            # so the old path's earlier commits fold onto the new path below.
            old, new = parts[1], parts[2]
            raw_counts[new] += 1
            if status[:1] == "R":
                renames[old] = new
        elif len(parts) >= 2:
            raw_counts[parts[1]] += 1

    def _final_path(path: str) -> str:
        seen = {path}
        while path in renames:
            path = renames[path]
            if path in seen:  # defensive: never loop on a pathological chain
                break
            seen.add(path)
        return path

    counts: Counter[str] = Counter()
    for path, n in raw_counts.items():
        final = _final_path(path)
        if not final.endswith(".py"):
            continue
        # `resolve()` here normalizes against symlinks and `..` segments,
        # matching what `parse_file` / graph node `path` produce upstream.
        counts[str((repo_root / final).resolve())] += n
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
