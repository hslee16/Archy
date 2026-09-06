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

archy:owns        BaseFamily, ConventionsReport, DocGap, ExportGap, Gate, ModelCensus,
                  ModulePartition, ModuleView, NamingFamily, NamingHome, RegistryEntry,
                  SharedConstant, SurfaceFamily, camel_suffix, censused_modules,
                  compute_conventions, compute_module_view
archy:mirrored-by ConventionsReport -> archy.cli, archy.headers, archy.mcp,
                  ModuleView -> archy.cli, archy.mcp, compute_conventions -> archy.cli,
                  archy.mcp, bench.conventions_bench, compute_module_view -> archy.cli,
                  archy.mcp
"""

from __future__ import annotations

import ast
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, computed_field

from archy.graph import (
    DEFAULT_IGNORED_DIRS,
    Module,
    _follow_reexport_chains,
    discover_modules,
    resolve_from_import,
    resolve_relative_import,
)

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


class NamingHome(BaseModel):
    """One module and the naming families it is the home of.

    Grouped this way on purpose. Ranked as a flat list of suffixes, a big
    generic family (`*Payload`, 13) buries a small sharply-located one
    (`*Violation`, 3 in `layers.py`) even though the second is the more
    useful answer: an agent is about to add a class to a PARTICULAR module,
    so the module is the right key. Concentration-weighting alone does not
    fix it -- `*Payload` is 13/13 in one module, so it is perfectly
    concentrated too and still outranks `*Violation`.
    """

    model_config = ConfigDict(frozen=True)

    module: str
    families: tuple[NamingFamily, ...]

    @computed_field
    @property
    def total(self) -> int:
        """Classes across every family this module hosts."""
        return sum(f.count for f in self.families)

    @computed_field
    @property
    def family_count(self) -> int:
        return len(self.families)


class SurfaceFamily(BaseModel):
    """A set of definitions that mirror each other and must move together.

    `kind='helper'`  -- one function per output surface in a single module
                        (`_x_to_text` / `_x_to_json`), `surfaces` are the
                        differing trailing segments.
    `kind='mirrored'` -- one name defined in several modules, `surfaces`
                        are those modules.
    `kind='consumer'` -- one definition imported by several modules, which is
                        the shape a stem-keyed census cannot see: `module` is
                        the defining module and `surfaces` are the importers.
                        A name defined in more than one module is omitted
                        unless the import says which one it came from, because
                        naming the wrong home is worse than naming none.
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

    `category` separates the two things a non-zero exit can mean, because
    they answer different questions and conflating them makes the count
    useless:

    `gate`   -- a FINDING failed. Written as an explicit exit code
                (`sys.exit(1)`, `raise SystemExit`, `ctx.exit`), because the
                code itself is the result being communicated.
    `error`  -- the USER did something wrong (bad config, missing file,
                unavailable extra). Written as a raised framework exception
                (`ClickException`, `UsageError`, `Abort`), which delegates
                the exit code to the framework.

    That split is a heuristic over HOW the exit is written, not a
    declaration the source makes; a project that raises its own exception
    subclass for findings would land in neither bucket cleanly. It
    reproduces the intended split exactly on Click projects, which write
    findings as `sys.exit(n)` precisely so the code is controllable.

    `control` is the lever: `flag:--strict` (a CLI option feeds the guard),
    `config:<attr>` (a config/result attribute does), `param:<name>` (a
    function argument does, with no flag found), or `hardcoded` (the exit
    is unconditional or guarded by something with no named lever).

    `code` is the literal exit status when the source states one. `None`
    means it is not a literal -- either computed
    (`sys.exit(0 if ok else 1)`) or left to the framework, which is itself
    the answer to "what code does this project fail with".
    """

    model_config = ConfigDict(frozen=True)

    module: str
    function: str
    category: str
    kind: str
    code: int | None
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


class SharedConstant(BaseModel):
    """A class-level constant that several members of one family set.

    This is the gate-vs-advisory question in its declarative form. In
    ``click`` every exception carries ``exit_code``, and its two literal
    values (1 and 2) *are* the severity convention -- stated nowhere in
    prose and invisible to an exit-site census, because the single
    ``sys.exit`` that consumes it lives in generic dispatch code.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    setters: int
    values: tuple[str, ...]
    distribution: tuple[tuple[str, int], ...]


class BaseFamily(BaseModel):
    """Classes grouped by the base they derive from, not by how they are spelled.

    A suffix family answers "what do I call it"; a base family answers
    "what is it a kind of", and the two disagree more often than not. In
    ``click`` twelve exception classes derive from ``ClickException``
    while only three carry an ``Error`` suffix, so a suffix census reports
    a weak convention where a strong one exists.
    """

    model_config = ConfigDict(frozen=True)

    base: str
    count: int
    home_module: str
    home_count: int
    members: tuple[str, ...]
    suffixes: tuple[str, ...]
    shared_constants: tuple[SharedConstant, ...] = ()

    @computed_field
    @property
    def concentration(self) -> float:
        return self.home_count / self.count if self.count else 0.0

    @computed_field
    @property
    def suffix_agreement(self) -> float:
        """Share of members carrying the family's most common suffix. Low
        agreement is the signal that the base, not the name, is the rule."""
        if not self.members:
            return 0.0
        return Counter(camel_suffix(m) for m in self.members).most_common(1)[0][1] / len(
            self.members
        )


