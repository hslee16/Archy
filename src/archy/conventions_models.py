"""The value objects `archy conventions` returns: families, gates, census, gaps, the report.

archy:owns        BaseFamily, ConventionsReport, DocGap, ExportGap, Gate, ModelCensus,
                  ModulePartition, ModuleView, NamingFamily, NamingHome, RegistryEntry,
                  SharedConstant, SurfaceFamily, UnparsedModule, camel_suffix
archy:mirrored-by BaseFamily -> archy.conventions_docs, archy.conventions_families,
                  Gate -> archy.cli_render_common, archy.conventions,
                  archy.conventions_gates, ModuleView -> archy.cli_render_conventions,
                  archy.conventions, archy.mcp_runs_check, archy.mcp_tools_conventions,
                  NamingFamily -> archy.conventions, archy.conventions_families,
                  RegistryEntry -> archy.conventions_docs, archy.conventions_families,
                  camel_suffix -> archy.conventions, archy.conventions_families
"""

from __future__ import annotations

import re
from collections import Counter

from pydantic import BaseModel, ConfigDict, computed_field

# A CamelCase name splits into an acronym run (`DSM`, `MCP`) or a single
# capitalized word. The trailing element is the family: `DSMDiff` -> `Diff`,
# `ReachViolation` -> `Violation`, `Hotspot` -> `Hotspot`.
_CAMEL_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*")


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


class UnparsedModule(BaseModel):
    """A module the census discovered but could not parse.

    Carried by name so a ModuleView can refuse the COMPLETE claim when any
    exist: an importer that failed to parse is invisible to `imported_by`,
    and silence there would read as "nobody imports this" (#666).
    """

    model_config = ConfigDict(frozen=True)

    module: str
    path: str
    reason: str
    python: str


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

    So every list here is COMPLETE for the module named when
    ``census_complete`` is true, and truncating any of them would defeat the
    point. When some modules could not be parsed,
    ``census_complete`` is false and ``unparsed_modules`` names each skip so
    absence from `imported_by` is not mistaken for a negative (#666). `status`
    matters for the same reason: a module that was set aside must say so, or
    its absence reads as "nothing to report" when the truth is "not looked at".
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
    census_complete: bool = True
    unparsed_modules: tuple[UnparsedModule, ...] = ()


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
    unparsed_modules: tuple[UnparsedModule, ...] = ()
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
