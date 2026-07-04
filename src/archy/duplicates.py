"""Duplicate-function detection via AST-shape hashing (advisory, not a score axis).

Clusters functions whose *normalized* body shape is identical: identifiers and
literals are folded to placeholders before hashing (see
`archy.complexity._analyze_body`), so two functions that differ only by names or
literal values land in the same cluster. A minimum `size` (normalized AST-node
count) threshold skips trivial getters and stubs, whose shapes collide but are
not duplication in the refactor-this sense.

This is deliberately advisory. Shape-hashing can cluster code that is
structurally identical yet semantically different (two validators with the same
control flow), so a group means "investigate," not "provably identical." It is
never folded into `archy score`; the empirical FP rate is the whole reason
duplicate detection ships where dead-code detection did not (see
`docs/research/RESEARCH_METRICS.md` section 12).

Unlike module-grained diagnostics (`hotspots`, `dsm`) that read the shared
`nx.DiGraph`, this one is function-grained: the graph keeps only per-module CC
aggregates, so `compute_duplicates` consumes the `(modules, parse_results)` pair
from `archy.graph.parse_project` instead.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from archy.graph import Module
from archy.parser import ParseResult

DEFAULT_MIN_SIZE = 20
DEFAULT_MIN_MEMBERS = 2


class DuplicateMember(BaseModel):
    """One function participating in a duplicate cluster.

    `module` is the module qualname (the `parse_results` key); `qualified_name`
    is the in-module dotted name (e.g. `Foo.bar`); `path` + `line` give the
    file:line citation.
    """

    model_config = ConfigDict(frozen=True)

    module: str
    qualified_name: str
    path: str
    line: int


class DuplicateGroup(BaseModel):
    """A cluster of functions sharing one normalized body shape.

    `size` is the shared normalized AST-node count; `redundancy`
    (`size * (member_count - 1)`) approximates how many normalized nodes could
    be removed by deduplicating down to a single definition, and is the primary
    ranking key.
    """

    model_config = ConfigDict(frozen=True)

    shape_hash: str
    size: int
    member_count: int
    redundancy: int
    members: tuple[DuplicateMember, ...]


def compute_duplicates(
    modules: list[Module],
    parse_results: dict[str, ParseResult],
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    min_members: int = DEFAULT_MIN_MEMBERS,
) -> list[DuplicateGroup]:
    """Cluster functions by normalized body shape across all modules.

    Buckets every function whose `size >= min_size` by its `shape_hash`, keeps
    buckets with at least `min_members` members, and returns them ranked by
    `(-redundancy, -size, -member_count, shape_hash)`. Clustering is by
    `shape_hash` alone; the 128-bit blake2b digest makes accidental collision
    negligible, so a composite key is unnecessary.
    """
    path_by_qual = {m.qualname: str(m.path) for m in modules}
    buckets: dict[str, list[DuplicateMember]] = {}
    size_by_hash: dict[str, int] = {}
    for qual, result in parse_results.items():
        path = path_by_qual.get(qual)
        if path is None:
            continue
        for fn in result.functions:
            if not fn.shape_hash or fn.size < min_size:
                continue
            buckets.setdefault(fn.shape_hash, []).append(
                DuplicateMember(
                    module=qual,
                    qualified_name=fn.qualified_name,
                    path=path,
                    line=fn.line,
                )
            )
            size_by_hash[fn.shape_hash] = fn.size

    groups: list[DuplicateGroup] = []
    for shape_hash, members in buckets.items():
        if len(members) < min_members:
            continue
        ordered = tuple(sorted(members, key=lambda m: (m.module, m.line, m.qualified_name)))
        size = size_by_hash[shape_hash]
        groups.append(
            DuplicateGroup(
                shape_hash=shape_hash,
                size=size,
                member_count=len(ordered),
                redundancy=size * (len(ordered) - 1),
                members=ordered,
            )
        )
    groups.sort(key=lambda g: (-g.redundancy, -g.size, -g.member_count, g.shape_hash))
    return groups
