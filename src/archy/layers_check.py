"""Judging an import graph against the layer rules: violations, reach, SDP, coverage.

archy:owns        check_external_patterns, compute_coverage, contracts_unverified,
                  find_reach_violations, find_sdp_violations, find_violations,
                  governed_roots
archy:mirrored-by check_external_patterns -> archy.contracts, archy.layers,
                  compute_coverage -> archy.cli_check, archy.cli_conventions,
                  archy.layers, archy.mcp_runs_check, bench.greenfield_eval,
                  contracts_unverified -> archy.cli_render_check, archy.layers,
                  archy.mcp_runs_check, find_reach_violations -> archy.cli_check,
                  archy.diff, archy.layers, archy.mcp_runs_check,
                  find_sdp_violations -> archy.cli_check, archy.diff, archy.layers,
                  archy.mcp_runs_check
"""

from __future__ import annotations

import networkx as nx

from archy.instability import compute_instability
from archy.layers_match import _qualname_matches_any, is_floating_pattern, match_layer
from archy.layers_models import (
    ENTRYPOINTS_SOURCE,
    ExactPatternHint,
    LayerConfig,
    LayerConfigError,
    LayerCoverage,
    ReachViolation,
    RequiredRule,
    SdpViolation,
    Violation,
)


def governed_roots(config: LayerConfig) -> frozenset[str]:
    """The top-level packages the config's LAYER PATTERNS talk about.

    A pattern is a dotted-name glob whose first segment names the namespace the
    author intended to govern, except a floating one (`**.services.**`), which
    names no namespace. Any floating pattern makes the scope unbounded, so this
    returns the empty set and `compute_coverage` does not scope by root.

    NOT `LayerConfig.roots`, despite the name proximity. That field declares
    extra PEP 420 scan roots so the graph builder can find namespace packages at
    all; this function asks which namespaces the rules claim authority over.
    Neither reads the other.
    """
    patterns = [pattern for layer in config.layers for pattern in layer.patterns]
    if any(is_floating_pattern(pattern) for pattern in patterns):
        return frozenset()
    return frozenset(pattern.split(".")[0] for pattern in patterns)


def compute_coverage(graph: nx.DiGraph, config: LayerConfig) -> LayerCoverage:
    """Measure the reach of `config`'s layers over `graph`.

    Scoped to the root packages the config names, NOT to everything the scan
    happened to reach. `archy check .` on this repository walks `bench/` too,
    and counting those scripts would report 7% coverage for a config that never
    claimed to govern them. The interpretable question is "of the code you said
    you were governing, how much do the rules reach", and modules outside those
    roots are counted separately rather than silently dropped.

    External nodes are not the user's code, so they stay out of the module and
    internal-edge counts. A layer that NAMES an external package
    (`sqlalchemy.**`) is still a layer that exists: the external nodes it
    matches count toward `layer_sizes` (so presence is true), and the edges into
    them are reported apart as `external_edges_governed` (#527).
    """
    ruled_layers = {rule.from_layer for rule in config.forbid} | {
        rule.to_layer for rule in config.forbid
    }
    layer_of, external_layer_of, outside = _classify_nodes(graph, config)
    sizes = _layer_sizes(config, layer_of, external_layer_of)

    matched = [node for node, layer in layer_of.items() if layer is not None]
    in_ruled = [node for node, layer in layer_of.items() if layer in ruled_layers]
    unlayered = sorted(node for node, layer in layer_of.items() if layer is None)

    internal_edges = [(src, dst) for src, dst in graph.edges if src in layer_of and dst in layer_of]
    governed = [
        (src, dst)
        for src, dst in internal_edges
        if layer_of[src] is not None and layer_of[dst] is not None
    ]

    external_governed = sum(
        1 for src, dst in graph.edges if layer_of.get(src) is not None and dst in external_layer_of
    )

    return LayerCoverage(
        exact_pattern_hints=_exact_pattern_hints(config, unlayered),
        external_edges_governed=external_governed,
        modules_total=len(layer_of),
        modules_matched=len(matched),
        modules_in_ruled_layer=len(in_ruled),
        edges_total=len(internal_edges),
        edges_governed=len(governed),
        unlayered_modules=tuple(unlayered),
        modules_outside_declared_roots=outside,
        layer_sizes=tuple((layer.name, sizes[layer.name]) for layer in config.layers),
    )


