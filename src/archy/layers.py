"""Layer-rule governance over an import graph.

A YAML config declares named layers (groups of modules matched by dotted-name
globs) and forbidden inter-layer edges. `find_violations` walks the import
graph and returns every edge that crosses a forbidden boundary.
"""

from __future__ import annotations

import re
from pathlib import Path

import networkx as nx
import yaml
from pydantic import BaseModel, ConfigDict


class LayerSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    patterns: tuple[str, ...]


class ForbidRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_layer: str
    to_layer: str


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
    exclude: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()
    sdp: SdpConfig = SdpConfig()


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
    exclude = _parse_str_list(raw.get("exclude", []), "exclude", path)
    roots = _parse_str_list(raw.get("roots", []), "roots", path)
    sdp = _parse_sdp(raw.get("sdp"), path)
    return LayerConfig(
        layers=tuple(layers),
        forbid=tuple(forbid),
        exclude=tuple(exclude),
        roots=tuple(roots),
        sdp=sdp,
    )


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

    Layers are required to be disjoint (enforced at load_config) so the
    walk below collects all matches and is expected to find at most one.
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
        out.append(LayerSpec(name=name, patterns=modules))
    return out


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
    if mode not in ("error", "warn"):
        raise LayerConfigError(
            f"`sdp.mode` must be 'error' or 'warn' in {path} (got {mode!r})"
        )
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
