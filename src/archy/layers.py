"""Layer-rule governance over an import graph.

A YAML config declares named layers (groups of modules matched by dotted-name
globs) and forbidden inter-layer edges. `find_violations` walks the import
graph and returns every edge that crosses a forbidden boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import yaml


@dataclass(frozen=True)
class LayerSpec:
    name: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class ForbidRule:
    from_layer: str
    to_layer: str


@dataclass(frozen=True)
class LayerConfig:
    layers: tuple[LayerSpec, ...]
    forbid: tuple[ForbidRule, ...]
    exclude: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class Violation:
    rule: ForbidRule
    source: str
    target: str
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
    return LayerConfig(
        layers=tuple(layers),
        forbid=tuple(forbid),
        exclude=tuple(exclude),
        roots=tuple(roots),
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
        if src_raw not in known_layers:
            raise LayerConfigError(
                f"forbid `from` references unknown layer {src_raw!r} in {path}; "
                f"known layers: {sorted(known_layers)}"
            )
        if tgt_raw not in known_layers:
            raise LayerConfigError(
                f"forbid `to` references unknown layer {tgt_raw!r} in {path}; "
                f"known layers: {sorted(known_layers)}"
            )
        out.append(ForbidRule(from_layer=src_raw, to_layer=tgt_raw))
    return out


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
