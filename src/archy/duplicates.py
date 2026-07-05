"""Duplicate-function detection via AST-shape hashing (advisory, not a score axis).

Clusters functions whose *normalized* body shape is identical: identifiers and
literals are folded to placeholders before hashing (see
`archy.complexity._analyze_body`), so two functions that differ only by names or
literal values land in the same cluster. A minimum `size` (normalized AST-node
count) threshold skips trivial getters and stubs, whose shapes collide but are
not duplication in the refactor-this sense.

This is a same-shape cluster *surfacer*, not a precision oracle. Output is split
into two tiers by a semantic de-noiser (`classify_variants`): a primary "likely
duplicate" tier (investigate these) and a demoted "variant" tier of
likely-intentional clusters (same-class siblings, `@overload` stubs, trivial
boilerplate, and path-isolated copies - clusters wholly in test suites or
vendoring/isolation dirs, issue #247). Nothing is hidden; the variant tier is
down-ranked, not dropped.
Within the primary tier, `exact` marks byte-identical (Type-1) clusters: the
concrete (un-normalized) body hashes match, so these are real copy-paste, the
highest-confidence slice (~63% precision on a 12-repo re-validation, ~74% on
non-test source, vs ~50% for the tier overall; §12d).

Refactorability is a *semantic* judgment that syntax cannot fully make. The clone
literature is blunt about this: Kapser & Godfrey found up to 71% of real clones
are benign (parameterized "templating" siblings, boilerplate, per-backend
variants), so ~50% refactorability precision is the expected ceiling for any
similarity-only detector, and no production tool (SourcererCC, NiCad, Deckard)
solves the benign-vs-refactorable split - they measure Type-1/2/3 similarity, a
different question. The one non-ML signal that measurably breaks the ceiling is
change-history co-change (a cluster whose members co-change consistently is
refactorable; one that never co-changes is benign, ~94% precision in the
literature). `demote_independent` consumes it (issue #242, via the
change-coupling machinery from #131): a primary cluster whose copies live in
actively-maintained files that never co-change is demoted to the `variant` tier
(reason `"independent"`), lifting primary precision above the syntactic ceiling.
The calling agent remains the final semantic judge. See
`docs/research/RESEARCH_METRICS.md` §12c/§12f.

Never folded into `archy score`. Even at ~50% it beats dead-code detection, which
the same FP discipline rejected outright at ~100% FP.

Unlike module-grained diagnostics (`hotspots`, `dsm`) that read the shared
`nx.DiGraph`, this one is function-grained: the graph keeps only per-module CC
aggregates, so `compute_duplicates` consumes the `(modules, parse_results)` pair
from `archy.graph.parse_project` instead.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archy.complexity import FunctionFeatures, extract_function_features
from archy.graph import Module
from archy.parser import ParseResult

# 30 is the calibrated shipping default: the FP spot-check (RESEARCH_METRICS.md
# section 12) showed the trivial-boilerplate false positives (property shims,
# one-line delegations) cluster below ~30 normalized nodes, while dropping the
# floor lower floods the list and raising it much higher starts losing genuine
# small duplicates. The floor removes only the trivial tail; the two-tier
# de-noiser (classify_variants) handles the structural intentional-clone classes,
# and co-change history (#131) is the semantic precision layer beyond that.
DEFAULT_MIN_SIZE = 30
DEFAULT_MIN_MEMBERS = 2

# Change-coupling demotion thresholds (issue #242), calibrated on the bench.
# A primary cluster whose copies live in >=2 actively-maintained files that
# never co-change is deliberately independent (per-backend siblings, symmetric
# methods) -> demote. `min_evidence` is the guard against demoting a real but
# rarely-touched duplicate: absence of co-change is only meaningful when both
# files change often enough to have HAD the chance to move together.
DEFAULT_COCHANGE_MIN_SUPPORT = 3
DEFAULT_COCHANGE_MIN_CONFIDENCE = 0.3
DEFAULT_COCHANGE_MIN_EVIDENCE = 5


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

    `category` splits the output into two tiers (issue #242): `"duplicate"` is a
    likely-real duplicate; `"variant"` is a likely-intentional cluster demoted by
    the semantic de-noiser, with `variant_reason` naming which signal fired
    (`overload` / `property` / `vendored` / `test` / `trivial` / `same_class` /
    `independent`). `vendored` / `test` are pure-path signals (issue #247): every
    member sits in a vendoring/isolation dir or a test suite, so the shared body is
    deliberate isolation or frozen test scaffolding, not refactorable copy-paste.
    `independent` is the git co-change signal (issue #242, set by
    `demote_independent`): the copies live in actively-maintained files that never
    change together, i.e. deliberately parallel implementations. `same_class`
    is the free structural signal (all members are methods of one class); it is set
    by `compute_duplicates`, while `variant_reason` / `category` are finalized by
    `classify_variants` (source + paths) and then `demote_independent` (git).
    """

    model_config = ConfigDict(frozen=True)

    shape_hash: str
    size: int
    member_count: int
    redundancy: int
    members: tuple[DuplicateMember, ...]
    same_class: bool = False
    variant_reason: str | None = None
    category: str = "duplicate"
    exact: bool = False


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
                same_class=_same_class(ordered),
            )
        )
    groups.sort(key=lambda g: (-g.redundancy, -g.size, -g.member_count, g.shape_hash))
    return groups


