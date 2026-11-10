"""Tests for layers: coverage."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from archy.layers import (
    ExactPatternHint,
    LayerConfigError,
    LayerCoverage,
    compute_coverage,
)
from tests.layers_helpers import _cfg


def test_coverage_reports_what_the_rules_cannot_reach(tmp_path: Path):
    """The failure this exists to make visible: a clean pass over almost nothing.

    archy's own config governed 9 of 42 modules and 16 of 117 edges while
    `check` reported "No layer violations" (#362).
    """
    config = _cfg(
        tmp_path,
        "layers:\n"
        "  api:\n"
        "    modules: ['app.api']\n"
        "  db:\n"
        "    modules: ['app.db']\n"
        "forbid:\n"
        "  - {from: db, to: api}\n",
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["app.api", "app.db", "app.util", "app.helpers"])
    graph.add_edges_from([("app.api", "app.db"), ("app.util", "app.helpers")])

    coverage = compute_coverage(graph, config)

    assert coverage.modules_total == 4
    assert coverage.modules_matched == 2
    assert coverage.unlayered_modules == ("app.helpers", "app.util")
    # The edge between two unlayered modules cannot be governed by any rule.
    assert coverage.edges_total == 2
    assert coverage.edges_governed == 1
    assert coverage.edge_ratio == 0.5


def test_coverage_excludes_modules_outside_the_declared_roots(tmp_path: Path):
    """Scanning a repo root pulls in bench/ and scripts/ beside the package.

    Counting those would report a fact about the scan path, not about the
    config: archy's own check read 7% before this scoping, 21% after.
    """
    config = _cfg(
        tmp_path,
        "layers:\n  core:\n    modules: ['app.core.**']\nforbid: []\n",
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["app.core.a", "app.other", "benchscript", "conftest"])

    coverage = compute_coverage(graph, config)

    assert coverage.modules_total == 2  # app.* only
    assert coverage.modules_matched == 1
    assert coverage.modules_outside_declared_roots == 2


def test_coverage_separates_layered_from_actually_ruled(tmp_path: Path):
    """A layer no forbid rule names cannot produce a violation."""
    config = _cfg(
        tmp_path,
        "layers:\n"
        "  api:\n"
        "    modules: ['app.api']\n"
        "  db:\n"
        "    modules: ['app.db']\n"
        "  orphan:\n"
        "    modules: ['app.orphan']\n"
        "forbid:\n"
        "  - {from: db, to: api}\n",
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["app.api", "app.db", "app.orphan"])

    coverage = compute_coverage(graph, config)

    assert coverage.modules_matched == 3
    assert coverage.modules_in_ruled_layer == 2  # app.orphan is covered on paper only


def test_coverage_ignores_external_nodes(tmp_path: Path):
    config = _cfg(tmp_path, "layers:\n  core:\n    modules: ['app.core']\nforbid: []\n")
    graph = nx.DiGraph()
    graph.add_node("app.core")
    graph.add_node("requests", external=True)

    coverage = compute_coverage(graph, config)

    assert coverage.modules_total == 1
    assert coverage.modules_outside_declared_roots == 0


def test_coverage_of_an_empty_scope_is_zero_not_perfect(tmp_path: Path):
    """ "0 of 0 modules (100%)" is this class's own failure mode, inside itself.

    Found by pointing a four-layer Clean Architecture config at a single-module
    project: nothing matched the declared roots, and coverage reported 100%.
    """
    config = _cfg(tmp_path, "layers:\n  routes:\n    modules: ['routes.**']\nforbid: []\n")
    graph = nx.DiGraph()
    graph.add_nodes_from(["app"])  # nothing under `routes`

    coverage = compute_coverage(graph, config)

    assert coverage.modules_total == 0
    assert coverage.module_ratio == 0.0
    assert coverage.edge_ratio == 0.0
    assert coverage.governs_nothing is True
    assert coverage.modules_outside_declared_roots == 1


def test_coverage_counts_each_declared_layer(tmp_path: Path):
    config = _cfg(
        tmp_path,
        "layers:\n"
        "  routes:\n    modules: ['routes.**']\n"
        "  services:\n    modules: ['services.**']\n"
        "  ghost:\n    modules: ['ghost.**']\n"
        "forbid: []\n",
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["routes.a", "routes.b", "services.a"])

    coverage = compute_coverage(graph, config)

    assert dict(coverage.layer_sizes) == {"routes": 2, "services": 1, "ghost": 0}
    assert coverage.empty_layers == ("ghost",)
    assert coverage.layers_present == 2


def test_min_layers_present_rejects_a_floor_above_the_declared_count(tmp_path: Path):
    """A config that could never pass is a typo, and its failure would otherwise
    read as a finding about the codebase."""
    with pytest.raises(LayerConfigError, match="could never pass"):
        _cfg(
            tmp_path,
            "min_layers_present: 4\nlayers:\n  a:\n    modules: ['a.**']\nforbid: []\n",
        )


def test_min_layers_present_defaults_to_no_gate(tmp_path: Path):
    config = _cfg(tmp_path, "layers:\n  a:\n    modules: ['a.**']\nforbid: []\n")
    assert config.min_layers_present is None


@pytest.mark.parametrize("value", ["-1", "'three'", "true"])
def test_min_layers_present_rejects_invalid(tmp_path: Path, value: str):
    with pytest.raises(LayerConfigError, match="min_layers_present"):
        _cfg(
            tmp_path,
            f"min_layers_present: {value}\n"
            "layers:\n  a:\n    modules: ['a.**']\n  b:\n    modules: ['b.**']\n"
            "  c:\n    modules: ['c.**']\n  d:\n    modules: ['d.**']\nforbid: []\n",
        )


def test_no_hint_when_a_bare_pattern_has_no_descendants(tmp_path: Path):
    """archy's own archy.yaml is bare-patterned over flat modules.

    A bare pattern is legal, so the hint must key on unlayered descendants
    existing, not on the pattern's shape. Otherwise it fires on this repo.
    """
    config = _cfg(
        tmp_path,
        "layers:\n"
        "  parser:\n"
        "    modules: ['app.parser']\n"
        "  cli:\n"
        "    modules: ['app.cli']\n"
        "forbid:\n"
        "  - {from: parser, to: cli}\n",
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["app.parser", "app.cli"])
    graph.add_edges_from([("app.cli", "app.parser")])

    coverage = compute_coverage(graph, config)

    assert coverage.exact_pattern_hints == ()
    assert coverage.governs_no_edges is False


def test_governs_no_edges_survives_model_dump():
    """MCP serializes coverage with `model_dump()`, which drops plain properties.

    Both fields have to be checked at a value that is not the default.
    `exact_pattern_hints` defaults to `()`, so asserting `== ()` on a coverage
    built without one held whether or not `model_dump()` carried the field at
    all (#441); the hint below is constructed so the dump has something to lose.
    """
    hint = ExactPatternHint(
        layer="store",
        pattern="shipping.store",
        unlayered_descendants=("shipping.store.repository",),
        suggestion="shipping.store.**",
    )
    coverage = LayerCoverage(
        modules_total=2,
        modules_matched=1,
        modules_in_ruled_layer=0,
        edges_total=1,
        edges_governed=0,
        unlayered_modules=("app.b",),
        exact_pattern_hints=(hint,),
    )
    dumped = coverage.model_dump()
    assert dumped["governs_no_edges"] is True
    assert dumped["exact_pattern_hints"] == (hint.model_dump(),)
