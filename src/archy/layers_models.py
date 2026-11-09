"""The value objects layer governance reads and returns: rules, config, violations, coverage.

archy:owns        ExactPatternHint, ForbidRule, LayerConfig, LayerConfigError,
                  LayerCoverage, LayerSpec, ReachViolation, RequiredRule, SdpConfig,
                  SdpViolation, Violation
archy:mirrored-by ExactPatternHint -> archy.layers, archy.layers_check,
                  ForbidRule -> archy.diff, archy.layers, archy.layers_config,
                  LayerSpec -> archy.layers, archy.layers_config, archy.layers_match,
                  bench.greenfield_eval, RequiredRule -> archy.cli_render_check,
                  archy.diff, archy.layers, archy.layers_check, archy.layers_config
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, computed_field


class LayerSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    patterns: tuple[str, ...]


class ForbidRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_layer: str
    to_layer: str


# The `required:` source that binds DERIVED entrypoints instead of a written
# pattern (#387 item 3). The `@` keeps it disjoint from every dotted-name glob,
# which must start with a package name.
ENTRYPOINTS_SOURCE = "@entrypoints"


class RequiredRule(BaseModel):
    """Every module matching `source` must transitively reach `must_reach`.

    The inverse of a `ForbidRule`, and the two answer different questions. A
    forbid rule catches an edge that should not exist; this catches an edge that
    should exist and does not, which no amount of forbidding can express.

    The motivating failure: a Django-style `commands/` package where each module
    is run standalone (`python -m commands.setup_user`) and every one of them
    needs the SQLAlchemy model registry imported before first mapper
    configuration, or string-based relationship resolution fails at runtime.
    Verified by importing each of the 34 command modules in a fresh subprocess:
    11 imported the registry directly, 21 failed mapper configuration, and 2
    passed WITHOUT a direct import because they happened to reach the registry
    through unrelated application imports. Nothing in a forbid-only config can
    state that requirement.

    Those 2 are why this is defined over reach and not imports. A rule counting
    direct imports would report 23 failures where 21 existed: on the one real
    sample available, direct-import matching carries an 8.7% false-positive rate
    that transitive reach removes entirely.

    `reason` is not decoration. A required-reach failure says "this module does
    not reach that one", which is a fact about the graph and not an explanation;
    without the author's reason the reader cannot tell an intentional constraint
    from an accident, so it is carried through to every output surface.

    Reach is TRANSITIVE and includes the implicit package-`__init__` edges (see
    `graph.package_init_edges`). Both are load-bearing: the idiomatic fix for
    the case above is one import in `commands/__init__.py`, which satisfies all
    34 modules indirectly. A direct-import rule would report all 34 as
    violations *after* a correct fix, which is worse than having no rule.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    must_reach: str
    reason: str = ""
    # Only with `source: "@entrypoints"`: keep the derived entrypoints that
    # match this pattern. Derivation finds every runnable file, bench scripts
    # and guarded test modules included, so a rule over all of them would fail
    # on files that were never meant to carry the obligation. `within` scopes it
    # to the part of the tree that does, while still catching a new script
    # anywhere in that part, which an explicit `source` glob cannot.
    within: str | None = None

    def describe_source(self) -> str:
        """`source` as a reader should see it, with `within` when set."""
        if self.within is None:
            return self.source
        return f"{self.source} within {self.within}"


class ReachViolation(BaseModel):
    """A `required:` rule that is not satisfied, or that cannot fire at all.

    `module` is the offending source module, or None when the rule itself is
    dead (its `source` or `must_reach` pattern matches nothing in the tree). A
    dead rule is reported as a violation rather than skipped because a rule that
    cannot fire is indistinguishable from a rule that passes, which is the exact
    failure `LayerCoverage` exists to prevent for `forbid:` rules. Two shipped
    bench configs were silently dead for weeks (#355).

    `detail` states why in one sentence, so no surface has to reconstruct it
    from the fields: a verdict without a reason is not actionable.
    """

    model_config = ConfigDict(frozen=True)

    rule: RequiredRule
    module: str | None
    detail: str


