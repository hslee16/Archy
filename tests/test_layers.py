from __future__ import annotations

import re
from pathlib import Path

import networkx as nx
import pytest

from archy.layers import (
    ForbidRule,
    LayerConfig,
    LayerConfigError,
    LayerCoverage,
    LayerSpec,
    RequiredRule,
    compute_coverage,
    discover_config,
    find_reach_violations,
    find_violations,
    load_config,
    match_layer,
)


def _g(*edges: tuple[str, str, tuple[int, ...]]) -> nx.DiGraph:
    g: nx.DiGraph = nx.DiGraph()
    for u, v, lines in edges:
        g.add_edge(u, v, lines=lines)
    return g


def _cfg(tmp_path: Path, body: str) -> LayerConfig:
    path = tmp_path / "archy.yaml"
    path.write_text(body)
    return load_config(path)


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
        "*foo",  # wildcard not a whole segment
        "foo**bar",  # ** not a whole segment -> previously a wrong regex
        ".foo",  # leading dot -> empty root segment
        "foo.",  # trailing dot
        "a..b",  # doubled dot
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


# --- max_modules parsing (#216) -----------------------------------------------


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


def _reach_graph(*edges: tuple[str, str], nodes: tuple[str, ...] = ()) -> nx.DiGraph:
    """A graph whose nodes carry the `external=False` marker real scans set."""
    g: nx.DiGraph = nx.DiGraph()
    for node in nodes:
        g.add_node(node, external=False)
    for u, v in edges:
        g.add_node(u, external=False)
        g.add_node(v, external=False)
        g.add_edge(u, v, lines=(1,))
    return g


_REGISTRY_CONFIG = (
    "layers: {}\nforbid: []\n"
    "required:\n"
    "  - source: 'commands.**'\n"
    "    must_reach: core.database.model_registry\n"
    "    reason: standalone entrypoints need the full mapper registry\n"
)


def test_required_reach_bootstrap_import_is_load_bearing(tmp_path: Path):
    """Negative control: the SAME graph, with only the bootstrap edge removed.

    Asked for by the incident reporter, who shipped a guard for this exact bug
    that passed with the fix REVERTED -- it asserted `configure_mappers()`
    succeeds, and with no models imported there are no mappers to configure. The
    generalizable failure is that a reach assertion over an under-connected
    graph passes vacuously, and an under-connected graph is what archy had
    before `package_init_edges`. A test that only asserts the clean case cannot
    tell "the rule is satisfied" from "the rule never fired".

    So both halves are pinned here, on one fixture, with one edge between them.
    """
    config = _cfg(tmp_path, _REGISTRY_CONFIG)
    satisfied = _reach_graph(
        ("commands", "core.database.model_registry"),
        nodes=("commands.setup_user", "commands.backfill", "core.database"),
    )

    assert find_reach_violations(satisfied, config) == []

    # Nothing else changes: drop the one import in `commands/__init__.py`.
    reverted = satisfied.copy()
    reverted.remove_edge("commands", "core.database.model_registry")

    assert [v.module for v in find_reach_violations(reverted, config)] == [
        "commands",
        "commands.backfill",
        "commands.setup_user",
    ]


def test_required_reach_submodule_needs_no_import_of_its_own(tmp_path: Path):
    """A command module with ZERO imports still satisfies a bootstrapped package.

    Surprising enough to pin: `commands.orphan` is an isolated node, yet Python
    runs `commands/__init__.py` before it, so the reach is real. This is the
    incident's fix stated at its limit, and it is what makes the rule usable at
    all -- otherwise every one of the 34 command modules would need its own
    import of the registry.
    """
    config = _cfg(tmp_path, _REGISTRY_CONFIG)
    graph = _reach_graph(
        ("commands", "core.database.model_registry"),
        nodes=("commands.orphan",),
    )

    assert find_reach_violations(graph, config) == []


def test_required_reach_does_not_pass_vacuously_on_an_empty_scan(tmp_path: Path):
    """The vacuity case that survives: nothing scanned, so nothing to reach.

    An empty or misrooted scan makes every reach question trivially unanswerable,
    and silence would read as "all rules hold". Same failure as a coverage report
    saying "0 of 0 modules (100%)".
    """
    config = _cfg(tmp_path, _REGISTRY_CONFIG)

    violations = find_reach_violations(nx.DiGraph(), config)

    assert [v.module for v in violations] == [None]
    assert "`must_reach` pattern" in violations[0].detail


def test_required_reach_flags_the_module_that_cannot_reach_it(tmp_path: Path):
    config = _cfg(tmp_path, _REGISTRY_CONFIG)
    graph = _reach_graph(
        ("commands.setup_user", "core.database.model_registry"),
        nodes=("commands", "commands.backfill"),
    )

    violations = find_reach_violations(graph, config)

    # `commands` is listed too, and correctly: `pkg.**` covers the package
    # module itself, and this fixture's `commands/__init__.py` imports nothing.
    assert [v.module for v in violations] == ["commands", "commands.backfill"]
    assert "does not transitively reach" in violations[1].detail
    assert violations[1].rule.reason.startswith("standalone entrypoints")


