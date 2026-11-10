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

archy:owns        CapturedSnapshot, CycleSetDiff, DiffReport, DiffSummary,
                  DiffSummaryItem, ReachViolationSetDiff, ScoreDelta,
                  SdpViolationSetDiff, Snapshot, ViolationSetDiff, compute_diff,
                  create_baseline_command, default_baseline_path,
                  missing_baseline_hint, read_snapshot, score_delta, snapshot_to_dict,
                  strict_baseline_problem, strict_verdict, take_snapshot, write_snapshot
archy:mirrored-by DiffReport -> archy.cli_render_history, archy.diff_summary,
                  archy.mcp_runs_check, archy.mcp_tools_check,
                  DiffSummary -> archy.cli_render_common, archy.diff_summary,
                  archy.simulate, ScoreDelta -> archy.cli_render_common,
                  archy.simulate, compute_diff -> archy.cli_history,
                  archy.mcp_runs_check, archy.simulate, bench.delta_direction,
                  bench.simulate_oracle, create_baseline_command -> archy.cli_history,
                  archy.mcp_runs_check, default_baseline_path -> archy.cli_history,
                  archy.mcp_runs_check, missing_baseline_hint -> archy.cli_history,
                  archy.mcp_runs_check, read_snapshot -> archy.cli_history,
                  archy.mcp_runs_check, strict_baseline_problem -> archy.cli_history,
                  archy.mcp_runs_check, strict_verdict -> archy.cli_history,
                  archy.mcp_runs_check, take_snapshot -> archy.cli_history,
                  archy.mcp_runs_check, archy.simulate, bench.delta_direction,
                  bench.simulate_oracle, write_snapshot -> archy.cli_history,
                  archy.mcp_runs_check
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable, Hashable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

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
    match_layer,
)
from archy.ratchet import (
    RulesFingerprint,
    StrictFinding,
    StrictVerdict,
    evaluate_strict,
    module_paths_for,
    weakened_rules,
)
from archy.score import Score, ScoreInputs, compute_complexity, compute_score


class Snapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    # None for a findings-only baseline (`archy snapshot --findings-only`),
    # which leaves the score out so regenerating a committed file diffs cleanly.
    score: Score | None
    cycles: tuple[Cycle, ...]
    violations: tuple[Violation, ...]
    sdp_violations: tuple[SdpViolation, ...] = ()
    required_violations: tuple[ReachViolation, ...] = ()
    # Every internal module at capture time. `diff --strict` needs it to tell
    # a new file joining a tangle (carried) from an existing module pulled
    # into one (new). Empty on snapshots written before it was recorded.
    modules: tuple[str, ...] = ()
    # True when read from a `--findings-only` file, which records no SDP
    # violations. Diffing SDP against it would call every current one new.
    findings_only: bool = False
    # Rules from archy.yaml at capture. Findings-only files record this so
    # `diff --strict` can fail when a later edit removes or narrows one.
    # None when no config was loaded, and on files written before it existed.
    rules: RulesFingerprint | None = None


class CapturedSnapshot(Snapshot):
    """A snapshot taken from a live graph, which always has a score. Only a
    baseline read back from disk can lack one."""

    score: Score


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

    # None when either side has no score (a findings-only baseline).
    score_delta: ScoreDelta | None
    cycles: CycleSetDiff
    violations: ViolationSetDiff
    sdp_violations: SdpViolationSetDiff = SdpViolationSetDiff()
    required_violations: ReachViolationSetDiff = ReachViolationSetDiff()
    # Set only by `--strict`: the no-new-cycles, no-new-violations verdict.
    strict: StrictVerdict | None = None
    # True when the baseline is a findings-only file: it has no score and no
    # SDP violations, so neither was compared (both read as absent, not zero).
    baseline_findings_only: bool = False
    # Populated by callers via `diff_summary.summarize_diff(report, graph)`;
    # left None for the pure `compute_diff` path so the function stays
    # graph-free.
    summary: DiffSummary | None = None


