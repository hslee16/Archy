"""Renderers for `conventions` and its module view.

archy:mirrored-by _conventions_to_dict -> archy.cli_conventions, archy.cli_render,
                  _conventions_to_text -> archy.cli_conventions, archy.cli_render,
                  _module_view_to_text -> archy.cli_conventions, archy.cli_render
"""

from __future__ import annotations

from archy.cli_render_common import _gate_row, _shown
from archy.conventions import (
    ConventionsReport,
    ModuleView,
)


def _conventions_to_dict(report: ConventionsReport, *, top_n: int) -> dict:
    """Hand-rolled payload, per this module's convention: the CLI decides what
    to truncate, so the JSON says how much it dropped rather than silently
    handing back a short list that looks complete. Gates and errors are never
    truncated -- the whole point of the section is the complete inventory."""
    payload = report.model_dump()
    payload["top_n"] = top_n
    payload["naming"] = [h.model_dump() for h in report.naming[:top_n]]
    payload["surfaces"] = [s.model_dump() for s in report.surfaces[:top_n]]
    payload["gates"] = [g.model_dump() for g in report.gates]
    payload["errors"] = [g.model_dump() for g in report.errors]
    payload["bases"] = [b.model_dump() for b in report.bases[:top_n]]
    payload["registries"] = [
        {**r.model_dump(), "literal_names": list(r.literal_names[:6])}
        for r in report.registries[:top_n]
    ]
    # Export gaps are never truncated: the section exists to be complete, for
    # the same reason gates are. A truncated list of "things you forgot to
    # wire up" is worse than none, because it reads as a finished checklist.
    payload["export_gaps"] = [g.model_dump() for g in report.export_gaps]
    payload["doc_gaps"] = [g.model_dump() for g in report.doc_gaps]
    payload["totals"] = {
        "naming": len(report.naming),
        "surfaces": len(report.surfaces),
        "gates": len(report.gates),
        "errors": len(report.errors),
        "bases": len(report.bases),
        "registries": len(report.registries),
        "export_gaps": len(report.export_gaps),
        "doc_gaps": len(report.doc_gaps),
    }
    return payload


def _module_view_to_text(view: ModuleView) -> str:
    """Terse and COMPLETE. Every list is the whole list; see ModuleView."""
    lines = [f"# {view.module}", f"  status        {view.status}"]
    # Classes and functions separately, each with a count. Completeness is the
    # point of this view, so nothing is dropped -- but a test module defines a
    # hundred `test_*` functions, and one undifferentiated wall is the same
    # delivery failure in a new place. A count lets a reader see the shape
    # before deciding to read the list.
    for label, items in (
        ("classes", view.classes),
        ("functions", view.functions),
        ("imports", view.imports_internal),
        ("imported by", view.imported_by),
        ("exports", view.exports if view.exports is not None else ()),
        ("name families", view.suffix_families),
    ):
        if label == "exports" and view.exports is None:
            lines.append(f"  {label:<13} (no __all__ and no explicit re-exports)")
            continue
        # "(none)" is a real answer here and must not be silence: it is what
        # makes "does this import that" answerable in the negative.
        count = f" ({len(items)})" if len(items) > 6 else ""
        lines.append(f"  {label + count:<13} {', '.join(items) if items else '(none)'}")
    if view.gates:
        lines.append("  exits")
        lines += [_gate_row(g) for g in view.gates]
    if view.census_complete:
        lines.append("  🔴 every list above is COMPLETE for this module; absence is an answer")
    else:
        skipped = view.unparsed_modules
        detail = (
            "; ".join(f"{u.module} ({u.path}): {u.reason} [Python {u.python}]" for u in skipped)
            or "(unparsed modules not listed)"
        )
        lines.append(
            "  ⚠ lists may be INCOMPLETE: "
            f"{len(skipped)} module(s) could not be parsed and were skipped: "
            f"{detail}"
        )
    return "\n".join(lines)


