"""Families a project repeats: naming, base classes, registries, mirrored surfaces, models."""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from collections.abc import Iterable

from archy.conventions_collect import _assigned_names, _dotted, _ModuleFacts
from archy.conventions_models import (
    BaseFamily,
    ModelCensus,
    NamingFamily,
    NamingHome,
    RegistryEntry,
    SharedConstant,
    SurfaceFamily,
    camel_suffix,
)

# Base names that mark a class as a declared value type rather than a plain
# object. Matched on the last dotted segment, so `pydantic.BaseModel` and a
# re-exported `BaseModel` are the same family.
_VALUE_BASES = frozenset(
    {"BaseModel", "TypedDict", "NamedTuple", "Enum", "IntEnum", "StrEnum", "Protocol"}
)


_TYPING_PLUMBING = frozenset(
    {"TypeVar", "ParamSpec", "TypeVarTuple", "NewType", "TypeAlias", "NamedTuple", "Generic"}
)


def _home_module(by_module: Counter[str]) -> tuple[str, int]:
    """The module hosting the most members of a family, and how many it hosts.

    One definition for all three family kinds, so "where does a new one go"
    cannot come out differently depending on which report asked (#415).

    Ties go to the module whose name sorts first. Not `Counter.most_common`,
    whose tie order is insertion order: `_base_families` builds its counter from
    a `set` of class names, so that order is string-hash order and changed with
    `PYTHONHASHSEED`, and one repo got a different home on each run (#875).
    Name order is not a judgement about which module is the better home; it
    only makes the answer a function of the project, not of the process.

    Callers pass a non-empty counter. `_base_families` is the only one that can
    produce an empty one (its members may all be missing from `home_of`), and it
    guards before calling.
    """
    return min(by_module.items(), key=lambda item: (-item[1], item[0]))


def _base_families(facts: Iterable[_ModuleFacts], *, min_count: int) -> tuple[BaseFamily, ...]:
    """Group classes by what they are a kind of, following inheritance to the root.

    Two decisions carry this function.

    *Transitive, not direct.* ``click`` defines twelve exceptions under
    ``ClickException``, but only ``UsageError`` and two others name it
    directly; the rest arrive through ``UsageError``. A direct-edge census
    reports the intermediate class as the family and misses the real one --
    the same edge-versus-path distinction that ``check`` and ``contracts``
    draw, applied to inheritance instead of imports.

    *Only bases defined in this repository.* A convention has to be the
    project's own. This is also what keeps the section readable: ``ABC``,
    ``Protocol``, ``TypedDict``, ``Generic`` and ``Exception`` are language
    plumbing that every project uses identically, they out-count every real
    family, and they say nothing an agent could act on. Requiring a local
    definition removes them without a stop-list to maintain.
    """
    home_of, node_of, parents = _class_index(list(facts))
    members = _family_members(node_of, parents)
    out: list[BaseFamily] = []
    for base, names in members.items():
        if len(names) < min_count:
            continue
        by_module = Counter(home_of[n] for n in names if n in home_of)
        if not by_module:
            continue
        home, home_count = _home_module(by_module)
        out.append(
            BaseFamily(
                base=base,
                count=len(names),
                home_module=home,
                home_count=home_count,
                members=tuple(sorted(names)),
                suffixes=tuple(sorted({camel_suffix(n) for n in names})),
                shared_constants=_shared_constants(
                    [node_of[n] for n in sorted(names) if n in node_of], min_count=min_count
                ),
            )
        )
    out.sort(key=lambda b: (-b.count, b.base))
    return tuple(out)


def _class_index(
    facts: list[_ModuleFacts],
) -> tuple[dict[str, str], dict[str, ast.ClassDef], dict[str, set[str]]]:
    """Home module, definition node and direct base names, each keyed by class name."""
    home_of: dict[str, str] = {}
    node_of: dict[str, ast.ClassDef] = {}
    parents: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        for cls in f.classes:
            home_of.setdefault(cls.name, f.qualname)
            node_of.setdefault(cls.name, cls)
            bases = set(_declared_bases(cls))
            if bases:
                parents[cls.name] |= bases
    return home_of, node_of, parents


def _ancestors(parents: dict[str, set[str]], name: str) -> set[str]:
    seen: set[str] = set()
    stack = list(parents.get(name, ()))
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(parents.get(cur, ()))
    return seen


