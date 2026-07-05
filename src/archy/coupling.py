"""Change coupling (temporal coupling): module pairs that change together in
git history but share no import/call edge (advisory, not a score axis).

The import + call graph captures *structural* coupling. This captures
*behavioral* coupling (Gall et al.; Tornhill / CodeScene's logical-coupling
lineage, the same git history `archy hotspots` mines): two modules that change
together commit after commit yet have no edge between them signal a hidden
dependency or a missing abstraction the structural analysis cannot see. The
output is deliberately filtered to pairs the graph *misses* (no import/call edge
either direction), so it complements the graph rather than restating it, and the
framing is agent-actionable ("you're editing X; Y historically co-changes, check
Y").

Co-change is noisy: a single sweeping commit (a reformat, a mass rename, a
license-header pass) touches hundreds of files and would couple all of them.
Two defenses, both required before the signal is trustworthy:

* **Commit-size normalization** - a commit touching more than `max_commit_files`
  `.py` files is dropped entirely (from both the pair counts and the per-file
  denominators), so only focused commits vote. This is why `git_cochange`'s
  per-file `counts` differ from `hotspots.git_churn` (which caps nothing): the
  coupling denominator is "focused commits touching this file", the honest base
  for "when I touch it in a real change, how often does the other come along".
* **A coupling-strength threshold** - `confidence = support / min(count_a,
  count_b)` (the CodeScene/Tornhill "degree of coupling": of the commits
  touching the *rarer* of the two files, the fraction that also touched the
  other), gated by a minimum `support` so a 2-of-2 accident cannot reach 100%.

Both defaults are calibrated on the bench, same FP discipline as the duplicate
(§12b) and dead-code (§12) studies; see `docs/research/RESEARCH_METRICS.md` §7.
Advisory only, never folded into `archy score`.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import NamedTuple

import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy.gitlog import fold_rename, normalize_py_path, resolve_repo_root

# Placeholder defaults; calibrated on the bench (bench/coupling_sweep.py ->
# bench/coupling_results.md) before shipping, mirroring how the duplicate
# `--min-nodes` and hotspot churn-window defaults were settled.
DEFAULT_MIN_SUPPORT = 5
DEFAULT_MIN_CONFIDENCE = 0.5
# A commit touching more than this many `.py` files is a sweeping change
# (reformat / mass rename / dependency bump), not a focused edit that couples
# its files. Above the cap the C(n, 2) pairs it would mint are pure noise, so it
# is dropped wholesale.
DEFAULT_MAX_COMMIT_FILES = 30


class CoChangeData(NamedTuple):
    """Raw co-change tallies from one `git log` pass over focused commits.

    A `NamedTuple`, not a frozen pydantic model: this is internal plumbing
    (like `git_churn`'s bare `dict` return), and `pair_support` can hold
    millions of entries on a large repo, so paying pydantic per-item validation
    to build it would be wasteful and buys nothing (it is never serialized).

    `counts`: per-file focused-commit count (the confidence denominator).
    `pair_support`: focused commits touching both files, keyed by the file-path
    pair sorted so `(a, b)` and `(b, a)` collapse to one entry.
    """

    counts: dict[str, int]
    pair_support: dict[tuple[str, str], int]


class CouplingPair(BaseModel):
    """One behaviorally-coupled, structurally-unconnected module pair.

    Modules are ordered (`module_a < module_b`) for deterministic output.
    `support` is the number of focused commits touching both; `confidence` is
    `support / min(count_a, count_b)`, the fraction of the rarer module's
    focused commits that also touched the other - the primary ranking key.
    """

    model_config = ConfigDict(frozen=True)

    module_a: str
    module_b: str
    path_a: str
    path_b: str
    support: int
    confidence: float
    count_a: int
    count_b: int


def git_cochange(
    root: Path,
    *,
    since: str | None = None,
    max_commit_files: int = DEFAULT_MAX_COMMIT_FILES,
    keep_paths: frozenset[str] | None = None,
) -> CoChangeData | None:
    """Mine co-change tallies from `git log`, or `None` if `root` is not a repo.

    One `git log -M --name-status --format=%H` pass: each commit is a `%H`
    marker line followed by its name-status block, so files are bucketed per
    commit (unlike `git_churn`, whose per-file pass discards the grouping).
    Rename (`R`) / copy (`C`) lines count the current path, and `R` links are
    remembered so a file's pre-rename history folds onto its final path -
    keeping a co-change pair stable across a `git mv` instead of splitting it.

    `max_commit_files` drops sweeping commits (see module docstring).
    `keep_paths`, when given, restricts counting to those resolved paths (the
    project's internal module files); this both scopes the result to rankable
    pairs and bounds `pair_support` on large repos, where the unrestricted
    cross product would be enormous.

    Rename folding shares `git_churn`'s model: if a path is reused after a
    `git mv` (a *new* `a.py` created after `a.py` was renamed to `b.py`), both
    fold onto `b.py`, so the reused path's history is attributed to the current
    file. This is a rare edge that needs `min_support` recurrences to surface a
    pair, accepted for parity with the churn pass.
    """
    repo_root = resolve_repo_root(root)
    if repo_root is None:
        return None

    # `-M` forces rename detection regardless of the user's `diff.renames` config
    # so the `R` lines the rename-folding relies on are always present.
    cmd = ["git", "-C", str(repo_root), "log", "-M", "--name-status", "--format=%H"]
    if since:
        cmd.append(f"--since={since}")
    try:
        log_proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="surrogateescape", check=True
        )
    except subprocess.CalledProcessError:
        return None

    commits: list[list[str]] = []
    current: list[str] = []
    renames: dict[str, str] = {}
    for line in log_proc.stdout.splitlines():
        if not line:
            continue
        if _is_commit_marker(line):
            if current:
                commits.append(current)
            current = []
            continue
        parts = line.split("\t")
        status = parts[0]
        if status[:1] in ("R", "C") and len(parts) == 3:
            old, new = parts[1], parts[2]
            current.append(new)
            if status[:1] == "R":
                renames[old] = new
        elif len(parts) >= 2:
            current.append(parts[1])
    if current:
        commits.append(current)

    counts: Counter[str] = Counter()
    pair_support: Counter[tuple[str, str]] = Counter()
    for raw_files in commits:
        all_py: set[str] = set()
        for raw in raw_files:
            normalized = normalize_py_path(repo_root, fold_rename(renames, raw))
            if normalized is not None:
                all_py.add(normalized)
        # The cap measures the WHOLE commit's `.py` footprint, before scoping to
        # internal modules: a 300-file reformat that grazes three modules is
        # still a sweep, and those three did not meaningfully co-change. Drop it
        # wholesale rather than let the incidental pair through.
        if not all_py or len(all_py) > max_commit_files:
            continue
        touched = all_py if keep_paths is None else all_py & keep_paths
        for f in touched:
            counts[f] += 1
        for a, b in combinations(sorted(touched), 2):
            pair_support[(a, b)] += 1
    return CoChangeData(counts=dict(counts), pair_support=dict(pair_support))


def _is_commit_marker(line: str) -> bool:
    """True for a bare `%H` commit hash line (all-hex, no tab).

    Name-status lines always carry a status char + tab, so a hex-only,
    tab-free line is unambiguously the commit marker (works for both 40-char
    sha1 and 64-char sha256 repositories).
    """
    return "\t" not in line and len(line) in (40, 64) and all(c in "0123456789abcdef" for c in line)


def internal_module_paths(graph: nx.DiGraph) -> dict[str, str]:
    """Map each internal module's resolved file path to its qualname.

    Keyed by `str(Path(path).resolve())` so it joins directly against the
    resolved paths `git_cochange` emits. External nodes and nodes without a
    path are skipped.
    """
    out: dict[str, str] = {}
    for node, data in graph.nodes(data=True):
        if data.get("external"):
            continue
        path = data.get("path")
        if path:
            out[str(Path(path).resolve())] = node
    return out


def compute_coupling(
    graph: nx.DiGraph,
    cochange: CoChangeData,
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[CouplingPair]:
    """Rank internal module pairs by co-change confidence, no structural edge.

    A pair is surfaced only if both endpoints are internal modules, they have
    NO import/call edge in either direction (the graph already covers structural
    coupling), `support >= min_support`, and `confidence >= min_confidence`.
    Ranked by `(-confidence, -support, module_a, module_b)`.
    """
    path_to_module = internal_module_paths(graph)
    out: list[CouplingPair] = []
    for (path_a, path_b), support in cochange.pair_support.items():
        if support < min_support:
            continue
        module_a = path_to_module.get(path_a)
        module_b = path_to_module.get(path_b)
        if module_a is None or module_b is None:
            continue
        if graph.has_edge(module_a, module_b) or graph.has_edge(module_b, module_a):
            continue
        count_a = cochange.counts[path_a]
        count_b = cochange.counts[path_b]
        denom = min(count_a, count_b)
        if denom == 0:  # defensive: a supported pair always has positive counts
            continue
        confidence = support / denom
        if confidence < min_confidence:
            continue
        # Order the two endpoints so output is deterministic and each pair
        # appears once; carry each module's own path and count alongside it.
        first, second = sorted([(module_a, path_a, count_a), (module_b, path_b, count_b)])
        out.append(
            CouplingPair(
                module_a=first[0],
                module_b=second[0],
                path_a=first[1],
                path_b=second[1],
                support=support,
                confidence=confidence,
                count_a=first[2],
                count_b=second[2],
            )
        )
    out.sort(key=lambda c: (-c.confidence, -c.support, c.module_a, c.module_b))
    return out
