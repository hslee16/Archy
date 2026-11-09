"""The `archy diff --strict` verdict: no new cycles, no new layer violations,
and no weakened rule in archy.yaml.

A ratchet for codebases that already carry findings. `archy cycles --strict`
fails on day one for most existing projects (25 of 28 bench graphs have a
cycle), so the likely response is to delete the gate. This one keeps every
finding the committed baseline already records and fails only on what is new.

**What counts as a new cycle (SCC-subset rule, #521).** A current cycle is
carried when, after ignoring modules the baseline never saw, what remains sits
inside ONE baseline cycle. So shrinking, splitting or renaming within a tangle
passes, a new file joining one passes, and a new import between two modules
already in the same tangle passes. A cycle fails when it forms among modules
that were outside every baseline cycle, when it pulls an existing acyclic
module into a tangle, or when it merges two baseline tangles.

The rejected alternative, keying on edges inside a cycle, would also fail a
new import between two members of one tangle. In a legacy tangle such as
django's 157-module SCC, those imports are routine edits, so that key fires
exactly where the ratchet is meant to stay installed.

"Ignoring modules the baseline never saw" needs the baseline's module list,
which snapshots record since `--strict` shipped. A baseline without one cannot
tell a new file from an existing module, so the verdict is refused rather than
guessed.

**What counts as a new violation.** A forbid-rule violation whose
`(from_layer, to_layer, source, target)` is not in the baseline.

**What counts as a weakened rule (#585).** Findings are computed from the
archy.yaml that is on disk now, so deleting `forbid` or excluding the
package that imports across it makes a violation disappear. A findings-only
baseline records a `rules` fingerprint. `strict_verdict` fails when a forbid,
layer, required contract, or root from that fingerprint is gone or weaker,
or when an `exclude` entry was added. A new rule, or a wider layer or
source pattern, does not fail. A baseline written before `rules` existed
skips this comparison and says how to regenerate one. The cycle and
violation checks still run.

archy:owns        RulesFingerprint, StrictEdge, StrictFinding, StrictVerdict,
                  evaluate_strict, module_paths_for, weakened_rules
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, computed_field

from archy.cycles import Cycle
from archy.layers import ENTRYPOINTS_SOURCE, Violation

# A new tangle can carry many edges; the reason lists this many, then a count.
_MAX_EDGES_SHOWN = 10


class StrictEdge(BaseModel):
    """One import edge a strict finding is about, located for `git blame`."""

    model_config = ConfigDict(frozen=True)

    source: str
    target: str
    # `path:line` for each line the import appears on; just `path` when the
    # line is unknown. Empty when the source module has no file in the graph.
    locations: tuple[str, ...] = ()


class StrictFinding(BaseModel):
    """A cycle, violation, or weakened rule the baseline does not allow."""

    model_config = ConfigDict(frozen=True)

    kind: str  # "cycle", "violation", or "rule"
    modules: tuple[str, ...]
    # For a cycle: its edges that no baseline cycle has (the ones that closed
    # it). For a violation: the forbidden edge itself.
    edges: tuple[StrictEdge, ...]
    detail: str


class StrictVerdict(BaseModel):
    """The `--strict` result. `passed` and `reason` are computed so every
    surface, MCP's `model_dump()` included, carries them."""

    model_config = ConfigDict(frozen=True)

    baseline: str
    findings: tuple[StrictFinding, ...] = ()
    carried_cycles: int = 0
    carried_violations: int = 0
    # Set when the baseline cannot support a verdict at all. A refused verdict
    # fails: a gate that cannot evaluate must not read as clean.
    refused: str | None = None
    # Set when the baseline predates the rules fingerprint. Not a failure.
    # The cycle and violation ratchet still runs; this only says how to record
    # rules so a later config edit cannot hide a finding.
    rules_hint: str | None = None

    @computed_field
    @property
    def passed(self) -> bool:
        return self.refused is None and not self.findings

    @computed_field
    @property
    def reason(self) -> str:
        if self.refused is not None:
            return f"strict diff refused against {self.baseline}: {self.refused}"
        carried = (
            f"{self.carried_cycles} cycle(s) and {self.carried_violations} violation(s) "
            f"carried from {self.baseline}"
        )
        if not self.findings:
            text = f"no new cycles or layer violations; {carried}"
        else:
            lines = [f"{len(self.findings)} new finding(s); {carried}:"]
            lines.extend(f"  {finding.detail}" for finding in self.findings)
            text = "\n".join(lines)
        if self.rules_hint:
            return f"{text}\n{self.rules_hint}"
        return text


