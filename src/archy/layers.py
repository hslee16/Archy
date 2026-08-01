"""Layer-rule governance over an import graph.

A YAML config declares named layers (groups of modules matched by dotted-name
globs) and forbidden inter-layer edges. `find_violations` walks the import
graph and returns every edge that crosses a forbidden boundary.
"""

from __future__ import annotations

import keyword
import re
from pathlib import Path

import networkx as nx
import yaml
from pydantic import BaseModel, ConfigDict, computed_field


class LayerSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    patterns: tuple[str, ...]


class ForbidRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_layer: str
    to_layer: str


class RequiredRule(BaseModel):
    """ "Every module matching `source` must transitively reach `must_reach`."

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


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: ForbidRule
    source: str
    target: str
    lines: tuple[int, ...]


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


def governed_roots(config: LayerConfig) -> frozenset[str]:
    """The top-level packages the config's LAYER PATTERNS talk about.

    A pattern is a dotted-name glob rooted at a real package (`_validate_layer_pattern`
    enforces that), so its first segment names the namespace the author intended
    to govern.

    NOT `LayerConfig.roots`, despite the name proximity. That field declares
    extra PEP 420 scan roots so the graph builder can find namespace packages at
    all; this function asks which namespaces the rules claim authority over.
    Neither reads the other.
    """
    return frozenset(pattern.split(".")[0] for layer in config.layers for pattern in layer.patterns)


def compute_coverage(graph: nx.DiGraph, config: LayerConfig) -> LayerCoverage:
    """Measure the reach of `config`'s layers over `graph`.

    Scoped to the root packages the config names, NOT to everything the scan
    happened to reach. `archy check .` on this repository walks `bench/` too,
    and counting those scripts would report 7% coverage for a config that never
    claimed to govern them. The interpretable question is "of the code you said
    you were governing, how much do the rules reach", and modules outside those
    roots are counted separately rather than silently dropped.

    External nodes are excluded: they are not the user's code and no layer rule
    is expected to name them.
    """
    ruled_layers = {rule.from_layer for rule in config.forbid} | {
        rule.to_layer for rule in config.forbid
    }
    roots = governed_roots(config)
    layer_of: dict[str, str | None] = {}
    outside = 0
    for node, data in graph.nodes(data=True):
        if data.get("external"):
            continue
        if roots and node.split(".")[0] not in roots:
            outside += 1
            continue
        layer_of[node] = match_layer(node, config.layers)

    sizes = {layer.name: 0 for layer in config.layers}
    for layer in layer_of.values():
        if layer is not None:
            sizes[layer] = sizes.get(layer, 0) + 1

    matched = [node for node, layer in layer_of.items() if layer is not None]
    in_ruled = [node for node, layer in layer_of.items() if layer in ruled_layers]
    unlayered = sorted(node for node, layer in layer_of.items() if layer is None)

    internal_edges = [(src, dst) for src, dst in graph.edges if src in layer_of and dst in layer_of]
    governed = [
        (src, dst)
        for src, dst in internal_edges
        if layer_of[src] is not None and layer_of[dst] is not None
    ]

    return LayerCoverage(
        modules_total=len(layer_of),
        modules_matched=len(matched),
        modules_in_ruled_layer=len(in_ruled),
        edges_total=len(internal_edges),
        edges_governed=len(governed),
        unlayered_modules=tuple(unlayered),
        modules_outside_declared_roots=outside,
        layer_sizes=tuple((layer.name, sizes[layer.name]) for layer in config.layers),
    )


class LayerConfigError(Exception):
    """Raised when an archy.yaml file is missing, malformed, or self-inconsistent."""


def load_config(path: Path) -> LayerConfig:
    if not path.exists():
        raise LayerConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise LayerConfigError(f"could not parse YAML at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LayerConfigError(f"config root must be a mapping in {path}")

    layers = _parse_layers(raw.get("layers", {}), path)
    forbid = _parse_forbid(raw.get("forbid", []), {layer.name for layer in layers}, path)
    required = _parse_required(raw.get("required", []), path)
    exclude = _parse_str_list(raw.get("exclude", []), "exclude", path)
    roots = _parse_str_list(raw.get("roots", []), "roots", path)
    sdp = _parse_sdp(raw.get("sdp"), path)
    max_modules = _parse_max_modules(raw.get("max_modules"), path)
    min_layers_present = _parse_min_layers_present(raw.get("min_layers_present"), len(layers), path)
    return LayerConfig(
        layers=tuple(layers),
        forbid=tuple(forbid),
        required=tuple(required),
        exclude=tuple(exclude),
        roots=tuple(roots),
        sdp=sdp,
        max_modules=max_modules,
        min_layers_present=min_layers_present,
    )


def _parse_non_negative_int(value: object, field: str, path: Path) -> int | None:
    """Shared guard for the integer config knobs. None when the key is absent.

    `bool` is an `int` subclass in Python, so `true` would otherwise sail
    through as 1 and configure something the author never asked for.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LayerConfigError(f"`{field}` must be a non-negative integer in {path}, got {value!r}")
    return value


