from __future__ import annotations

from pathlib import Path

from archy.graph import build_graph
from archy.impact import find_impact


def _make_chain(tmp_path: Path) -> Path:
    # routers -> services -> libs.db; only libs.db imports os
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


def test_impact_returns_transitive_dependents(tmp_path: Path):
    project = _make_chain(tmp_path)
    g = build_graph(project)
    result = find_impact(g, [project / "app" / "libs" / "db.py"])
    assert result.changed == ("app.libs.db",)
    assert set(result.impacted) == {"app.routers.user", "app.services.auth"}
    assert result.unresolved == ()


def test_impact_changed_module_excluded_from_impacted(tmp_path: Path):
    project = _make_chain(tmp_path)
    g = build_graph(project)
    result = find_impact(g, [project / "app" / "libs" / "db.py"])
    assert "app.libs.db" not in result.impacted


def test_impact_external_dependents_filtered_out(tmp_path: Path):
    # An external node would never appear in the graph as a path, so it
    # cannot be a changed module. But its ancestors can include external
    # nodes if external nodes pull in internal ones; ensure the impacted
    # set is internal-only regardless of how the graph was built.
    project = _make_chain(tmp_path)
    g = build_graph(project)
    result = find_impact(g, [project / "app" / "libs" / "db.py"])
    assert all(q != "os" for q in result.impacted)


def test_impact_leaf_module_has_no_dependents(tmp_path: Path):
    project = _make_chain(tmp_path)
    g = build_graph(project)
    result = find_impact(g, [project / "app" / "routers" / "user.py"])
    assert result.changed == ("app.routers.user",)
    assert result.impacted == ()


def test_impact_multiple_files_unioned(tmp_path: Path):
    project = _make_chain(tmp_path)
    # Add a parallel chain that shares no modules with the first.
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


def test_impact_outputs_sorted_for_deterministic_json(tmp_path: Path):
    project = _make_chain(tmp_path)
    g = build_graph(project)
    result = find_impact(g, [project / "app" / "libs" / "db.py"])
    assert list(result.impacted) == sorted(result.impacted)