def test_required_reach_counts_indirect_paths(tmp_path: Path):
    """Transitive, not direct: a hop through a bootstrap module still satisfies."""
    config = _cfg(tmp_path, _REGISTRY_CONFIG)
    graph = _reach_graph(
        ("commands.setup_user", "commands.bootstrap"),
        ("commands.bootstrap", "core.database.model_registry"),
        ("commands", "commands.bootstrap"),
    )

    assert [v.module for v in find_reach_violations(graph, config)] == []


def test_required_reach_source_pattern_can_exclude_the_package_itself(tmp_path: Path):
    """`pkg.*` scopes the rule to submodules; `pkg.**` includes `pkg/__init__.py`.

    Worth pinning because the two read alike and the choice decides whether an
    empty `__init__.py` is a violation. A package that only *forwards* to the
    registry for its submodules has no reason to reach it itself.
    """
    config = _cfg(
        tmp_path,
        "layers: {}\nforbid: []\n"
        "required:\n  - source: 'commands.*'\n    must_reach: core.registry\n",
    )
    graph = _reach_graph(
        ("commands.setup_user", "core.registry"),
        nodes=("commands",),  # the package itself reaches nothing
    )

    assert find_reach_violations(graph, config) == []


def test_required_reach_reports_a_rule_that_cannot_fire(tmp_path: Path):
    """A dead rule must not read as a clean pass (the #355 failure, inverted).

    `commands` (no `.**`) matches one exact module, so a config that meant the
    package governs nothing. Silence here would be indistinguishable from 34
    modules all satisfying the rule.
    """
    config = _cfg(
        tmp_path,
        "layers: {}\nforbid: []\nrequired:\n  - source: commands\n    must_reach: core.registry\n",
    )
    graph = _reach_graph(("commands.setup_user", "core.registry"))

    violations = find_reach_violations(graph, config)

    assert [v.module for v in violations] == [None]
    assert "cannot fire" in violations[0].detail
    assert "`source` pattern" in violations[0].detail


def test_required_reach_reports_an_unmatched_target(tmp_path: Path):
    config = _cfg(tmp_path, _REGISTRY_CONFIG)
    graph = _reach_graph(("commands.setup_user", "core.database.models"))

    violations = find_reach_violations(graph, config)

    assert [v.module for v in violations] == [None]
    assert "`must_reach` pattern" in violations[0].detail
    assert "no module imports it any more" in violations[0].detail


def test_required_reach_allows_an_external_target(tmp_path: Path):
    config = _cfg(
        tmp_path,
        "layers: {}\nforbid: []\n"
        "required:\n  - source: 'commands.**'\n    must_reach: sqlalchemy\n",
    )
    graph = _reach_graph(nodes=("commands.setup_user",))
    graph.add_node("sqlalchemy", external=True)
    graph.add_edge("commands.setup_user", "sqlalchemy", lines=(1,))

    assert find_reach_violations(graph, config) == []


def test_required_reach_is_absent_by_default(tmp_path: Path):
    """Configs predating the feature keep their exit codes exactly."""
    config = _cfg(tmp_path, "layers:\n  a:\n    modules: ['a.**']\nforbid: []\n")

    assert config.required == ()
    assert find_reach_violations(_reach_graph(nodes=("a.b",)), config) == []


def test_required_reach_parses_the_rule(tmp_path: Path):
    config = _cfg(tmp_path, _REGISTRY_CONFIG)

    assert config.required == (
        RequiredRule(
            source="commands.**",
            must_reach="core.database.model_registry",
            reason="standalone entrypoints need the full mapper registry",
        ),
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("required:\n  - must_reach: core.registry\n", "missing required key 'source'"),
        ("required:\n  - source: 'commands.**'\n", "missing required key 'must_reach'"),
        ("required: 'commands.**'\n", "`required` must be a list"),
        (
            "required:\n  - source: '**'\n    must_reach: core.registry\n",
            "invalid `source` pattern",
        ),
        (
            "required:\n  - source: 'commands.**'\n    must_reach: 'core..registry'\n",
            "invalid `must_reach` pattern",
        ),
        (
            "required:\n  - source: 'commands.**'\n    must_reach: core.registry\n    reason: 3\n",
            "`reason` must be a string",
        ),
    ],
)
def test_required_rejects_malformed_config(tmp_path: Path, body: str, message: str):
    with pytest.raises(LayerConfigError, match=re.escape(message)):
        _cfg(tmp_path, f"layers: {{}}\nforbid: []\n{body}")


@pytest.mark.parametrize("value", ["-1", "'three'", "true"])
def test_min_layers_present_rejects_invalid(tmp_path: Path, value: str):
    with pytest.raises(LayerConfigError, match="min_layers_present"):
        _cfg(
            tmp_path,
            f"min_layers_present: {value}\n"
            "layers:\n  a:\n    modules: ['a.**']\n  b:\n    modules: ['b.**']\n"
            "  c:\n    modules: ['c.**']\n  d:\n    modules: ['d.**']\nforbid: []\n",
        )


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


def test_governs_no_edges_survives_model_dump():
    """MCP serializes coverage with `model_dump()`, which drops properties."""
    coverage = LayerCoverage(
        modules_total=2,
        modules_matched=1,
        modules_in_ruled_layer=0,
        edges_total=1,
        edges_governed=0,
        unlayered_modules=("app.b",),
    )
    dumped = coverage.model_dump()
    assert dumped["governs_no_edges"] is True
    assert dumped["exact_pattern_hints"] == ()


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
