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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any  # noqa: TID251  # phase B follow-up types compute_diff outputs

from pydantic import BaseModel, ConfigDict

from archy.cycles import find_cycles
from archy.layers import (
    LayerConfig,
    discover_config,
    find_violations,
    load_config,
)
from archy.score import Score, compute_score


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: Score
    cycles: tuple[dict[str, Any], ...]
    violations: tuple[dict[str, Any], ...]


def take_snapshot(graph, config_path: Path | None = None) -> Snapshot:
    """Capture score, cycles, and layer violations from a single graph build."""
    score = compute_score(graph)
    cycles = tuple(_cycle_to_dict(c) for c in find_cycles(graph, min_size=2))
    violations: tuple[dict[str, Any], ...] = ()
    config = _load_config_if_present(config_path)
    if config is not None:
        violations = tuple(_violation_to_dict(v) for v in find_violations(graph, config))
    return Snapshot(score=score, cycles=cycles, violations=violations)


def snapshot_to_dict(snap: Snapshot) -> dict[str, Any]:
    return {
        "score": _score_to_dict(snap.score),
        "cycles": list(snap.cycles),
        "violations": list(snap.violations),
    }


def write_snapshot(snap: Snapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot_to_dict(snap), indent=2, sort_keys=True))


def read_snapshot(path: Path) -> Snapshot | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return _snapshot_from_dict(payload)


def compute_diff(baseline: Snapshot, current: Snapshot) -> dict[str, Any]:
    """Compute per-component score deltas plus added/resolved cycles & violations.

    Cycle identity = the frozenset of member modules; violation identity =
    (rule.from, rule.to, source, target). These mean a cycle that gains an
    extra module reads as "resolved + added" rather than "modified", which
    is fine for a first-pass agent signal.
    """
    return {
        "score_delta": _score_delta(baseline.score, current.score),
        "cycles": _set_diff(
            baseline.cycles,
            current.cycles,
            key=lambda c: frozenset(c["modules"]),
        ),
        "violations": _set_diff(
            baseline.violations,
            current.violations,
            key=lambda v: (v["rule"]["from"], v["rule"]["to"], v["source"], v["target"]),
        ),
    }


# --- internals ----------------------------------------------------------------


def _load_config_if_present(config_path: Path | None) -> LayerConfig | None:
    # `take_snapshot` is called against a graph the caller already built with
    # whatever config they want. Re-discovering the config here is purely for
    # finding violations; if there's no archy.yaml, just skip the violations
    # section rather than failing the whole snapshot.
    if config_path is None:
        return None
    return load_config(config_path)


def discover_config_for(path: Path) -> Path | None:
    return discover_config(path)


def _cycle_to_dict(cycle) -> dict[str, Any]:
    return {
        "modules": list(cycle.modules),
        "edges": [
            {"source": e.source, "target": e.target, "lines": list(e.lines)} for e in cycle.edges
        ],
    }


def _violation_to_dict(v) -> dict[str, Any]:
    return {
        "rule": {"from": v.rule.from_layer, "to": v.rule.to_layer},
        "source": v.source,
        "target": v.target,
        "lines": list(v.lines),
    }


def _score_to_dict(s: Score) -> dict[str, Any]:
    return {
        "overall": s.overall,
        "components": {
            "modularity": s.modularity,
            "acyclicity": s.acyclicity,
            "depth": s.depth,
            "equality": s.equality,
        },
        "inputs": s.inputs.model_dump(),
    }


def _snapshot_from_dict(payload: dict[str, Any]) -> Snapshot:
    from archy.score import ScoreInputs

    score_dict = payload["score"]
    components = score_dict["components"]
    raw_inputs = dict(score_dict["inputs"])
    # tangle_ratio added post-tangle-ratio rollout; default for old snapshots.
    raw_inputs.setdefault("tangle_ratio", 0.0)
    inputs = ScoreInputs(**raw_inputs)
    score = Score(
        overall=score_dict["overall"],
        modularity=components["modularity"],
        acyclicity=components["acyclicity"],
        depth=components["depth"],
        equality=components["equality"],
        inputs=inputs,
    )
    return Snapshot(
        score=score,
        cycles=tuple(payload.get("cycles", ())),
        violations=tuple(payload.get("violations", ())),
    )


def _score_delta(baseline: Score, current: Score) -> dict[str, float]:
    return {
        "overall": current.overall - baseline.overall,
        "modularity": current.modularity - baseline.modularity,
        "acyclicity": current.acyclicity - baseline.acyclicity,
        "depth": current.depth - baseline.depth,
        "equality": current.equality - baseline.equality,
    }


def _set_diff(
    baseline: tuple[dict[str, Any], ...],
    current: tuple[dict[str, Any], ...],
    *,
    key,
) -> dict[str, list[dict[str, Any]]]:
    baseline_keys = {key(item) for item in baseline}
    current_keys = {key(item) for item in current}
    return {
        "added": [item for item in current if key(item) not in baseline_keys],
        "resolved": [item for item in baseline if key(item) not in current_keys],
    }
