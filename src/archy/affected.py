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

archy:owns        Affected, find_affected
archy:mirrored-by Affected -> archy.cli, archy.cli_render, archy.mcp, archy.mcp_runs,
                  find_affected -> archy.cli, archy.mcp_runs
"""

from __future__ import annotations

import re
from pathlib import Path

import networkx as nx
from pydantic import BaseModel, ConfigDict, computed_field

from archy.graph import suggest_modules
from archy.impact import STATIC_ONLY_CAVEAT, _index_by_path

DEFAULT_DEPTH = 5

DELETED_OR_RENAMED_REASON = (
    "deleted or renamed; archy scans the current tree, so its dependents are not selected"
)

_FILTER_MATCHED_NOTHING = (
    "matches no file in the graph "
    "(paths are project-relative; only files inside a package, and test files, are scanned)"
)

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
    deleted: tuple[str, ...]
    impacted_modules: tuple[str, ...]
    impacted_tests: tuple[str, ...]
    depth: int
    test_filter: str | None = None

    # `computed_field` for the same reason as `Impact.empty_caveat`: a plain
    # property is dropped by `model_dump()` and so never reaches the MCP wire.
    @computed_field
    @property
    def empty_caveat(self) -> str | None:
        if self.changed and not self.impacted_tests:
            return STATIC_ONLY_CAVEAT
        return None

    @computed_field
    @property
    def deleted_reason(self) -> str | None:
        if self.deleted:
            return DELETED_OR_RENAMED_REASON
        return None


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
    "any path segment named 'tests'" are used. Raises `ValueError` when
    `test_filter` matches no file in the graph, because then every test
    would be reported as a non-test (see `_refuse_unmatched_filter`).

    `impacted_tests` and `impacted_modules` are disjoint by construction:
    every impacted module is in exactly one of them. The changed set
    itself is excluded from both (consistent with `find_impact`).
    """
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")

    path_to_qualname = _index_by_path(graph)

    changed: set[str] = set()
    unresolved: list[str] = []
    deleted: list[str] = []
    for f in files:
        resolved = f.resolve()
        qualname = path_to_qualname.get(resolved)
        if qualname is None:
            if _is_deleted_or_renamed(resolved, project_root):
                deleted.append(str(f))
            else:
                unresolved.append(str(f))
        else:
            changed.add(qualname)

    impacted = _bounded_ancestors(graph, changed, depth=depth)
    impacted -= changed
    impacted = {q for q in impacted if not graph.nodes[q].get("external")}

    is_test = _test_classifier(project_root, test_filter)
    if test_filter is not None:
        _refuse_unmatched_filter(graph, test_filter, project_root, is_test)
    tests: set[str] = set()
    modules: set[str] = set()
    for q in impacted:
        node_path = graph.nodes[q].get("path")
        if node_path and is_test(Path(node_path)):
            tests.add(q)
        else:
            modules.add(q)

    tests, modules = _expand_impacted_conftests(graph, tests, modules, is_test)

    return Affected(
        changed=tuple(sorted(changed)),
        unresolved=tuple(sorted(unresolved)),
        deleted=tuple(sorted(deleted)),
        impacted_modules=tuple(sorted(modules)),
        impacted_tests=tuple(sorted(tests)),
        depth=depth,
        test_filter=test_filter,
    )


