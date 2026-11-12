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

archy:owns        compute_conventions, compute_module_view
archy:mirrored-by compute_conventions -> archy.cli_conventions, archy.mcp_runs_check,
                  bench.conventions_bench,
                  compute_module_view -> archy.cli_conventions, archy.mcp_runs_check
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import networkx as nx

from archy.conventions_collect import (
    _classify_module,
    _collect_all,
    _ModuleFacts,
    _reexport_maps,
    _resolved_imports,
    _shadow_roots,
    censused_modules,
)
from archy.conventions_docs import _doc_gaps, _export_gaps, _harvest_docs
from archy.conventions_families import (
    _base_families,
    _model_census,
    _naming_families,
    _registries,
    _surface_families,
)
from archy.conventions_gates import _gates
from archy.conventions_models import (
    ConventionsReport,
    Gate,
    ModulePartition,
    ModuleView,
    NamingFamily,
    UnparsedModule,
    camel_suffix,
)
from archy.graph import (
    DEFAULT_IGNORED_DIRS,
    Module,
    discover_modules,
    suggest_files,
    suggest_modules,
)

__all__ = [
    "ConventionsReport",
    "Gate",
    "ModuleView",
    "NamingFamily",
    "camel_suffix",
    "censused_modules",
    "compute_conventions",
    "compute_module_view",
]


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
    facts, skipped = _collect_all(modules, root)

    gates, errors = _gates(facts)
    bases = _base_families(facts, min_count=min_family)
    registries = _registries(facts, min_count=min_family)
    docs, docs_seen = _harvest_docs(root, frozenset(ignored_dirs))
    return ConventionsReport(
        root=str(root),
        modules_scanned=len(facts),
        modules_unparsed=len(skipped),
        unparsed_modules=tuple(skipped),
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

    if _looks_like_path(module) and module not in {m.qualname for m in modules}:
        # Callers think in file paths, and the graph surfaces resolve them
        # (`archy_graph(focus=["myapp/core.py"])` finds `myapp.core`), so a
        # path naming a discovered file resolves here too. An unresolved
        # path falls through with its spelling intact, so the refusal below
        # can answer it with file hints instead of a bare no.
        module = _resolve_module_path(root, modules, module) or module

    collected, skipped = _collect_all(modules, root)
    facts = {f.qualname: f for f in collected}
    unparsed = tuple(skipped)

    if module not in facts:
        return _missing_module_view(
            module, root=root, modules=modules, facts=facts, unparsed=unparsed
        )

    f = facts[module]
    known = frozenset(facts)
    reexports = _reexport_maps(facts, known)
    resolved = {q: _resolved_imports(g, known, reexports) for q, g in facts.items()}
    # Complete on both sides. A "does X import Y" question is answered by the
    # presence or ABSENCE of Y here, so a partial list is worse than none.
    # When any module was unparsed, that completeness claim is false (#666).
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
        census_complete=not unparsed,
        unparsed_modules=unparsed,
    )


def _missing_module_view(
    module: str,
    *,
    root: Path,
    modules: list[Module],
    facts: dict[str, _ModuleFacts],
    unparsed: tuple[UnparsedModule, ...],
) -> ModuleView:
    """The answer for a module the census did not collect.

    Split out of `compute_module_view`: the collected path there is a
    straight line, and this detour is where the branching lives — a view
    for a module that exists but did not parse, a refusal for one that
    does not exist.
    """
    miss = next((u for u in unparsed if u.module == module), None)
    if miss is not None:
        # The module exists on disk but this interpreter cannot census it.
        # Returning a view (not LookupError) keeps the negative answerable:
        # the lists are empty because parsing failed, not because the name
        # is unknown (#666).
        return ModuleView(
            module=module,
            status=(f"could not be parsed (Python {miss.python}): {miss.reason} ({miss.path})"),
            classes=(),
            functions=(),
            imports_internal=(),
            imported_by=(),
            exports=None,
            suffix_families=(),
            gates=(),
            census_complete=False,
            unparsed_modules=(miss,),
        )
    if _looks_like_path(module):
        # `suggest_modules` compares dotted names; a mistyped path is
        # answered by the file hints, whose answer is translated back
        # to the qualname this refusal speaks in.
        near = _suggest_module_path(root, modules, module)
    else:
        near = suggest_modules(facts, module)
    # Also suggest unparsed names: an agent asking for pkg.cache after a
    # SyntaxError skip must not be told the module does not exist.
    near_unparsed = [u.module for u in unparsed if module.split(".")[-1] in u.module]
    suggestions = list(dict.fromkeys([*near, *near_unparsed]))
    raise LookupError(
        f"no module {module!r} under {root}"
        + (f"; did you mean {', '.join(suggestions[:5])}?" if suggestions else "")
    )


def _looks_like_path(module: str) -> bool:
    """A module argument spelled as a file path rather than a dotted name."""
    return module.endswith(".py") or "/" in module


def _module_rel_paths(root: Path, modules: list[Module]) -> dict[str, str]:
    """`{qualname: POSIX path relative to root}` for the modules under `root`."""
    resolved_root = root.resolve()
    rel_paths: dict[str, str] = {}
    for m in modules:
        try:
            rel_paths[m.qualname] = m.path.relative_to(resolved_root).as_posix()
        except ValueError:
            continue
    return rel_paths


def _resolve_module_path(root: Path, modules: list[Module], typed: str) -> str | None:
    """The qualname a file path names, or None when it names none or several.

    The path may be absolute, relative to the root, or carry extra leading
    directories (a checkout prefix, or a `src/` the caller left on): a
    discovered file matches when its path is the typed one, ends with it, or
    is ended by it. Only an unambiguous match resolves; several matches
    would be a guess, and a lookup that guesses breaks the refusal contract
    the same way resolving a bare name would.
    """
    typed_posix = typed.replace("\\", "/").removeprefix("./")
    rel_paths = _module_rel_paths(root, modules)
    matches = {
        m.qualname
        for m in modules
        if str(m.path) == typed
        or rel_paths.get(m.qualname) == typed_posix
        or rel_paths.get(m.qualname, "").endswith("/" + typed_posix)
        or typed_posix.endswith("/" + rel_paths.get(m.qualname, "\0"))
    }
    return matches.pop() if len(matches) == 1 else None


def _suggest_module_path(root: Path, modules: list[Module], typed: str) -> list[str]:
    """Qualnames a mistyped file path was probably meant to name, best first.

    `suggest_files` answers in the caller's form (paths); this refusal
    speaks in qualnames like every other module-view refusal, so the paths
    it returns are mapped back through the module list. The graph built
    here carries only the nodes `suggest_files` reads (module paths under
    a root); no edges are needed to compare file names.
    """
    graph = nx.DiGraph()
    graph.graph["root"] = str(root)
    for m in modules:
        graph.add_node(m.qualname, path=str(m.path))
    hints = suggest_files(graph, [typed], base=root)
    if not hints:
        return []
    by_form: dict[str, str] = {str(m.path): m.qualname for m in modules}
    for qualname, rel in _module_rel_paths(root, modules).items():
        by_form[rel] = qualname
    return [by_form[form] for form in hints[0].did_you_mean if form in by_form]
