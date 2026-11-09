"""Reading and validating `archy.yaml`: layers, forbid and required rules, scan options.

archy:owns        discover_config, load_config
"""

from __future__ import annotations

import difflib
import keyword
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import yaml

from archy.layers_match import is_floating_pattern
from archy.layers_models import (
    ENTRYPOINTS_SOURCE,
    ForbidRule,
    LayerConfig,
    LayerConfigError,
    LayerSpec,
    RequiredRule,
    SdpConfig,
)

# The keys `load_config` reads at each level, spelled out so a key it does NOT read
# is an error rather than a silent no-op (#525). `forbidden:` for `forbid:` used to
# print "No layer violations" and exit 0: a rule that cannot fire looks exactly like
# a clean codebase. Keep these in step with the readers below; a test loads every
# tracked archy.yaml, so a key added to a reader but not here fails there.
_TOP_LEVEL_KEYS = (
    "layers",
    "forbid",
    "required",
    "exclude",
    "roots",
    "sdp",
    "max_modules",
    "min_layers_present",
    "exclude_type_checking_imports",
)


_LAYER_KEYS = ("modules",)


_FORBID_KEYS = ("from", "to")


_REQUIRED_KEYS = ("source", "must_reach", "reason", "within")


_SDP_KEYS = ("enabled", "tolerance", "mode")


# `exclude:` entries are compared as whole directory names, so an entry with any of
# these can never match a directory and would read as a rule that silently does nothing.
_EXCLUDE_NOT_A_NAME = ("/", "\\", "*", "?", "[")


def _reject_unknown_keys(
    keys: Iterable[object], allowed: tuple[str, ...], where: str, path: Path
) -> None:
    """Raise when `keys` (a mapping's keys) holds one the reader never looks at (#525).

    Every unknown key is named, each with its closest allowed spelling when there
    is one, because the usual cause is a typo (`forbidden`, `module`, `enable`).
    A key with no close match (`reason` on a forbid entry) gets the allowed list
    instead, since that is a misunderstanding of the format rather than a slip.
    """
    unknown = [key for key in keys if key not in allowed]
    if not unknown:
        return
    described = []
    for key in unknown:
        close = difflib.get_close_matches(str(key), allowed, n=1)
        hint = f" (did you mean `{close[0]}`?)" if close else ""
        described.append(f"`{key}`{hint}")
    allowed_text = ", ".join(f"`{a}`" for a in allowed)
    noun = "key" if len(unknown) == 1 else "keys"
    raise LayerConfigError(
        f"unknown {noun} in {where} of {path}: {'; '.join(described)}. "
        f"Allowed keys: {allowed_text}."
    )


def _reject_path_like_excludes(exclude: list[str], path: Path) -> None:
    for entry in exclude:
        bad = next((ch for ch in _EXCLUDE_NOT_A_NAME if ch in entry), None)
        if bad is not None:
            raise LayerConfigError(
                f"`exclude` entry {entry!r} in {path} contains {bad!r}, but `exclude` "
                "matches whole directory names only (for example `generated`), never "
                "paths or globs, so this entry could never match."
            )


