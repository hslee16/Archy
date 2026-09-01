"""Derive a repository's own house style from its source.

Motivation: an agent editing an unfamiliar codebase spends most of its
deliberation on questions the code has already answered -- what do I call
a new class and where does it live, how many parallel surfaces must I
wire a new field through, does a new finding *fail* the build or only
warn. Measured over real agent transcripts working on this repository,
design deliberation dominated navigation by roughly 4:1, and the single
largest slice was re-litigating a binary (gate vs advisory) whose answer
was mechanically derivable from the source the whole time.

So this module answers those questions from the AST rather than from
prose. Four censuses, all derived, nothing hardcoded about any particular
project:

``naming``
    Class names clustered by CamelCase suffix and home module. A family
    like ``*Violation`` concentrated in one module is the answer to "what
    do I call a new one and where does it go".

``surfaces``
    Families of helpers that exist once per output surface (the
    ``_x_to_text`` / ``_x_to_json`` shape), plus names defined in more
    than one module. Both are "update these together or ship a
    half-wired feature" sets, which is this repo's most common
    historical defect and a generic one.

``gates``
    Every site where the program exits non-zero, with what controls it:
    a CLI flag, a config attribute, or nothing (hardcoded). This is the
    gate-vs-advisory question, answered as an inventory instead of an
    argument.

``models``
    Base-class and config census over the project's classes -- how many
    are pydantic, how many are frozen, and whether collection fields are
    written as tuples or lists.

Read-only and advisory by construction: it parses with stdlib ``ast``,
writes nothing, and has no failure mode that should stop a build. A file
that will not parse is counted in ``modules_unparsed`` and skipped, which
keeps a syntactically broken work-in-progress from turning a reporting
command into an error.

Implementation notes. ``ast`` rather than archy's tree-sitter walker
because the questions here are about *definitions* (class bases, keyword
arguments, decorator shapes, the statement nesting a ``sys.exit`` sits
in), and the stdlib grammar already models those exactly. Module
discovery is shared with the graph builder so the file set matches what
every other archy command sees.
"""

from __future__ import annotations

import ast
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, computed_field

from archy.graph import DEFAULT_IGNORED_DIRS, discover_modules

# A CamelCase name splits into an acronym run (`DSM`, `MCP`) or a single
# capitalized word. The trailing element is the family: `DSMDiff` -> `Diff`,
# `ReachViolation` -> `Violation`, `Hotspot` -> `Hotspot`.
_CAMEL_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*")

# Call/raise targets that end the process non-zero. `ctx.exit` and a bare
# `exit` are matched on their last dotted segment so any Click context
# variable name works, not just the conventional `ctx`.
_EXIT_CALLS = frozenset({"sys.exit", "os._exit", "exit"})
_EXIT_RAISES = frozenset({"SystemExit", "ClickException", "UsageError", "Abort", "Exit"})

# Base names that mark a class as a declared value type rather than a plain
# object. Matched on the last dotted segment, so `pydantic.BaseModel` and a
# re-exported `BaseModel` are the same family.
_VALUE_BASES = frozenset(
    {"BaseModel", "TypedDict", "NamedTuple", "Enum", "IntEnum", "StrEnum", "Protocol"}
)


class NamingFamily(BaseModel):
    """A cluster of class names sharing a CamelCase suffix.

    `home_module` is the module holding the most members, which is the
    answer to "where does a new one go"; `concentration` says how much to
    trust that answer.
    """

    model_config = ConfigDict(frozen=True)

    suffix: str
    count: int
    home_module: str
    home_count: int
    modules: tuple[str, ...]
    examples: tuple[str, ...]

    # `computed_field`, not a plain property: these payloads go over the MCP
    # wire via `model_dump()`, which silently drops properties. A consumer
    # reading `home_module` without `concentration` cannot tell a convention
    # from a coincidence.
    @computed_field
    @property
    def concentration(self) -> float:
        return self.home_count / self.count if self.count else 0.0