class RegistryEntry(BaseModel):
    """A constructor called repeatedly at module level to declare values.

    Not every project declares its findings as classes. ``mypy`` declares
    79 error codes as module-level ``X = ErrorCode(...)`` assignments, so
    a census that walks only ``ClassDef`` sees none of them -- and the
    keyword that decides whether a new code is on by default
    (``default_enabled``) is a keyword argument here, not a class
    attribute and not an exit site.
    """

    model_config = ConfigDict(frozen=True)

    constructor: str
    count: int
    home_module: str
    home_count: int
    examples: tuple[str, ...]
    keyword_defaults: tuple[SharedConstant, ...]
    literal_names: tuple[str, ...]


class ExportGap(BaseModel):
    """Family members absent from the ``__all__`` that governs them.

    The generic form of the most-replicated defect in this project's own
    history: a new type wired into some surfaces and not others. An
    ``__all__`` list is a literal, so the diff against a family is
    derived, never guessed.
    """

    model_config = ConfigDict(frozen=True)

    export_module: str
    family: str
    exported: int
    defined: int
    missing: tuple[str, ...]


class DocGap(BaseModel):
    """Family members named nowhere in the project's own documentation.

    The last surface a census can reach, and the one two of four surveyed
    projects keep half their answer in. ``mypy`` splits its error codes
    across ``error_code_list.rst`` and ``error_code_list2.rst`` by whether
    they are on by default, so the docs *are* the gate convention;
    ``pytest`` ships ``PytestFDWarning`` exported and with no ``autoclass``
    entry, which is the same half-wired defect as a missing re-export.

    Prose is matched, never parsed. A name is "documented" if it appears
    as a directive target (``.. autoclass::``, ``::: pkg.Thing``) or
    inside a code span. That admits a passing mention as documentation,
    which is the conservative direction: this section should under-report
    rather than send an agent to write docs that already exist.
    """

    model_config = ConfigDict(frozen=True)

    doc_root: str
    family: str
    documented: int
    defined: int
    missing: tuple[str, ...]


class ModuleView(BaseModel):
    """Everything the census knows about ONE module, complete and unranked.

    🔴 THIS EXISTS TO ANSWER NEGATIVES, WHICH A RANKED DIGEST CANNOT.
    Twenty-four pieces of real agent reasoning were scored against the ordinary
    report -- every one chosen because a census could in principle answer it --
    and it scored zero. Both blind readers gave the same reason: the report says
    `150; showing 12`, so absence from the list proves nothing. The questions
    being asked were of the form "does `risk` import `hotspots`" and "does ANY
    of graph/cycles/score reach `layers`", and a top-N ranking is the wrong
    shape for both.

    So every list here is COMPLETE for the module named, and truncating any of
    them would defeat the point. `status` matters for the same reason: a module
    that was set aside must say so, or its absence reads as "nothing to report"
    when the truth is "not looked at".
    """

    model_config = ConfigDict(frozen=True)

    module: str
    status: str
    classes: tuple[str, ...]
    functions: tuple[str, ...]
    imports_internal: tuple[str, ...]
    imported_by: tuple[str, ...]
    exports: tuple[str, ...] | None
    suffix_families: tuple[str, ...]
    gates: tuple[Gate, ...]


class ModulePartition(BaseModel):
    """What was censused and what was set aside, with the reason.

    Reporting the partition is load-bearing rather than cosmetic. Before
    it existed, ``pydantic``'s naming home came back as
    ``pydantic.v1.errors`` -- the vendored legacy copy, 93 classes -- in
    place of the live ``pydantic.errors`` with 6. That is a *wrong*
    answer rather than a missing one, and an agent acting on it would add
    its class to a deprecated shim.
    """

    model_config = ConfigDict(frozen=True)

    production: int
    tests: int
    shadowed: int
    nonsource: int = 0
    shadow_roots: tuple[str, ...] = ()


