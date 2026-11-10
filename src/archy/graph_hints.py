"""Did-you-mean hints and name resolution for a module or file the caller mistyped.

archy:owns        hint_unresolved, resolve_modules, suggest_files, suggest_modules
archy:mirrored-by hint_unresolved -> archy.graph, archy.mcp_runs_graph, archy.simulate,
                  resolve_modules -> archy.graph, archy.mcp_runs_graph, archy.simulate,
                  suggest_files -> archy.graph, archy.impact,
                  suggest_modules -> archy.affected, archy.cli_graph,
                  archy.conventions, archy.graph, archy.mcp_runs_graph
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

import networkx as nx
from pydantic import BaseModel, ConfigDict

from archy.graph_models import UnresolvedHint

# Lowest difflib ratio at which `suggest_modules` calls a name a misspelling. A
# one-letter slip in a short dotted name (`archy.scor`) scores ~0.95; two
# unrelated modules sharing a package prefix (`archy.cli`, `archy.dsm`) score
# ~0.67, and must not be offered as the answer to each other.
_SUGGEST_MIN_SIMILARITY = 0.8


# How far below the closest misspelling a candidate may fall and still be offered.
_SUGGEST_MARGIN = 0.1


def suggest_modules(known: Iterable[str], ref: str, limit: int = 5) -> list[str]:
    """Module names `ref` was probably meant to be, best first; `[]` when nothing is close.

    For a lookup that found nothing, to name the near misses in the refusal
    rather than leave the caller to guess again. It suggests and never accepts:
    a bare `cli` would match `archy.cli` and every `tests.*cli*` module, so
    resolving it would pick one of them for the caller.

    Names that contain `ref` come first (`cli` finds `archy.cli`), then names
    spelled like it (`archy.scoer` finds `archy.score`, similarity ratio 20/22).
    Of the misspellings only those within `_SUGGEST_MARGIN` of the closest are
    kept: a clear winner is not diluted by siblings that merely share its
    prefix (`tests.test_dsmm` finds `tests.test_dsm`, not also `tests.test_mcp`).
    Each tier is ordered by name, the second by similarity then name, so the
    answer does not depend on the order `known` arrives in.
    """
    if not ref:
        return []
    names = sorted(set(known))
    contained = [n for n in names if ref in n]
    taken = set(contained)
    scored = []
    for n in names:
        if n in taken:
            continue
        ratio = difflib.SequenceMatcher(None, ref, n).ratio()
        if ratio >= _SUGGEST_MIN_SIMILARITY:
            scored.append((-ratio, n))
    scored.sort()
    closest = -scored[0][0] if scored else 0.0
    kept = [n for r, n in scored if -r >= closest - _SUGGEST_MARGIN]
    return (contained + kept)[:limit]


def hint_unresolved(
    graph: nx.DiGraph, refs: Iterable[str], *, base: Path | None = None
) -> tuple[UnresolvedHint, ...]:
    """A hint for each unresolved reference that has a close match, in input order.

    A `.py` reference is a file path and is answered with file paths
    (`suggest_files`); anything else is a module name and is answered with module
    names. A reference with no close match gets no entry rather than an empty one.
    `base` is what a relative path was meant relative to; see `suggest_files`.
    """
    ordered = list(dict.fromkeys(refs))
    by_file = {h.ref: h for h in suggest_files(graph, ordered, base=base)}
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]
    hints = (_hint_for(ref, by_file, internal) for ref in ordered)
    return tuple(h for h in hints if h is not None)


def _hint_for(
    ref: str, by_file: dict[str, UnresolvedHint], internal: list[str]
) -> UnresolvedHint | None:
    if ref in by_file:
        return by_file[ref]
    if ref.endswith(".py"):
        return None
    found = suggest_modules(internal, ref)
    return UnresolvedHint(ref=ref, did_you_mean=tuple(found)) if found else None


def suggest_files(
    graph: nx.DiGraph, refs: Iterable[str], *, base: Path | None = None
) -> tuple[UnresolvedHint, ...]:
    """Name the module file each unresolved `.py` path was probably meant to be.

    The similarity floor in `suggest_modules` is tuned for dotted module names.
    On whole paths the shared `src/archy/` prefix lifts every sibling over it
    (`src/archy/clx.py` would match `dsm.py` at 0.81), so the path is turned into
    the module name it would have and compared as one. The layout prefix (`src/`)
    is learned from the graph's own modules rather than assumed. Suggestions are
    shown in the form the caller typed: absolute when the path was absolute,
    otherwise anchored the same way. A relative path is read against `base` when
    one is given, else the working directory. No hint without a root to anchor
    on, and none for a reference that is not a `.py` path.
    """
    root_raw = graph.graph.get("root")
    refs = list(refs)
    if not root_raw or not refs:
        return ()
    index = _file_index(graph, Path(root_raw).resolve())
    hints = (_file_hint(raw_ref, base, index) for raw_ref in refs)
    return tuple(h for h in hints if h is not None)


class _FileIndex(BaseModel):
    """The project's module files, as `suggest_files` needs to compare a typed path."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    root: Path
    by_qualname: dict[str, PurePosixPath]
    longest_first: tuple[tuple[str, ...], ...]