def _same_class(members: tuple[DuplicateMember, ...]) -> bool:
    """True when every member is a method of one and the same class.

    Detected from the dotted `qualified_name`: a method's parent is everything
    before the last segment (`Foo.bar` -> `Foo`), so all members must share one
    `(module, parent)` and have a non-empty parent. Module-level functions (no
    dot) have no parent class and never count, so genuinely duplicated free
    functions in one module stay in the primary tier.
    """
    scopes = set()
    for m in members:
        if "." not in m.qualified_name:
            return False
        scopes.add((m.module, m.qualified_name.rsplit(".", 1)[0]))
    return len(scopes) == 1


def classify_variants(groups: list[DuplicateGroup]) -> list[DuplicateGroup]:
    """Finalize each group's `category` / `variant_reason` from member source.

    Demotes likely-intentional clusters (issue #242) to the `"variant"` tier:
    `@overload` stubs, trivial `@property` shims, pure-boilerplate bodies, and
    same-class sibling methods. Reads each member file at most once; a member
    whose file cannot be read is treated as featureless (its signals abstain).
    """
    features_by_path: dict[str, dict[int, FunctionFeatures]] = {}

    def features_for(path: str) -> dict[int, FunctionFeatures]:
        cached = features_by_path.get(path)
        if cached is None:
            try:
                cached = extract_function_features(Path(path).read_bytes())
            except OSError:
                cached = {}
            features_by_path[path] = cached
        return cached

    classified: list[DuplicateGroup] = []
    for group in groups:
        feats = [features_for(m.path).get(m.line) for m in group.members]
        reason = _variant_reason(group, feats)
        classified.append(
            group.model_copy(
                update={
                    "variant_reason": reason,
                    "category": "variant" if reason is not None else "duplicate",
                    "exact": _is_exact(feats),
                }
            )
        )
    return classified


def demote_independent(
    groups: list[DuplicateGroup],
    *,
    counts: dict[str, int],
    pair_support: dict[tuple[str, str], int],
    min_support: int = DEFAULT_COCHANGE_MIN_SUPPORT,
    min_confidence: float = DEFAULT_COCHANGE_MIN_CONFIDENCE,
    min_evidence: int = DEFAULT_COCHANGE_MIN_EVIDENCE,
) -> list[DuplicateGroup]:
    """Demote primary clusters whose copies never co-change to the variant tier (#242).

    The one non-ML signal that breaks the ~50% syntactic precision ceiling
    (RESEARCH_METRICS §12c): a duplicate cluster whose member modules co-change
    in git is real shared logic drifting together (refactor it); one whose
    files are actively maintained yet never move together is deliberately
    independent (per-backend implementations, symmetric methods) - demote it,
    reason `"independent"`.

    `counts` / `pair_support` come from `coupling.git_cochange` (per-file focused
    commit counts and per-file-pair co-change support, keyed by sorted resolved
    path); the dicts are passed raw rather than the `CoChangeData` type to keep
    this module free of a `coupling` import (coupling already imports
    `is_test_path` from here). Only re-checks clusters still in the primary tier;
    an already-demoted cluster keeps its reason. Uninformative cases abstain:
    a same-file cluster (co-change can't distinguish its members) and a cluster
    any of whose files is below `min_evidence` commits (too little history to
    call it independent rather than merely stable).
    """
    out: list[DuplicateGroup] = []
    for group in groups:
        if group.category != "duplicate":
            out.append(group)
            continue
        distinct = sorted({m.path for m in group.members})
        if len(distinct) < 2 or any(counts.get(p, 0) < min_evidence for p in distinct):
            out.append(group)
            continue
        # `distinct` is sorted, so `combinations` yields (a, b) with a < b,
        # matching `pair_support`'s sorted keys. confidence = support / the rarer
        # file's commit count (same metric as `coupling`, inlined to avoid the
        # import cycle). One co-changing pair is enough to call the copies coupled.
        co_changes = any(
            (support := pair_support.get((a, b), 0)) >= min_support
            and (denom := min(counts.get(a, 0), counts.get(b, 0))) > 0
            and support / denom >= min_confidence
            for a, b in combinations(distinct, 2)
        )
        if co_changes:
            out.append(group)
        else:
            out.append(
                group.model_copy(update={"category": "variant", "variant_reason": "independent"})
            )
    return out


