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


@pytest.fixture
def core_cli_config() -> LayerConfig:
    return LayerConfig(
        layers=(
            LayerSpec(name="core", patterns=("myapp.core.**",)),
            LayerSpec(name="cli", patterns=("myapp.cli.**",)),
        ),
        forbid=(ForbidRule(from_layer="core", to_layer="cli"),),
    )


# --- pattern matching ---------------------------------------------------------


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
    assert config.forbid == (ForbidRule(from_layer="core", to_layer="cli"),)


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


@pytest.mark.parametrize(
    "bad",
    [
        "**",  # leading ** -> contracts root extraction would yield "**"
        "*",  # leading * -> root "*"
        "*foo",  # wildcard not a whole segment
        "foo**bar",  # ** not a whole segment -> previously a wrong regex
        ".foo",  # leading dot -> empty root segment
        "foo.",  # trailing dot
        "a..b",  # doubled dot
    ],
)
def test_load_config_rejects_malformed_layer_pattern(tmp_path: Path, bad: str):
    # Malformed patterns must fail at load with a clear archy error rather than
    # surfacing later as a cryptic import-linter ModuleNotFoundError or a
    # silently-wrong match regex. Quote the pattern to keep YAML happy.
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(f'layers:\n  core: {{modules: ["{bad}"]}}\nforbid: []\n')
    with pytest.raises(LayerConfigError, match="invalid module pattern"):
        load_config(cfg)


@pytest.mark.parametrize("good", ["myapp", "myapp.core.**", "myapp.*", "app.routers.user"])
def test_load_config_accepts_canonical_layer_patterns(tmp_path: Path, good: str):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(f'layers:\n  core: {{modules: ["{good}"]}}\nforbid: []\n')
    config = load_config(cfg)
    assert config.layers[0].patterns == (good,)


# --- discover_config ----------------------------------------------------------


def test_discover_finds_config_in_parent(tmp_path: Path):
    (tmp_path / "archy.yaml").write_text("layers: {}\n")
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    assert discover_config(nested) == (tmp_path / "archy.yaml")


def test_discover_returns_none_when_absent(tmp_path: Path):
    assert discover_config(tmp_path) is None


# --- find_violations ----------------------------------------------------------


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


def test_violations_ignores_unlayered_endpoints():
    g = _g(("third_party.x", "myapp.cli.y", (1,)))
    config = LayerConfig(
        layers=(LayerSpec(name="cli", patterns=("myapp.cli.**",)),),
        forbid=(),
    )
    assert find_violations(g, config) == []


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
