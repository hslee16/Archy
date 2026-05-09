from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from archy.layers import (
    ForbidRule,
    LayerConfig,
    LayerConfigError,
    LayerSpec,
    discover_config,
    find_violations,
    load_config,
    match_layer,
)


def _g(*edges: tuple[str, str, tuple[int, ...]]) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for u, v, lines in edges:
        g.add_edge(u, v, lines=lines)
    return g


# --- pattern matching ---------------------------------------------------------


def test_match_exact_qualname():
    layers = (LayerSpec("core", ("myapp.core",)),)
    assert match_layer("myapp.core", layers) == "core"
    assert match_layer("myapp.core.x", layers) is None


def test_match_double_star_includes_package_and_descendants():
    layers = (LayerSpec("core", ("myapp.core.**",)),)
    assert match_layer("myapp.core", layers) == "core"
    assert match_layer("myapp.core.x", layers) == "core"
    assert match_layer("myapp.core.x.y", layers) == "core"
    assert match_layer("myapp.other", layers) is None


def test_match_single_star_one_segment():
    layers = (LayerSpec("apps", ("myapp.*",)),)
    assert match_layer("myapp.cli", layers) == "apps"
    assert match_layer("myapp.cli.commands", layers) is None
    assert match_layer("myapp", layers) is None


def test_match_returns_none_for_unlayered():
    layers = (LayerSpec("core", ("myapp.core.**",)),)
    assert match_layer("third_party.lib", layers) is None


def test_match_raises_on_ambiguous_overlap():
    layers = (
        LayerSpec("a", ("myapp.**",)),
        LayerSpec("b", ("myapp.core.**",)),
    )
    with pytest.raises(LayerConfigError, match="multiple layers"):
        match_layer("myapp.core.thing", layers)


# --- load_config --------------------------------------------------------------


def test_load_config_minimal(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(
        "layers:\n"
        "  core:\n"
        "    modules: [myapp.core.**]\n"
        "  cli:\n"
        "    modules: [myapp.cli.**]\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    config = load_config(cfg)
    assert {layer.name for layer in config.layers} == {"core", "cli"}
    assert config.forbid == (ForbidRule("core", "cli"),)


def test_load_config_missing_file(tmp_path: Path):
    with pytest.raises(LayerConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")


def test_load_config_malformed_yaml(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers: {core: [unclosed]\n")
    with pytest.raises(LayerConfigError, match="parse YAML"):
        load_config(cfg)


def test_load_config_unknown_layer_in_forbid(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(
        "layers:\n  core: {modules: [myapp.core.**]}\nforbid:\n  - {from: core, to: ghost}\n"
    )
    with pytest.raises(LayerConfigError, match="ghost"):
        load_config(cfg)


def test_load_config_missing_forbid_key(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers:\n  core: {modules: [myapp.core.**]}\nforbid:\n  - {from: core}\n")
    with pytest.raises(LayerConfigError, match="missing required key"):
        load_config(cfg)


def test_load_config_exclude_omitted_defaults_to_empty(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers:\n  core: {modules: [myapp.core.**]}\nforbid: []\n")
    assert load_config(cfg).exclude == ()


def test_load_config_exclude_parsed(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(
        "layers:\n  core: {modules: [myapp.core.**]}\n"
        "forbid: []\n"
        "exclude:\n  - baml_client\n  - generated\n"
    )
    assert load_config(cfg).exclude == ("baml_client", "generated")


def test_load_config_exclude_must_be_list_of_strings(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers: {core: {modules: [myapp.core.**]}}\nforbid: []\nexclude: not_a_list\n")
    with pytest.raises(LayerConfigError, match="exclude"):
        load_config(cfg)


def test_load_config_roots_omitted_defaults_to_empty(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers:\n  core: {modules: [myapp.core.**]}\nforbid: []\n")
    assert load_config(cfg).roots == ()


def test_load_config_roots_parsed(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(
        "layers:\n  core: {modules: [app.**]}\nforbid: []\nroots:\n  - app\n  - experiments\n"
    )
    assert load_config(cfg).roots == ("app", "experiments")


def test_load_config_roots_must_be_list_of_strings(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers: {core: {modules: [app.**]}}\nforbid: []\nroots: not_a_list\n")
    with pytest.raises(LayerConfigError, match="roots"):
        load_config(cfg)


# --- discover_config ----------------------------------------------------------


def test_discover_finds_config_in_parent(tmp_path: Path):
    (tmp_path / "archy.yaml").write_text("layers: {}\n")
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    assert discover_config(nested) == (tmp_path / "archy.yaml")


def test_discover_returns_none_when_absent(tmp_path: Path):
    assert discover_config(tmp_path) is None


# --- find_violations ----------------------------------------------------------


def test_violations_reports_forbidden_edge():
    g = _g(("myapp.core.user", "myapp.cli.runner", (12,)))
    config = LayerConfig(
        layers=(
            LayerSpec("core", ("myapp.core.**",)),
            LayerSpec("cli", ("myapp.cli.**",)),
        ),
        forbid=(ForbidRule("core", "cli"),),
    )
    [violation] = find_violations(g, config)
    assert violation.rule == ForbidRule("core", "cli")
    assert violation.source == "myapp.core.user"
    assert violation.target == "myapp.cli.runner"
    assert violation.lines == (12,)


def test_violations_ignores_allowed_edge():
    g = _g(("myapp.cli.runner", "myapp.core.user", (1,)))
    config = LayerConfig(
        layers=(
            LayerSpec("core", ("myapp.core.**",)),
            LayerSpec("cli", ("myapp.cli.**",)),
        ),
        forbid=(ForbidRule("core", "cli"),),
    )
    assert find_violations(g, config) == []


def test_violations_ignores_unlayered_endpoints():
    g = _g(("third_party.x", "myapp.cli.y", (1,)))
    config = LayerConfig(
        layers=(LayerSpec("cli", ("myapp.cli.**",)),),
        forbid=(),
    )
    assert find_violations(g, config) == []


def test_violations_aggregates_per_edge_lines():
    g = _g(("myapp.core.user", "myapp.cli.runner", (2, 5, 9)))
    config = LayerConfig(
        layers=(
            LayerSpec("core", ("myapp.core.**",)),
            LayerSpec("cli", ("myapp.cli.**",)),
        ),
        forbid=(ForbidRule("core", "cli"),),
    )
    [violation] = find_violations(g, config)
    assert violation.lines == (2, 5, 9)


def test_violations_sorted_by_rule_then_endpoints():
    g = _g(
        ("myapp.core.b", "myapp.cli.x", (1,)),
        ("myapp.core.a", "myapp.cli.y", (2,)),
        ("myapp.core.a", "myapp.cli.x", (3,)),
    )
    config = LayerConfig(
        layers=(
            LayerSpec("core", ("myapp.core.**",)),
            LayerSpec("cli", ("myapp.cli.**",)),
        ),
        forbid=(ForbidRule("core", "cli"),),
    )
    pairs = [(v.source, v.target) for v in find_violations(g, config)]
    assert pairs == [
        ("myapp.core.a", "myapp.cli.x"),
        ("myapp.core.a", "myapp.cli.y"),
        ("myapp.core.b", "myapp.cli.x"),
    ]