def evaluate_strict(
    *,
    baseline_label: str,
    baseline_modules: tuple[str, ...],
    baseline_cycles: Iterable[Cycle],
    baseline_violations: Iterable[Violation],
    current_cycles: Iterable[Cycle],
    current_violations: Iterable[Violation],
    module_paths: Mapping[str, str],
    ungoverned: tuple[Violation, ...] | None,
    regenerate: str,
) -> StrictVerdict:
    """Compare current findings against a committed baseline.

    `module_paths` maps a module to the file path a reader should see (relative
    to the project when possible), for the `path:line` locations.
    """
    if not baseline_modules:
        return StrictVerdict(
            baseline=baseline_label,
            refused=(
                "the baseline records no module list (written before `--strict` "
                "existed, or from an empty scan), so a new file cannot be told from an "
                f"existing module. Regenerate it with `{regenerate}`."
            ),
        )
    baseline_violations = list(baseline_violations)
    refused = _refusal_for_dead_rules(baseline_violations, ungoverned, regenerate)
    if refused is not None:
        return StrictVerdict(baseline=baseline_label, refused=refused)
    cycle_findings, carried_cycles = _new_cycles(
        set(baseline_modules), list(baseline_cycles), current_cycles, module_paths
    )
    violation_findings, carried_violations = _new_violations(
        baseline_violations, current_violations, module_paths
    )
    return StrictVerdict(
        baseline=baseline_label,
        findings=(*cycle_findings, *violation_findings),
        carried_cycles=carried_cycles,
        carried_violations=carried_violations,
    )


def _refusal_for_dead_rules(
    baseline_violations: list[Violation],
    ungoverned: tuple[Violation, ...] | None,
    regenerate: str,
) -> str | None:
    """Why the baseline's violations cannot be judged, or None when they can.

    A baseline violation can only be carried or resolved by a rule that still
    runs. With no archy.yaml, or a layer whose patterns now match nothing (a
    bare `pkg.core` for `pkg.core.**`, a package renamed under the config),
    the violation silently drops out and new ones go unseen: a rule set that
    cannot fire reading as clean.
    """
    if not baseline_violations:
        return None
    remedy = (
        "Restore the rules, or if they were removed on purpose, regenerate the "
        f"baseline with `{regenerate}`."
    )
    if ungoverned is None:
        return (
            f"the baseline records {len(baseline_violations)} layer violation(s), "
            "but no archy.yaml was found for this scan, so no layer rule was "
            f"checked. {remedy}"
        )
    if ungoverned:
        first = ungoverned[0]
        return (
            f"the rules in archy.yaml no longer cover {len(ungoverned)} baseline "
            f"violation(s) whose modules still exist (e.g. {first.source} -> "
            f"{first.target}, {first.rule.from_layer} -> {first.rule.to_layer}), so "
            f"those rules cannot fire. {remedy}"
        )
    return None