def _conventions_to_text(report: ConventionsReport, *, top_n: int) -> str:
    """Terse on purpose: an agent pays per token to read this."""
    lines = [
        _conventions_headline(report),
        *_set_aside_lines(report),
        "",
        *_naming_lines(report, top_n),
        *_kinds_lines(report, top_n),
        *_registry_lines(report, top_n),
        *_gap_lines(report),
        *_surface_lines(report, top_n),
        *_gate_lines(report),
        *_error_lines(report),
        *_model_lines(report),
    ]
    return "\n".join(lines)


def _conventions_headline(report: ConventionsReport) -> str:
    unparsed = (
        f", {report.modules_unparsed} unparsed ("
        + ", ".join(u.module for u in report.unparsed_modules)
        + ")"
        if report.modules_unparsed
        else ""
    )
    docs = f", {report.docs_scanned} doc file(s)" if report.docs_scanned else ""
    return f"# {report.root}: {report.modules_scanned} module(s) parsed{unparsed}{docs}"


def _set_aside_lines(report: ConventionsReport) -> list[str]:
    part = report.partition
    if not part or not (part.tests or part.shadowed or part.nonsource):
        return []
    parts = (
        f"{part.tests} test" if part.tests else "",
        f"{part.nonsource} non-source" if part.nonsource else "",
        f"{part.shadowed} in {', '.join(part.shadow_roots)} (duplicates its parent)"
        if part.shadowed
        else "",
    )
    return [
        "# set aside: "
        + ", ".join(p for p in parts if p)
        # 🔴 Say how to get them back. Setting tests aside is right when a
        # vendored copy or a fixture pile would otherwise win every count,
        # and wrong when the question IS about tests -- "where do helpers
        # live in this test file, and what are they called" is a real
        # convention this command silently cannot answer. One of the 24
        # scored derivations asked exactly that.
        + ("  (--include-tests to census them)" if part.tests else "")
    ]


def _naming_lines(report: ConventionsReport, top_n: int) -> list[str]:
    lines = [f"## naming - by home module {_shown(len(report.naming), top_n)}"]
    if not report.naming:
        lines.append("  (none: no class-name suffix is shared by enough definitions)")
    for home in report.naming[:top_n]:
        families = " ".join(f"*{f.suffix}({f.count})" for f in home.families)
        lines.append(f"  {home.module:<34} {home.total:>3}  {families}")
        top = home.families[0]
        lines.append(f"  {'':<34}      e.g. {', '.join(top.examples)}")
    return lines


def _kinds_lines(report: ConventionsReport, top_n: int) -> list[str]:
    # Kinds before names. A base family answers "what is it a kind of", which is
    # the question a suffix census silently fails when the two disagree -- and
    # they disagree in most projects, so this section leads.
    lines = [
        "",
        f"## kinds - classes grouped by what they derive from {_shown(len(report.bases), top_n)}",
    ]
    if not report.bases:
        lines.append("  (none: no base defined in this project has enough subclasses)")
    for b in report.bases[:top_n]:
        agree = f"{b.suffix_agreement:.0%}"
        note = (
            ""
            if b.suffix_agreement >= 0.8
            else f"  <- name is NOT the rule ({agree} share a suffix)"
        )
        lines.append(f"  {b.base:<28} {b.count:>3} @ {b.home_module}{note}")
        lines.append(f"  {'':<28}     e.g. {', '.join(b.members[:5])}")
        for c in b.shared_constants[:3]:
            dist = ", ".join(f"{v}x{n}" for v, n in c.distribution)
            lines.append(f"  {'':<28}     {c.name} = {dist}  ({c.setters} of {b.count} set it)")
    return lines


