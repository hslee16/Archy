from __future__ import annotations

from pathlib import Path

import pytest

from archy.graph import build_graph
from archy.impact import find_impact


def _make_chain(tmp_path: Path) -> Path:
    # Three-hop chain so transitive (not just direct) propagation is exercised.
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    libs = pkg / "libs"
    libs.mkdir()
    (libs / "__init__.py").write_text("")
    (libs / "db.py").write_text("import os\n")
    services = pkg / "services"
    services.mkdir()
    (services / "__init__.py").write_text("")
    (services / "auth.py").write_text("from app.libs.db import x\n")
    routers = pkg / "routers"
    routers.mkdir()
    (routers / "__init__.py").write_text("")
    (routers / "user.py").write_text("from app.services.auth import y\n")
    return tmp_path


def test_impact_returns_transitive_dependents(db_impact):
    result = db_impact
    assert result.changed == ("app.libs.db",)
    assert set(result.impacted) == {"app.routers.user", "app.services.auth"}
    assert result.unresolved == ()


@pytest.fixture
def chain(tmp_path: Path):
    """The `_make_chain` project and its graph. Returns `(project, graph)`.

    Only the two propagation-cost tests need the graph; everything else wants
    an impact result, which the fixtures below derive from this one.
    """
    project = _make_chain(tmp_path)
    return project, build_graph(project)


@pytest.fixture
def db_impact(chain):
    """`find_impact` on the `app/libs/db.py` leaf, which most tests use.

    The literal path was written out at every call site, so a rename inside
    `_make_chain` meant editing a dozen tests (#452).
    """
    project, g = chain
    return find_impact(g, [project / "app" / "libs" / "db.py"])


@pytest.fixture
def user_impact(chain):
    """The `app/routers/user.py` case, the module with no internal dependents."""
    project, g = chain
    return find_impact(g, [project / "app" / "routers" / "user.py"])


def _internal_count(g) -> int:
    return sum(1 for _, d in g.nodes(data=True) if not d.get("external"))


def test_impact_propagation_cost_matches_changed_plus_impacted_over_internal(chain):
    # Pins the documented contract of Impact.propagation_cost so a future
    # refactor (e.g. switching to per-changed-file average) can't silently
    # change the semantic.
    project, g = chain
    result = find_impact(g, [project / "app" / "libs" / "db.py"])
    expected = (len(result.changed) + len(result.impacted)) / _internal_count(g)
    assert result.propagation_cost == expected


def test_impact_leaf_with_no_dependents_has_zero_propagation_cost(chain):
    # Touching a sink module is the lower-bound case: changed=1, impacted=0.
    project, g = chain
    result = find_impact(g, [project / "app" / "routers" / "user.py"])
    assert result.propagation_cost == 1 / _internal_count(g)


def test_impact_changed_module_excluded_from_impacted(db_impact):
    result = db_impact
    assert "app.libs.db" not in result.impacted


def test_impact_external_dependents_filtered_out(db_impact):
    # An external node would never appear in the graph as a path, so it
    # cannot be a changed module. But its ancestors can include external
    # nodes if external nodes pull in internal ones; ensure the impacted
    # set is internal-only regardless of how the graph was built.
    result = db_impact
    assert all(q != "os" for q in result.impacted)


def test_impact_leaf_module_has_no_dependents(user_impact):
    result = user_impact
    assert result.changed == ("app.routers.user",)
    assert result.impacted == ()


def test_impact_multiple_files_unioned(tmp_path: Path):
    project = _make_chain(tmp_path)
    # Disjoint second chain ensures multi-file impact unions independent
    # subgraphs rather than short-circuiting on the first resolved path.
    other = project / "other"
    other.mkdir()
    (other / "__init__.py").write_text("")
    (other / "lib.py").write_text("")
    (other / "main.py").write_text("from other.lib import z\n")
    g = build_graph(project)
    result = find_impact(
        g,
        [project / "app" / "libs" / "db.py", project / "other" / "lib.py"],
    )
    assert set(result.changed) == {"app.libs.db", "other.lib"}
    assert set(result.impacted) == {
        "app.routers.user",
        "app.services.auth",
        "other.main",
    }


def test_impact_unresolved_files_reported(tmp_path: Path):
    project = _make_chain(tmp_path)
    g = build_graph(project)
    bogus = project / "app" / "libs" / "nonexistent.py"
    result = find_impact(g, [bogus])
    assert result.changed == ()
    assert result.impacted == ()
    assert len(result.unresolved) == 1


def test_impact_outputs_sorted_for_deterministic_json(db_impact):
    result = db_impact
    assert list(result.impacted) == sorted(result.impacted)


def test_impact_chains_give_shortest_import_path_to_changed(db_impact):
    # app.routers.user -> app.services.auth -> app.libs.db. Editing db, the
    # direct dependent (auth) gets a one-hop chain and the transitive one
    # (user) a two-hop chain, each ending at the changed module.
    result = db_impact
    by_module = {c.impacted: c for c in result.chains}

    auth = by_module["app.services.auth"]
    assert auth.changed == "app.libs.db"
    assert auth.via == ("app.services.auth", "app.libs.db")
    assert [(h.source, h.target) for h in auth.hops] == [("app.services.auth", "app.libs.db")]

    user = by_module["app.routers.user"]
    assert user.via == ("app.routers.user", "app.services.auth", "app.libs.db")
    assert user.changed == "app.libs.db"


def test_impact_chain_hops_carry_import_line_numbers(db_impact):
    # The "because" must be citable: each hop names the line where the
    # import lives in its source module. Both fixture imports are on line 1.
    result = db_impact
    auth = next(c for c in result.chains if c.impacted == "app.services.auth")
    assert auth.hops[0].lines == (1,)


def test_impact_chains_ranked_shortest_first(db_impact):
    result = db_impact
    lengths = [len(c.via) for c in result.chains]
    assert lengths == sorted(lengths)


def test_impact_max_chains_caps_and_reports_omitted(tmp_path: Path):
    project = _make_chain(tmp_path)
    g = build_graph(project)
    result = find_impact(g, [project / "app" / "libs" / "db.py"], max_chains=1)
    assert len(result.chains) == 1
    # Two modules are impacted; one chain shown leaves one omitted.
    assert result.chains_omitted == len(result.impacted) - 1
    # The closest dependent is the one kept.
    assert result.chains[0].impacted == "app.services.auth"


def test_impact_negative_max_chains_returns_all(tmp_path: Path):
    project = _make_chain(tmp_path)
    g = build_graph(project)
    result = find_impact(g, [project / "app" / "libs" / "db.py"], max_chains=-1)
    assert len(result.chains) == len(result.impacted)
    assert result.chains_omitted == 0


def test_impact_no_changed_modules_has_no_chains(tmp_path: Path):
    project = _make_chain(tmp_path)
    g = build_graph(project)
    bogus = project / "app" / "libs" / "nonexistent.py"
    result = find_impact(g, [bogus])
    assert result.chains == ()
    assert result.chains_omitted == 0
