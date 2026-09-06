"""Snapshot + diff of archy's analysis surface for the agent feedback loop.

A "snapshot" captures the score, cycle list, and layer-violation list at
a point in time. `compute_diff` then compares two snapshots and reports
deltas: which cycles or violations appeared, which got resolved, and how
each score component moved. This is the missing primitive that turns
the score from a number into something an agent can act on between
edits.

Snapshots live in `.archy/baseline.json` by default (one per project,
overwritten each capture). Sentrux uses an in-process `session_start`
baseline; archy uses a file so the loop can survive process restarts
and so the baseline shape is the same one the CLI emits.

archy:owns        CycleSetDiff, DiffReport, DiffSummary, DiffSummaryItem,
                  ReachViolationSetDiff, ScoreDelta, SdpViolationSetDiff, Snapshot,
                  ViolationSetDiff, compute_diff, read_snapshot, snapshot_to_dict,
                  take_snapshot, write_snapshot
archy:mirrored-by DiffReport -> archy.cli, archy.diff_summary, archy.mcp,
                  DiffSummary -> archy.cli, archy.diff_summary, archy.simulate,
                  compute_diff -> archy.cli, archy.mcp, archy.simulate,
                  bench.delta_direction, bench.simulate_oracle,
                  read_snapshot -> archy.cli, archy.mcp, take_snapshot -> archy.cli,
                  archy.mcp, archy.simulate, bench.delta_direction,
                  bench.simulate_oracle, write_snapshot -> archy.cli, archy.mcp
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from archy.cycles import Cycle, CycleEdge, find_cycles
from archy.layers import (
    ForbidRule,
    LayerConfig,
    ReachViolation,
    RequiredRule,
    SdpViolation,
    Violation,
    find_reach_violations,
    find_sdp_violations,
    find_violations,
    load_config,
)
from archy.score import Score, ScoreInputs, compute_complexity, compute_score


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: Score
    cycles: tuple[Cycle, ...]
    violations: tuple[Violation, ...]
    sdp_violations: tuple[SdpViolation, ...] = ()
    required_violations: tuple[ReachViolation, ...] = ()


class ScoreDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    overall: float
    modularity: float
    acyclicity: float
    depth: float
    equality: float
    complexity: float


class CycleSetDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    added: tuple[Cycle, ...] = ()
    resolved: tuple[Cycle, ...] = ()


class ViolationSetDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    added: tuple[Violation, ...] = ()
    resolved: tuple[Violation, ...] = ()


class SdpViolationSetDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    added: tuple[SdpViolation, ...] = ()
    resolved: tuple[SdpViolation, ...] = ()


class ReachViolationSetDiff(BaseModel):
    """Required-reach failures that appeared or were fixed since the baseline.

    This is the ratchet the feature is actually for. Nothing static derives
    "these entrypoints need the model registry" on its own; once a human writes
    the rule, this is what stops the next edit from quietly undoing it. An
    `added` entry here typically means a bootstrap import was deleted as unused,
    which is exactly what it looks like to a linter and to an agent.
    """

    model_config = ConfigDict(frozen=True)

    added: tuple[ReachViolation, ...] = ()
    resolved: tuple[ReachViolation, ...] = ()


class DiffSummaryItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    risk: float
    modules: tuple[str, ...]
    description: str
    # `description` states the delta ("new cycle: a, b"); `prompt` reframes
    # it as the judgment question a reviewer should answer ("...intended, or
    # should an edge be inverted?"). Numbers don't tell a reviewer what to
    # decide; the question does.
    prompt: str


class DiffSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    headline: str
    top_regressions: tuple[DiffSummaryItem, ...]
    top_improvements: tuple[DiffSummaryItem, ...]


class DiffReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    score_delta: ScoreDelta
    cycles: CycleSetDiff
    violations: ViolationSetDiff
    sdp_violations: SdpViolationSetDiff = SdpViolationSetDiff()
    required_violations: ReachViolationSetDiff = ReachViolationSetDiff()
    # Populated by callers via `diff_summary.summarize_diff(report, graph)`;
    # left None for the pure `compute_diff` path so the function stays
    # graph-free.
    summary: DiffSummary | None = None


def take_snapshot(graph, config_path: Path | None = None, *, reach_graph=None) -> Snapshot:
    """Capture score, cycles, layer, SDP, and required-reach violations from a graph.

    `graph` is expected to be internal-only: that is what `compute_score` and
    `find_cycles` want, and every caller strips external nodes before calling.

    `reach_graph` exists because required-reach rules are the one check that
    needs those external nodes. `must_reach: sqlalchemy` is a legitimate rule,
    and against an internal-only graph the target matches nothing, so the rule
    reports as dead. That is not a cosmetic difference: `archy check` (which
    keeps externals) called it satisfied while `snapshot` called it dead on the
    same tree, and because the false "dead rule" appeared on BOTH sides of a
    diff, deleting the bootstrap import could never surface as a regression.
    Pass the graph that still has external nodes; defaults to `graph`, which
    keeps the old behavior for callers that have no externals to give.
    """
    score = compute_score(graph)
    cycles = tuple(find_cycles(graph, min_size=2))
    violations: tuple[Violation, ...] = ()
    sdp_violations: tuple[SdpViolation, ...] = ()
    reach_violations: tuple[ReachViolation, ...] = ()
    config = _load_config_if_present(config_path)
    if config is not None:
        violations = tuple(find_violations(graph, config))
        reach_violations = tuple(
            find_reach_violations(graph if reach_graph is None else reach_graph, config)
        )
        if config.sdp.enabled:
            sdp_violations = tuple(find_sdp_violations(graph, tolerance=config.sdp.tolerance))
    return Snapshot(
        score=score,
        cycles=cycles,
        violations=violations,
        sdp_violations=sdp_violations,
        required_violations=reach_violations,
    )


def snapshot_to_dict(snap: Snapshot) -> dict[str, object]:
    """Serialize to the legacy JSON wire shape (rule.from/to, score.components).

    We hand-build this rather than using `model_dump()` because the legacy
    shape predates pydantic and existing `.archy/baseline.json` files on
    disk depend on it.
    """
    return {
        "score": _score_to_dict(snap.score),
        "cycles": [_cycle_to_dict(c) for c in snap.cycles],
        "violations": [_violation_to_dict(v) for v in snap.violations],
        "sdp_violations": [_sdp_violation_to_dict(v) for v in snap.sdp_violations],
        "required_violations": [_reach_violation_to_dict(v) for v in snap.required_violations],
    }


def write_snapshot(snap: Snapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot_to_dict(snap), indent=2, sort_keys=True))


def read_snapshot(path: Path) -> Snapshot | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return _snapshot_from_dict(payload)


def compute_diff(baseline: Snapshot, current: Snapshot) -> DiffReport:
    """Compute per-component score deltas plus added/resolved cycles & violations.

    Cycle identity = the frozenset of member modules; violation identity =
    (rule.from_layer, rule.to_layer, source, target). These mean a cycle
    that gains an extra module reads as "resolved + added" rather than
    "modified", which is fine for a first-pass agent signal.
    """
    return DiffReport(
        score_delta=_score_delta(baseline.score, current.score),
        cycles=_cycle_set_diff(baseline.cycles, current.cycles),
        violations=_violation_set_diff(baseline.violations, current.violations),
        sdp_violations=_sdp_violation_set_diff(baseline.sdp_violations, current.sdp_violations),
        required_violations=_reach_violation_set_diff(
            baseline.required_violations, current.required_violations
        ),
    )


# --- internals ----------------------------------------------------------------


def _load_config_if_present(config_path: Path | None) -> LayerConfig | None:
    # `take_snapshot` is called against a graph the caller already built with
    # whatever config they want. Re-discovering the config here is purely for
    # finding violations; if there's no archy.yaml, just skip the violations
    # section rather than failing the whole snapshot.
    if config_path is None:
        return None
    return load_config(config_path)


def _cycle_to_dict(cycle: Cycle) -> dict[str, object]:
    return {
        "modules": list(cycle.modules),
        "edges": [
            {"source": e.source, "target": e.target, "lines": list(e.lines)} for e in cycle.edges
        ],
    }


def _violation_to_dict(v: Violation) -> dict[str, object]:
    return {
        "rule": {"from": v.rule.from_layer, "to": v.rule.to_layer},
        "source": v.source,
        "target": v.target,
        "lines": list(v.lines),
    }


def _reach_violation_to_dict(v: ReachViolation) -> dict[str, object]:
    return {
        "rule": {
            "source": v.rule.source,
            "must_reach": v.rule.must_reach,
            "reason": v.rule.reason,
        },
        "module": v.module,
        "detail": v.detail,
    }


def _sdp_violation_to_dict(v: SdpViolation) -> dict[str, object]:
    return {
        "source": v.source,
        "target": v.target,
        "source_instability": v.source_instability,
        "target_instability": v.target_instability,
        "lines": list(v.lines),
    }


def _score_to_dict(s: Score) -> dict[str, object]:
    return {
        "overall": s.overall,
        "components": {
            "modularity": s.modularity,
            "acyclicity": s.acyclicity,
            "depth": s.depth,
            "equality": s.equality,
            "complexity": s.complexity,
        },
        "inputs": s.inputs.model_dump(),
    }


def _snapshot_from_dict(payload: dict[str, object]) -> Snapshot:
    score_dict = _expect_dict(payload["score"])
    components = _expect_dict(score_dict["components"])
    raw_inputs = dict(_expect_dict(score_dict["inputs"]))
    # tangle_ratio added post-tangle-ratio rollout; default for old snapshots.
    raw_inputs.setdefault("tangle_ratio", 0.0)
    # ScoreInputs.model_validate handles per-field type coercion and raises a
    # clear ValidationError if a key is missing or wrongly typed.
    inputs = ScoreInputs.model_validate(raw_inputs)
    # complexity (v0.20): re-derive from cc_mean / function_count on an old
    # snapshot rather than refusing to load. The components dict on a pre-v0.20
    # baseline.json doesn't carry the field; reconstructing it from inputs
    # keeps the diff loop working across the version boundary.
    if "complexity" in components:
        complexity = _expect_float(components["complexity"])
    else:
        complexity = compute_complexity(inputs.cc_mean, inputs.function_count)
    score = Score(
        overall=_expect_float(score_dict["overall"]),
        modularity=_expect_float(components["modularity"]),
        acyclicity=_expect_float(components["acyclicity"]),
        depth=_expect_float(components["depth"]),
        equality=_expect_float(components["equality"]),
        complexity=complexity,
        inputs=inputs,
    )
    cycles = tuple(
        _cycle_from_dict(_expect_dict(c)) for c in _expect_list(payload.get("cycles", []))
    )
    violations = tuple(
        _violation_from_dict(_expect_dict(v)) for v in _expect_list(payload.get("violations", []))
    )
    sdp_violations = tuple(
        _sdp_violation_from_dict(_expect_dict(v))
        for v in _expect_list(payload.get("sdp_violations", []))
    )
    # `required_violations` postdates the first baseline format; an older
    # `.archy/baseline.json` simply has none, which reads as "no required-reach
    # failures at baseline" and makes any current failure show up as `added`.
    # That is the safe direction: a stale baseline over-reports a regression
    # rather than hiding one.
    reach_violations = tuple(
        _reach_violation_from_dict(_expect_dict(v))
        for v in _expect_list(payload.get("required_violations", []))
    )
    return Snapshot(
        score=score,
        cycles=cycles,
        violations=violations,
        sdp_violations=sdp_violations,
        required_violations=reach_violations,
    )


def _cycle_from_dict(d: dict[str, object]) -> Cycle:
    edges = tuple(
        CycleEdge(
            source=str(_expect_dict(e)["source"]),
            target=str(_expect_dict(e)["target"]),
            lines=tuple(_expect_int(x) for x in _expect_list(_expect_dict(e)["lines"])),
        )
        for e in _expect_list(d["edges"])
    )
    return Cycle(modules=tuple(str(m) for m in _expect_list(d["modules"])), edges=edges)


def _violation_from_dict(d: dict[str, object]) -> Violation:
    rule = _expect_dict(d["rule"])
    return Violation(
        rule=ForbidRule(from_layer=str(rule["from"]), to_layer=str(rule["to"])),
        source=str(d["source"]),
        target=str(d["target"]),
        lines=tuple(_expect_int(x) for x in _expect_list(d["lines"])),
    )


def _reach_violation_from_dict(d: dict[str, object]) -> ReachViolation:
    rule = _expect_dict(d["rule"])
    module = d.get("module")
    return ReachViolation(
        rule=RequiredRule(
            source=str(rule["source"]),
            must_reach=str(rule["must_reach"]),
            reason=str(rule.get("reason", "")),
        ),
        module=None if module is None else str(module),
        detail=str(d["detail"]),
    )


def _sdp_violation_from_dict(d: dict[str, object]) -> SdpViolation:
    return SdpViolation(
        source=str(d["source"]),
        target=str(d["target"]),
        source_instability=_expect_float(d["source_instability"]),
        target_instability=_expect_float(d["target_instability"]),
        lines=tuple(_expect_int(x) for x in _expect_list(d["lines"])),
    )


def _score_delta(baseline: Score, current: Score) -> ScoreDelta:
    return ScoreDelta(
        overall=current.overall - baseline.overall,
        modularity=current.modularity - baseline.modularity,
        acyclicity=current.acyclicity - baseline.acyclicity,
        depth=current.depth - baseline.depth,
        equality=current.equality - baseline.equality,
        complexity=current.complexity - baseline.complexity,
    )


def _cycle_set_diff(baseline: tuple[Cycle, ...], current: tuple[Cycle, ...]) -> CycleSetDiff:
    baseline_keys = {frozenset(c.modules) for c in baseline}
    current_keys = {frozenset(c.modules) for c in current}
    return CycleSetDiff(
        added=tuple(c for c in current if frozenset(c.modules) not in baseline_keys),
        resolved=tuple(c for c in baseline if frozenset(c.modules) not in current_keys),
    )


def _violation_set_diff(
    baseline: tuple[Violation, ...], current: tuple[Violation, ...]
) -> ViolationSetDiff:
    def _key(v: Violation) -> tuple[str, str, str, str]:
        return (v.rule.from_layer, v.rule.to_layer, v.source, v.target)

    baseline_keys = {_key(v) for v in baseline}
    current_keys = {_key(v) for v in current}
    return ViolationSetDiff(
        added=tuple(v for v in current if _key(v) not in baseline_keys),
        resolved=tuple(v for v in baseline if _key(v) not in current_keys),
    )


def _reach_violation_set_diff(
    baseline: tuple[ReachViolation, ...], current: tuple[ReachViolation, ...]
) -> ReachViolationSetDiff:
    # Identity is (rule, offending module). `detail` is derived text, so keying
    # on it would make a reworded message read as resolved-plus-added.
    def _key(v: ReachViolation) -> tuple[str, str, str]:
        return (v.rule.source, v.rule.must_reach, v.module or "")

    baseline_keys = {_key(v) for v in baseline}
    current_keys = {_key(v) for v in current}
    return ReachViolationSetDiff(
        added=tuple(v for v in current if _key(v) not in baseline_keys),
        resolved=tuple(v for v in baseline if _key(v) not in current_keys),
    )


def _sdp_violation_set_diff(
    baseline: tuple[SdpViolation, ...], current: tuple[SdpViolation, ...]
) -> SdpViolationSetDiff:
    def _key(v: SdpViolation) -> tuple[str, str]:
        return (v.source, v.target)

    baseline_keys = {_key(v) for v in baseline}
    current_keys = {_key(v) for v in current}
    return SdpViolationSetDiff(
        added=tuple(v for v in current if _key(v) not in baseline_keys),
        resolved=tuple(v for v in baseline if _key(v) not in current_keys),
    )


def _expect_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"expected mapping, got {type(value).__name__}")
    return {str(k): v for k, v in value.items()}


def _expect_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"expected list, got {type(value).__name__}")
    return list(value)


def _expect_float(value: object) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"expected number, got {type(value).__name__}")
    return float(value)


def _expect_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"expected int, got {type(value).__name__}")
    return value