def _new_cycles(
    known: set[str],
    baseline_cycles: list[Cycle],
    current_cycles: Iterable[Cycle],
    module_paths: Mapping[str, str],
) -> tuple[list[StrictFinding], int]:
    """The current cycles the baseline does not carry, and how many it does."""
    old_cycles = [set(c.modules) for c in baseline_cycles]
    old_cycle_edges = {(e.source, e.target) for c in baseline_cycles for e in c.edges}
    # Which baseline cycle each module sat in. An edge between two members of
    # the same one deepened a tangle the baseline already carries; it did not
    # close the new cycle, so naming it would send the reader to the wrong line.
    old_cycle_of = {m: i for i, members in enumerate(old_cycles) for m in members}

    def closes(source: str, target: str) -> bool:
        if (source, target) in old_cycle_edges:
            return False
        return old_cycle_of.get(source, -1) != old_cycle_of.get(target, -2)

    findings: list[StrictFinding] = []
    carried_cycles = 0
    for cycle in current_cycles:
        seen = set(cycle.modules) & known
        if _cycle_is_carried(cycle, seen, old_cycles, old_cycle_edges):
            carried_cycles += 1
            continue
        closing = [e for e in cycle.edges if closes(e.source, e.target)]
        edges = tuple(
            StrictEdge(
                source=e.source,
                target=e.target,
                locations=_locations(module_paths.get(e.source), e.lines),
            )
            for e in sorted(closing or cycle.edges, key=lambda e: (e.source, e.target))
        )
        findings.append(
            StrictFinding(
                kind="cycle",
                modules=tuple(sorted(cycle.modules)),
                edges=edges,
                detail=_cycle_detail(cycle, seen, old_cycles, edges),
            )
        )
    return findings, carried_cycles


def _cycle_is_carried(
    cycle: Cycle,
    seen: set[str],
    old_cycles: list[set[str]],
    old_cycle_edges: set[tuple[str, str]],
) -> bool:
    if len(cycle.modules) == 1:
        # A self-import is its own kind of cycle: carried only if the
        # baseline had that exact self-edge, not merely because the module
        # once sat in a larger cycle that has since been broken.
        [module] = cycle.modules
        return (module, module) in old_cycle_edges
    return bool(seen) and any(seen <= old for old in old_cycles)


def _new_violations(
    baseline_violations: list[Violation],
    current_violations: Iterable[Violation],
    module_paths: Mapping[str, str],
) -> tuple[list[StrictFinding], int]:
    """The current violations the baseline does not carry, and how many it does."""
    old_violations = {_violation_key(v) for v in baseline_violations}
    findings: list[StrictFinding] = []
    carried_violations = 0
    for v in current_violations:
        if _violation_key(v) in old_violations:
            carried_violations += 1
            continue
        edge = StrictEdge(
            source=v.source,
            target=v.target,
            locations=_locations(module_paths.get(v.source), v.lines),
        )
        findings.append(
            StrictFinding(
                kind="violation",
                modules=(v.source, v.target),
                edges=(edge,),
                detail=(
                    f"new layer violation ({v.rule.from_layer} -> {v.rule.to_layer}): "
                    f"{_edge_text(edge)}"
                ),
            )
        )
    return findings, carried_violations


def _cycle_detail(
    cycle: Cycle, seen: set[str], old_cycles: list[set[str]], edges: tuple[StrictEdge, ...]
) -> str:
    # A grown legacy tangle has hundreds of members; listing them buries the
    # cause. The finding's `modules` carries the full list either way.
    if len(cycle.modules) <= _MAX_EDGES_SHOWN:
        members = ", ".join(sorted(cycle.modules))
    else:
        members = f"{len(cycle.modules)} modules"
    if not seen:
        why = "a cycle among modules the baseline never saw"
    elif sum(1 for old in old_cycles if seen & old) > 1:
        why = "it merges cycles the baseline kept separate"
    else:
        outside = sorted(m for m in seen if not any(m in old for old in old_cycles))
        pulled = (
            ", ".join(outside)
            if len(outside) <= _MAX_EDGES_SHOWN
            else f"{len(outside)} acyclic modules"
        )
        why = f"it pulls {pulled} into a cycle" if outside else "a new cycle"
    shown = "; ".join(_edge_text(e) for e in edges[:_MAX_EDGES_SHOWN])
    more = f"; +{len(edges) - _MAX_EDGES_SHOWN} more" if len(edges) > _MAX_EDGES_SHOWN else ""
    return f"new import cycle ({why}): {members}. Closing edge(s): {shown}{more}"