def _classify_nodes(
    graph: nx.DiGraph, config: LayerConfig
) -> tuple[dict[str, str | None], dict[str, str], int]:
    """Layer of each in-scope internal node, of each layered external node, and the
    count of internal nodes outside the config's declared roots."""
    roots = governed_roots(config)
    layer_of: dict[str, str | None] = {}
    external_layer_of: dict[str, str] = {}
    outside = 0
    for node, data in graph.nodes(data=True):
        if data.get("external"):
            external_layer = match_layer(node, config.layers)
            if external_layer is not None:
                external_layer_of[node] = external_layer
            continue
        if roots and node.split(".")[0] not in roots:
            outside += 1
            continue
        layer_of[node] = match_layer(node, config.layers)
    return layer_of, external_layer_of, outside


def _layer_sizes(
    config: LayerConfig, layer_of: dict[str, str | None], external_layer_of: dict[str, str]
) -> dict[str, int]:
    sizes = {layer.name: 0 for layer in config.layers}
    for layer in (*layer_of.values(), *external_layer_of.values()):
        if layer is not None:
            sizes[layer] = sizes.get(layer, 0) + 1
    return sizes


def contracts_unverified(
    config: LayerConfig, coverage: LayerCoverage, violations: list[Violation]
) -> bool:
    """True when a clean direct-edge pass cannot prove the `forbid` rules hold.

    Shared by every surface that reports a check, because the honesty of a clean
    verdict is not a rendering concern. `archy check` states it in text, its JSON
    carries it as a field, and `archy_check` carries it on the wire; the CLI got
    it first and the MCP surface, the one agents actually call, went on reporting
    `passed=true` with nothing saying only direct edges had been looked at (#343).

    🔴 THE CONDITION IS "THIS RUN CANNOT PROVE THE ABSENCE", NOT "COVERAGE IS
    ZERO". A first attempt fired only on `governs_nothing` and stayed silent on
    archy's own repository -- forbid rules declared, 13% of edges governed, 34
    unlayered modules, and `contracts` finding a genuinely broken rule the
    direct-edge pass missed. Some coverage is not enough coverage; the question
    is whether a path could hide in the part this check does not govern.

    False when there is nothing to say: no `forbid` rule means nothing to verify,
    and a run that already found a concrete violation has given the reader
    somewhere to go without this.
    """
    if not config.forbid or violations:
        return False
    return (
        coverage.governs_nothing
        or coverage.governs_no_edges
        or bool(coverage.exact_pattern_hints)
        or bool(coverage.unlayered_modules)
        or coverage.edges_governed < coverage.edges_total
    )


def _exact_pattern_hints(config: LayerConfig, unlayered: list[str]) -> tuple[ExactPatternHint, ...]:
    """Find bare-qualname patterns that left descendants unlayered.

    Scoped to `unlayered`, which is already restricted to the config's own
    declared roots, so a package outside them cannot raise a hint.

    A pattern containing any `*` is skipped: the author reached for a glob and
    got what globbing gives. Only the metacharacter-free form silently means
    "this one module".
    """
    hints: list[ExactPatternHint] = []
    for layer in config.layers:
        for pattern in layer.patterns:
            if "*" in pattern:
                continue
            prefix = f"{pattern}."
            descendants = tuple(sorted(m for m in unlayered if m.startswith(prefix)))
            if not descendants:
                continue
            hints.append(
                ExactPatternHint(
                    layer=layer.name,
                    pattern=pattern,
                    unlayered_descendants=descendants,
                    suggestion=f"{pattern}.**",
                )
            )
    return tuple(hints)


def check_external_patterns(graph: nx.DiGraph, config: LayerConfig) -> None:
    """Refuse a pattern that reaches below an external package's top-level name.

    The graph collapses every external import to its top-level package, so
    `from sqlalchemy.orm import Session` is an edge to the node `sqlalchemy` and
    there is never a node `sqlalchemy.orm`. A pattern such as `sqlalchemy.orm.**`
    can therefore never match, and `check` exited 0 on a real import it was
    written to catch (#527). Decided against the graph, not at YAML load: only
    a scanned tree says which first segments are external packages and which are
    the project's own.

    Raises:
        LayerConfigError: naming the pattern and the form that works.
    """
    external = {n for n, d in graph.nodes(data=True) if d.get("external")}
    if not external:
        return
    for where, pattern in _named_patterns(config):
        if is_floating_pattern(pattern):
            continue
        root = pattern.split(".", 1)[0]
        if root in external and pattern not in (root, f"{root}.**"):
            raise LayerConfigError(
                f"{where} pattern {pattern!r} names a submodule of the external package "
                f"{root!r}, but the graph keeps only the top-level name, so it can never "
                f"match. Use {root + '.**'!r}."
            )


