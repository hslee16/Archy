"""Dotted-name glob matching: which layer a module belongs to.

archy:owns        is_floating_pattern, match_layer
archy:mirrored-by _compile_pattern -> archy.layers, bench.q1b_layers_check,
                  is_floating_pattern -> archy.contracts, archy.layers,
                  archy.layers_check, archy.layers_config, match_layer -> archy.diff,
                  archy.layers, archy.layers_check
"""

from __future__ import annotations

import re

from archy.layers_models import LayerConfigError, LayerSpec


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


_FLOATING_PREFIX = "**."


def is_floating_pattern(pattern: str) -> bool:
    """True for a pattern that starts with `**.`: it has no fixed root package."""
    return pattern.startswith(_FLOATING_PREFIX)


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
    if is_floating_pattern(pattern):
        # Leading `**.` is zero or more WHOLE segments: `**.services.**` matches
        # `services` and `app.services.x` but not `myservices.x`.
        parts.append(r"(?:.*\.)?")
        i = len(_FLOATING_PREFIX)
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