def _edge_text(edge: StrictEdge) -> str:
    where = f" at {', '.join(edge.locations)}" if edge.locations else ""
    return f"{edge.source} -> {edge.target}{where}"


def _locations(path: str | None, lines: tuple[int, ...]) -> tuple[str, ...]:
    if path is None:
        return ()
    if not lines:
        return (path,)
    return tuple(f"{path}:{line}" for line in lines)


def _violation_key(v: Violation) -> tuple[str, str, str, str]:
    return (v.rule.from_layer, v.rule.to_layer, v.source, v.target)


class RulesFingerprint(BaseModel):
    """Canonical archy.yaml rules a findings-only baseline commits.

    Lists are sorted and de-duplicated so two equivalent configs write the
    same bytes. `required` entries are `(source, must_reach, within)` and
    omit `reason`, which does not change what the rule covers.
    """

    model_config = ConfigDict(frozen=True)

    forbid: tuple[tuple[str, str], ...] = ()
    layers: tuple[tuple[str, tuple[str, ...]], ...] = ()
    required: tuple[tuple[str, str, str | None], ...] = ()
    exclude: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()

    @classmethod
    def canonical(
        cls,
        *,
        forbid: Iterable[tuple[str, str]] = (),
        layers: Iterable[tuple[str, Iterable[str]]] = (),
        required: Iterable[tuple[str, str, str | None]] = (),
        exclude: Iterable[str] = (),
        roots: Iterable[str] = (),
    ) -> RulesFingerprint:
        merged: dict[str, set[str]] = {}
        for name, patterns in layers:
            merged.setdefault(name, set()).update(patterns)
        return cls(
            forbid=tuple(sorted(set(forbid))),
            layers=tuple((name, tuple(sorted(pats))) for name, pats in sorted(merged.items())),
            required=tuple(sorted(set(required), key=_required_key)),
            exclude=tuple(sorted(set(exclude))),
            roots=tuple(sorted(set(roots))),
        )


def weakened_rules(
    baseline: RulesFingerprint, current: RulesFingerprint, *, since: str
) -> tuple[str, ...]:
    """Rules `current` dropped or made weaker than `baseline`.

    An added forbid, layer, required contract, or root is not a weakening.
    A wider layer pattern or required `source` is not one either. A wider
    `must_reach` is, because more modules then count as satisfying it. An
    added `exclude` entry is too, because it drops directories out of the scan.
    """
    notes = [
        *_weakened_forbids(baseline, current, since),
        *_weakened_layers(baseline, current, since),
        *_weakened_required(baseline, current, since),
        *_added_excludes(baseline, current, since),
        *_removed_roots(baseline, current, since),
    ]
    return tuple(sorted(notes))


def _weakened_forbids(
    baseline: RulesFingerprint, current: RulesFingerprint, since: str
) -> list[str]:
    current_forbid = set(current.forbid)
    return [
        f"forbid {src} -> {dst} removed since {since}"
        for src, dst in baseline.forbid
        if (src, dst) not in current_forbid
    ]


def _weakened_layers(
    baseline: RulesFingerprint, current: RulesFingerprint, since: str
) -> list[str]:
    notes: list[str] = []
    current_layers = dict(current.layers)
    for name, patterns in baseline.layers:
        if name not in current_layers:
            notes.append(f"layer {name} removed since {since}")
            continue
        notes.extend(
            f"layer {name} pattern {pattern} narrowed since {since}"
            for pattern in patterns
            if not any(_pattern_covers(wider, pattern) for wider in current_layers[name])
        )
    return notes