def _named_patterns(config: LayerConfig) -> list[tuple[str, str]]:
    """Every pattern the config writes, with where it was written."""
    named: list[tuple[str, str]] = []
    for layer in config.layers:
        named.extend((f"layer {layer.name!r}", pattern) for pattern in layer.patterns)
    for rule in config.required:
        for field, pattern in (
            ("source", rule.source),
            ("must_reach", rule.must_reach),
            ("within", rule.within),
        ):
            if pattern and pattern != ENTRYPOINTS_SOURCE:
                named.append((f"required rule {field}", pattern))
    return named


def find_violations(graph: nx.DiGraph, config: LayerConfig) -> list[Violation]:
    check_external_patterns(graph, config)
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
                path=graph.graph.get("relative_paths", {}).get(source, ""),
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
    check_external_patterns(graph, config)

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
    internal = [n for n, d in graph.nodes(data=True) if not d.get("external")]

    violations: list[ReachViolation] = []
    for rule in config.required:
        violations.extend(_reach_violations_for_rule(rule, graph, augmented, internal))
    violations.sort(
        key=lambda v: (v.rule.source, v.rule.within or "", v.rule.must_reach, v.module or "")
    )
    return violations


def _reach_violations_for_rule(
    rule: RequiredRule,
    graph: nx.DiGraph,
    augmented: nx.DiGraph,
    internal: list[str],
) -> list[ReachViolation]:
    # Targets may be external: "every entrypoint must reach `sqlalchemy`" is
    # a legitimate thing to require, and external nodes are real nodes here.
    targets = {n for n in graph.nodes if _qualname_matches_any(n, (rule.must_reach,))}
    sources, why, refusal = _reach_sources(rule, graph, internal)
    problem = refusal or _unfireable_reason(rule, sources, targets)
    if problem is not None:
        return [ReachViolation(rule=rule, module=None, detail=problem)]
    return [
        ReachViolation(
            rule=rule,
            module=module,
            detail=(
                f"{module} does not transitively reach {rule.must_reach!r} "
                "(package __init__ imports included)."
                + (f" It is an entrypoint: {why[module]}." if module in why else "")
            ),
        )
        for module in sources
        if module not in targets and not (nx.descendants(augmented, module) & targets)
    ]


def _reach_sources(
    rule: RequiredRule, graph: nx.DiGraph, internal: list[str]
) -> tuple[list[str], dict[str, str], str | None]:
    """The modules `rule` binds, why each is an entrypoint, and a refusal if any."""
    if rule.source != ENTRYPOINTS_SOURCE:
        return sorted(n for n in internal if _qualname_matches_any(n, (rule.source,))), {}, None
    errors = graph.graph.get("entrypoint_errors", ())
    if errors:
        # The derived set is incomplete, so a pass would be a pass over
        # a subset nobody chose. Refuse the verdict rather than give it.
        return [], {}, f"entrypoints could not be fully derived: {'; '.join(errors)}"
    derived = graph.graph.get("entrypoints", {})
    why = {
        n: reason
        for n, reason in derived.items()
        if rule.within is None or _qualname_matches_any(n, (rule.within,))
    }
    return sorted(n for n in internal if n in why), why, None


def _unfireable_reason(rule: RequiredRule, sources: list[str], targets: set[str]) -> str | None:
    """Why `rule` cannot fire against this tree, or None when it can."""
    if not targets:
        return (
            f"nothing matches the `must_reach` pattern {rule.must_reach!r} in the "
            "scanned tree: either the pattern is wrong, or -- for an external "
            "package -- no module imports it any more, which is itself the "
            "failure the rule is about."
        )
    if sources:
        return None
    if rule.source == ENTRYPOINTS_SOURCE:
        scope = "" if rule.within is None else f" within {rule.within!r}"
        return (
            f"rule cannot fire: no entrypoint was derived{scope}. An entrypoint is a "
            'module with a top-level `if __name__ == "__main__":` guard, a '
            "`__main__.py`, or a script target in a pyproject.toml between the "
            "scanned modules and the repository root."
        )
    return (
        f"rule cannot fire: `source` pattern {rule.source!r} matches no internal "
        'module (patterns are dotted-name globs -- "pkg.**" matches a package and '
        'its descendants, "pkg" only that exact module).'
    )


def find_sdp_violations(graph: nx.DiGraph, *, tolerance: float = 0.0) -> list[SdpViolation]:
    """Edges where the target is strictly less stable than the source.

    "Less stable" means higher Martin's I. The Stable Dependencies
    Principle says modules should depend in the direction of stability,
    so an edge from `Is = 0.2` to `It = 0.8` (target depends on more
    things, more likely to change) is a violation. Only internal-to-
    internal edges are considered; external dependencies have no I.
    """
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
                    path=graph.graph.get("relative_paths", {}).get(source, ""),
                )
            )
    violations.sort(key=lambda v: (v.source, v.target))
    return violations