def _parse_min_layers_present(value: object, declared: int, path: Path) -> int | None:
    """Validate `min_layers_present:`. Absent -> None (no presence gate).

    Rejects a floor higher than the number of layers actually declared, because
    such a config can never pass and the failure would otherwise look like a
    finding about the codebase rather than a typo in the config.
    """
    parsed = _parse_non_negative_int(value, "min_layers_present", path)
    if parsed is None:
        return None
    if parsed > declared:
        raise LayerConfigError(
            f"`min_layers_present` is {parsed} in {path} but only {declared} layer(s) are "
            "declared, so the check could never pass."
        )
    return parsed


def _parse_max_modules(value: object, path: Path) -> int | None:
    """Validate `max_modules:` from archy.yaml. Absent -> None (use the default).

    Rejects non-integers and negatives (a `bool` is an `int` subclass in Python,
    so it is rejected explicitly). `0` is allowed and means "disable the guard".
    """
    return _parse_non_negative_int(value, "max_modules", path)


def discover_config(start: Path) -> Path | None:
    """Walk from `start` up toward the filesystem root looking for archy.yaml."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    while True:
        candidate = current / "archy.yaml"
        if candidate.exists():
            return candidate
        if current == current.parent:
            return None
        current = current.parent


def match_layer(qualname: str, layers: tuple[LayerSpec, ...]) -> str | None:
    """Return the layer name covering `qualname`, or None if unlayered.

    Layers are expected to be disjoint. That is checked here at runtime (the
    walk collects all matches and raises if a module matches more than one),
    not at load_config, which validates pattern *syntax* but not cross-layer
    overlap. Disjointness can only be decided against a concrete module set.
    """
    matches = [layer.name for layer in layers if _qualname_matches_any(qualname, layer.patterns)]
    if len(matches) > 1:
        raise LayerConfigError(
            f"module {qualname!r} matches multiple layers ({', '.join(matches)}); "
            "make pattern coverage disjoint"
        )
    return matches[0] if matches else None


def find_violations(graph: nx.DiGraph, config: LayerConfig) -> list[Violation]:
    forbid_index = {(rule.from_layer, rule.to_layer): rule for rule in config.forbid}
    violations: list[Violation] = []
    for source, target, data in graph.edges(data=True):
        src_layer = match_layer(source, config.layers)
        tgt_layer = match_layer(target, config.layers)
        if src_layer is None or tgt_layer is None:
            continue
        rule = forbid_index.get((src_layer, tgt_layer))
        if rule is None:
            continue
        violations.append(
            Violation(
                rule=rule,
                source=source,
                target=target,
                lines=tuple(data.get("lines", ())),
            )
        )
    violations.sort(key=lambda v: (v.rule.from_layer, v.rule.to_layer, v.source, v.target))
    return violations


def find_reach_violations(graph: nx.DiGraph, config: LayerConfig) -> list[ReachViolation]:
    """Every `required:` rule that a module fails to satisfy.

    Reach is transitive and computed over `graph.with_package_init_edges`, so a
    package that imports the target once in its `__init__.py` satisfies the rule
    for all of its submodules. A module trivially satisfies a rule it matches on
    both sides (it reaches itself).

    Rules that cannot fire are reported here too, as violations with
    `module=None`. Skipping them would reproduce the failure that motivated
    layer coverage: a typo'd pattern matching nothing looks exactly like a
    codebase that satisfies the rule everywhere.

    Cost is one `nx.descendants` per matching source module. Fine for the shapes
    this targets (a `commands.**` package, a handful of entrypoints); a rule
    whose `source` is a bare `**`-rooted pattern over a very large tree pays for
    the breadth it asked for.
    """
    if not config.required:
        return []

    # `archy.reach`, NOT `archy.graph`, which is where this helper first lived.
    # graph pulls in parser/complexity/risk behind it, so importing it here put
    # policy 4 hops from a leaf and cost 2 on max import depth. Both spellings
    # pass `archy check` (policy -> graph is permitted) and both type-check; the
    # depth axis is what caught it, and archy's own `LayerConfig.max_modules`
    # comment already recorded the intent that policy not depend on graph.
    # A function-local import would NOT have helped: archy records function-local
    # imports as graph edges (tests/test_graph.py pins that), so the edge and the
    # depth cost are identical either way.
    from archy.reach import with_package_init_edges

    augmented = with_package_init_edges(graph)
    all_nodes = list(graph.nodes)
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]

    violations: list[ReachViolation] = []
    for rule in config.required:
        # Targets may be external: "every entrypoint must reach `sqlalchemy`" is
        # a legitimate thing to require, and external nodes are real nodes here.
        targets = {n for n in all_nodes if _qualname_matches_any(n, (rule.must_reach,))}
        sources = sorted(n for n in internal if _qualname_matches_any(n, (rule.source,)))
        if not targets:
            violations.append(
                ReachViolation(
                    rule=rule,
                    module=None,
                    detail=(
                        f"rule cannot fire: `must_reach` pattern {rule.must_reach!r} matches no "
                        "module in the scanned tree."
                    ),
                )
            )
            continue
        if not sources:
            violations.append(
                ReachViolation(
                    rule=rule,
                    module=None,
                    detail=(
                        f"rule cannot fire: `source` pattern {rule.source!r} matches no internal "
                        'module (patterns are dotted-name globs -- "pkg.**" matches a package and '
                        'its descendants, "pkg" only that exact module).'
                    ),
                )
            )
            continue
        for module in sources:
            if module in targets:
                continue
            if nx.descendants(augmented, module) & targets:
                continue
            violations.append(
                ReachViolation(
                    rule=rule,
                    module=module,
                    detail=(
                        f"{module} does not transitively reach {rule.must_reach!r} "
                        "(package __init__ imports included)."
                    ),
                )
            )
    violations.sort(key=lambda v: (v.rule.source, v.rule.must_reach, v.module or ""))
    return violations


def find_sdp_violations(graph: nx.DiGraph, *, tolerance: float = 0.0) -> list[SdpViolation]:
    """Edges where the target is strictly less stable than the source.

    "Less stable" means higher Martin's I. The Stable Dependencies
    Principle says modules should depend in the direction of stability,
    so an edge from `Is = 0.2` to `It = 0.8` (target depends on more
    things, more likely to change) is a violation. Only internal-to-
    internal edges are considered; external dependencies have no I.
    """
    from archy.instability import compute_instability

    instability = compute_instability(graph)
    violations: list[SdpViolation] = []
    for source, target, data in graph.edges(data=True):
        if source not in instability or target not in instability:
            continue
        i_src = instability[source]
        i_tgt = instability[target]
        if i_tgt > i_src + tolerance:
            violations.append(
                SdpViolation(
                    source=source,
                    target=target,
                    source_instability=i_src,
                    target_instability=i_tgt,
                    lines=tuple(data.get("lines", ())),
                )
            )
    violations.sort(key=lambda v: (v.source, v.target))
    return violations


# --- internals ----------------------------------------------------------------


def _parse_layers(raw: object, path: Path) -> list[LayerSpec]:
    raw_layers = _as_str_dict(raw, "`layers`", path)
    out: list[LayerSpec] = []
    for name, body_raw in raw_layers.items():
        if not name:
            raise LayerConfigError(f"layer names must be non-empty strings in {path}")
        body = _as_str_dict(body_raw, f"layer {name!r} body", path)
        modules_raw = body.get("modules", [])
        if not isinstance(modules_raw, list) or not all(
            isinstance(m, str) and m for m in modules_raw
        ):
            raise LayerConfigError(
                f"layer {name!r} must define `modules` as a list of non-empty strings in {path}"
            )
        # mypy/ty: explicit narrowing - the all(...) check above guarantees str.
        modules: tuple[str, ...] = tuple(m for m in modules_raw if isinstance(m, str))
        for pattern in modules:
            _validate_layer_pattern(pattern, name, path)
        out.append(LayerSpec(name=name, patterns=modules))
    return out


def _validate_layer_pattern(pattern: str, layer_name: str, path: Path) -> None:
    _validate_pattern(pattern, f"layer {layer_name!r} has an invalid module pattern", path)


def _validate_pattern(pattern: str, prefix: str, path: Path) -> None:
    """Reject malformed dotted-name globs at config load with a clear error.

    A pattern is a dotted-name glob: a leading valid-identifier root package,
    then segments that are each a valid identifier, ``*`` (one segment), or
    ``**`` (zero or more segments). Validating here means a typo like ``**`` or
    ``*foo`` fails fast and legibly, instead of later surfacing as a cryptic
    import-linter ``ModuleNotFoundError`` (the contracts fallback derives the
    root package from the first segment) or a silently-wrong match regex.

    `prefix` names the offending config entry, so the same rules serve both
    `layers:` patterns and `required:` rule patterns without either borrowing
    the other's error wording.
    """
    segments = pattern.split(".")
    if any(seg == "" for seg in segments):
        raise LayerConfigError(
            f"{prefix} {pattern!r} in {path}: "
            "empty path segment (no leading, trailing, or doubled dots)."
        )
    # A package-name segment must be an identifier that is not a Python keyword:
    # no importable package is named `import`/`class`, so accepting one here
    # would just defer the failure to a cryptic import-linter error instead of
    # the clean message this validation exists to give.
    if not _is_package_segment(segments[0]):
        raise LayerConfigError(
            f"{prefix} {pattern!r} in {path}: "
            f"must start with a Python package name, not {segments[0]!r} "
            '(e.g. "myapp.domain.**", not "**").'
        )
    for seg in segments[1:]:
        if seg not in ("*", "**") and not _is_package_segment(seg):
            raise LayerConfigError(
                f"{prefix} {pattern!r} in {path}: "
                f"segment {seg!r} must be a package name, '*' (one segment), "
                "or '**' (zero or more segments)."
            )


def _is_package_segment(segment: str) -> bool:
    return segment.isidentifier() and not keyword.iskeyword(segment)


def _parse_forbid(raw: object, known_layers: set[str], path: Path) -> list[ForbidRule]:
    if not isinstance(raw, list):
        raise LayerConfigError(f"`forbid` must be a list in {path}")
    out: list[ForbidRule] = []
    for entry_raw in raw:
        entry = _as_str_dict(entry_raw, "forbid entry", path)
        for required in ("from", "to"):
            if required not in entry:
                raise LayerConfigError(
                    f"forbid entry is missing required key {required!r} in {path}"
                )
        src_raw = entry["from"]
        tgt_raw = entry["to"]
        if not isinstance(src_raw, str) or not isinstance(tgt_raw, str):
            raise LayerConfigError(f"forbid `from`/`to` values must be strings in {path}")
        _check_known_layer(src_raw, "from", known_layers, path)
        _check_known_layer(tgt_raw, "to", known_layers, path)
        out.append(ForbidRule(from_layer=src_raw, to_layer=tgt_raw))
    return out


def _parse_required(raw: object, path: Path) -> list[RequiredRule]:
    """Validate `required:`. Absent or empty -> no required-reach rules.

    Both patterns are validated as dotted-name globs at load time for the same
    reason layer patterns are: `commands` matches one exact module while
    `commands.**` matches the package and its descendants, and a config that
    meant the latter and wrote the former produces a dead rule that reads as a
    clean pass.
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        raise LayerConfigError(f"`required` must be a list in {path}")
    out: list[RequiredRule] = []
    for entry_raw in raw:
        entry = _as_str_dict(entry_raw, "required entry", path)
        for key in ("source", "must_reach"):
            if key not in entry:
                raise LayerConfigError(f"required entry is missing required key {key!r} in {path}")
        source = entry["source"]
        must_reach = entry["must_reach"]
        reason = entry.get("reason", "")
        if not isinstance(source, str) or not isinstance(must_reach, str):
            raise LayerConfigError(
                f"required `source`/`must_reach` values must be strings in {path}"
            )
        if not isinstance(reason, str):
            raise LayerConfigError(f"required `reason` must be a string in {path}")
        _validate_pattern(source, "required rule has an invalid `source` pattern", path)
        _validate_pattern(must_reach, "required rule has an invalid `must_reach` pattern", path)
        out.append(RequiredRule(source=source, must_reach=must_reach, reason=reason))
    return out


