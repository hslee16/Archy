"""MCP tool registrations for conventions and module-view.

archy:owns        register_archy_conventions, register_archy_module_view
"""

from __future__ import annotations

from archy.conventions import (
    ConventionsReport,
    ModuleView,
)
from archy.mcp_compat import Server
from archy.mcp_runs import (
    run_conventions,
    run_module_view,
)
from archy.mcp_tools_common import _READ_ONLY_ANNOTATIONS, _project_dir


def register_archy_conventions(server: Server) -> None:
    @server.tool(
        name="archy_conventions",
        title="Report the project's house style",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "**Call before adding a new class, field, or command to an "
            "unfamiliar project.** Derives the repository's own conventions "
            "from its source instead of leaving you to infer them by reading. "
            "Returns five censuses: `naming` (class-name suffix families "
            "GROUPED BY HOME MODULE, so the module you are about to edit "
            "shows its own patterns - the answer to 'what do I call a new one "
            "and where does it go'), `surfaces` (mirrored helper sets like "
            "`_x_to_text`/`_x_to_json` and names defined in several modules - "
            "the answer to 'how many places must I wire a new field'), "
            "`gates` (sites where a FAILED FINDING exits non-zero, each with "
            "its literal exit `code` and the lever that controls it: "
            "`flag:--strict`, `config:<attr>`, `param:<name>`, or "
            "`hardcoded` - the answer to 'should my new finding fail the "
            "build or only warn'), `errors` (the separate, usually much "
            "larger set of USER-ERROR exits - bad config, missing file - "
            "which say nothing about gating and are kept apart so the gate "
            "count stays meaningful), `models` (base-class, frozen, and "
            "tuple-vs-list census over the value types), `bases` (kind "
            "families grouped by what they derive from TRANSITIVELY, "
            "counting only bases this project defines - a family reached "
            "through an intermediate class is reported under the base that "
            "names it, and suffix agreement is stated so a family whose name "
            "is not the rule says so), `registries` (`NAME = Ctor(...)` "
            "families with the distribution of every keyword passed - how a "
            "project declares codes, messages, or flags when it does not use "
            "classes), `export_gaps` (family members absent from an "
            "`__all__`, or from a `from .x import Y as Y` block, that "
            "already lists their siblings) and `doc_gaps` (members the "
            "project's own `.rst`/`.md` surface does not name, where it "
            "already documents most of the family). Advisory only; it "
            "never fails and mutates nothing. `min_family` raises the floor "
            "for reporting a family (default 2, raise it on a large project "
            "to cut noise). `include_tests` censuses test modules too "
            "(default false: test fixtures dilute a real family). This "
            "report is RANKED and truncated, so absence from it proves "
            "nothing; for a negative (`does risk import hotspots`) call "
            "`archy_module_view` instead."
        ),
    )
    def archy_conventions(
        path: str, min_family: int = 2, include_tests: bool = False
    ) -> ConventionsReport:
        return run_conventions(
            _project_dir(path), min_family=min_family, include_tests=include_tests
        )


def register_archy_module_view(server: Server) -> None:
    @server.tool(
        name="archy_module_view",
        title="Everything known about ONE module, complete and unranked",
        annotations=_READ_ONLY_ANNOTATIONS,
        description=(
            "**Call when the question is a NEGATIVE.** `archy_conventions` "
            "ranks and truncates, so a module's absence from it means "
            "nothing; every list here is COMPLETE for the one `module` "
            "named, so absence IS the answer, about STATIC edges only: an "
            "empty `imported_by` does not rule out importlib.import_module / "
            "__import__, entry points, plugin registries, INSTALLED_APPS, "
            "pytest plugins or config-driven dispatch, so `rg` for those "
            "before calling a module dead. Use it for `does risk import "
            "hotspots`, `does anything still reach layers`, `is this module "
            "dead`. Returns `imports_internal` (the project modules this one "
            "imports, with relative imports resolved), `imported_by` (every "
            "project module that imports it, tests included even when they "
            "are otherwise set aside, because a module imported only by "
            "tests IS imported), `classes`, `functions`, `exports` (null "
            "when the module declares no `__all__` and no explicit "
            "re-exports, which is different from an empty one), "
            "`suffix_families`, `gates` (its exit sites) and `status` - "
            "which says whether the module was censused or set aside and "
            "why, so an empty result is never mistaken for `nothing to "
            "report`. Raises on an unknown module name rather than "
            "returning an empty view. Advisory: mutates nothing."
        ),
    )
    def archy_module_view(path: str, module: str, include_tests: bool = False) -> ModuleView:
        return run_module_view(_project_dir(path), module, include_tests=include_tests)
