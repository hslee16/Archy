"""Tests for layers: reach."""

from __future__ import annotations

import re
from pathlib import Path

import networkx as nx
import pytest

from archy.layers import (
    LayerConfigError,
    RequiredRule,
    find_reach_violations,
)
from tests.layers_helpers import (
    _ENTRYPOINTS_CONFIG,
    _REGISTRY_CONFIG,
    _cfg,
    _entrypoint_graph,
    _reach_graph,
)


def test_required_rule_still_rejects_floating_pattern(tmp_path: Path):
    body = (
        "layers:\n  a: {modules: [app.**]}\nforbid: []\n"
        'required:\n  - {source: "**.x.**", must_reach: "app.**"}\n'
    )
    with pytest.raises(LayerConfigError, match="must start with a Python package name"):
        _cfg(tmp_path, body)


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


def test_required_reach_a_source_that_is_itself_the_target_is_satisfied(tmp_path: Path):
    """A module matching both sides reaches itself, even with no outgoing edge.

    `descendants` excludes the node itself, so without the explicit membership
    check `commands.registry` would be reported for not reaching `commands.registry`.
    The sibling that imports nothing proves the rule still fires in this graph.
    """
    config = _cfg(
        tmp_path,
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: 'commands.**'\n"
        "    must_reach: commands.registry\n",
    )
    graph = _reach_graph(nodes=("commands.registry", "commands.other"))

    assert [v.module for v in find_reach_violations(graph, config)] == ["commands.other"]


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
    """Configs predating the feature parse to an empty `required`, so they keep
    their exit codes exactly.

    The `find_reach_violations(...) == []` line that used to follow is entailed
    by `config.required == ()` on the line above: the function loops over
    `config.required`, so an empty one returns `[]` under every implementation
    (#441). The default is the real claim, and it is the one asserted.
    """
    config = _cfg(tmp_path, "layers:\n  a:\n    modules: ['a.**']\nforbid: []\n")

    assert config.required == ()


def test_required_reach_parses_the_rule(tmp_path: Path):
    config = _cfg(tmp_path, _REGISTRY_CONFIG)

    assert config.required == (
        RequiredRule(
            source="commands.**",
            must_reach="core.database.model_registry",
            reason="standalone entrypoints need the full mapper registry",
        ),
    )


def test_required_reach_entrypoints_catch_a_script_no_glob_named(tmp_path: Path):
    """The reason `@entrypoints` exists, with its negative control.

    `app.worker` sits outside `app.commands`, so the glob a careful author
    would have written (`app.commands.*`) never sees it. Derived entrypoints
    do. Then the same graph with the one import that fixes it: the rule must
    move, or it is decorative.
    """
    config = _cfg(tmp_path, _ENTRYPOINTS_CONFIG)
    entrypoints = {"app.commands.setup_user": "guard", "app.worker": "guard"}
    broken = _entrypoint_graph(("app.commands", "app.core.registry"), entrypoints=entrypoints)

    [violation] = find_reach_violations(broken, config)
    assert violation.module == "app.worker"
    assert violation.detail.endswith("It is an entrypoint: guard.")

    fixed = broken.copy()
    fixed.add_edge("app.worker", "app.core.registry", lines=(1,))
    assert find_reach_violations(fixed, config) == []


def test_required_reach_entrypoints_honour_within(tmp_path: Path):
    """A guarded test module is an entrypoint too; `within` is how a rule leaves it out.

    The fixture's out-of-scope module is a violation without `within`, which is
    what makes the scoped pass mean something.
    """
    entrypoints = {"app.worker": "guard", "tests.test_worker": "guard"}
    graph = _entrypoint_graph(("app.worker", "app.core.registry"), entrypoints=entrypoints)
    unscoped = _cfg(tmp_path, _ENTRYPOINTS_CONFIG.replace("    within: 'app.**'\n", ""))
    assert [v.module for v in find_reach_violations(graph, unscoped)] == ["tests.test_worker"]

    assert find_reach_violations(graph, _cfg(tmp_path, _ENTRYPOINTS_CONFIG)) == []


def test_required_reach_entrypoints_reach_through_the_package_init(tmp_path: Path):
    """Same transitive semantics as a written source: the bootstrap covers a
    derived entrypoint that imports nothing itself."""
    config = _cfg(tmp_path, _ENTRYPOINTS_CONFIG)
    graph = _entrypoint_graph(
        ("app.commands", "app.core.registry"),
        entrypoints={"app.commands.setup_user": "guard"},
    )

    assert find_reach_violations(graph, config) == []


@pytest.mark.parametrize(
    "entrypoints",
    [
        pytest.param({}, id="nothing-derived"),
        pytest.param({"tests.test_worker": "guard"}, id="nothing-within"),
        pytest.param(None, id="graph-without-derivation"),
    ],
)
def test_required_reach_entrypoints_that_bind_nothing_are_a_dead_rule(
    tmp_path: Path, entrypoints: dict[str, str] | None
):
    """Silence would read as "every entrypoint reaches the registry"."""
    config = _cfg(tmp_path, _ENTRYPOINTS_CONFIG)
    graph = _reach_graph(("app.core", "app.core.registry"), nodes=("tests.test_worker",))
    if entrypoints is not None:
        graph.graph["entrypoints"] = entrypoints

    [violation] = find_reach_violations(graph, config)

    assert violation.module is None
    assert "no entrypoint was derived within 'app.**'" in violation.detail


def test_required_reach_entrypoints_refuse_a_verdict_on_a_partial_derivation(tmp_path: Path):
    """Satisfied modules plus a pyproject that could not be read is not a pass."""
    config = _cfg(tmp_path, _ENTRYPOINTS_CONFIG)
    graph = _entrypoint_graph(
        ("app.worker", "app.core.registry"), entrypoints={"app.worker": "guard"}
    )
    assert find_reach_violations(graph, config) == []

    graph.graph["entrypoint_errors"] = ("pyproject.toml could not be read",)
    [violation] = find_reach_violations(graph, config)

    assert violation.module is None
    assert "could not be fully derived: pyproject.toml could not be read" in violation.detail


def test_required_reach_parses_an_entrypoints_rule(tmp_path: Path):
    config = _cfg(tmp_path, _ENTRYPOINTS_CONFIG)

    assert config.required == (
        RequiredRule(
            source="@entrypoints",
            must_reach="app.core.registry",
            reason="entrypoints need the mapper registry",
            within="app.**",
        ),
    )
    assert config.required[0].describe_source() == "@entrypoints within app.**"


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
        (
            "required:\n  - source: '@entrypoints'\n    within: '**'\n    must_reach: a.b\n",
            "invalid `within` pattern",
        ),
        (
            "required:\n  - source: '@entrypoints'\n    within: 3\n    must_reach: a.b\n",
            "`within` must be a string",
        ),
        (
            "required:\n  - source: 'app.**'\n    within: 'app.x.**'\n    must_reach: a.b\n",
            "`within` only applies to `source: '@entrypoints'`",
        ),
        (
            "required:\n  - source: '@main'\n    must_reach: a.b\n",
            "invalid `source` pattern",
        ),
    ],
)
def test_required_rejects_malformed_config(tmp_path: Path, body: str, message: str):
    with pytest.raises(LayerConfigError, match=re.escape(message)):
        _cfg(tmp_path, f"layers: {{}}\nforbid: []\n{body}")