class SdpConfig(BaseModel):
    """Stable Dependencies Principle: a module should not depend on one
    that is *strictly less stable* (higher Martin's I).

    `tolerance` is the slack: an edge from a module with I=Is to one
    with I=It is flagged only if `It > Is + tolerance`. Default 0.0
    (any strict violation flagged); raise it to ignore borderline
    cases in noisy graphs.

    `mode` controls how violations affect `archy check`'s exit code:
    `error` (default) makes any SDP violation fail the gate, matching
    forbid-rule behavior; `warn` reports violations to stdout but
    leaves the exit code clean so existing layer rules stay the only
    hard gate. Useful for adopting SDP on a codebase that already has
    violations: turn warn on, watch the count, then flip to error
    once the floor is at zero.
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    tolerance: float = 0.0
    mode: str = "error"


class LayerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    layers: tuple[LayerSpec, ...]
    forbid: tuple[ForbidRule, ...]
    # Required-reach rules. Empty by default, so a config that never declared
    # any keeps its old exit codes exactly.
    required: tuple[RequiredRule, ...] = ()
    exclude: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()
    sdp: SdpConfig = SdpConfig()
    # Scan-size ceiling (see graph.DEFAULT_MAX_MODULES). None = unset (callers
    # apply the default); 0 = explicitly disabled; positive = that ceiling. Kept
    # as a raw override here so the policy layer carries no dependency on graph.
    max_modules: int | None = None
    # How many DECLARED layers must actually contain at least one module for the
    # check to pass. None (default) = no gate, which is backward compatible: a
    # config that never asked for this keeps its old exit codes.
    #
    # Exists because forbidding edges between layers says nothing about whether
    # the layers are there. A codebase that collapsed four layers into one file
    # satisfies every `forbid` rule by having no cross-layer edges at all, and
    # the Constraint Decay paper (arxiv:2605.06445) reports that is exactly what
    # agents produce under architectural constraints. Its verifier pairs
    # dependency direction with a presence floor of "at least 3 of 4 layers", so
    # this is that floor, generalised.
    min_layers_present: int | None = None
    # `archy cycles` default for leaving out edges that exist only under
    # `if TYPE_CHECKING:` (#520). None = unset, which means off, so a config that
    # never asked keeps its old exit codes. The CLI flag and the MCP argument
    # override it in either direction. Named after import-linter's key.
    exclude_type_checking_imports: bool | None = None


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: ForbidRule
    source: str
    target: str
    lines: tuple[int, ...]
    # Repo-relative path of `source`'s file, where `lines` are (#522).
    path: str = ""


class SdpViolation(BaseModel):
    """An import edge where the target is less stable than the source.

    `source_instability` and `target_instability` are reported so
    callers can show the gap that triggered the finding.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    source_instability: float
    target_instability: float
    lines: tuple[int, ...]
    # Repo-relative path of `source`'s file, where `lines` are (#522).
    path: str = ""


class ExactPatternHint(BaseModel):
    """A bare-qualname layer pattern whose descendant modules fall outside it.

    `_translate_pattern` matches a pattern with no glob metacharacter EXACTLY,
    so `modules: ["shipping.store"]` covers `shipping.store` and not
    `shipping.store.repository`. import-linter (behind `archy contracts`)
    matches the same string by PACKAGE, so the two tools answer different
    questions about a byte-identical config -- and the layer config is the one
    that silently governs almost nothing.

    This is the single most common reason coverage is degenerate, and it is
    invisible in the verdict: the config looks like it names the right
    packages. Reported as a hint rather than an error because a bare qualname
    is legal and occasionally intended (archy's own `archy.yaml` uses bare
    patterns for flat modules that have no descendants, and correctly produces
    no hint). The trigger is not "the pattern is bare" but "the pattern is bare
    AND at least one descendant of it is unlayered", which is the state that
    cannot be intentional.
    """

    model_config = ConfigDict(frozen=True)

    layer: str
    pattern: str
    # Unlayered modules under `pattern`. Sorted, and carried in full rather
    # than as a count: naming the module is what makes the hint actionable.
    unlayered_descendants: tuple[str, ...]
    # Always `<pattern>.**`, the spelling that adds the descendants. Stored on
    # the hint rather than recomputed, so the renderers quote one string.
    suggestion: str


