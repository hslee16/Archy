"""Map changed source files to impacted modules and test files.

Sibling of `archy.impact`: where `find_impact` returns the *entire* reverse-
reachable set with no depth cap, `find_affected` is the CI-shaped variant
that (a) caps traversal depth so a one-line edit doesn't fan out to
thousands of modules on a monorepo, and (b) classifies the impacted set
into "test files to run" and "other modules touched" using either
auto-detected test conventions or a user-supplied glob.

The intended use is `git diff --name-only | archy affected --stdin`:
given the changed files in a PR, surface the precise set of tests whose
behavior could be affected. Internal-only at launch (see
`docs/SPEC_INDEX_AND_INSTALL.md` Q3); third-party / vendored code is out
of scope until real user demand surfaces.
"""

from __future__ import annotations

import re
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy.impact import _index_by_path

DEFAULT_DEPTH = 5

# Auto-detected test conventions. A node is treated as a test if its
# path (relative to project root, POSIX) matches any of these, *or* any
# path segment equals "tests". Conventions chosen to match what pytest
# itself discovers by default; users with a different layout pass
# --filter explicitly.
_AUTO_TEST_GLOBS: tuple[str, ...] = (
    "**/test_*.py",
    "**/*_test.py",
)
_AUTO_TEST_DIR = "tests"


class Affected(BaseModel):
    """Result of `find_affected`. JSON-stable: tuples are sorted."""

    model_config = ConfigDict(frozen=True)

    changed: tuple[str, ...]
    unresolved: tuple[str, ...]
    impacted_modules: tuple[str, ...]
    impacted_tests: tuple[str, ...]
    depth: int
    test_filter: str | None = None


def find_affected(
    graph: nx.DiGraph,
    files: list[Path],
    *,
    project_root: Path,
    depth: int = DEFAULT_DEPTH,
    test_filter: str | None = None,
) -> Affected:
    """Return modules and tests transitively affected by `files`.

    Traversal is reverse-reachable up to `depth` hops, matching the
    structural-impact direction of `archy.impact.find_impact` but bounded
    so monorepo blast radius stays useful in CI. `test_filter`, if
    provided, is a recursive glob (`**` matches any number of path
    segments) evaluated against project-relative POSIX paths; when
    omitted, the built-in pytest conventions in `_AUTO_TEST_GLOBS` plus
    "any path segment named 'tests'" are used.

    `impacted_tests` and `impacted_modules` are disjoint by construction:
    every impacted module is in exactly one of them. The changed set
    itself is excluded from both (consistent with `find_impact`).
    """
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")

    path_to_qualname = _index_by_path(graph)

    changed: set[str] = set()
    unresolved: list[str] = []
    for f in files:
        resolved = f.resolve()
        qualname = path_to_qualname.get(resolved)
        if qualname is None:
            unresolved.append(str(f))
        else:
            changed.add(qualname)

    impacted = _bounded_ancestors(graph, changed, depth=depth)
    impacted -= changed
    impacted = {q for q in impacted if not graph.nodes[q].get("external")}

    is_test = _test_classifier(project_root, test_filter)
    tests: set[str] = set()
    modules: set[str] = set()
    for q in impacted:
        node_path = graph.nodes[q].get("path")
        if node_path and is_test(Path(node_path)):
            tests.add(q)
        else:
            modules.add(q)

    return Affected(
        changed=tuple(sorted(changed)),
        unresolved=tuple(sorted(unresolved)),
        impacted_modules=tuple(sorted(modules)),
        impacted_tests=tuple(sorted(tests)),
        depth=depth,
        test_filter=test_filter,
    )


def _bounded_ancestors(graph: nx.DiGraph, sources: set[str], *, depth: int) -> set[str]:
    """Reverse-reachable set from `sources` within `depth` hops.

    NetworkX has `nx.ancestors` (unbounded) but no depth-capped version.
    `single_source_shortest_path_length` on the reversed graph gives us
    distances; we union across all sources and keep nodes whose minimum
    distance is <= depth.
    """
    reverse = graph.reverse(copy=False)
    out: set[str] = set()
    for src in sources:
        if src not in reverse:
            continue
        distances = nx.single_source_shortest_path_length(reverse, src, cutoff=depth)
        out.update(distances.keys())
    return out


def _test_classifier(project_root: Path, test_filter: str | None):
    """Return a callable `path -> bool` that decides if `path` is a test file.

    If `test_filter` is given, only that glob applies (single-pattern mode,
    so users can express tighter or looser rules than the defaults).
    Otherwise the auto-detection rules apply: filename matches `test_*.py`
    or `*_test.py`, OR any path segment equals `tests`.
    """
    root = project_root.resolve()

    if test_filter is not None:
        pattern = _compile_glob(test_filter)

        def _user(path: Path) -> bool:
            try:
                rel = path.resolve().relative_to(root).as_posix()
            except ValueError:
                return False
            return bool(pattern.fullmatch(rel))

        return _user

    auto_patterns = [_compile_glob(g) for g in _AUTO_TEST_GLOBS]

    def _auto(path: Path) -> bool:
        try:
            rel_path = path.resolve().relative_to(root)
        except ValueError:
            return False
        rel = rel_path.as_posix()
        if any(p.fullmatch(rel) for p in auto_patterns):
            return True
        return _AUTO_TEST_DIR in rel_path.parts

    return _auto


def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate a recursive glob to a regex.

    Semantics match the common "gitignore-ish" recursive glob:
    - `**` matches any number of path segments (including zero)
    - `*` matches anything except `/`
    - `?` matches a single character except `/`
    - everything else is literal

    This is reimplemented rather than using `fnmatch.translate` because
    `fnmatch` has no concept of `**`, and rather than using
    `PurePath.match` because recursive `**` only landed in Python 3.13
    and archy targets 3.10+.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # `**` only crosses path separators when it is a *complete*
                # path segment, i.e. bounded by `/` (or string start/end) on
                # both sides -- matching git/pathlib `full_match` semantics.
                # `**/<rest>` matches zero or more leading segments; a bare
                # trailing `**` matches any remainder. A `**` glued to other
                # characters (e.g. `**.py`, `a/**b`) is NOT recursive; it
                # degrades to a single in-segment `*` so it can't leak across
                # `/` and silently over-select nested paths.
                left_ok = i == 0 or pattern[i - 1] == "/"
                if left_ok and i + 2 < len(pattern) and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                if left_ok and i + 2 == len(pattern):
                    out.append(".*")
                    i += 2
                    continue
                out.append("[^/]*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c in ".^$+{}()|\\":
            out.append(re.escape(c))
        else:
            out.append(c)
        i += 1
    return re.compile("".join(out))