class ConventionsReport(BaseModel):
    """The whole derived house style for one project."""

    model_config = ConfigDict(frozen=True)

    root: str
    modules_scanned: int
    modules_unparsed: int
    naming: tuple[NamingHome, ...]
    surfaces: tuple[SurfaceFamily, ...]
    gates: tuple[Gate, ...]
    errors: tuple[Gate, ...]
    models: ModelCensus
    bases: tuple[BaseFamily, ...] = ()
    registries: tuple[RegistryEntry, ...] = ()
    export_gaps: tuple[ExportGap, ...] = ()
    doc_gaps: tuple[DocGap, ...] = ()
    docs_scanned: int = 0
    partition: ModulePartition | None = None

    @computed_field
    @property
    def gate_modules(self) -> tuple[str, ...]:
        """Modules that fail the build on a FINDING. Everything else is
        advisory -- user-error exits live in `errors` and say nothing about
        whether a new finding should gate."""
        return tuple(sorted({g.module for g in self.gates}))

    @computed_field
    @property
    def gate_codes(self) -> tuple[int, ...]:
        """The literal exit statuses this project fails findings with."""
        return tuple(sorted({g.code for g in self.gates if g.code is not None}))


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
        # Module-level assignments only, not `ast.walk`: a registry is a
        # top-level declaration, and a same-shaped call inside a function
        # body is a local variable that means nothing about house style.
        self.body_assignments: list[ast.Assign | ast.AnnAssign] = []
        # None means the module declares no `__all__` at all, which is a
        # different fact from declaring an empty one.
        self.exports: frozenset[str] | None = None
        # Names this module pulls in by `from ... import X`. Kept unfiltered;
        # `_surface_families` decides which of them this project defines.
        # (source module, name). The module is "" for a relative import, which
        # is not resolved here: a name that is unambiguous project-wide does not
        # need it, and one that is not must not be guessed at. See
        # `_surface_families`.
        self.internal_imports: set[tuple[str, str]] = set()
        # The MODULES this one imports, dotted and absolute, with relative form
        # resolved. Deliberately separate from `internal_imports` above, which
        # holds SYMBOLS and leaves `from . import x` unresolved on purpose:
        # "does `risk` import `hotspots`" is a question about modules, and
        # answering it from symbols alone would miss both `import pkg.mod` and
        # every relative import in the project.
        self.imported_modules: set[str] = set()
        # One entry per `from X import ...`: the resolved base and the (name,
        # local-name) pairs. Kept unresolved because whether a name is a
        # submodule, a re-export or a plain symbol depends on the project's
        # module set; see `_resolved_imports`.
        self.import_froms: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        # Whether this module is a package's `__init__.py`. Only those can
        # re-export, so only those contribute to the re-export map.
        self.is_package = False


def _collect(tree: ast.Module, qualname: str, *, is_package: bool = False) -> _ModuleFacts:
    facts = _ModuleFacts(qualname)
    facts.is_package = is_package
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            facts.classes.append(node)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            facts.functions.append(node)
    # The package a relative import is relative to. For a package's own
    # `__init__.py` that is the qualname itself, because Python sets
    # `__package__ == __name__` there; stripping a level as if it were a plain
    # submodule resolves `from . import x` one level too shallow, and the bogus
    # name is then filtered out as unknown -- a silent false negative in the
    # aggregating `__init__.py` that is the commonest place to see one.
    pkg = qualname if is_package else (qualname.rsplit(".", 1)[0] if "." in qualname else "")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                facts.imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            source = "" if node.level else (node.module or "")
            for alias in node.names:
                if alias.name != "*":
                    facts.internal_imports.add((source, alias.name))
            base = _resolve_relative(node.module, node.level, pkg)
            if base:
                # `from X import a, b` is statically ambiguous: the names may be
                # submodules or symbols in X's namespace. Which one cannot be
                # decided here, because it depends on what the project defines,
                # so the statement is recorded whole and resolved by
                # `_resolved_imports` once the module set is known. `node.module`
                # is carried because `from . import sibling` imports the sibling
                # and must never fall back to the containing package.
                facts.import_froms.append(
                    (base, tuple((a.name, a.asname or a.name) for a in node.names if a.name != "*"))
                )
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            facts.body_assignments.append(node)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    facts.exports = _string_literals(node.value)
    if facts.exports is None:
        # `click` publishes its API with `from .x import Y as Y` and no
        # `__all__` at all -- the PEP 484 explicit re-export form, which type
        # checkers treat as public. Reading only `__all__` would report that
        # such a package exports nothing, so a missing member would never
        # surface. Only the redundant-alias form counts: a plain
        # `from .x import Y` is an implementation import, not a promise.
        reexports = {
            alias.asname
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.asname and alias.asname == alias.name
        }
        if reexports:
            facts.exports = frozenset(reexports)
    return facts


def _resolve_relative(module: str | None, level: int, pkg: str) -> str:
    """`graph.resolve_relative_import` in this module's vocabulary.

    The decision is not duplicated here; only the empty-string convention this
    file's callers expect, where the graph uses None for an import that walks
    past the project root.
    """
    return resolve_relative_import(module, level, pkg) or ""


def _string_literals(node: ast.expr | None) -> frozenset[str]:
    """The string constants of a list/tuple literal. Anything dynamic yields
    what could be read statically, never a guess about the rest."""
    if not isinstance(node, ast.List | ast.Tuple):
        return frozenset()
    return frozenset(
        e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
    )


# --------------------------------------------------------------------------
# Which modules the house style actually lives in.
#
# A census over every file a project ships answers a different question
# from the one an agent is asking. Test modules carry their own strong and
# deliberately throwaway conventions -- ``test_*`` functions, ``Foo`` and
# ``MyModel`` fixtures -- and there are usually more of them than of the
# thing being described, so they win every count they are entered in.
# Measured across four projects, the top mirrored "surface" was a test
# fixture in four cases out of four.
#
# Vendored copies are worse than noisy, because they are plausible. They
# duplicate the parent package's module names by construction, so the
# duplicate outranks the original on volume and the census names the dead
# copy as home.
_TEST_PARTS = frozenset({"tests", "test", "testing", "_test", "_tests"})
_TYPING_PLUMBING = frozenset(
    {"TypeVar", "ParamSpec", "TypeVarTuple", "NewType", "TypeAlias", "NamedTuple", "Generic"}
)


