"""Duplicate-function detection via AST-shape hashing (advisory, not a score axis).

Clusters functions whose *normalized* body shape is identical: identifiers and
literals are folded to placeholders before hashing (see
`archy.complexity._analyze_body`), so two functions that differ only by names or
literal values land in the same cluster. A minimum `size` (normalized AST-node
count) threshold skips trivial getters and stubs, whose shapes collide but are
not duplication in the refactor-this sense.

This is deliberately advisory, and empirically high-recall / moderate-precision.
A 15x3 false-positive spot-check on fastapi / pytest / django (RESEARCH_METRICS.md
section 12) put precision at ~42% at the shipping default: shape-hashing clusters
code that is structurally identical yet not real duplication (intentional
public-API signature expansion; paired/symmetric methods differing only by the
one constant that is the point). A node-count floor (`min_size`) removes the
trivial-boilerplate tail but cannot separate those structural false positives, so
a group means "investigate," not "provably identical." A semantic de-noiser
(suppress same-class siblings differing only by a literal constant) is the real
precision fix and is tracked as a follow-up. This is never folded into
`archy score`; even so it beats dead-code detection, which the same FP discipline
rejected outright at ~100% FP.

Unlike module-grained diagnostics (`hotspots`, `dsm`) that read the shared
`nx.DiGraph`, this one is function-grained: the graph keeps only per-module CC
aggregates, so `compute_duplicates` consumes the `(modules, parse_results)` pair
from `archy.graph.parse_project` instead.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from archy.graph import Module
from archy.parser import ParseResult

# 30 is the calibrated shipping default: the FP spot-check (RESEARCH_METRICS.md
# section 12) showed the trivial-boilerplate false positives (property shims,
# one-line delegations) cluster below ~30 normalized nodes, while dropping the
# floor lower floods the list and raising it much higher starts losing genuine
# small duplicates. It does not remove the structural FP classes (that needs the
# semantic de-noiser follow-up), only the trivial tail.
DEFAULT_MIN_SIZE = 30
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

    Raises `ValueError` for `min_members < 2` (a group of one is not a
    duplicate); the invariant lives here so every caller inherits it, not just
    the CLI.
    """
    if min_members < 2:
        raise ValueError(f"min_members must be >= 2; got {min_members}")
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