def _weakened_required(
    baseline: RulesFingerprint, current: RulesFingerprint, since: str
) -> list[str]:
    notes: list[str] = []
    for contract in baseline.required:
        if any(_required_covers(other, contract) for other in current.required):
            continue
        source, must, within = contract
        scope = f" within {within}" if within is not None else ""
        notes.append(f"required {source}{scope} must reach {must} removed since {since}")
    return notes


def _added_excludes(baseline: RulesFingerprint, current: RulesFingerprint, since: str) -> list[str]:
    baseline_exclude = set(baseline.exclude)
    return [
        f"exclude {name} added since {since}"
        for name in current.exclude
        if name not in baseline_exclude
    ]


def _removed_roots(baseline: RulesFingerprint, current: RulesFingerprint, since: str) -> list[str]:
    current_roots = set(current.roots)
    return [
        f"root {name} removed since {since}" for name in baseline.roots if name not in current_roots
    ]


def _required_key(contract: tuple[str, str, str | None]) -> tuple[str, str, bool, str]:
    source, must, within = contract
    return (source, must, within is None, within or "")


def _required_covers(
    current: tuple[str, str, str | None], baseline: tuple[str, str, str | None]
) -> bool:
    """True when `current` obliges at least what `baseline` did.

    A wider source or `within` obliges more modules. A narrower `must_reach`
    is harder to satisfy, so it still enforces the old target.
    """
    c_source, c_must, c_within = current
    b_source, b_must, b_within = baseline
    return (
        _source_covers(c_source, b_source)
        and _pattern_covers(b_must, c_must)
        and _within_covers(c_within, b_within)
    )


def _source_covers(wider: str, narrower: str) -> bool:
    if wider == ENTRYPOINTS_SOURCE or narrower == ENTRYPOINTS_SOURCE:
        return wider == narrower
    return _pattern_covers(wider, narrower)


def _within_covers(wider: str | None, narrower: str | None) -> bool:
    # None means every derived entrypoint. A written `within` covers fewer.
    if wider is None:
        return True
    if narrower is None:
        return False
    return _pattern_covers(wider, narrower)


def _pattern_covers(wider: str, narrower: str) -> bool:
    """True when every qualname matching `narrower` also matches `wider`.

    The language is the dotted glob `load_config` accepts: a literal segment,
    `*` for one segment, `**` for zero or more.
    """
    if wider == narrower:
        return True
    return _segments_cover(tuple(wider.split(".")), tuple(narrower.split(".")))


def _segments_cover(wider: tuple[str, ...], narrower: tuple[str, ...]) -> bool:
    # `**` on the wide side consumes one narrow token and stays. A narrow `**`
    # is one token here because the wide `**` can match the whole run it
    # stands for; the tokens after it still have to line up.
    memo: dict[tuple[int, int], bool] = {}

    def cover(i: int, j: int) -> bool:
        key = (i, j)
        if key in memo:
            return memo[key]
        if j == len(narrower):
            result = all(part == "**" for part in wider[i:])
        elif i == len(wider):
            result = False
        elif wider[i] == "**":
            result = cover(i + 1, j) or cover(i, j + 1)
        elif narrower[j] == "**":
            result = False
        elif narrower[j] == "*":
            result = wider[i] == "*" and cover(i + 1, j + 1)
        else:
            result = wider[i] in ("*", narrower[j]) and cover(i + 1, j + 1)
        memo[key] = result
        return result

    return cover(0, 0)


def module_paths_for(graph, root: Path) -> dict[str, str]:
    """Each internal module's file, relative to `root` when it sits under it."""
    resolved = root.resolve()
    out: dict[str, str] = {}
    for node, data in graph.nodes(data=True):
        raw = data.get("path")
        if not raw:
            continue
        path = Path(raw).resolve()
        out[node] = str(path.relative_to(resolved)) if path.is_relative_to(resolved) else str(path)
    return out