def _is_nonsource_module(qualname: str) -> bool:
    """Modules under a dot-directory: tooling that ships in the repo but is not
    the library. `pydantic` keeps a GitHub Action in `.github/actions/`, whose
    classes were outranking `pydantic`'s own on a `BaseModel` census."""
    return qualname.startswith(".") or any(part.startswith(".") for part in qualname.split("."))


def _is_test_module(qualname: str) -> bool:
    parts = qualname.split(".")
    if _TEST_PARTS & set(parts):
        return True
    # A directory name is not always a Python identifier: `mypyc/test-data/`
    # holds typeshed fixtures that redefine `RuntimeError` and `LookupError`,
    # and they were being reported as this project's own kind families.
    if any(part.startswith(("test-", "test_", "tests-", "_test")) for part in parts[:-1]):
        return True
    leaf = parts[-1]
    return leaf.startswith("test_") or leaf.endswith("_test") or leaf == "conftest"


def _shadow_roots(
    qualnames: Iterable[str], *, min_modules: int = 5, ratio: float = 0.5
) -> frozenset[str]:
    """Subpackages that re-implement their own parent, found by overlap.

    Derived rather than pattern-matched on purpose. Hardcoding ``v1`` would
    catch ``pydantic`` and nothing else, and would be wrong for a project
    whose ``v1`` is the live one. A subtree that restates half its parent's
    module names under a second prefix is a copy whatever it is called.
    """
    names = list(qualnames)
    by_prefix: dict[str, set[str]] = defaultdict(set)
    outside: dict[str, set[str]] = defaultdict(set)
    for q in names:
        parts = q.split(".")
        if len(parts) < 3:
            continue
        prefix = ".".join(parts[:2])
        by_prefix[prefix].add(".".join(parts[2:]))
    for prefix in by_prefix:
        root = prefix.split(".")[0]
        for q in names:
            if q.startswith(prefix + "."):
                continue
            if q.startswith(root + "."):
                outside[prefix].add(q[len(root) + 1 :])
    shadows = set()
    for prefix, inner in by_prefix.items():
        if len(inner) < min_modules:
            continue
        overlap = len(inner & outside[prefix])
        if overlap / len(inner) >= ratio:
            shadows.add(prefix)
    return frozenset(shadows)


def _reexport_maps(
    facts: dict[str, _ModuleFacts], known: frozenset[str]
) -> dict[str, dict[str, str]]:
    """`{package: {exported name: the module it actually lives in}}`.

    Re-exports live in package `__init__.py` files. Without this map, `from
    archy.install import run_install` resolves to the package `archy.install`,
    and the module that really defines `run_install` (`archy.install.runner`)
    never appears -- a FALSE NEGATIVE on archy's own source, which `archy
    impact` gets right. Mirrors `graph._build_reexport_maps`; the two must
    agree, because a lookup that contradicts the dependency graph is worse
    than no lookup.
    """
    maps: dict[str, dict[str, str]] = {}
    for qualname, f in facts.items():
        if not f.is_package:
            continue
        pkg_map: dict[str, str] = {}
        for base, names in f.import_froms:
            # Only a SELF-referential import re-exports. `graph._reexport_source`
            # skips anything else, on the grounds that re-exporting a module from
            # outside the package says nothing about where the package's own names
            # live; treating it as a re-export routed `from pkgA import Thing` to
            # `pkgB.impl` and reported nothing importing `pkgA`, where the graph
            # reports the `pkgA` edge. A relative import always lands inside the
            # package by construction, so this one check covers both forms.
            if base != qualname and not base.startswith(qualname + "."):
                continue
            for name, local in names:
                # `from .x import Foo` re-exports Foo from the submodule `x`;
                # `from .x import y` where `x.y` is itself a module is a
                # module import, not a symbol re-export.
                source = f"{base}.{name}" if f"{base}.{name}" in known else base
                if source in known and source != qualname:
                    pkg_map[local] = source
        if pkg_map:
            maps[qualname] = pkg_map
    # The graph's own walker, not a copy of it. The two resolvers have to agree
    # about where a re-exported name lives, and a second hand-synchronized
    # implementation is exactly the drift this module already had to fix.
    _follow_reexport_chains(maps)
    return maps


def _resolved_imports(
    facts: _ModuleFacts, known: frozenset[str], reexports: dict[str, dict[str, str]]
) -> set[str]:
    """The project modules `facts` imports, resolved against what exists.

    The resolution itself is `graph.resolve_from_import`, not a copy of it, so
    this lookup and archy's own dependency graph cannot give different answers.
    They did, five ways, before the algorithm was shared (#414, #419).
    """
    resolved = {m for m in facts.imported_modules if m in known}
    for base, names in facts.import_froms:
        resolved |= set(resolve_from_import(base, (n for n, _ in names), known, reexports))
    return resolved - {facts.qualname}


