"""Renderers for `impact`, `affected` and `graph`.

archy:mirrored-by _affected_to_dict -> archy.cli_graph, archy.cli_render,
                  _affected_to_text -> archy.cli_graph, archy.cli_render,
                  _deleted_lines -> archy.cli_graph, archy.cli_render,
                  _graph_to_text -> archy.cli_graph, archy.cli_render,
                  _impact_to_dict -> archy.cli_graph, archy.cli_render,
                  _impact_to_text -> archy.cli_graph, archy.cli_render
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import networkx as nx

from archy.affected import DELETED_OR_RENAMED_REASON, Affected
from archy.cli_render_common import _format_lines
from archy.impact import Impact


def _affected_to_dict(result: Affected) -> dict:
    return {
        "changed": list(result.changed),
        "unresolved": list(result.unresolved),
        "deleted": list(result.deleted),
        "deleted_reason": result.deleted_reason,
        "impacted_modules": list(result.impacted_modules),
        "impacted_tests": list(result.impacted_tests),
        "depth": result.depth,
        "test_filter": result.test_filter,
        "empty_caveat": result.empty_caveat,
    }


def _deleted_lines(deleted: Sequence[str]) -> list[str]:
    if not deleted:
        return []
    head, _, tail = DELETED_OR_RENAMED_REASON.partition("; ")
    return [
        f"# {len(deleted)} {head} .py file(s); {tail}:",
        *(f"  ? {path}" for path in deleted),
    ]


def _unresolved_and_changed_lines(
    unresolved: Sequence[str],
    changed: Sequence[str],
    did_you_mean: Mapping[str, Sequence[str]] | None = None,
) -> list[str]:
    """The `did not resolve` block and the `Changed:` list shared by `impact` and `affected`."""
    suggestions = did_you_mean or {}
    lines: list[str] = []
    if unresolved:
        lines.append(
            f"# {len(unresolved)} file(s) did not resolve to a module "
            "(non-Python, excluded, or outside any package):"
        )
        lines.extend(
            f"  ? {f}"
            + (f"  (did you mean {', '.join(suggestions[f])}?)" if f in suggestions else "")
            for f in unresolved
        )
    if changed:
        lines.append("")
        lines.append("Changed:")
        lines.extend(f"  - {q}" for q in changed)
    return lines


def _affected_to_text(result: Affected) -> str:
    lines = [
        f"# depth={result.depth}, "
        f"{len(result.impacted_tests)} test(s), "
        f"{len(result.impacted_modules)} module(s) "
        f"affected by {len(result.changed)} changed module(s)"
    ]
    if result.test_filter:
        lines.append(f"# test filter: {result.test_filter}")
    lines.extend(_deleted_lines(result.deleted))
    lines.extend(_unresolved_and_changed_lines(result.unresolved, result.changed))
    if result.impacted_tests:
        lines.append("")
        lines.append("Tests to run:")
        for q in result.impacted_tests:
            lines.append(f"  - {q}")
    if result.impacted_modules:
        lines.append("")
        lines.append("Other modules touched:")
        for q in result.impacted_modules:
            lines.append(f"  - {q}")
    if result.empty_caveat:
        lines.append("")
        lines.append(f"# {result.empty_caveat}")
    return "\n".join(lines)


def _impact_to_dict(result: Impact) -> dict:
    return {
        "changed": list(result.changed),
        "unresolved": list(result.unresolved),
        "unresolved_hints": [
            {"ref": h.ref, "did_you_mean": list(h.did_you_mean)} for h in result.unresolved_hints
        ],
        "nothing_resolved": result.nothing_resolved,
        "impacted": list(result.impacted),
        "chains": [
            {
                "impacted": c.impacted,
                "changed": c.changed,
                "via": list(c.via),
                "hops": [
                    {"source": h.source, "target": h.target, "lines": list(h.lines)} for h in c.hops
                ],
            }
            for c in result.chains
        ],
        "chains_omitted": result.chains_omitted,
        "empty_caveat": result.empty_caveat,
    }


def _impact_to_text(result: Impact) -> str:
    if result.nothing_resolved:
        # "0 module(s) depend on 0 changed module(s)" reads as a clean answer; no
        # file was looked up, so there is no answer.
        headline = (
            f"# nothing analysed: none of {len(result.unresolved)} file(s) resolved to a module, "
            "so no blast radius was computed"
        )
    else:
        headline = (
            f"# {len(result.impacted)} module(s) depend on {len(result.changed)} changed module(s)"
        )
    lines = [headline]
    lines.extend(
        _unresolved_and_changed_lines(
            result.unresolved,
            result.changed,
            {h.ref: h.did_you_mean for h in result.unresolved_hints},
        )
    )
    if result.impacted:
        lines.append("")
        lines.append("Impacted (transitive dependents):")
        for q in result.impacted:
            lines.append(f"  - {q}")
    if result.empty_caveat:
        lines.append("")
        lines.append(f"# {result.empty_caveat}")
    if result.chains:
        lines.append("")
        lines.append("Why (shortest import path to a changed module):")
        for c in result.chains:
            lines.append(f"  - {' -> '.join(c.via)}")
            for h in c.hops:
                lines.append(f"      {h.source} imports {h.target}  {_format_lines(h.lines)}")
        if result.chains_omitted:
            lines.append(
                f"  # {result.chains_omitted} more impacted module(s) not shown "
                "(raise --max-chains)."
            )
    return "\n".join(lines)


def _graph_to_text(g: nx.DiGraph) -> str:
    internal = sorted(n for n, d in g.nodes(data=True) if not d.get("external"))
    lines = [f"# {len(internal)} internal module(s), {g.number_of_edges()} import edge(s)"]
    for n in internal:
        lines.append(f"{n}")
        for t in sorted(g.successors(n)):
            marker = "ext" if g.nodes[t].get("external") else "int"
            lines.append(f"  -> [{marker}] {t}")
    return "\n".join(lines)