def _is_exact(feats: list[FunctionFeatures | None]) -> bool:
    """True when every member's body is byte-identical (a Type-1 clone).

    Requires every member to be readable and to share one non-empty
    `concrete_hash`. These are the highest-confidence duplicates: real
    copy-paste, not parameterized siblings that merely share a normalized shape.
    """
    if not feats or any(f is None for f in feats):
        return False
    hashes = {f.concrete_hash for f in feats if f is not None}
    return len(hashes) == 1 and "" not in hashes


# Path segments that mark a file as vendored / dependency-isolated: code copied
# in on purpose (pip's `_vendor`, `third_party`) or re-implemented to avoid an
# import (ansible `module_utils` ships to remote targets, so it must not import
# controller-side packages). A byte-identical body across two such files is
# deliberate isolation, not refactorable duplication (issue #247, class 1).
_VENDOR_SEGMENTS = frozenset(
    {"_vendor", "vendored", "third_party", "site-packages", "module_utils"}
)
# Conventional pytest/unittest suite-directory names. Per-file markers
# (conftest.py, test_*.py, *_test.py) are matched separately in is_test_path,
# so this set only needs the directory-level convention, not every test file.
_TEST_DIR_SEGMENTS = frozenset({"tests", "test"})


def _is_vendored_path(path: str) -> bool:
    """True when any path segment marks the file as vendored / isolation code."""
    return any(seg in _VENDOR_SEGMENTS for seg in Path(path).parts)


def is_test_path(path: str) -> bool:
    """True when the file is test code, by basename convention or a test dir.

    Public because change-coupling (#131) reuses it to default `archy coupling`
    to source-only (test co-change is mostly a test tracking the module it
    covers, which buries the source-to-source hidden coupling that matters).

    Parallel/legacy test suites produce byte-identical bodies by design (numpy's
    frozen `test_random.py` vs `test_randomstate.py`, per-module compliance
    scaffolding), so a cluster wholly inside test code is scaffolding, not a
    refactor target (issue #247, class 3).
    """
    p = Path(path)
    name = p.name
    if name == "conftest.py" or name.endswith("_test.py") or name.startswith("test_"):
        return True
    return any(seg in _TEST_DIR_SEGMENTS for seg in p.parts)


def _has_decorator(feat: FunctionFeatures | None, suffix: str) -> bool:
    return feat is not None and any(d.rsplit(".", 1)[-1] == suffix for d in feat.decorators)


def _variant_reason(group: DuplicateGroup, feats: list[FunctionFeatures | None]) -> str | None:
    """Which de-noise signal (if any) makes this cluster a likely-intentional variant.

    Precedence: overload -> property -> vendored -> test -> trivial -> same_class.
    The decorator and `is_trivial` signals read member source; `vendored` / `test`
    are pure-path (issue #247) and so fire even when a member file is unreadable.
    Every signal requires *all* members to qualify: a cross-tier cluster (one test
    or vendored member sharing a body with real source) stays primary, since that
    is a genuine "your source duplicates isolated code" finding worth surfacing.
    The decorator/path checks require *every* member to carry the marker (an API
    surface is expanded uniformly, a copy-paste usually is not).
    """
    present = [f for f in feats if f is not None]
    if present and all(_has_decorator(f, "overload") for f in feats):
        return "overload"
    if (
        present
        and all(_has_decorator(f, "property") for f in feats)
        and all(f.is_trivial for f in present)
    ):
        return "property"
    if all(_is_vendored_path(m.path) for m in group.members):
        return "vendored"
    if all(is_test_path(m.path) for m in group.members):
        return "test"
    if present and all(f.is_trivial for f in present):
        return "trivial"
    if group.same_class:
        return "same_class"
    return None