class LayerCoverage(BaseModel):
    """How much of a codebase the declared layers actually reach.

    Exists because a ruleset that cannot fire is indistinguishable from a clean
    codebase: `check` prints "No layer violations" either way. That is not
    hypothetical. archy's own `archy.yaml` governs 9 of 42 modules and 16 of 117
    internal edges while reporting a clean pass, and a real violation
    (`archy.diff -> archy.layers`) sat on the main branch under it (#362). The
    same failure killed two shipped bench configs whose patterns matched one
    empty `__init__.py` instead of a 338-module package (#355).

    Three numbers, because the loose one flatters the config:

    - `modules_matched`: in at least one layer. The weakest reading.
    - `modules_in_ruled_layer`: in a layer that some `forbid` rule names. A
      layer no rule mentions cannot produce a violation, so a module in one is
      covered on paper only.
    - `edges_governed`: import edges whose BOTH endpoints are layered, and so
      could be subject to a rule. This is the honest metric: a config can put
      90% of modules in layers while ruling almost none of the edges between
      them.
    """

    model_config = ConfigDict(frozen=True)

    modules_total: int
    modules_matched: int
    modules_in_ruled_layer: int
    edges_total: int
    edges_governed: int
    unlayered_modules: tuple[str, ...]
    # (layer name, module count) for every DECLARED layer, in config order. A
    # zero here is a layer that exists on paper only: it can never source or
    # receive a violation, so every rule naming it is dead.
    layer_sizes: tuple[tuple[str, int], ...] = ()
    # Modules the scan found OUTSIDE every root package the config names, e.g.
    # `bench/` scripts and top-level tooling sitting beside the package. Held
    # apart from the ratios on purpose: counting them would make any project
    # with scripts beside its package look uncovered, which is a fact about the
    # scan path and not about the config. Reported so the exclusion is visible.
    modules_outside_declared_roots: int = 0
    # Import edges from a layered internal module to a layered EXTERNAL package
    # (`orm: {modules: ["sqlalchemy.**"]}`). Counted apart from `edges_total` /
    # `edges_governed` on purpose: those are the internal-edge ratio #362 and #355
    # protect, and folding third-party edges in would move it for a reason that
    # says nothing about how much of the user's own code is governed (#527).
    external_edges_governed: int = 0
    # Bare-qualname patterns with unlayered descendants. See `ExactPatternHint`:
    # this names the CAUSE of a degenerate `edge_ratio`, where the ratios only
    # report the symptom. Defaulted so every existing construction site keeps
    # working unchanged.
    exact_pattern_hints: tuple[ExactPatternHint, ...] = ()

    # An empty scope reports 0.0, not 1.0. "0 of 0 modules (100%)" is the exact
    # failure this class exists to prevent, reproduced inside it: a config whose
    # declared roots match nothing in the tree governs nothing, and saying it
    # governs everything is worse than saying nothing at all. Found by pointing
    # archy at a single-module project whose config named four layer packages.
    # `computed_field`, not a bare property: MCP tool returns are serialized with
    # `model_dump()`, which silently drops properties. Without this an agent
    # receiving `passed=false` for a presence shortfall got a coverage object
    # with nothing in it explaining why, which is the "indistinguishable from a
    # bug in archy" state this class exists to prevent, reproduced on the wire.
    @computed_field
    @property
    def module_ratio(self) -> float:
        return self.modules_matched / self.modules_total if self.modules_total else 0.0

    @computed_field
    @property
    def edge_ratio(self) -> float:
        return self.edges_governed / self.edges_total if self.edges_total else 0.0

    @computed_field
    @property
    def governs_no_edges(self) -> bool:
        """No import edge in the tree can be subject to any `forbid` rule.

        True both when internal edges exist and none has two layered endpoints,
        and when there are no internal edges at all. An edge into a layered
        external package counts as governed (`external_edges_governed`): a rule
        naming `sqlalchemy.**` can fire on it. Either way every `forbid`
        rule is unfireable, so a clean verdict carries no information -- which
        is precisely what the verdict must say out loud.

        The threshold is exactly zero, not a percentage. Any other number is a
        policy choice about how much governance is "enough", which this module
        has no basis to pick; zero is the one value where "no violations found"
        is provably vacuous rather than merely thin.

        `computed_field`, not a property: `model_dump()` drops properties, and
        the MCP surface returns this object through `model_dump()`.
        """
        return self.edges_governed == 0 and self.external_edges_governed == 0

    @computed_field
    @property
    def governs_nothing(self) -> bool:
        """No module in the tree falls under any root the config names."""
        return self.modules_total == 0

    @computed_field
    @property
    def empty_layers(self) -> tuple[str, ...]:
        """Declared layers that matched no module at all."""
        return tuple(name for name, size in self.layer_sizes if size == 0)

    @computed_field
    @property
    def layers_present(self) -> int:
        return sum(1 for _, size in self.layer_sizes if size)


class LayerConfigError(Exception):
    """Raised when an archy.yaml file is missing, malformed, or self-inconsistent."""