class SurfaceFamily(BaseModel):
    """A set of definitions that mirror each other and must move together.

    `kind='helper'`  -- one function per output surface in a single module
                        (`_x_to_text` / `_x_to_json`), `surfaces` are the
                        differing trailing segments.
    `kind='mirrored'` -- one name defined in several modules, `surfaces`
                        are those modules.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    stem: str
    module: str
    surfaces: tuple[str, ...]

    @computed_field
    @property
    def surface_count(self) -> int:
        return len(self.surfaces)


class Gate(BaseModel):
    """One site where the program exits non-zero.

    `control` is the lever: `flag:--strict` (a CLI option feeds the guard),
    `param:<name>` (a function argument does, with no flag found),
    `config:<attr>` (a config/result attribute does), or `hardcoded` (the
    exit is unconditional or guarded by something with no named lever).
    """

    model_config = ConfigDict(frozen=True)

    module: str
    function: str
    kind: str
    control: str
    is_command: bool

    @computed_field
    @property
    def optional(self) -> bool:
        """True when a caller can turn this gate off without editing code."""
        return self.control.startswith(("flag:", "param:", "config:"))


class ModelCensus(BaseModel):
    """Base-class and field-style census over every class in the project."""

    model_config = ConfigDict(frozen=True)

    total_classes: int
    value_classes: int
    frozen_classes: int
    base_counts: tuple[tuple[str, int], ...]
    config_flags: tuple[tuple[str, int], ...]
    tuple_fields: int
    list_fields: int

    @computed_field
    @property
    def dominant_base(self) -> str | None:
        return self.base_counts[0][0] if self.base_counts else None

    @computed_field
    @property
    def frozen_ratio(self) -> float:
        return self.frozen_classes / self.value_classes if self.value_classes else 0.0

    @computed_field
    @property
    def tuple_ratio(self) -> float:
        total = self.tuple_fields + self.list_fields
        return self.tuple_fields / total if total else 0.0


class ConventionsReport(BaseModel):
    """The whole derived house style for one project."""

    model_config = ConfigDict(frozen=True)

    root: str
    modules_scanned: int
    modules_unparsed: int
    naming: tuple[NamingFamily, ...]
    surfaces: tuple[SurfaceFamily, ...]
    gates: tuple[Gate, ...]
    models: ModelCensus

    @computed_field
    @property
    def gate_modules(self) -> tuple[str, ...]:
        """Modules that can end the process -- everything else is advisory."""
        return tuple(sorted({g.module for g in self.gates}))


def camel_suffix(name: str) -> str:
    """The trailing CamelCase segment of `name`, or `name` if it has one part."""
    parts = _CAMEL_PART.findall(name)
    return parts[-1] if parts else name


def _dotted(node: ast.expr) -> str:
    """Render `a.b.c` / `a` from an expression, or "" for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class _ModuleFacts:
    """Everything one parsed module contributes, collected in a single pass."""

    def __init__(self, qualname: str) -> None:
        self.qualname = qualname
        self.classes: list[ast.ClassDef] = []
        self.functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []


def _collect(tree: ast.Module, qualname: str) -> _ModuleFacts:
    facts = _ModuleFacts(qualname)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            facts.classes.append(node)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            facts.functions.append(node)
    return facts


def _naming_families(facts: Iterable[_ModuleFacts], *, min_count: int) -> tuple[NamingFamily, ...]:
    members: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for f in facts:
        for cls in f.classes:
            members[camel_suffix(cls.name)].append((cls.name, f.qualname))

    families: list[NamingFamily] = []
    for suffix, rows in members.items():
        if len(rows) < min_count:
            continue
        by_module = Counter(module for _, module in rows)
        home, home_count = by_module.most_common(1)[0]
        families.append(
            NamingFamily(
                suffix=suffix,
                count=len(rows),
                home_module=home,
                home_count=home_count,
                modules=tuple(sorted(by_module)),
                examples=tuple(sorted({name for name, _ in rows})[:4]),
            )
        )
    families.sort(key=lambda f: (-f.count, f.suffix))
    return tuple(families)