def _check_known_layer(name: str, field: str, known: set[str], path: Path) -> None:
    if name not in known:
        raise LayerConfigError(
            f"forbid `{field}` references unknown layer {name!r} in {path}; "
            f"known layers: {sorted(known)}"
        )


def _parse_sdp(raw: object, path: Path) -> SdpConfig:
    if raw is None:
        return SdpConfig()
    body = _as_str_dict(raw, "`sdp`", path)
    enabled = body.get("enabled", False)
    tolerance = body.get("tolerance", 0.0)
    mode = body.get("mode", "error")
    if not isinstance(enabled, bool):
        raise LayerConfigError(f"`sdp.enabled` must be a bool in {path}")
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool):
        raise LayerConfigError(f"`sdp.tolerance` must be a number in {path}")
    if not isinstance(mode, str) or mode not in ("error", "warn"):
        raise LayerConfigError(f"`sdp.mode` must be 'error' or 'warn' in {path} (got {mode!r})")
    return SdpConfig(enabled=enabled, tolerance=float(tolerance), mode=mode)


def _parse_str_list(raw: object, field: str, path: Path) -> list[str]:
    # Shared shape check for the optional list-of-strings keys (`exclude`,
    # `roots`). `exclude` adds directory basenames to the built-in ignore set
    # (.venv, node_modules, ...) for codegen and vendored trees. `roots`
    # declares PEP 420 namespace-package roots so descendants get rooted
    # qualnames without forcing __init__.py markers into the source tree.
    if not raw:
        return []
    if not isinstance(raw, list) or not all(isinstance(d, str) and d for d in raw):
        raise LayerConfigError(f"`{field}` must be a list of non-empty strings in {path}")
    return [d for d in raw if isinstance(d, str)]