def _registry_lines(report: ConventionsReport, top_n: int) -> list[str]:
    lines = [
        "",
        "## registries - values declared by repeated construction "
        + _shown(len(report.registries), top_n),
    ]
    if not report.registries:
        lines.append("  (none)")
    for r in report.registries[:top_n]:
        lines.append(f"  {r.constructor:<28} {r.count:>3} @ {r.home_module} ({r.home_count})")
        if r.literal_names:
            shown = ", ".join(r.literal_names[:6])
            more = f" (+{len(r.literal_names) - 6})" if len(r.literal_names) > 6 else ""
            lines.append(f"  {'':<28}     names: {shown}{more}")
        for c in r.keyword_defaults[:3]:
            dist = ", ".join(f"{v}x{n}" for v, n in c.distribution)
            # "13 of 80 pass it" is the fact; a bare distribution reads as if the
            # whole family were declared that way, which inverts the meaning of
            # an argument that is usually left at its default.
            lines.append(f"  {'':<28}     {c.name} = {dist}  ({c.setters} of {r.count} pass it)")
    return lines


def _gap_lines(report: ConventionsReport) -> list[str]:
    lines: list[str] = []
    if report.export_gaps:
        lines += [
            "",
            f"## export gaps ({len(report.export_gaps)}) - defined but not re-exported "
            "beside their siblings",
        ]
        for g in report.export_gaps:
            lines.append(
                f"  {g.export_module:<28} {g.family}: {g.exported}/{g.defined}"
                f"  missing {', '.join(g.missing)}"
            )
    if report.doc_gaps:
        lines += [
            "",
            f"## doc gaps ({len(report.doc_gaps)}) - public members their own docs do not name",
        ]
        for g in report.doc_gaps:
            lines.append(
                f"  {g.doc_root + '/':<28} {g.family}: {g.documented}/{g.defined}"
                f"  missing {', '.join(g.missing)}"
            )
    return lines


def _surface_lines(report: ConventionsReport, top_n: int) -> list[str]:
    lines = [
        "",
        f"## surfaces {_shown(len(report.surfaces), top_n)} - wire all of these together",
    ]
    if not report.surfaces:
        lines.append("  (none)")
    for s in report.surfaces[:top_n]:
        where = s.module or "across modules"
        lines.append(
            f"  {s.kind:<8} {s.stem:<28} {s.surface_count} @ {where}: {', '.join(s.surfaces)}"
        )
    return lines


def _gate_lines(report: ConventionsReport) -> list[str]:
    # The question this section exists for is "should MY NEW FINDING gate?", so
    # a finding-failure exit and a bad-input exit must not share a count.
    optional = [g for g in report.gates if g.optional]
    codes = ", ".join(str(c) for c in report.gate_codes) or "none literal"
    lines = [
        "",
        f"## gates ({len(report.gates)} finding-failure exit(s), "
        f"{len(optional)} caller-controlled; exit code(s): {codes})",
    ]
    if not report.gates:
        lines.append("  (none: no finding in this project fails the build)")
    return [*lines, *(_gate_row(g) for g in report.gates)]


def _error_lines(report: ConventionsReport) -> list[str]:
    kinds = ", ".join(
        f"{kind}={sum(1 for g in report.errors if g.kind == kind)}"
        for kind in sorted({g.kind for g in report.errors})
    )
    where = ", ".join(sorted({g.module for g in report.errors}))
    return [
        "",
        f"## errors ({len(report.errors)} user-error exit(s) - bad input, not a finding)",
        f"  {kinds} @ {where}" if report.errors else "  (none)",
    ]


def _model_lines(report: ConventionsReport) -> list[str]:
    m = report.models
    bases = ", ".join(f"{b}={n}" for b, n in m.base_counts[:5])
    return [
        "",
        "## models",
        f"  {m.value_classes} value class(es) of {m.total_classes} class(es); "
        f"{m.frozen_classes} frozen ({m.frozen_ratio:.0%})",
        f"  dominant base: {m.dominant_base or '(none)'}" + (f"  bases: {bases}" if bases else ""),
        f"  config: {', '.join(f'{k}={n}' for k, n in m.config_flags) or '(none)'}",
        f"  collection fields: {m.tuple_fields} tuple / {m.list_fields} list "
        f"({m.tuple_ratio:.0%} tuple)",
    ]