def _refuse_unmatched_filter(
    graph: nx.DiGraph, test_filter: str, project_root: Path, is_test
) -> None:
    """Raise `ValueError` when no file in `graph` matches `test_filter`.

    Every file the filter misses counts as a non-test, so a typo moved each test
    into `impacted_modules` and the empty `impacted_tests` then read as "no
    static edge reaches this" (#660). The check runs over the whole graph, not
    the impacted set: a filter that matches a test but no impacted one is a
    true empty answer. Refusing is a design choice following AGENTS.md, "A name
    that matched nothing is a refusal"; no lint enforces that rule, and the
    stdout of `--quiet` is the path list xargs reads, so a reason cannot go there.
    Near misses are directories, written as filters, because that is the form
    the caller has to type next.
    """
    paths = [Path(raw) for _, data in graph.nodes(data=True) if (raw := data.get("path"))]
    if any(is_test(path) for path in paths):
        return
    root = project_root.resolve()
    directories = {
        parent.as_posix()
        for path in paths
        if (rel := _rel_to_root(path, root)) is not None
        for parent in rel.parents
        if parent != Path(".")
    }
    literal = re.split(r"[*?]", test_filter, maxsplit=1)[0].rstrip("/")
    near = suggest_modules(directories, literal)
    hint = f"; did you mean {', '.join(f'{d}/**' for d in near)}?" if near else ""
    raise ValueError(f"test filter {test_filter!r} {_FILTER_MATCHED_NOTHING}{hint}")


def _expand_impacted_conftests(
    graph: nx.DiGraph,
    tests: set[str],
    modules: set[str],
    is_test,
) -> tuple[set[str], set[str]]:
    """Replace impacted ``conftest.py`` nodes with the tests they serve.

    A ``conftest.py`` is classified as a test only because of its path (under
    ``tests/`` or matching ``--filter``), or it lands in ``impacted_modules``
    when it sits outside those conventions (e.g. a repo-root conftest). Either
    way it is not itself a runnable test file: expand it to every
    test-classified graph node whose path is in or below the conftest's
    directory, then drop the conftest.
    """
    conftest_qs = {
        q
        for q in tests | modules
        if (path := graph.nodes[q].get("path")) and Path(path).name == "conftest.py"
    }
    if not conftest_qs:
        return tests, modules

    tests = set(tests)
    modules = set(modules)
    for cq in conftest_qs:
        cpath = Path(graph.nodes[cq]["path"]).resolve()
        cdir = cpath.parent
        tests.discard(cq)
        modules.discard(cq)
        for q, data in graph.nodes(data=True):
            if data.get("external"):
                continue
            npath = data.get("path")
            if not npath:
                continue
            np = Path(npath).resolve()
            if np.name in {"conftest.py", "__init__.py"}:
                continue
            if not is_test(np):
                continue
            try:
                np.relative_to(cdir)
            except ValueError:
                continue
            tests.add(q)
            modules.discard(q)
    return tests, modules


def _is_deleted_or_renamed(path: Path, project_root: Path) -> bool:
    """A project `.py` the scan could not have read.

    `resolve` follows parents that still exist when the file itself is gone.
    A file that is present was visible to the scan, so it stays unresolved
    even when it is not a module.
    """
    if path.suffix != ".py":
        return False
    resolved = path.resolve()
    if resolved.exists():
        return False
    return _rel_to_root(resolved, project_root.resolve()) is not None


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
            rel = _rel_to_root(path, root)
            if rel is None:
                return False
            return bool(pattern.fullmatch(rel.as_posix()))

        return _user

    auto_patterns = [_compile_glob(g) for g in _AUTO_TEST_GLOBS]

    def _auto(path: Path) -> bool:
        rel = _rel_to_root(path, root)
        if rel is None:
            return False
        if any(p.fullmatch(rel.as_posix()) for p in auto_patterns):
            return True
        return _AUTO_TEST_DIR in rel.parts

    return _auto


def _rel_to_root(path: Path, root: Path) -> Path | None:
    """Resolve `path` relative to `root`, or None if it falls outside.

    Both classifier closures need the project-relative path; centralizing the
    resolve + `relative_to` guard keeps the out-of-tree handling identical.
    """
    try:
        return path.resolve().relative_to(root)
    except ValueError:
        return None


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
        else:
            # Every non-glob character is a literal. Escape via `re.escape`
            # (a no-op for ordinary chars) so ALL regex metachars are covered
            # -- a hand-maintained allowlist silently omitted `[`/`]`, which
            # made a user `--filter` containing them raise an uncaught
            # `re.error` instead of matching them literally.
            out.append(re.escape(c))
        i += 1
    return re.compile("".join(out))