def _try_collect(module: Module) -> _ModuleFacts | None:
    """Parse and census one module, or None when it cannot be read.

    Advisory command: an unreadable or half-written file is a fact to report,
    never a reason to fail. Shared so the two callers cannot drift on WHICH
    failures are tolerated -- a narrower except tuple in one of them would turn
    a work-in-progress file into a crash on only one of the two surfaces.
    """
    try:
        tree = ast.parse(module.path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        return None
    return _collect(tree, module.qualname, is_package=module.is_package)


def _classify_module(
    qualname: str, shadows: frozenset[str], *, include_tests: bool
) -> tuple[str, str]:
    """Which partition bucket `qualname` falls in, and why, in ONE check order.

    Both the ranked census and the single-module lookup have to answer this,
    and they used to answer it separately. The copies drifted in check order,
    so a test module inside a shadow root was counted as a test by one and
    reported as shadowed by the other: the same module, two different answers
    about why it was set aside. A lookup whose whole purpose is being trusted
    in the negative cannot afford to disagree with the census it sits beside,
    so the order lives here once and neither caller gets its own.
    """
    if _is_nonsource_module(qualname):
        return "nonsource", "set aside: under a dot-directory, not library source"
    if not include_tests and _is_test_module(qualname):
        return "test", "set aside: a test module -- pass --include-tests to census it"
    root_of = next(
        (sh for sh in sorted(shadows) if qualname == sh or qualname.startswith(sh + ".")), None
    )
    if root_of:
        return "shadowed", f"set aside: in {root_of}, which duplicates its parent"
    return "censused", "censused"


def censused_modules(qualnames: Iterable[str], *, include_tests: bool = False) -> frozenset[str]:
    """The subset of `qualnames` the census actually looked at.

    A third caller of `_classify_module`, for the same reason the other two
    share it: a surface that reports on modules the census set aside is
    reporting about a population the census never measured. `--emit-headers`
    got this wrong first time and wrote a derived block into all 143 modules,
    tests included, while the census had looked at 79 of them.
    """
    names = list(qualnames)
    shadows = _shadow_roots(names)
    return frozenset(
        name
        for name in names
        if _classify_module(name, shadows, include_tests=include_tests)[0] == "censused"
    )


def _home_module(by_module: Counter[str]) -> tuple[str, int]:
    """The module hosting the most members of a family, and how many it hosts.

    One definition for all three family kinds, so "where does a new one go"
    cannot come out differently depending on which report asked (#415).

    Ties are broken by `Counter.most_common`, which is insertion order, so the
    winner is whichever module the scan reached first. That is not a meaningful
    ordering; it is recorded here rather than at three call sites so a caller
    who needs a deterministic tie-break has one place to change.

    Callers pass a non-empty counter. `_base_families` is the only one that can
    produce an empty one (its members may all be missing from `home_of`), and it
    guards before calling.
    """
    return by_module.most_common(1)[0]


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
    facts = list(facts)
    home_of: dict[str, str] = {}
    node_of: dict[str, ast.ClassDef] = {}
    parents: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        for cls in f.classes:
            home_of.setdefault(cls.name, f.qualname)
            node_of.setdefault(cls.name, cls)
            for base in cls.bases:
                name = _dotted(base).split(".")[-1]
                if name:
                    parents[cls.name].add(name)

    def ancestors(name: str) -> set[str]:
        seen: set[str] = set()
        stack = list(parents.get(name, ()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(parents.get(cur, ()))
        return seen

    members: dict[str, set[str]] = defaultdict(set)
    for name in node_of:
        for base in ancestors(name):
            if base in node_of:  # local definitions only; see the docstring
                members[base].add(name)
                # The class that names the family belongs to it. Without this the
                # root's own declarations are invisible, and the root is exactly
                # where a family states its defaults: `click` sets
                # `exit_code = 1` on `ClickException` and `2` on `UsageError`,
                # which is the whole severity convention and reads as a single
                # unexplained override if the base is left out.
                members[base].add(base)

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
    calls: dict[str, list[tuple[str, str, ast.Call]]] = defaultdict(list)
    for f in facts:
        for node in f.body_assignments:
            if not isinstance(node.value, ast.Call):
                continue
            ctor = _dotted(node.value.func).split(".")[-1]
            if not ctor or not ctor[:1].isupper():
                continue
            # Type-system plumbing is declared this way in every typed project
            # and is identical everywhere, so it out-counts real registries while
            # telling an agent nothing about this repository.
            if ctor in _TYPING_PLUMBING:
                continue
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    calls[ctor].append((target.id, f.qualname, node.value))
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
            if (
                call.args
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                first = call.args[0].value
                # 🔴 Slugs only. `mypy.message_registry` declares 102 entries whose
                # first argument is a full diagnostic sentence, and a census that
                # echoed them would emit tens of kilobytes of prose -- spending the
                # context this command exists to save. A name an agent could copy is
                # short and has no spaces; anything else is content, not convention.
                if first and len(first) <= 40 and " " not in first:
                    literals.append(first)
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


# Directive targets: reStructuredText `.. autoclass:: Thing` and the
# mkdocstrings `::: pkg.Thing` form. Both name a symbol unambiguously.
_DOC_DIRECTIVE = re.compile(
    r"^\s*(?:\.\.\s+(?:auto)?(?:class|exception|function|data|attribute|method|module)::"
    r"|:::)\s*([\w.]+)",
    re.M,
)
# Anything inside a code span. Deliberately generous: see DocGap's docstring.
_DOC_CODE_SPAN = re.compile(r"`{1,2}\s*([A-Za-z_][\w.-]*)\s*`{1,2}")
# Bare lowercase-hyphenated slugs anywhere in the prose. Strictness is scaled
# to how ambiguous a token is: `Path` or `Command` in a sentence says nothing,
# so identifiers must appear in a code span or a directive, but `arg-type` is
# not an English word and a bare-word match is safe. Without this, `mypy` --
# which documents each of its 79 codes as `[arg-type]` in a section heading --
# reads as documenting 13 of them.
_DOC_SLUG = re.compile(r"(?<![\w-])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?![\w-])")
# A bracketed token is markup, not prose, so an ambiguous single word is safe
# inside one. `mypy` heads each section `Check argument types [arg-type]`, and
# six of its codes are ordinary English words -- `override`, `abstract`,
# `syntax` -- which no hyphen rule can reach and which were being reported as
# undocumented while their own section headings named them.
_DOC_BRACKET = re.compile(r"\[([A-Za-z_][\w.-]*)\]")
_DOC_SUFFIXES = (".rst", ".md")
_DOC_MAX_BYTES = 2_000_000

# A partial-coverage gap is only worth reporting where the surface already
# governs most of the family. One member mentioned or exported in passing is
# not a promise about the rest, so `_doc_gaps` and `_export_gaps` share this
# floor rather than each carrying its own copy of the same policy.
_PARTIAL_COVERAGE_FLOOR = 0.5


def _is_partial_coverage(covered: int, total: int, *, min_count: int) -> bool:
    """Is this family covered enough to be worth reporting a gap on, and not fully?

    Three conditions, and `_doc_gaps` and `_export_gaps` need all three to agree
    (#415/#416): enough members covered to be a promise rather than a passing
    mention, not already complete (a complete family has no gap), and above the
    shared floor. They already shared the constant, which is what made the drift
    risk real: two copies of the arithmetic could disagree about how the one
    number is applied while still looking governed by it.

    `min_count` is the only part that differs: 2 for exports, `min_documented`
    for docs.
    """
    if covered < min_count or covered == total:
        return False
    return covered / total >= _PARTIAL_COVERAGE_FLOOR


def _harvest_docs(root: Path, ignored_dirs: frozenset[str]) -> tuple[dict[str, set[str]], int]:
    """Identifiers named in each documentation directory, and the file count.

    Keyed on the top-level directory so the report can say *where* a family
    is documented; a project with `docs/` and a separate `website/` should
    not have a gap in one hidden by coverage in the other.
    """
    found: dict[str, set[str]] = defaultdict(set)
    seen = 0
    for path in sorted(root.rglob("*")):
        if path.suffix not in _DOC_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in ignored_dirs or part.startswith(".") for part in rel.parts[:-1]):
            continue
        try:
            if path.stat().st_size > _DOC_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        seen += 1
        key = rel.parts[0] if len(rel.parts) > 1 else "."
        names = found[key]
        for match in _DOC_DIRECTIVE.finditer(text):
            names.add(match.group(1).rsplit(".", 1)[-1])
        for match in _DOC_CODE_SPAN.finditer(text):
            names.add(match.group(1).rsplit(".", 1)[-1])
        for match in _DOC_SLUG.finditer(text):
            names.add(match.group(1))
        for match in _DOC_BRACKET.finditer(text):
            names.add(match.group(1).rsplit(".", 1)[-1])
    return dict(found), seen


def _doc_gaps(
    families: tuple[BaseFamily, ...],
    registries: tuple[RegistryEntry, ...],
    docs: dict[str, set[str]],
    *,
    min_documented: int = 2,
) -> tuple[DocGap, ...]:
    """Public family members a documentation directory does not name."""
    groups: list[tuple[str, tuple[str, ...]]] = [(f.base, f.members) for f in families]
    # A registry's values are documented by the string they are declared with,
    # not by the constant that holds them: `mypy` documents `arg-type`, and
    # splits its codes across two files by whether they are on by default, so
    # for that project the docs *are* the gate convention.
    groups += [(f"{r.constructor}(...)", r.literal_names) for r in registries if r.literal_names]
    out: list[DocGap] = []
    for name, members in groups:
        # Private names are not a documentation defect; a project is entitled
        # to leave `_PydanticGeneralMetadata` out of its docs on purpose.
        public = tuple(m for m in members if not m.startswith("_"))
        if len(public) < min_documented:
            continue
        # One row per family, from the directory that documents it best. A
        # project with a `docs/` tree and a README covers most families in
        # both, and reporting each twice buries the real gaps under near
        # duplicates that differ only in which stray mention they caught.
        best: DocGap | None = None
        for doc_root, names in sorted(docs.items()):
            documented = [m for m in public if m in names]
            if not _is_partial_coverage(len(documented), len(public), min_count=min_documented):
                continue
            if best is None or len(documented) > best.documented:
                best = DocGap(
                    doc_root=doc_root,
                    family=name,
                    documented=len(documented),
                    defined=len(public),
                    missing=tuple(sorted(set(public) - names)),
                )
        if best is not None:
            out.append(best)
    out.sort(key=lambda g: (-(g.defined - g.documented), g.doc_root, g.family))
    return tuple(out)


def _export_gaps(
    facts: Iterable[_ModuleFacts], families: tuple[BaseFamily, ...]
) -> tuple[ExportGap, ...]:
    """Family members missing from an ``__all__`` that already lists their siblings."""
    exports = {f.qualname: f.exports for f in facts if f.exports is not None}
    if not exports:
        return ()
    out: list[ExportGap] = []
    for family in families:
        for module, names in exports.items():
            listed = [m for m in family.members if m in names]
            if not _is_partial_coverage(len(listed), len(family.members), min_count=2):
                continue
            out.append(
                ExportGap(
                    export_module=module,
                    family=family.base,
                    exported=len(listed),
                    defined=len(family.members),
                    missing=tuple(sorted(set(family.members) - set(names))),
                )
            )
    out.sort(key=lambda g: (-(g.defined - g.exported), g.export_module))
    return tuple(out)


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

    # 🔴 EVERY module that defines the name, not the first one seen. Keying on
    # the bare name and taking the first definition is a WRONG ANSWER, not a
    # missing one: with `foo` defined in both `pkg.a` and `pkg.b`, an importer
    # of `pkg.b.foo` was reported as consuming `pkg.a`, pointing an agent at a
    # module it must not edit. This repository carries seven such collisions
    # (`_load_graph`, `_graph_kwargs` and `_score_to_dict` among them).
    defined_in: dict[str, set[str]] = defaultdict(set)
    for f in facts:
        for node in f.classes:
            defined_in[node.name].add(f.qualname)
        for node in f.functions:
            defined_in[node.name].add(f.qualname)
    # Keyed by (home, name), never by name alone. Two modules can define the
    # same name and each be imported unambiguously; merging them into one
    # family reports half its consumers against the wrong home, which is the
    # same wrong answer one level up.
    consumers: dict[tuple[str, str], set[str]] = defaultdict(set)
    for f in facts:
        for source, name in f.internal_imports:
            candidates = defined_in.get(name)
            if not candidates:
                continue  # not a symbol this project defines
            if source in candidates:
                home = source
            elif len(candidates) == 1:
                # A relative import, or one written through a re-export. The
                # name has one definition project-wide, so there is nothing to
                # get wrong.
                home = next(iter(candidates))
            else:
                # Ambiguous and unresolved. Saying nothing beats naming the
                # wrong module, which is the failure this section causes most.
                continue
            # A module importing from itself is not a surface.
            if home != f.qualname:
                consumers[(home, name)].add(f.qualname)
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

    # Cross-module first, then by size. See the docstring: a same-module family is
    # wired in one edit; a cross-module one is what gets half-wired.
    out.sort(key=lambda s: (0 if s.kind == "helper" else -1, -s.surface_count, s.kind, s.stem))
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


def _literal_code(call: ast.expr | None) -> int | None:
    """The literal exit status of an exit call, or None when it is computed.

    `sys.exit(0 if ok else 1)` deliberately returns None: two codes are
    possible and reporting either one would be a lie.
    """
    if not isinstance(call, ast.Call) or not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, int):
        return first.value
    return None


def _exit_site(node: ast.stmt) -> tuple[str, str, int | None] | None:
    """`(category, kind, code)` for an exiting statement, else None.

    A raised framework exception is the ERROR channel (the framework picks
    the code); an explicit exit call or `SystemExit` is the GATE channel
    (the code is the result). See `Gate` for why the split matters.
    """
    if isinstance(node, ast.Raise) and node.exc is not None:
        call = node.exc if isinstance(node.exc, ast.Call) else None
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = _dotted(exc).rsplit(".", 1)[-1]
        if name == "SystemExit":
            return ("gate", name, _literal_code(call))
        if name in _EXIT_RAISES:
            return ("error", name, None)
        return None
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        dotted = _dotted(call.func)
        if dotted in _EXIT_CALLS or dotted.rsplit(".", 1)[-1] == "exit":
            return ("gate", dotted, _literal_code(call))
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
    out: list[tuple[tuple[str, str, int | None], ast.expr | None]],
) -> None:
    """Walk statements, remembering the innermost `if` test above each exit."""
    for node in body:
        site = _exit_site(node)
        if site is not None:
            out.append((site, condition))
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


def _gates(facts: Iterable[_ModuleFacts]) -> tuple[tuple[Gate, ...], tuple[Gate, ...]]:
    """`(gates, errors)`.

    Only FUNCTION bodies are scanned, which is also what drops the
    `if __name__ == "__main__": sys.exit(main())` entry point: it is
    module-level, and it forwards a return value rather than stating a
    verdict, so counting it as a gate would inflate every Click project by
    exactly one.
    """
    found_gates: list[Gate] = []
    found_errors: list[Gate] = []
    for f in facts:
        for fn in f.functions:
            found: list[tuple[tuple[str, str, int | None], ast.expr | None]] = []
            _scan_gates(fn.body, condition=None, out=found)
            if not found:
                continue
            flags = _click_flags(fn)
            args = fn.args
            params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
            for (category, kind, code), condition in found:
                site = Gate(
                    module=f.qualname,
                    function=fn.name,
                    category=category,
                    kind=kind,
                    code=code,
                    control=_control_for(condition, params, flags),
                    is_command=_is_command(fn),
                )
                (found_gates if category == "gate" else found_errors).append(site)
    key = lambda g: (g.module, g.function, g.kind, g.control)  # noqa: E731
    found_gates.sort(key=key)
    found_errors.sort(key=key)
    return tuple(found_gates), tuple(found_errors)


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
    include_tests: bool = False,
) -> ConventionsReport:
    """Derive `root`'s house style from its own source.

    `min_family` is the floor for reporting a naming family or a mirrored
    surface set: at 2 a single pair counts, which is the right default for
    a small project and noisy on a large one.
    """
    modules = list(discover_modules(root, ignored_dirs=ignored_dirs, extra_roots=extra_roots))
    # 🔴 Partition BEFORE parsing, and report what was set aside. A census run
    # over tests and vendored copies answers a different question from the one
    # asked; see ModulePartition for the measured failure that motivated this.
    shadows = _shadow_roots(m.qualname for m in modules)
    counts: Counter[str] = Counter()
    kept = []
    for module in modules:
        bucket, _ = _classify_module(module.qualname, shadows, include_tests=include_tests)
        counts[bucket] += 1
        if bucket == "censused":
            kept.append(module)
    n_tests, n_shadowed, n_nonsource = (
        counts["test"],
        counts["shadowed"],
        counts["nonsource"],
    )
    modules = kept
    facts: list[_ModuleFacts] = []
    unparsed = 0
    for module in modules:
        collected = _try_collect(module)
        if collected is None:
            unparsed += 1
            continue
        facts.append(collected)

    gates, errors = _gates(facts)
    bases = _base_families(facts, min_count=min_family)
    registries = _registries(facts, min_count=min_family)
    docs, docs_seen = _harvest_docs(root, frozenset(ignored_dirs))
    return ConventionsReport(
        root=str(root),
        modules_scanned=len(facts),
        modules_unparsed=unparsed,
        naming=_naming_families(facts, min_count=min_family),
        surfaces=_surface_families(facts, min_count=min_family),
        gates=gates,
        errors=errors,
        models=_model_census(facts),
        bases=bases,
        registries=registries,
        export_gaps=_export_gaps(facts, bases),
        doc_gaps=_doc_gaps(bases, registries, docs),
        docs_scanned=docs_seen,
        partition=ModulePartition(
            production=len(facts),
            tests=n_tests,
            shadowed=n_shadowed,
            nonsource=n_nonsource,
            shadow_roots=tuple(sorted(shadows)),
        ),
    )


