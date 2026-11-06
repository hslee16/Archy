"""The graph summary's lists break ties by module name, ascending (#500).

Six sorts in `mcp_runs.py` write the same rule, value descending then name, and the
tie-break is part of the wire contract: summary lists must be stable across runs.
Only the brief's edit-risk ranking was pinned; reversing the tie-break at any of
the five summary lists left the suite green. These tests give the refactor to one
shared helper something to go red against.

A fixture where every value differs would leave a tie-break untested, so each
test first asserts that its list really contains equal values.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest

from archy.mcp_payloads import GraphSummaryEntry
from archy.mcp_runs import run_graph_summary

_LISTS = ["top_fan_in", "top_fan_out", "top_pagerank", "top_edit_risk", "external_deps"]


@pytest.fixture
def tied_summary(tmp_path: Path):
    """Three symmetric leaves around one hub, so most values tie.

    `pkg.m1`..`pkg.m3` each import the hub, so they tie on fan-in (0), fan-out (1),
    PageRank and edit risk. `zeta` is written before `alpha` and both are imported
    twice, so the external list ties with the names out of insertion order.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hub.py").write_text("")
    (pkg / "m1.py").write_text("from pkg.hub import x\nimport zeta\nimport alpha\n")
    (pkg / "m2.py").write_text("from pkg.hub import y\nimport zeta\nimport alpha\n")
    (pkg / "m3.py").write_text("from pkg.hub import z\n")
    return run_graph_summary(tmp_path, top_n=10)


def _has_a_tie(entries: tuple[GraphSummaryEntry, ...]) -> bool:
    return any(a.value == b.value for a, b in pairwise(entries))


@pytest.mark.parametrize("name", _LISTS)
def test_every_summary_list_is_value_descending_then_name_ascending(tied_summary, name: str):
    entries = getattr(tied_summary, name)
    assert _has_a_tie(entries), f"{name} has no equal values, so its tie-break is untested"
    assert list(entries) == sorted(entries, key=lambda e: (-e.value, e.module))


def test_fan_in_ties_are_ordered_by_name(tied_summary):
    """Hand-worked: the hub has fan-in 3; `pkg` and the three leaves tie at 0."""
    assert [e.module for e in tied_summary.top_fan_in] == [
        "pkg.hub",
        "pkg",
        "pkg.m1",
        "pkg.m2",
        "pkg.m3",
    ]


def test_fan_out_ties_are_ordered_by_name(tied_summary):
    """Hand-worked: the three leaves import the hub (1 each); `pkg` and the hub import nothing."""
    assert [e.module for e in tied_summary.top_fan_out] == [
        "pkg.m1",
        "pkg.m2",
        "pkg.m3",
        "pkg",
        "pkg.hub",
    ]


def test_external_ties_are_ordered_by_name_not_by_import_order(tied_summary):
    """`zeta` is imported before `alpha`; both are imported twice."""
    assert [(e.module, e.value) for e in tied_summary.external_deps] == [
        ("alpha", 2.0),
        ("zeta", 2.0),
    ]