def load_config(path: Path) -> LayerConfig:
    if not path.exists():
        raise LayerConfigError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise LayerConfigError(f"could not parse YAML at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LayerConfigError(f"config root must be a mapping in {path}")
    _reject_unknown_keys(raw, _TOP_LEVEL_KEYS, "the top level", path)

    layers = _parse_layers(raw.get("layers", {}), path)
    forbid = _parse_forbid(raw.get("forbid", []), {layer.name for layer in layers}, path)
    required = _parse_required(raw.get("required", []), path)
    exclude = _parse_str_list(raw.get("exclude", []), "exclude", path)
    _reject_path_like_excludes(exclude, path)
    roots = _parse_str_list(raw.get("roots", []), "roots", path)
    sdp = _parse_sdp(raw.get("sdp"), path)
    max_modules = _parse_max_modules(raw.get("max_modules"), path)
    min_layers_present = _parse_min_layers_present(raw.get("min_layers_present"), len(layers), path)
    exclude_type_checking = _parse_optional_bool(
        raw.get("exclude_type_checking_imports"), "exclude_type_checking_imports", path
    )
    return LayerConfig(
        layers=tuple(layers),
        forbid=tuple(forbid),
        required=tuple(required),
        exclude=tuple(exclude),
        roots=tuple(roots),
        sdp=sdp,
        max_modules=max_modules,
        min_layers_present=min_layers_present,
        exclude_type_checking_imports=exclude_type_checking,
    )


def _parse_optional_bool(value: object, field: str, path: Path) -> bool | None:
    """Validate a boolean config knob. None when the key is absent.

    Only a real YAML boolean passes: `"yes"` and `1` are rejected rather than
    guessed at, so a typo cannot silently turn a gate on or off.
    """
    if value is None:
        return None
    if not isinstance(value, bool):
        raise LayerConfigError(f"`{field}` must be true or false in {path}, got {value!r}")
    return value


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


def _parse_layers(raw: object, path: Path) -> list[LayerSpec]:
    raw_layers = _as_str_dict(raw, "`layers`", path)
    out: list[LayerSpec] = []
    for name, body_raw in raw_layers.items():
        if not name:
            raise LayerConfigError(f"layer names must be non-empty strings in {path}")
        body = _as_str_dict(body_raw, f"layer {name!r} body", path)
        _reject_unknown_keys(body, _LAYER_KEYS, f"layer {name!r}", path)
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
    _validate_pattern(
        pattern, f"layer {layer_name!r} has an invalid module pattern", path, allow_floating=True
    )


def _validate_pattern(
    pattern: str, prefix: str, path: Path, *, allow_floating: bool = False
) -> None:
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

    `allow_floating` admits one more shape, for `layers:` only: a leading `**.`
    followed by a real package name (`**.services.**`, "a `services` package
    wherever it sits"). It has no root package for the contracts fallback to
    derive, so that fallback refuses it with its own message instead.
    """
    segments = pattern.split(".")
    where = f"{prefix} {pattern!r} in {path}: "
    if any(seg == "" for seg in segments):
        raise LayerConfigError(
            f"{where}empty path segment (no leading, trailing, or doubled dots)."
        )
    if allow_floating and is_floating_pattern(pattern):
        if len(segments) < 2 or not _is_package_segment(segments[1]):
            raise LayerConfigError(
                f"{where}a leading '**' must be followed by a Python package name "
                '(e.g. "**.services.**").'
            )
        segments = segments[1:]
    else:
        _check_root_segment(segments[0], where)
    for seg in segments[1:]:
        if seg not in ("*", "**") and not _is_package_segment(seg):
            raise LayerConfigError(
                f"{where}segment {seg!r} must be a package name, '*' (one segment), "
                "or '**' (zero or more segments)."
            )


def _check_root_segment(segment: str, where: str) -> None:
    # A package-name segment must be an identifier that is not a Python keyword:
    # no importable package is named `import`/`class`, so accepting one here
    # would just defer the failure to a cryptic import-linter error instead of
    # the clean message this validation exists to give.
    if not _is_package_segment(segment):
        raise LayerConfigError(
            f"{where}must start with a Python package name, not {segment!r} "
            '(e.g. "myapp.domain.**", not "**").'
        )


def _is_package_segment(segment: str) -> bool:
    return segment.isidentifier() and not keyword.iskeyword(segment)


def _require_str_keys(
    entry: dict[str, object], keys: tuple[str, ...], kind: str, path: Path
) -> list[str]:
    """Check that `keys` are present on `entry` and are strings, and return them.

    Both `forbid` and `required` entries had their own copy of this, differing
    only in the key names and the message. The file already extracts this class
    of validation (`_parse_non_negative_int`, `_check_known_layer`), so a third
    inline copy was the odd one out. Returns the values in `keys` order so the
    caller does not re-index what was just validated.
    """
    for key in keys:
        if key not in entry:
            raise LayerConfigError(f"{kind} entry is missing required key {key!r} in {path}")
    values = [entry[key] for key in keys]
    if not all(isinstance(value, str) for value in values):
        named = "/".join(f"`{key}`" for key in keys)
        raise LayerConfigError(f"{kind} {named} values must be strings in {path}")
    return cast("list[str]", values)


def _parse_forbid(raw: object, known_layers: set[str], path: Path) -> list[ForbidRule]:
    if not isinstance(raw, list):
        raise LayerConfigError(f"`forbid` must be a list in {path}")
    out: list[ForbidRule] = []
    for index, entry_raw in enumerate(raw):
        entry = _as_str_dict(entry_raw, "forbid entry", path)
        _reject_unknown_keys(entry, _FORBID_KEYS, f"forbid entry {index}", path)
        src_raw, tgt_raw = _require_str_keys(entry, ("from", "to"), "forbid", path)
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
    return [_parse_required_entry(entry_raw, index, path) for index, entry_raw in enumerate(raw)]


def _parse_required_entry(entry_raw: object, index: int, path: Path) -> RequiredRule:
    entry = _as_str_dict(entry_raw, "required entry", path)
    _reject_unknown_keys(entry, _REQUIRED_KEYS, f"required entry {index}", path)
    source, must_reach = _require_str_keys(entry, ("source", "must_reach"), "required", path)
    reason = entry.get("reason", "")
    if not isinstance(reason, str):
        raise LayerConfigError(f"required `reason` must be a string in {path}")
    within = entry.get("within")
    if within is not None and not isinstance(within, str):
        raise LayerConfigError(f"required `within` must be a string in {path}")
    _validate_required_scope(source, within, path)
    _validate_pattern(must_reach, "required rule has an invalid `must_reach` pattern", path)
    return RequiredRule(source=source, must_reach=must_reach, reason=reason, within=within)


def _validate_required_scope(source: str, within: str | None, path: Path) -> None:
    if source == ENTRYPOINTS_SOURCE:
        if within is not None:
            _validate_pattern(within, "required rule has an invalid `within` pattern", path)
        return
    if within is not None:
        # Accepting it would make `within` a second spelling of an
        # intersection the `source` glob already expresses, and a reader
        # could not tell which of the two the author meant to bind.
        raise LayerConfigError(
            f"required `within` only applies to `source: {ENTRYPOINTS_SOURCE!r}` in "
            f"{path}; narrow a written pattern by editing `source` instead."
        )
    _validate_pattern(source, "required rule has an invalid `source` pattern", path)


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
    _reject_unknown_keys(body, _SDP_KEYS, "`sdp`", path)
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