def _surface_families(
    facts: Iterable[_ModuleFacts], *, min_count: int
) -> tuple[SurfaceFamily, ...]:
    """Two mirror shapes: per-surface helpers, and one name in many modules.

    A helper family is keyed on the function name minus its final
    underscore segment, so `_hotspots_to_text` / `_hotspots_to_json` group
    under `_hotspots_to` with surfaces `json`, `text`. Requiring at least
    two DISTINCT trailing segments is what separates a real per-surface
    family from an accidental prefix collision.
    """
    facts = list(facts)
    out: list[SurfaceFamily] = []

    for f in facts:
        variants: dict[str, set[str]] = defaultdict(set)
        for fn in f.functions:
            stem, sep, tail = fn.name.rpartition("_")
            if sep and stem and tail:
                variants[stem].add(tail)
        for stem, tails in variants.items():
            if len(tails) >= min_count:
                out.append(
                    SurfaceFamily(
                        kind="helper",
                        stem=stem,
                        module=f.qualname,
                        surfaces=tuple(sorted(tails)),
                    )
                )

    homes: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        for cls in f.classes:
            homes[cls.name].add(f.qualname)
    for name, modules in homes.items():
        if len(modules) >= min_count:
            out.append(
                SurfaceFamily(
                    kind="mirrored", stem=name, module="", surfaces=tuple(sorted(modules))
                )
            )

    out.sort(key=lambda s: (-s.surface_count, s.kind, s.stem))
    return tuple(out)


def _click_flags(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, str]:
    """Map parameter name -> the CLI flag that fills it, from Click decorators.

    Click's own resolution rules: an explicit non-dash string argument names
    the destination, otherwise the longest `--long-option` becomes it with
    dashes folded to underscores.
    """
    flags: dict[str, str] = {}
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        target = _dotted(dec.func).rsplit(".", 1)[-1]
        if target not in {"option", "argument"}:
            continue
        literals: list[str] = []
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literals.append(arg.value)
        longs = sorted(
            (s for s in literals if s.startswith("--")), key=lambda s: len(s), reverse=True
        )
        explicit = [s for s in literals if not s.startswith("-")]
        display = longs[0] if longs else (explicit[0] if explicit else "")
        if not display:
            continue
        dest = explicit[0] if explicit else longs[0].lstrip("-").replace("-", "_")
        flags[dest] = display
    return flags


def _is_command(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        _dotted(dec.func if isinstance(dec, ast.Call) else dec).endswith(("command", "group"))
        for dec in fn.decorator_list
    )


def _exit_kind(node: ast.stmt) -> str | None:
    """The exit flavour a statement performs, or None if it is not an exit."""
    if isinstance(node, ast.Raise) and node.exc is not None:
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = _dotted(exc).rsplit(".", 1)[-1]
        return name if name in _EXIT_RAISES else None
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        dotted = _dotted(node.value.func)
        if dotted in _EXIT_CALLS or dotted.rsplit(".", 1)[-1] == "exit":
            return dotted
    return None


def _control_for(condition: ast.expr | None, params: set[str], flags: dict[str, str]) -> str:
    """Name the lever that decides whether the innermost guard fires."""
    if condition is None:
        return "hardcoded"
    names = [n.id for n in ast.walk(condition) if isinstance(n, ast.Name)]
    for name in names:
        if name in flags:
            return f"flag:{flags[name]}"
    # An attribute read beats a bare parameter: `config.fail_on_error` names
    # the field a caller sets, where `param:config` only names the object it
    # is reached through, which is not a lever anybody can turn.
    attrs = [_dotted(n) for n in ast.walk(condition) if isinstance(n, ast.Attribute) and _dotted(n)]
    if attrs:
        return f"config:{sorted(attrs)[0]}"
    for name in names:
        if name in params:
            return f"param:{name}"
    return "hardcoded"


def _scan_gates(
    body: Iterable[ast.stmt],
    *,
    condition: ast.expr | None,
    out: list[tuple[str, ast.expr | None]],
) -> None:
    """Walk statements, remembering the innermost `if` test above each exit."""
    for node in body:
        kind = _exit_kind(node)
        if kind is not None:
            out.append((kind, condition))
            continue
        if isinstance(node, ast.If):
            _scan_gates(node.body, condition=node.test, out=out)
            _scan_gates(node.orelse, condition=condition, out=out)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            # A nested def is scanned as its own function by the caller's
            # `ast.walk`; descending here would double-count it.
            continue
        else:
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(node, field, None)
                if isinstance(nested, list):
                    _scan_gates(nested, condition=condition, out=out)
            for handler in getattr(node, "handlers", []) or []:
                _scan_gates(handler.body, condition=condition, out=out)