def compute_module_view(
    root: Path,
    module: str,
    *,
    ignored_dirs: Iterable[str] = DEFAULT_IGNORED_DIRS,
    extra_roots: Iterable[str] = (),
    include_tests: bool = False,
) -> ModuleView:
    """The complete, unranked picture of one module. See ModuleView.

    Note that this parses EVERY module regardless of `include_tests`, because
    "who imports me" is a question about the whole project: a module imported
    only by tests is imported, and reporting it as unused because tests were set
    aside would be a false negative of exactly the kind this function exists to
    prevent. `include_tests` decides `status`, not the search.
    """
    modules = list(discover_modules(root, ignored_dirs=ignored_dirs, extra_roots=extra_roots))
    shadows = _shadow_roots(m.qualname for m in modules)

    facts: dict[str, _ModuleFacts] = {}
    for m in modules:
        collected = _try_collect(m)
        if collected is not None:
            facts[m.qualname] = collected

    if module not in facts:
        near = sorted(q for q in facts if module in q or q.endswith("." + module))
        raise LookupError(
            f"no module {module!r} under {root}"
            + (f"; did you mean {', '.join(near[:5])}?" if near else "")
        )

    f = facts[module]
    known = frozenset(facts)
    reexports = _reexport_maps(facts, known)
    resolved = {q: _resolved_imports(g, known, reexports) for q, g in facts.items()}
    # Complete on both sides. A "does X import Y" question is answered by the
    # presence or ABSENCE of Y here, so a partial list is worse than none.
    imports = sorted(resolved[module] - {module})
    by = sorted(q for q, mods in resolved.items() if q != module and module in mods)
    fams = sorted({camel_suffix(c.name) for c in f.classes})
    gates, errors = _gates([f])
    return ModuleView(
        module=module,
        status=_classify_module(module, shadows, include_tests=include_tests)[1],
        classes=tuple(sorted(c.name for c in f.classes)),
        functions=tuple(sorted(fn.name for fn in f.functions)),
        imports_internal=tuple(imports),
        imported_by=tuple(by),
        exports=tuple(sorted(f.exports)) if f.exports is not None else None,
        suffix_families=tuple(fams),
        gates=tuple(gates) + tuple(errors),
    )