def _as_str_dict(value: object, label: str, path: Path) -> dict[str, object]:
    """Validate `value` is a mapping with str keys and return it with precise types."""
    if not isinstance(value, dict):
        raise LayerConfigError(f"{label} must be a mapping in {path}")
    result: dict[str, object] = {}
    for key, val in value.items():
        if not isinstance(key, str):
            raise LayerConfigError(f"{label} keys must be strings in {path}")
        result[key] = val
    return result


def _qualname_matches_any(qualname: str, patterns: tuple[str, ...]) -> bool:
    return any(_compile_pattern(p).fullmatch(qualname) for p in patterns)


_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    cached = _PATTERN_CACHE.get(pattern)
    if cached is not None:
        return cached
    compiled = re.compile(_translate_pattern(pattern))
    _PATTERN_CACHE[pattern] = compiled
    return compiled


def _translate_pattern(pattern: str) -> str:
    # Each pattern is a dotted-name glob:
    #   `**` matches zero or more dotted segments
    #   `*`  matches a single segment (no dots)
    #   bare `pkg.foo` matches that exact qualname
    # `pkg.**` is canonical for "the package and all descendants"; we expand
    # the trailing `**` into "(\..*)?$" so the package itself is covered.
    parts: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*" and i + 1 < len(pattern) and pattern[i + 1] == "*":
            # Trailing `**` after a `.`: collapse the literal dot so the bare
            # parent matches too.
            if parts and parts[-1] == r"\.":
                parts.pop()
                parts.append(r"(?:\..*)?")
            else:
                parts.append(r".*")
            i += 2
        elif ch == "*":
            parts.append(r"[^.]*")
            i += 1
        elif ch == ".":
            parts.append(r"\.")
            i += 1
        else:
            parts.append(re.escape(ch))
            i += 1
    return "^" + "".join(parts) + "$"
