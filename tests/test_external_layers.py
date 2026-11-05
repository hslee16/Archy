"""Layers that name a third-party package (#527).

`orm: {modules: ["thirdpartyorm.**"]}` always fired on a direct import, because
external packages are graph nodes, while presence, coverage and contracts all
treated the layer as nonexistent. Each part is asserted on every surface that
reports it, on the serialized form (`model_dump()`), because FastMCP sends that
and drops plain properties.

`thirdpartyorm` is not installed anywhere: an external package only has to be
imported by the scanned tree to become a node.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.contracts import ContractsConfigError, run_contracts
from archy.graph import build_graph
from archy.layers import LayerConfigError, compute_coverage, find_violations, load_config
from archy.mcp_payloads import CheckPayload
from archy.mcp_runs import run_check

EXTERNAL = "thirdpartyorm"


def _project(
    root: Path,
    *,
    domain_imports_orm: bool,
    orm_pattern: str = f"{EXTERNAL}.**",
    extra_yaml: str = "",
) -> Path:
    """`myapp.domain.model` and `myapp.service.repo`; only `repo` always imports the ORM."""
    for package in ("myapp", "myapp/domain", "myapp/service"):
        (root / package).mkdir(parents=True, exist_ok=True)
        (root / package / "__init__.py").write_text("")
    domain = f"import {EXTERNAL}\nfrom {EXTERNAL}.orm import Session\n"
    (root / "myapp/domain/model.py").write_text(domain if domain_imports_orm else "x = 1\n")
    (root / "myapp/service/repo.py").write_text(f"import {EXTERNAL}\n")
    (root / "archy.yaml").write_text(
        "layers:\n"
        "  domain: {modules: ['myapp.domain.**']}\n"
        "  service: {modules: ['myapp.service.**']}\n"
        f"  orm: {{modules: ['{orm_pattern}']}}\n"
        "forbid:\n  - {from: domain, to: orm}\n" + extra_yaml
    )
    return root


def test_a_layer_naming_an_external_package_is_present(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=True)
    config = load_config(root / "archy.yaml")
    graph = build_graph(root)

    coverage = compute_coverage(graph, config)

    # The fixture reaches the branch: the layer's only member is an external node.
    assert [n for n, d in graph.nodes(data=True) if d.get("external")] == [EXTERNAL]
    assert dict(coverage.layer_sizes)["orm"] == 1
    assert coverage.empty_layers == ()
    assert coverage.layers_present == 3
    # Two edges reach the ORM (domain.model, service.repo); both are layered.
    assert coverage.external_edges_governed == 2
    # The internal ratio is untouched: no internal edge exists in this tree.
    assert (coverage.edges_total, coverage.edges_governed) == (0, 0)
    assert coverage.governs_no_edges is False
    assert [v.target for v in find_violations(graph, config)] == [EXTERNAL]


def test_a_pattern_matching_no_node_at_all_is_still_an_empty_layer(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=True, orm_pattern="neverimported.**")
    coverage = compute_coverage(build_graph(root), load_config(root / "archy.yaml"))

    assert coverage.empty_layers == ("orm",)
    assert coverage.external_edges_governed == 0


def test_check_text_reports_the_violation_and_does_not_call_the_layer_empty(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=True, extra_yaml="min_layers_present: 3\n")

    result = CliRunner().invoke(main, ["check", str(root)])

    assert result.exit_code == 1, result.output
    assert f"myapp.domain.model -> {EXTERNAL}" in result.output
    assert "empty:" not in result.output
    assert "FAIL:" not in result.output
    assert "2 internal-to-external edge(s) governed" in result.output
    assert "0 of 0 internal edges" in result.output


def test_clean_check_does_not_claim_that_no_forbid_rule_can_fire(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=False)

    result = CliRunner().invoke(main, ["check", str(root)])

    assert result.exit_code == 0, result.output
    assert "no forbid rule can fire" not in result.output


def test_check_json_carries_presence_and_the_external_edge_count(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=True)

    result = CliRunner().invoke(main, ["check", str(root), "--format", "json"])

    coverage = json.loads(result.output)["coverage"]
    assert coverage["layers_present"] == 3
    assert coverage["empty_layers"] == []
    assert coverage["layer_sizes"]["orm"] == 1
    assert coverage["external_edges_governed"] == 2
    assert coverage["governs_no_edges"] is False


def test_mcp_check_passes_the_presence_floor_with_an_external_layer(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=False, extra_yaml="min_layers_present: 3\n")

    payload = run_check(root, config_path=None)

    assert isinstance(payload, CheckPayload)
    dumped = payload.model_dump()
    assert dumped["presence_fails"] is False
    assert dumped["passed"] is True
    assert dumped["coverage"]["layers_present"] == 3
    assert dumped["coverage"]["external_edges_governed"] == 1


def test_mcp_check_reports_the_violation_on_the_wire(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=True)

    dumped = run_check(root, config_path=None).model_dump()

    assert dumped["passed"] is False
    assert [v["target"] for v in dumped["violations"]] == [EXTERNAL]
    assert dumped["coverage"]["external_edges_governed"] == 2


def test_contracts_evaluate_a_forbid_rule_that_names_an_external_package(tmp_path: Path):
    pytest.importorskip("importlinter")
    root = _project(tmp_path, domain_imports_orm=True)

    with pytest.warns(UserWarning, match="best-effort"):
        result = run_contracts(root)

    assert result.unverifiable == 0
    assert result.broken == 1


def test_contracts_hold_when_the_forbidden_external_package_is_not_imported(tmp_path: Path):
    pytest.importorskip("importlinter")
    root = _project(tmp_path, domain_imports_orm=False)

    with pytest.warns(UserWarning, match="best-effort"):
        result = run_contracts(root)

    assert result.broken == 0
    assert result.unverifiable == 0
    assert result.verified


def test_an_external_layer_cannot_be_the_source_of_a_contract(tmp_path: Path):
    pytest.importorskip("importlinter")
    root = _project(tmp_path, domain_imports_orm=True)
    (root / "archy.yaml").write_text(
        "layers:\n"
        "  domain: {modules: ['myapp.domain.**']}\n"
        f"  orm: {{modules: ['{EXTERNAL}.**']}}\n"
        "forbid:\n  - {from: orm, to: domain}\n"
    )

    with pytest.raises(ContractsConfigError, match="external package"):
        run_contracts(root)


DEEP = f"{EXTERNAL}.orm.**"


def test_a_pattern_below_an_external_top_level_name_is_refused_by_check(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=True, orm_pattern=DEEP)

    result = CliRunner().invoke(main, ["check", str(root)])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert DEEP in result.output
    assert f"Use '{EXTERNAL}.**'" in result.output


def test_a_pattern_below_an_external_top_level_name_is_refused_by_mcp_check(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=True, orm_pattern=DEEP)

    with pytest.raises(LayerConfigError, match=r"can never match"):
        run_check(root, config_path=None)


def test_a_pattern_below_an_external_top_level_name_is_refused_by_contracts(tmp_path: Path):
    pytest.importorskip("importlinter")
    root = _project(tmp_path, domain_imports_orm=True, orm_pattern=DEEP)

    with pytest.raises(ContractsConfigError, match="can never match"):
        run_contracts(root)


def test_the_bare_external_name_is_accepted(tmp_path: Path):
    root = _project(tmp_path, domain_imports_orm=True, orm_pattern=EXTERNAL)

    graph = build_graph(root)
    config = load_config(root / "archy.yaml")

    assert [v.target for v in find_violations(graph, config)] == [EXTERNAL]


def test_an_internal_package_that_happens_to_share_a_segment_is_not_refused(tmp_path: Path):
    """Only a first segment that is an EXTERNAL node is judged. `myapp.orm.**` has
    an internal first segment, so it is an ordinary pattern, however deep."""
    root = _project(tmp_path, domain_imports_orm=True, orm_pattern="myapp.orm.**")

    find_violations(build_graph(root), load_config(root / "archy.yaml"))


@pytest.mark.parametrize("field", ["source", "must_reach"])
def test_a_required_rule_below_an_external_name_is_refused_not_called_dead(
    tmp_path: Path, field: str
):
    """The old message said no module imports the package, while `repo.py` does."""
    rule = {"source": "myapp.service.repo", "must_reach": f"{EXTERNAL}.orm"}
    if field == "source":
        rule = {"source": f"{EXTERNAL}.orm", "must_reach": f"{EXTERNAL}"}
    spelled = ", ".join(f"{k}: '{v}'" for k, v in rule.items())
    root = _project(
        tmp_path,
        domain_imports_orm=True,
        extra_yaml=f"required:\n  - {{{spelled}, reason: x}}\n",
    )

    result = CliRunner().invoke(main, ["check", str(root)])

    assert result.exit_code == 1, result.output
    assert f"{EXTERNAL}.orm" in result.output
    assert "can never match" in result.output
    assert "no module imports it" not in result.output