def _family_members(
    node_of: dict[str, ast.ClassDef], parents: dict[str, set[str]]
) -> dict[str, set[str]]:
    members: dict[str, set[str]] = defaultdict(set)
    for name in node_of:
        for base in _ancestors(parents, name):
            if base in node_of:  # local definitions only; see `_base_families`
                members[base].add(name)
                # The class that names the family belongs to it. Without this the
                # root's own declarations are invisible, and the root is exactly
                # where a family states its defaults: `click` sets
                # `exit_code = 1` on `ClickException` and `2` on `UsageError`,
                # which is the whole severity convention and reads as a single
                # unexplained override if the base is left out.
                members[base].add(base)
    return members


def _shared_constants_from(
    seen: dict[str, list[str]], *, min_count: int
) -> tuple[SharedConstant, ...]:
    """Collected literal values, as a census ordered by how many sites set each name.

    Shared by the class-attribute and registry-keyword censuses. The ordering is
    the load-bearing part: values by frequency then name, names by setter count
    then name, so a caller reading the first row is reading the majority.
    """
    out = []
    for name, values in seen.items():
        if len(values) < min_count:
            continue
        dist = Counter(values)
        out.append(
            SharedConstant(
                name=name,
                setters=len(values),
                values=tuple(sorted(dist)),
                distribution=tuple(sorted(dist.items(), key=lambda kv: (-kv[1], kv[0]))),
            )
        )
    out.sort(key=lambda c: (-c.setters, c.name))
    return tuple(out)