def _file_index(graph: nx.DiGraph, root: Path) -> _FileIndex:
    by_qualname: dict[str, PurePosixPath] = {}
    prefixes: set[tuple[str, ...]] = {()}
    for qualname, data in graph.nodes(data=True):
        raw = data.get("path")
        if data.get("external") or not raw:
            continue
        try:
            rel = PurePosixPath(Path(raw).resolve().relative_to(root).as_posix())
        except ValueError:
            continue
        by_qualname[qualname] = rel
        parts = _module_parts(rel)
        names = tuple(qualname.split("."))
        if parts[len(parts) - len(names) :] == names:
            prefixes.add(parts[: len(parts) - len(names)])
    longest_first = tuple(sorted(prefixes, key=lambda prefix: len(prefix), reverse=True))
    return _FileIndex(root=root, by_qualname=by_qualname, longest_first=longest_first)


def _file_hint(raw_ref: str, base: Path | None, index: _FileIndex) -> UnresolvedHint | None:
    typed = Path(raw_ref)
    if typed.suffix != ".py":
        return None
    anchored = typed if base is None or typed.is_absolute() else base / typed
    try:
        rel = PurePosixPath(anchored.resolve().relative_to(index.root).as_posix())
    except ValueError:
        return None
    parts = _module_parts(rel)
    stripped = next((parts[len(p) :] for p in index.longest_first if parts[: len(p)] == p), parts)
    found = suggest_modules(index.by_qualname, ".".join(stripped))
    if not found:
        return None
    shown = [_in_callers_form(typed, rel, index.by_qualname[q]) for q in found]
    return UnresolvedHint(ref=raw_ref, did_you_mean=tuple(shown))


def _module_parts(rel: PurePosixPath) -> tuple[str, ...]:
    """`pkg/mod.py` -> `("pkg", "mod")`; `pkg/__init__.py` -> `("pkg",)`."""
    parts = rel.with_suffix("").parts
    return parts[:-1] if parts[-1] == "__init__" else parts


def _in_callers_form(typed: Path, typed_rel: PurePosixPath, candidate_rel: PurePosixPath) -> str:
    """`candidate_rel` anchored the way `typed` was: same leading directories."""
    lead = typed.parts[: max(0, len(typed.parts) - len(typed_rel.parts))]
    return str(Path(*lead, *candidate_rel.parts))


def resolve_modules(
    graph: nx.DiGraph,
    refs: Iterable[str],
    *,
    project_root: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve qualname-or-path strings to internal qualnames in `graph`.

    Each `refs` entry is treated as a qualname if it matches an internal node
    directly; otherwise as a filesystem path (relative paths are resolved
    against `project_root`, which defaults to `graph.graph["root"]`). Returns
    `(resolved, unresolved)` where `resolved` preserves first-seen order
    deduplicated and `unresolved` lists the original strings that matched
    nothing.
    """
    internal_nodes = {n for n, d in graph.nodes(data=True) if not d.get("external")}
    path_index = _path_index(graph, internal_nodes)
    base = project_root or (Path(graph.graph["root"]) if graph.graph.get("root") else Path.cwd())

    resolved: list[str] = []
    seen: set[str] = set()
    unresolved: list[str] = []
    for ref in refs:
        qualname = _resolve_one(ref, internal_nodes, path_index, base)
        if qualname is None:
            unresolved.append(ref)
        elif qualname not in seen:
            resolved.append(qualname)
            seen.add(qualname)
    return resolved, unresolved


def _path_index(graph: nx.DiGraph, internal_nodes: set[str]) -> dict[Path, str]:
    index: dict[Path, str] = {}
    for n in internal_nodes:
        raw = graph.nodes[n].get("path")
        if raw:
            index[Path(raw).resolve()] = n
    return index


def _resolve_one(
    ref: str, internal_nodes: set[str], path_index: dict[Path, str], base: Path
) -> str | None:
    """A qualname if `ref` is one, else the qualname of the module at path `ref`."""
    if ref in internal_nodes:
        return ref
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = base / candidate
    return path_index.get(candidate.resolve())
