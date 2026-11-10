"""Tests for layers: config."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from archy.layers import (
    ForbidRule,
    LayerConfigError,
    compute_coverage,
    discover_config,
    governed_roots,
    load_config,
    match_layer,
)
from tests.layers_helpers import _cfg


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
    assert _cfg(tmp_path, "layers:\n  core: {modules: [myapp.core.**]}\nforbid: []\n").exclude == ()


def test_load_config_exclude_parsed(tmp_path: Path):
    config = _cfg(
        tmp_path,
        "layers:\n  core: {modules: [myapp.core.**]}\n"
        "forbid: []\n"
        "exclude:\n  - baml_client\n  - generated\n",
    )
    assert config.exclude == ("baml_client", "generated")


def test_load_config_exclude_must_be_list_of_strings(tmp_path: Path):
    with pytest.raises(LayerConfigError, match="exclude"):
        _cfg(
            tmp_path,
            "layers: {core: {modules: [myapp.core.**]}}\nforbid: []\nexclude: not_a_list\n",
        )


def test_load_config_roots_omitted_defaults_to_empty(tmp_path: Path):
    assert _cfg(tmp_path, "layers:\n  core: {modules: [myapp.core.**]}\nforbid: []\n").roots == ()


def test_load_config_roots_parsed(tmp_path: Path):
    config = _cfg(
        tmp_path,
        "layers:\n  core: {modules: [app.**]}\nforbid: []\nroots:\n  - app\n  - experiments\n",
    )
    assert config.roots == ("app", "experiments")


def test_load_config_roots_must_be_list_of_strings(tmp_path: Path):
    with pytest.raises(LayerConfigError, match="roots"):
        _cfg(tmp_path, "layers: {core: {modules: [app.**]}}\nforbid: []\nroots: not_a_list\n")


@pytest.mark.parametrize(
    "bad",
    [
        "**",  # leading ** -> contracts root extraction would yield "**"
        "*",  # leading * -> root "*"
        "**.*",  # floating needs a real package name after the **
        "**.",  # nothing after the **
        "**.import",  # keyword is never a package name
        "*foo",  # wildcard not a whole segment
        "foo**bar",  # ** not a whole segment -> previously a wrong regex
        ".foo",  # leading dot -> empty root segment
        "foo.",  # trailing dot -> empty trailing segment
        "a..b",  # doubled dot -> empty segment between them
        "import",  # Python keyword -> never an importable package name
        "class.foo",  # keyword root would defer to a cryptic import-linter error
    ],
)
def test_load_config_rejects_malformed_layer_pattern(tmp_path: Path, bad: str):
    # Malformed patterns must fail at load with a clear archy error rather than
    # surfacing later as a cryptic import-linter ModuleNotFoundError or a
    # silently-wrong match regex. Quote the pattern to keep YAML happy.
    with pytest.raises(LayerConfigError, match="invalid module pattern"):
        _cfg(tmp_path, f'layers:\n  core: {{modules: ["{bad}"]}}\nforbid: []\n')


@pytest.mark.parametrize("good", ["myapp", "myapp.core.**", "myapp.*", "app.routers.user"])
def test_load_config_accepts_canonical_layer_patterns(tmp_path: Path, good: str):
    config = _cfg(tmp_path, f'layers:\n  core: {{modules: ["{good}"]}}\nforbid: []\n')
    assert config.layers[0].patterns == (good,)


def test_floating_pattern_matches_whole_segments_at_any_depth(tmp_path: Path):
    config = _cfg(tmp_path, 'layers:\n  svc: {modules: ["**.services.**"]}\nforbid: []\n')
    layers = config.layers
    assert match_layer("services", layers) == "svc"  # zero leading segments
    assert match_layer("conduit.services", layers) == "svc"
    assert match_layer("conduit.services.user", layers) == "svc"
    assert match_layer("a.b.services.user.deep", layers) == "svc"
    assert match_layer("myservices.x", layers) is None  # whole segment only
    assert match_layer("conduit.myservices", layers) is None
    assert match_layer("conduit.services_x", layers) is None


def test_coverage_counts_modules_a_floating_pattern_reaches(tmp_path: Path):
    """A floating pattern names no root, so coverage must not scope by one.

    `lib.services.z` sits under a root (`lib`) that no pattern's first segment
    names. Scoping by the bogus root `**` counted it as outside and zeroed the
    coverage of a config whose only layer is floating.
    """
    config = _cfg(tmp_path, 'layers:\n  svc: {modules: ["**.services.**"]}\nforbid: []\n')
    graph = nx.DiGraph()
    graph.add_edge("lib.services.z", "lib.services.y")
    graph.add_edge("lib.other", "lib.services.y")
    coverage = compute_coverage(graph, config)
    assert governed_roots(config) == frozenset()
    assert coverage.modules_outside_declared_roots == 0
    assert coverage.modules_total == 3
    assert coverage.modules_matched == 2
    assert coverage.unlayered_modules == ("lib.other",)
    assert coverage.edges_governed == 1


def test_floating_pattern_overlap_is_a_loud_error(tmp_path: Path):
    config = _cfg(
        tmp_path,
        'layers:\n  api: {modules: ["**.api.**"]}\n  data: {modules: ["**.data.**"]}\nforbid: []\n',
    )
    with pytest.raises(LayerConfigError, match="matches multiple layers"):
        match_layer("conduit.api.data", config.layers)


def test_discover_finds_config_in_parent(tmp_path: Path):
    (tmp_path / "archy.yaml").write_text("layers: {}\n")
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    assert discover_config(nested) == (tmp_path / "archy.yaml")


def test_discover_returns_none_when_absent(tmp_path: Path):
    assert discover_config(tmp_path) is None


def test_load_config_max_modules_valid(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers: {}\nforbid: []\nmax_modules: 2500\n")
    assert load_config(cfg).max_modules == 2500


def test_load_config_max_modules_absent_is_none(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers: {}\nforbid: []\n")
    assert load_config(cfg).max_modules is None


def test_load_config_max_modules_zero_allowed(tmp_path: Path):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text("layers: {}\nforbid: []\nmax_modules: 0\n")
    assert load_config(cfg).max_modules == 0


@pytest.mark.parametrize("bad", ["-1", "3.5", "'lots'", "true"])
def test_load_config_max_modules_rejects_invalid(tmp_path: Path, bad: str):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(f"layers: {{}}\nforbid: []\nmax_modules: {bad}\n")
    with pytest.raises(LayerConfigError, match="max_modules"):
        load_config(cfg)


def test_bare_qualname_pattern_hints_at_unlayered_descendants(tmp_path: Path):
    """The walkthrough's own config shape: `modules: ["shipping.store"]`.

    `_translate_pattern` matches that exactly, so every submodule is unlayered
    and no forbid rule can fire -- while import-linter, matching the identical
    string by package, governs the whole subtree.
    """
    config = _cfg(
        tmp_path,
        "layers:\n"
        "  store:\n"
        "    modules: ['shipping.store']\n"
        "  api:\n"
        "    modules: ['shipping.api']\n"
        "forbid:\n"
        "  - {from: store, to: api}\n",
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(
        ["shipping.store", "shipping.store.repository", "shipping.api", "shipping.api.context"]
    )
    graph.add_edges_from([("shipping.store.repository", "shipping.api.context")])

    coverage = compute_coverage(graph, config)

    assert coverage.governs_no_edges is True
    hints = {hint.layer: hint for hint in coverage.exact_pattern_hints}
    assert set(hints) == {"store", "api"}
    assert hints["store"].pattern == "shipping.store"
    assert hints["store"].unlayered_descendants == ("shipping.store.repository",)
    assert hints["store"].suggestion == "shipping.store.**"


def test_glob_patterns_never_hint(tmp_path: Path):
    """A glob author got what globbing gives; an unlayered descendant of one is
    a different problem, and hinting "you wrote an exact pattern" about it would
    be a false positive.

    `shipping.*` matches a single segment, so `shipping.store` is layered and
    `shipping.store.repository` is not. That unlayered descendant is the fixture
    precondition: it is exactly what a bare `shipping.store` pattern would
    (correctly) be hinted about, so it is asserted first. The earlier fixture
    used `shipping.store.**`, which matched both nodes and left nothing
    unlayered, and the hint scan iterates only over unlayered modules (#440).
    """
    config = _cfg(
        tmp_path,
        "layers:\n  store:\n    modules: ['shipping.*']\nforbid: []\n",
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["shipping.store", "shipping.store.repository"])

    coverage = compute_coverage(graph, config)

    assert coverage.unlayered_modules == ("shipping.store.repository",)
    assert coverage.exact_pattern_hints == ()


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("false", False), (None, None)])
def test_load_config_exclude_type_checking_imports(tmp_path: Path, raw: str | None, expected):
    cfg = tmp_path / "archy.yaml"
    extra = "" if raw is None else f"exclude_type_checking_imports: {raw}\n"
    cfg.write_text("layers: {}\nforbid: []\n" + extra)
    assert load_config(cfg).exclude_type_checking_imports is expected


@pytest.mark.parametrize("bad", ["1", "'yes'", "[true]"])
def test_load_config_exclude_type_checking_imports_rejects_a_non_boolean(tmp_path: Path, bad: str):
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(f"layers: {{}}\nforbid: []\nexclude_type_checking_imports: {bad}\n")
    with pytest.raises(LayerConfigError, match="exclude_type_checking_imports"):
        load_config(cfg)