def _shared_constants(classes: list[ast.ClassDef], *, min_count: int) -> tuple[SharedConstant, ...]:
    """Class-level constants several members of a family assign literally.

    Only literals are reported. A computed default says nothing an agent
    can copy, and including it would turn a fact into a guess.
    """
    seen: dict[str, list[str]] = defaultdict(list)
    for cls in classes:
        for node in cls.body:
            target = None
            value = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target, value = node.target.id, node.value
            elif (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                target, value = node.targets[0].id, node.value
            if target is None or value is None or target.startswith("__"):
                continue
            if isinstance(value, ast.Constant):
                seen[target].append(repr(value.value))
    return _shared_constants_from(seen, min_count=min_count)


def _registries(facts: Iterable[_ModuleFacts], *, min_count: int) -> tuple[RegistryEntry, ...]:
    """Module-level ``NAME = Ctor(...)`` families. See RegistryEntry."""
    calls = _constructor_calls(facts)
    out: list[RegistryEntry] = []
    for ctor, rows in calls.items():
        if len(rows) < max(min_count, 3):
            continue
        by_module = Counter(module for _, module, _ in rows)
        home, home_count = _home_module(by_module)
        kw: dict[str, list[str]] = defaultdict(list)
        literals: list[str] = []
        for _, _, call in rows:
            for keyword in call.keywords:
                if keyword.arg and isinstance(keyword.value, ast.Constant):
                    kw[keyword.arg].append(repr(keyword.value.value))
            slug = _first_arg_slug(call)
            if slug:
                literals.append(slug)
        # `min_count`, not the registry's own floor of 3: a keyword passed by
        # two members of a larger family is precisely the interesting case --
        # the minority that opts out of a default is what the split is about.
        defaults = _shared_constants_from(kw, min_count=min_count)
        out.append(
            RegistryEntry(
                constructor=ctor,
                count=len(rows),
                home_module=home,
                home_count=home_count,
                examples=tuple(sorted({n for n, _, _ in rows})[:4]),
                keyword_defaults=defaults,
                literal_names=tuple(sorted(set(literals))),
            )
        )
    out.sort(key=lambda r: (-r.count, r.constructor))
    return tuple(out)


def _registry_call(node: ast.Assign | ast.AnnAssign) -> tuple[str, ast.Call] | None:
    """(constructor, call) for a module-level ``NAME = Ctor(...)``, else None."""
    call = node.value
    if not isinstance(call, ast.Call):
        return None
    ctor = _dotted(call.func).split(".")[-1]
    if not ctor or not ctor[:1].isupper():
        return None
    # Type-system plumbing is declared this way in every typed project
    # and is identical everywhere, so it out-counts real registries while
    # telling an agent nothing about this repository.
    return None if ctor in _TYPING_PLUMBING else (ctor, call)


def _constructor_calls(
    facts: Iterable[_ModuleFacts],
) -> dict[str, list[tuple[str, str, ast.Call]]]:
    """Module-level ``NAME = Ctor(...)`` rows of (name, module, call), by constructor."""
    calls: dict[str, list[tuple[str, str, ast.Call]]] = defaultdict(list)
    for f in facts:
        for node in f.body_assignments:
            found = _registry_call(node)
            if found is None:
                continue
            ctor, call = found
            for name in _assigned_names(node):
                calls[ctor].append((name, f.qualname, call))
    return calls


def _first_arg_slug(call: ast.Call) -> str | None:
    """The call's first argument when it is a short, copyable string, else None."""
    if not (
        call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
    ):
        return None
    first = call.args[0].value
    # 🔴 Slugs only. `mypy.message_registry` declares 102 entries whose
    # first argument is a full diagnostic sentence, and a census that
    # echoed them would emit tens of kilobytes of prose -- spending the
    # context this command exists to save. A name an agent could copy is
    # short and has no spaces; anything else is content, not convention.
    if first and len(first) <= 40 and " " not in first:
        return first
    return None


def _naming_families(facts: Iterable[_ModuleFacts], *, min_count: int) -> tuple[NamingHome, ...]:
    members: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for f in facts:
        for cls in f.classes:
            members[camel_suffix(cls.name)].append((cls.name, f.qualname))

    families: list[NamingFamily] = []
    for suffix, rows in members.items():
        if len(rows) < min_count:
            continue
        by_module = Counter(module for _, module in rows)
        home, home_count = _home_module(by_module)
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

    by_home: dict[str, list[NamingFamily]] = defaultdict(list)
    for family in families:
        by_home[family.home_module].append(family)
    homes = [NamingHome(module=module, families=tuple(rows)) for module, rows in by_home.items()]
    # Biggest naming surface first, then most distinct families, then name:
    # the module an agent is most likely to be adding a class to.
    homes.sort(key=lambda h: (-h.total, -h.family_count, h.module))
    return tuple(homes)


def _surface_families(
    facts: Iterable[_ModuleFacts], *, min_count: int, max_consumers: int = 5
) -> tuple[SurfaceFamily, ...]:
    """Three mirror shapes: consumers of one type, per-surface helpers, and
    one name in many modules.

    A helper family is keyed on the function name minus its final
    underscore segment, so `_hotspots_to_text` / `_hotspots_to_json` group
    under `_hotspots_to` with surfaces `json`, `text`. Requiring at least
    two DISTINCT trailing segments is what separates a real per-surface
    family from an accidental prefix collision.

    🔴 A CONSUMER FAMILY EXISTS BECAUSE NAME STEMS DO NOT CROSS MODULES.
    In this project the CLI renders layer violations through
    `_violations_to_text` and `_violations_to_json`, which share a stem and
    group correctly -- and the MCP surface renders the same result through
    `_run_check`, which shares nothing with them. A stem-keyed census is
    structurally blind to the third surface, and that surface is the one
    half-wired features actually miss. What the three have in common is not
    a name: it is the symbol they consume. So a consumer family is keyed on
    a definition and lists the internal modules that import it.

    Ranking puts cross-module families first, ahead of larger same-module
    ones. A family confined to one file is wired or not in a single edit; a
    family spanning several is the one that gets half-wired, which is the
    question this section is asked. Sorting by member count alone buried
    `_violations_to` at rank 38 of 50, behind a 13-member family of
    one-helper-per-MCP-tool that no caller needs to keep in step.
    """
    facts = list(facts)
    out = [
        *_helper_families(facts, min_count),
        *_consumer_families(facts, min_count, max_consumers),
        *_mirrored_families(facts, min_count),
    ]
    # Cross-module first, then by size. See the docstring: a same-module family is
    # wired in one edit; a cross-module one is what gets half-wired.
    out.sort(key=lambda s: (0 if s.kind == "helper" else -1, -s.surface_count, s.kind, s.stem))
    return tuple(out)


def _helper_families(facts: list[_ModuleFacts], min_count: int) -> list[SurfaceFamily]:
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
    return out


def _definition_homes(facts: list[_ModuleFacts]) -> dict[str, set[str]]:
    """Every module that defines each class or function name.

    🔴 EVERY module that defines the name, not the first one seen. Keying on
    the bare name and taking the first definition is a WRONG ANSWER, not a
    missing one: with `foo` defined in both `pkg.a` and `pkg.b`, an importer
    of `pkg.b.foo` was reported as consuming `pkg.a`, pointing an agent at a
    module it must not edit. This repository carries five such collisions
    (`_load_graph`, `_load_graph_pair` and `_score_to_dict` among them).
    """
    defined_in: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        for node in f.classes:
            defined_in[node.name].add(f.qualname)
        for node in f.functions:
            defined_in[node.name].add(f.qualname)
    return defined_in


def _import_home(source: str, candidates: set[str]) -> str | None:
    """The module a ``from source import name`` resolves to, or None if unclear."""
    if source in candidates:
        return source
    if len(candidates) == 1:
        # A relative import, or one written through a re-export. The
        # name has one definition project-wide, so there is nothing to
        # get wrong.
        return next(iter(candidates))
    # Ambiguous and unresolved. Saying nothing beats naming the
    # wrong module, which is the failure this section causes most.
    return None


def _consumer_families(
    facts: list[_ModuleFacts], min_count: int, max_consumers: int
) -> list[SurfaceFamily]:
    defined_in = _definition_homes(facts)
    # Keyed by (home, name), never by name alone. Two modules can define the
    # same name and each be imported unambiguously; merging them into one
    # family reports half its consumers against the wrong home, which is the
    # same wrong answer one level up.
    consumers: dict[tuple[str, str], set[str]] = defaultdict(set)
    for f in facts:
        for source, name in f.internal_imports:
            candidates = defined_in.get(name)
            home = _import_home(source, candidates) if candidates else None
            # A module importing from itself is not a surface.
            if home is not None and home != f.qualname:
                consumers[(home, name)].add(f.qualname)
    out: list[SurfaceFamily] = []
    for (home, name), modules in sorted(consumers.items()):
        # 🔴 A co-update set is SMALL BY NATURE, and the cap is what separates one
        # from a popular utility. Sixteen modules import `build_graph`; forgetting
        # one of them is not a failure mode, that is just infrastructure. Two or
        # three modules rendering the same result IS the failure mode -- it is how
        # a feature ships wired to the CLI and not to the MCP payload. Without the
        # cap this section ranks the most-imported helper first and never reaches
        # the sets it exists to name.
        if min_count <= len(modules) <= max_consumers:
            out.append(
                SurfaceFamily(
                    kind="consumer",
                    stem=name,
                    module=home,
                    surfaces=tuple(sorted(modules)),
                )
            )
    return out


def _mirrored_families(facts: list[_ModuleFacts], min_count: int) -> list[SurfaceFamily]:
    homes: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        for cls in f.classes:
            homes[cls.name].add(f.qualname)
    return [
        SurfaceFamily(kind="mirrored", stem=name, module="", surfaces=tuple(sorted(modules)))
        for name, modules in homes.items()
        if len(modules) >= min_count
    ]


def _model_config_call(stmt: ast.stmt) -> ast.Call | None:
    """The `ConfigDict(...)` call of a `model_config = ...` statement, else None."""
    if isinstance(stmt, ast.Assign):
        targets = list(stmt.targets)
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
    else:
        return None
    if not any(isinstance(t, ast.Name) and t.id == "model_config" for t in targets):
        return None
    return stmt.value if isinstance(stmt.value, ast.Call) else None


def _is_frozen_true(kw: ast.keyword) -> bool:
    return kw.arg == "frozen" and isinstance(kw.value, ast.Constant) and kw.value.value is True


def _frozen_config(cls: ast.ClassDef) -> tuple[bool, list[str]]:
    """Read a pydantic `model_config = ConfigDict(...)` assignment off a class."""
    flags: list[str] = []
    frozen = False
    for stmt in cls.body:
        call = _model_config_call(stmt)
        if call is None:
            continue
        for kw in call.keywords:
            if kw.arg is None:
                continue
            flags.append(kw.arg)
            frozen = frozen or _is_frozen_true(kw)
    return frozen, flags


def _collection_fields(cls: ast.ClassDef) -> tuple[int, int]:
    """How many annotated fields of `cls` are tuples and how many are lists."""
    tuples = lists = 0
    for stmt in cls.body:
        if not isinstance(stmt, ast.AnnAssign) or stmt.annotation is None:
            continue
        head = ast.unparse(stmt.annotation).split("[", 1)[0].strip()
        if head.endswith(("tuple", "Tuple")):
            tuples += 1
        elif head.endswith(("list", "List")):
            lists += 1
    return tuples, lists


def _declared_bases(cls: ast.ClassDef) -> list[str]:
    """The last dotted segment of each base class expression, non-empty only.

    The one definition of a class's base names. A list, so a base named twice is
    counted twice by the census; `_class_index` wants the set and wraps it.
    """
    return [name for b in cls.bases if (name := _dotted(b).rsplit(".", 1)[-1])]


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
            declared = _declared_bases(cls)
            bases.update(declared)
            if not any(n in _VALUE_BASES for n in declared):
                continue
            value += 1
            was_frozen, flags = _frozen_config(cls)
            frozen += int(was_frozen)
            config_flags.update(flags)
            more_tuples, more_lists = _collection_fields(cls)
            tuples += more_tuples
            lists += more_lists

    return ModelCensus(
        total_classes=total,
        value_classes=value,
        frozen_classes=frozen,
        base_counts=tuple(sorted(bases.items(), key=lambda kv: (-kv[1], kv[0]))),
        config_flags=tuple(sorted(config_flags.items(), key=lambda kv: (-kv[1], kv[0]))),
        tuple_fields=tuples,
        list_fields=lists,
    )
