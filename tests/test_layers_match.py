"""Tests for layers: match."""

from __future__ import annotations

from pathlib import Path

import pytest

from archy.layers import (
    ForbidRule,
    LayerConfig,
    LayerConfigError,
    LayerSpec,
    find_violations,
    load_config,
    match_layer,
)
from tests.layers_helpers import _g


def test_match_exact_qualname():
    layers = (LayerSpec(name="core", patterns=("myapp.core",)),)
    assert match_layer("myapp.core", layers) == "core"
    assert match_layer("myapp.core.x", layers) is None


def test_match_double_star_includes_package_and_descendants():
    layers = (LayerSpec(name="core", patterns=("myapp.core.**",)),)
    assert match_layer("myapp.core", layers) == "core"
    assert match_layer("myapp.core.x", layers) == "core"
    assert match_layer("myapp.core.x.y", layers) == "core"
    assert match_layer("myapp.other", layers) is None


def test_match_single_star_one_segment():
    layers = (LayerSpec(name="apps", patterns=("myapp.*",)),)
    assert match_layer("myapp.cli", layers) == "apps"
    assert match_layer("myapp.cli.commands", layers) is None
    assert match_layer("myapp", layers) is None


def test_match_returns_none_for_unlayered():
    layers = (LayerSpec(name="core", patterns=("myapp.core.**",)),)
    assert match_layer("third_party.lib", layers) is None


def test_match_raises_on_ambiguous_overlap():
    layers = (
        LayerSpec(name="a", patterns=("myapp.**",)),
        LayerSpec(name="b", patterns=("myapp.core.**",)),
    )
    with pytest.raises(LayerConfigError, match="multiple layers"):
        match_layer("myapp.core.thing", layers)


def test_violations_reports_forbidden_edge(core_cli_config: LayerConfig):
    g = _g(("myapp.core.user", "myapp.cli.runner", (12,)))
    [violation] = find_violations(g, core_cli_config)
    assert violation.rule == ForbidRule(from_layer="core", to_layer="cli")
    assert violation.source == "myapp.core.user"
    assert violation.target == "myapp.cli.runner"
    assert violation.lines == (12,)


def test_violations_ignores_allowed_edge(core_cli_config: LayerConfig):
    g = _g(("myapp.cli.runner", "myapp.core.user", (1,)))
    assert find_violations(g, core_cli_config) == []


def test_violations_ignores_unlayered_endpoints(core_cli_config: LayerConfig):
    """An unlayered endpoint belongs to no layer, so no `forbid` rule can fire
    on the edge it sits on.

    The config has to be one whose rule CAN fire, and the target has to sit in
    that rule's `to` layer, so the unlayered source is the only thing standing
    between the fixture and a violation. The control edge pins that: same
    target, a `core` source, one violation. The earlier fixture declared
    `forbid=()`, under which `find_violations` returns `[]` for every graph and
    every layer-matching behaviour (#440).
    """
    assert match_layer("third_party.x", core_cli_config.layers) is None

    unlayered = _g(("third_party.x", "myapp.cli.y", (1,)))
    assert find_violations(unlayered, core_cli_config) == []

    layered = _g(("myapp.core.x", "myapp.cli.y", (1,)))
    assert len(find_violations(layered, core_cli_config)) == 1


def test_violations_aggregates_per_edge_lines(core_cli_config: LayerConfig):
    g = _g(("myapp.core.user", "myapp.cli.runner", (2, 5, 9)))
    [violation] = find_violations(g, core_cli_config)
    assert violation.lines == (2, 5, 9)


def test_violations_sorted_by_rule_then_endpoints(core_cli_config: LayerConfig):
    g = _g(
        ("myapp.core.b", "myapp.cli.x", (1,)),
        ("myapp.core.a", "myapp.cli.y", (2,)),
        ("myapp.core.a", "myapp.cli.x", (3,)),
    )
    pairs = [(v.source, v.target) for v in find_violations(g, core_cli_config)]
    assert pairs == [
        ("myapp.core.a", "myapp.cli.x"),
        ("myapp.core.a", "myapp.cli.y"),
        ("myapp.core.b", "myapp.cli.x"),
    ]


def test_forbid_and_required_report_the_same_shape_of_key_error(tmp_path: Path):
    """Both rule kinds route their key/type validation through one helper, so
    the two messages have to stay recognisably the same shape. They were
    separately inlined before, which is how they could have drifted."""
    base = "layers:\n  api:\n    modules: ['app.api']\n  store:\n    modules: ['app.store']\n"

    missing_forbid = tmp_path / "a.yaml"
    missing_forbid.write_text(base + "forbid:\n  - {from: store}\n")
    with pytest.raises(LayerConfigError, match=r"forbid entry is missing required key 'to'"):
        load_config(missing_forbid)

    missing_required = tmp_path / "b.yaml"
    missing_required.write_text(base + "required:\n  - {source: 'app.**'}\n")
    with pytest.raises(
        LayerConfigError, match=r"required entry is missing required key 'must_reach'"
    ):
        load_config(missing_required)

    bad_type = tmp_path / "c.yaml"
    bad_type.write_text(base + "forbid:\n  - {from: store, to: 3}\n")
    with pytest.raises(LayerConfigError, match=r"forbid `from`/`to` values must be strings"):
        load_config(bad_type)