def _gates(facts: Iterable[_ModuleFacts]) -> tuple[Gate, ...]:
    gates: list[Gate] = []
    for f in facts:
        for fn in f.functions:
            found: list[tuple[str, ast.expr | None]] = []
            _scan_gates(fn.body, condition=None, out=found)
            if not found:
                continue
            flags = _click_flags(fn)
            args = fn.args
            params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
            for kind, condition in found:
                gates.append(
                    Gate(
                        module=f.qualname,
                        function=fn.name,
                        kind=kind,
                        control=_control_for(condition, params, flags),
                        is_command=_is_command(fn),
                    )
                )
    gates.sort(key=lambda g: (g.module, g.function, g.kind, g.control))
    return tuple(gates)


def _frozen_config(cls: ast.ClassDef) -> tuple[bool, list[str]]:
    """Read a pydantic `model_config = ConfigDict(...)` assignment off a class."""
    flags: list[str] = []
    frozen = False
    for stmt in cls.body:
        targets: list[ast.expr] = []
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, ast.AnnAssign):
            targets = [stmt.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "model_config" for t in targets):
            continue
        value = stmt.value
        if not isinstance(value, ast.Call):
            continue
        for kw in value.keywords:
            if kw.arg is None:
                continue
            flags.append(kw.arg)
            if kw.arg == "frozen" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                frozen = True
    return frozen, flags


def _model_census(facts: Iterable[_ModuleFacts]) -> ModelCensus:
    total = 0
    value = 0
    frozen = 0
    bases: Counter[str] = Counter()
    config_flags: Counter[str] = Counter()
    tuples = 0
    lists = 0

    for f in facts:
        for cls in f.classes:
            total += 1
            names = [_dotted(b).rsplit(".", 1)[-1] for b in cls.bases]
            declared = [n for n in names if n]
            is_value = any(n in _VALUE_BASES for n in declared)
            for name in declared:
                bases[name] += 1
            if not is_value:
                continue
            value += 1
            was_frozen, flags = _frozen_config(cls)
            frozen += int(was_frozen)
            for flag in flags:
                config_flags[flag] += 1
            for stmt in cls.body:
                if not isinstance(stmt, ast.AnnAssign) or stmt.annotation is None:
                    continue
                head = ast.unparse(stmt.annotation).split("[", 1)[0].strip()
                if head.endswith("tuple") or head.endswith("Tuple"):
                    tuples += 1
                elif head.endswith("list") or head.endswith("List"):
                    lists += 1

    return ModelCensus(
        total_classes=total,
        value_classes=value,
        frozen_classes=frozen,
        base_counts=tuple(sorted(bases.items(), key=lambda kv: (-kv[1], kv[0]))),
        config_flags=tuple(sorted(config_flags.items(), key=lambda kv: (-kv[1], kv[0]))),
        tuple_fields=tuples,
        list_fields=lists,
    )


def compute_conventions(
    root: Path,
    *,
    ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS,
    extra_roots: Iterable[str] = (),
    min_family: int = 2,
) -> ConventionsReport:
    """Derive `root`'s house style from its own source.

    `min_family` is the floor for reporting a naming family or a mirrored
    surface set: at 2 a single pair counts, which is the right default for
    a small project and noisy on a large one.
    """
    modules = discover_modules(root, ignored_dirs=ignored_dirs, extra_roots=extra_roots)
    facts: list[_ModuleFacts] = []
    unparsed = 0
    for module in modules:
        try:
            source = module.path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
            # Advisory command: an unreadable or half-written file is a fact
            # to report, never a reason to fail.
            unparsed += 1
            continue
        facts.append(_collect(tree, module.qualname))

    return ConventionsReport(
        root=str(root),
        modules_scanned=len(facts),
        modules_unparsed=unparsed,
        naming=_naming_families(facts, min_count=min_family),
        surfaces=_surface_families(facts, min_count=min_family),
        gates=_gates(facts),
        models=_model_census(facts),
    )