def take_snapshot(graph, config_path: Path | None = None, *, reach_graph=None) -> CapturedSnapshot:
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
    rules = None
    if config is not None:
        violations = tuple(find_violations(graph, config))
        reach_violations = tuple(
            find_reach_violations(graph if reach_graph is None else reach_graph, config)
        )
        if config.sdp.enabled:
            sdp_violations = tuple(find_sdp_violations(graph, tolerance=config.sdp.tolerance))
        rules = _rules_fingerprint(config)
    return CapturedSnapshot(
        score=score,
        cycles=cycles,
        violations=violations,
        sdp_violations=sdp_violations,
        required_violations=reach_violations,
        modules=tuple(sorted(n for n, d in graph.nodes(data=True) if not d.get("external"))),
        rules=rules,
    )


def snapshot_to_dict(snap: Snapshot, *, findings_only: bool = False) -> dict[str, object]:
    """Serialize to the legacy JSON wire shape (rule.from/to, score.components).

    We hand-build this rather than using `model_dump()` because the legacy
    shape predates pydantic and existing `.archy/baseline.json` files on
    disk depend on it.
    """
    payload: dict[str, object] = {
        "cycles": [_cycle_to_dict(c) for c in snap.cycles],
        "violations": [_violation_to_dict(v) for v in snap.violations],
        "sdp_violations": [_sdp_violation_to_dict(v) for v in snap.sdp_violations],
        "required_violations": [_reach_violation_to_dict(v) for v in snap.required_violations],
        "modules": list(snap.modules),
    }
    if findings_only:
        # A committed baseline is regenerated by hand and reviewed as a diff, so
        # it keeps only what identifies a finding: no score, no line numbers
        # (any edit above an import moves them), every list sorted. The rules
        # fingerprint is part of that finding: without it a deleted rule reads
        # as a clean diff.
        payload = _findings_only(payload)
        if snap.rules is not None:
            payload["rules"] = _rules_to_dict(snap.rules)
    elif snap.score is not None:
        payload["score"] = _score_to_dict(snap.score)
    return payload


def write_snapshot(snap: Snapshot, path: Path, *, findings_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot_to_dict(snap, findings_only=findings_only)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_snapshot(path: Path) -> Snapshot | None:
    """Load a snapshot, or None when `path` does not exist.

    JSON that is not a snapshot raises ValueError. The message names `path`
    and the reason, and tells the caller to re-run `archy snapshot`.
    """
    if not path.exists():
        return None
    parsed = _read_payload(path)
    try:
        return _snapshot_from_dict(parsed)
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise _unreadable_snapshot(path, _snapshot_reason(exc)) from exc


def _read_payload(path: Path) -> dict[str, object]:
    """The JSON object in `path`, or the ValueError that says why it is not one."""
    if path.is_dir():
        raise _unreadable_snapshot(path, "it is a directory, not a file")
    try:
        text = path.read_text()
    except UnicodeDecodeError as exc:
        raise _unreadable_snapshot(path, f"not valid UTF-8 ({exc.reason})") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _unreadable_snapshot(path, f"invalid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        got = type(payload).__name__
        raise _unreadable_snapshot(path, f"expected a JSON object, got {got}")
    return {str(key): value for key, value in payload.items()}


def _unreadable_snapshot(path: Path, reason: str) -> ValueError:
    return ValueError(f"{path} is not a readable archy snapshot; re-run `archy snapshot`: {reason}")


def _snapshot_reason(exc: BaseException) -> str:
    if isinstance(exc, KeyError):
        missing = exc.args[0] if exc.args else exc
        return f"missing {missing!r}"
    if isinstance(exc, ValidationError):
        return "score inputs do not match a snapshot"
    return str(exc)


def compute_diff(baseline: Snapshot, current: Snapshot) -> DiffReport:
    """Compute per-component score deltas plus added/resolved cycles & violations.

    Cycle identity = the frozenset of member modules; violation identity =
    (rule.from_layer, rule.to_layer, source, target). These mean a cycle
    that gains an extra module reads as "resolved + added" rather than
    "modified", which is fine for a first-pass agent signal.
    """
    return DiffReport(
        score_delta=(
            None
            if baseline.score is None or current.score is None
            else score_delta(baseline.score, current.score)
        ),
        cycles=_cycle_set_diff(baseline.cycles, current.cycles),
        violations=_violation_set_diff(baseline.violations, current.violations),
        sdp_violations=(
            SdpViolationSetDiff()
            if baseline.findings_only
            else _sdp_violation_set_diff(baseline.sdp_violations, current.sdp_violations)
        ),
        required_violations=_reach_violation_set_diff(
            baseline.required_violations, current.required_violations
        ),
        baseline_findings_only=baseline.findings_only,
    )


def default_baseline_path(root: Path) -> Path:
    """Where `archy snapshot` and the MCP `archy_snapshot` write by default."""
    return root / ".archy" / "baseline.json"


def strict_baseline_problem(root: Path, target: Path, *, suggested: Path) -> str | None:
    """Why `target` cannot be the `--strict` baseline, or None if it can.

    The default snapshot file is overwritten by every `archy snapshot`, and an
    agent is told to call `archy_snapshot` at the start of each task. Strict
    against it would grandfather a new cycle on the next snapshot, so it is
    refused even when named explicitly.
    """
    default = default_baseline_path(root)
    resolved = target.resolve()
    # Any `.archy/baseline.json`, not only this scan's: `archy snapshot .`
    # from a parent writes one too, and an agent's next snapshot rewrites it.
    same = resolved.name == default.name and resolved.parent.name == default.parent.name
    # `resolve()` misses a hard link and, on a case-insensitive filesystem, a
    # differently-cased spelling; `Path.samefile` compares the files themselves.
    if not same:
        try:
            same = target.exists() and default.exists() and target.samefile(default)
        except OSError:
            # An unreadable `.archy/` cannot be the baseline being read anyway.
            same = False
    if same:
        return (
            f"{target} is the file `archy snapshot` overwrites, so it cannot hold a "
            f"ratchet. Commit a separate baseline with "
            f"`{create_baseline_command(root, suggested)}` and pass that."
        )
    return None


def create_baseline_command(root: Path, target: Path) -> str:
    """The command that writes a committed baseline for the tree at `root`.

    Names `root` explicitly: run from elsewhere, a bare `archy snapshot` scans
    the current directory, misses an `archy.yaml` under `root`, and records a
    baseline that makes every existing violation read as new.
    """
    return (
        f"archy snapshot {shlex.quote(str(root))} --findings-only --out {shlex.quote(str(target))}"
    )


def missing_baseline_hint(root: Path, target: Path, *, committed: bool) -> str:
    """The fix for a missing baseline. A committed one is created with
    `--findings-only --out`; a bare `archy snapshot` would write elsewhere."""
    if committed:
        return f"no baseline at {target}; create it with `{create_baseline_command(root, target)}`."
    return f"no baseline at {target}; run `archy snapshot {shlex.quote(str(root))}` first."


def strict_verdict(
    baseline: Snapshot,
    current: Snapshot,
    *,
    graph,
    root: Path,
    target: Path,
    label: str,
    config_path: Path | None,
    location_root: Path,
) -> StrictVerdict:
    """The `--strict` ratchet verdict for `current` against a committed `baseline`.

    One entry point so the CLI and MCP surfaces cannot drift. `graph` is the
    one `current` was taken from; `location_root` is what `path:line`
    locations are relative to (the CLI's working directory, so they paste
    straight into `git blame`; MCP's `path`).
    """
    config = _load_config_if_present(config_path)
    ungoverned = _ungoverned_violations(baseline, graph, config) if config is not None else None
    verdict = evaluate_strict(
        baseline_label=label,
        baseline_modules=baseline.modules,
        baseline_cycles=baseline.cycles,
        baseline_violations=baseline.violations,
        current_cycles=current.cycles,
        current_violations=current.violations,
        module_paths=module_paths_for(graph, location_root),
        ungoverned=ungoverned,
        regenerate=create_baseline_command(root, target),
    )
    # A refused verdict already fails, and its reason is the module list or
    # the ungoverned-violation check. The fingerprint is the other hole: a
    # clean baseline, whose violation was never recorded, after the rule that
    # would have caught it was removed or narrowed.
    if verdict.refused is not None:
        return verdict
    if baseline.rules is None:
        return verdict.model_copy(
            update={
                "rules_hint": (
                    "baseline has no rules fingerprint; regenerate with "
                    f"`{create_baseline_command(root, target)}` to catch a weakened archy.yaml"
                )
            }
        )
    current_rules = _rules_fingerprint(config) if config is not None else RulesFingerprint()
    extra = tuple(
        StrictFinding(kind="rule", modules=(), edges=(), detail=detail)
        for detail in weakened_rules(baseline.rules, current_rules, since=label)
    )
    if not extra:
        return verdict
    return verdict.model_copy(update={"findings": (*verdict.findings, *extra)})


def _ungoverned_violations(baseline: Snapshot, graph, config: LayerConfig) -> tuple[Violation, ...]:
    """Baseline violations whose modules exist but that today's rules would not flag.

    Its layer patterns or forbid rule changed under it. Not a fix, since the
    import may still be there.
    """
    forbidden = {(r.from_layer, r.to_layer) for r in config.forbid}
    internal = {n for n, d in graph.nodes(data=True) if not d.get("external")}
    return tuple(
        v
        for v in baseline.violations
        if v.source in internal and v.target in internal and _rule_dropped(v, forbidden, config)
    )


def _rule_dropped(v: Violation, forbidden: set[tuple[str, str]], config: LayerConfig) -> bool:
    return (
        (v.rule.from_layer, v.rule.to_layer) not in forbidden
        or match_layer(v.source, config.layers) != v.rule.from_layer
        or match_layer(v.target, config.layers) != v.rule.to_layer
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
            {"source": e.source, "target": e.target, "lines": list(e.lines), "path": e.path}
            for e in cycle.edges
        ],
    }


def _violation_to_dict(v: Violation) -> dict[str, object]:
    return {
        "rule": {"from": v.rule.from_layer, "to": v.rule.to_layer},
        "source": v.source,
        "target": v.target,
        "lines": list(v.lines),
        "path": v.path,
    }


def _reach_violation_to_dict(v: ReachViolation) -> dict[str, object]:
    return {
        "rule": {
            "source": v.rule.source,
            "must_reach": v.rule.must_reach,
            "reason": v.rule.reason,
            "within": v.rule.within,
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
        "path": v.path,
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


def _recognized_snapshot_keys() -> frozenset[str]:
    """Keys `snapshot_to_dict` can write. `score` is omitted when it is None."""
    bare = Snapshot(score=None, cycles=(), violations=())
    keys = set(snapshot_to_dict(bare)) | set(snapshot_to_dict(bare, findings_only=True))
    keys.add("score")
    return frozenset(keys)


def _snapshot_from_dict(payload: dict[str, object]) -> Snapshot:
    if _recognized_snapshot_keys().isdisjoint(payload):
        raise ValueError("missing snapshot fields")
    raw_score = payload.get("score")
    score = None if raw_score is None else _score_from_dict(_expect_dict(raw_score))
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
    raw_rules = payload.get("rules")
    rules = None if raw_rules is None else _rules_from_dict(_expect_dict(raw_rules))
    return Snapshot(
        score=score,
        cycles=cycles,
        violations=violations,
        sdp_violations=sdp_violations,
        required_violations=reach_violations,
        modules=tuple(str(m) for m in _expect_list(payload.get("modules", []))),
        findings_only=payload.get("findings_only") is True,
        rules=rules,
    )


def _findings_only(payload: dict[str, object]) -> dict[str, object]:
    # `lines` and `path` are where a finding sits, not what it is: both are left
    # out so a committed baseline does not churn when code moves or when a file
    # written before `path` existed is compared with one written after (#522).
    location_keys = ("lines", "path")

    def strip(item: object) -> object:
        if isinstance(item, dict):
            return {k: strip(v) for k, v in item.items() if k not in location_keys}
        if isinstance(item, list):
            return [strip(v) for v in item]
        return item

    def ordered(items: object) -> list[object]:
        return sorted((strip(i) for i in _expect_list(items)), key=_canonical)

    # SDP violations carry instability ratios that move whenever any module's
    # fan-in or fan-out does, so they would churn the committed file on edits
    # that change no finding. `--strict` does not read them.
    out: dict[str, object] = {
        key: ordered(value)
        for key, value in payload.items()
        if key not in ("modules", "cycles", "sdp_violations")
    }
    # `detail` on a required-reach row is prose that can embed an absolute
    # path or a derived reason; rows are keyed without it, so drop it here.
    if "required_violations" in out:
        out["required_violations"] = _required_rows_without_detail(out["required_violations"])
    out["findings_only"] = True
    out["modules"] = sorted(str(m) for m in _expect_list(payload["modules"]))
    out["cycles"] = sorted(
        (
            {
                "modules": sorted(str(m) for m in _expect_list(c["modules"])),
                "edges": sorted(_expect_list(c["edges"]), key=_canonical),
            }
            for c in (_expect_dict(strip(c)) for c in _expect_list(payload["cycles"]))
        ),
        key=_canonical,
    )
    return out


def _required_rows_without_detail(rows: object) -> list[dict[str, object]]:
    return sorted(
        (
            {k: v for k, v in _expect_dict(row).items() if k != "detail"}
            for row in _expect_list(rows)
        ),
        key=_canonical,
    )


def _rules_fingerprint(config: LayerConfig) -> RulesFingerprint:
    """Canonical rules for a findings-only baseline when an archy.yaml was loaded."""
    return RulesFingerprint.canonical(
        forbid=((rule.from_layer, rule.to_layer) for rule in config.forbid),
        layers=((layer.name, layer.patterns) for layer in config.layers),
        required=((rule.source, rule.must_reach, rule.within) for rule in config.required),
        exclude=config.exclude,
        roots=config.roots,
    )


def _rules_to_dict(rules: RulesFingerprint) -> dict[str, object]:
    return {
        "exclude": list(rules.exclude),
        "forbid": [[src, dst] for src, dst in rules.forbid],
        "layers": [[name, list(patterns)] for name, patterns in rules.layers],
        "required": [
            {"must_reach": must, "source": source, "within": within}
            for source, must, within in rules.required
        ],
        "roots": list(rules.roots),
    }


def _rules_from_dict(raw: dict[str, object]) -> RulesFingerprint:
    return RulesFingerprint.canonical(
        forbid=_forbid_from_raw(raw.get("forbid", [])),
        layers=_layers_from_raw(raw.get("layers", [])),
        required=_required_from_raw(raw.get("required", [])),
        exclude=_string_tuple(raw.get("exclude", []), "exclude"),
        roots=_string_tuple(raw.get("roots", []), "roots"),
    )


def _forbid_from_raw(raw: object) -> list[tuple[str, str]]:
    forbid: list[tuple[str, str]] = []
    for item in _expect_list(raw):
        pair = _expect_list(item)
        if len(pair) != 2 or not all(isinstance(part, str) for part in pair):
            raise ValueError(f"forbid pair must be two strings, got {item!r}")
        forbid.append((str(pair[0]), str(pair[1])))
    return forbid


def _layers_from_raw(raw: object) -> list[tuple[str, tuple[str, ...]]]:
    layers: list[tuple[str, tuple[str, ...]]] = []
    for item in _expect_list(raw):
        row = _expect_list(item)
        if len(row) != 2 or not isinstance(row[0], str):
            raise ValueError(f"layer entry must be [name, patterns], got {item!r}")
        patterns = _expect_list(row[1])
        if not all(isinstance(pattern, str) for pattern in patterns):
            raise ValueError(f"layer patterns must be strings, got {item!r}")
        layers.append((row[0], tuple(str(pattern) for pattern in patterns)))
    return layers


def _required_from_raw(raw: object) -> list[tuple[str, str, str | None]]:
    required: list[tuple[str, str, str | None]] = []
    for item in _expect_list(raw):
        entry = _expect_dict(item)
        source = entry.get("source")
        must = entry.get("must_reach")
        within = entry.get("within")
        if not isinstance(source, str) or not isinstance(must, str):
            raise ValueError(f"required entry needs string source and must_reach, got {item!r}")
        if within is not None and not isinstance(within, str):
            raise ValueError(f"required within must be a string or null, got {within!r}")
        required.append((source, must, within))
    return required


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    items = _expect_list(value)
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return tuple(str(item) for item in items)


def _canonical(item: object) -> str:
    return json.dumps(item, sort_keys=True)


def _score_from_dict(score_dict: dict[str, object]) -> Score:
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
    return Score(
        overall=_expect_float(score_dict["overall"]),
        modularity=_expect_float(components["modularity"]),
        acyclicity=_expect_float(components["acyclicity"]),
        depth=_expect_float(components["depth"]),
        equality=_expect_float(components["equality"]),
        complexity=complexity,
        inputs=inputs,
    )


def _cycle_from_dict(d: dict[str, object]) -> Cycle:
    edges = tuple(
        CycleEdge(
            source=str(_expect_dict(e)["source"]),
            target=str(_expect_dict(e)["target"]),
            lines=tuple(_expect_int(x) for x in _expect_list(_expect_dict(e).get("lines", []))),
            path=str(_expect_dict(e).get("path", "")),
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
        lines=tuple(_expect_int(x) for x in _expect_list(d.get("lines", []))),
        path=str(d.get("path", "")),
    )


def _reach_violation_from_dict(d: dict[str, object]) -> ReachViolation:
    rule = _expect_dict(d["rule"])
    module = d.get("module")
    return ReachViolation(
        rule=RequiredRule(
            source=str(rule["source"]),
            must_reach=str(rule["must_reach"]),
            reason=str(rule.get("reason", "")),
            within=None if rule.get("within") is None else str(rule["within"]),
        ),
        module=None if module is None else str(module),
        detail=str(d.get("detail", "")),
    )


def _sdp_violation_from_dict(d: dict[str, object]) -> SdpViolation:
    return SdpViolation(
        source=str(d["source"]),
        target=str(d["target"]),
        source_instability=_expect_float(d["source_instability"]),
        target_instability=_expect_float(d["target_instability"]),
        lines=tuple(_expect_int(x) for x in _expect_list(d.get("lines", []))),
        path=str(d.get("path", "")),
    )


def score_delta(baseline: Score, current: Score) -> ScoreDelta:
    return ScoreDelta(
        overall=current.overall - baseline.overall,
        modularity=current.modularity - baseline.modularity,
        acyclicity=current.acyclicity - baseline.acyclicity,
        depth=current.depth - baseline.depth,
        equality=current.equality - baseline.equality,
        complexity=current.complexity - baseline.complexity,
    )


_T = TypeVar("_T")


def _added_resolved(
    baseline: tuple[_T, ...],
    current: tuple[_T, ...],
    key: Callable[[_T], Hashable],
    *,
    resolvable: Callable[[_T], bool] = lambda _: True,
) -> tuple[tuple[_T, ...], tuple[_T, ...]]:
    """Split two snapshots into (added, resolved) by identity ``key``.

    ``resolvable`` lets a caller veto a "resolved" row it cannot vouch for.
    """
    baseline_keys = {key(v) for v in baseline}
    current_keys = {key(v) for v in current}
    return (
        tuple(v for v in current if key(v) not in baseline_keys),
        tuple(v for v in baseline if key(v) not in current_keys and resolvable(v)),
    )


def _cycle_set_diff(baseline: tuple[Cycle, ...], current: tuple[Cycle, ...]) -> CycleSetDiff:
    added, resolved = _added_resolved(baseline, current, lambda c: frozenset(c.modules))
    return CycleSetDiff(added=added, resolved=resolved)


def _violation_set_diff(
    baseline: tuple[Violation, ...], current: tuple[Violation, ...]
) -> ViolationSetDiff:
    added, resolved = _added_resolved(
        baseline,
        current,
        lambda v: (v.rule.from_layer, v.rule.to_layer, v.source, v.target),
    )
    return ViolationSetDiff(added=added, resolved=resolved)


def _reach_violation_set_diff(
    baseline: tuple[ReachViolation, ...], current: tuple[ReachViolation, ...]
) -> ReachViolationSetDiff:
    # Identity is (rule, offending module). `detail` is derived text, so keying
    # on it would make a reworded message read as resolved-plus-added.
    def _key(v: ReachViolation) -> tuple[str, str, str, str]:
        return (v.rule.source, v.rule.within or "", v.rule.must_reach, v.module or "")

    # A rule that is dead NOW (one `module=None` row) evaluated no module, so
    # its baseline failures are unknown, not fixed. Reporting them as resolved
    # would tell the reader an unreadable pyproject.toml repaired the code. The
    # reverse needs no guard: a rule coming back to life reports its failures as
    # added, which over-reports and is the safe direction.
    dead_now = {_key(v)[:3] for v in current if v.module is None}
    added, resolved = _added_resolved(
        baseline, current, _key, resolvable=lambda v: _key(v)[:3] not in dead_now
    )
    return ReachViolationSetDiff(added=added, resolved=resolved)


def _sdp_violation_set_diff(
    baseline: tuple[SdpViolation, ...], current: tuple[SdpViolation, ...]
) -> SdpViolationSetDiff:
    added, resolved = _added_resolved(baseline, current, lambda v: (v.source, v.target))
    return SdpViolationSetDiff(added=added, resolved=resolved)


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
