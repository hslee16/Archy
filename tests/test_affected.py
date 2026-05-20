from __future__ import annotations

from pathlib import Path

import pytest

from archy.affected import _compile_glob, find_affected
from archy.graph import build_graph

# Setup pattern inlines `_make_project` + `build_graph` per test, matching
# the sibling `tests/test_impact.py` convention. Reconsider extracting a
# `@pytest.fixture` returning (project, graph) if this file grows past ~15
# tests; the consistency win is worth more than the line savings until then.


def _make_project(tmp_path: Path) -> Path:
    """Three-hop chain plus a parallel test tree.

    Source chain (each module imports the previous):
        app.libs.db  <-  app.services.auth  <-  app.routers.user

    Tests, each importing the module under test directly:
        tests/test_db.py        -> app.libs.db
        tests/test_auth.py      -> app.services.auth
        tests/integration/test_user_e2e.py -> app.routers.user
        app/services/auth_test.py          -> app.services.auth   (suffix style)
    """
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
    (services / "auth_test.py").write_text("from app.services.auth import y\n")

    routers = pkg / "routers"
    routers.mkdir()
    (routers / "__init__.py").write_text("")
    (routers / "user.py").write_text("from app.services.auth import y\n")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_db.py").write_text("from app.libs.db import x\n")
    (tests / "test_auth.py").write_text("from app.services.auth import y\n")
    integration = tests / "integration"
    integration.mkdir()
    (integration / "__init__.py").write_text("")
    (integration / "test_user_e2e.py").write_text("from app.routers.user import z\n")

    return tmp_path


def test_affected_classifies_tests_and_modules(tmp_path: Path):
    project = _make_project(tmp_path)
    g = build_graph(project)

    result = find_affected(
        g,
        [project / "app" / "libs" / "db.py"],
        project_root=project,
    )

    assert result.changed == ("app.libs.db",)
    assert "app.services.auth" in result.impacted_modules
    assert "app.routers.user" in result.impacted_modules
    assert set(result.impacted_tests) == {
        "tests.test_db",
        "tests.test_auth",
        "tests.integration.test_user_e2e",
        "app.services.auth_test",
    }


def test_affected_depth_caps_traversal(tmp_path: Path):
    project = _make_project(tmp_path)
    g = build_graph(project)

    # depth=1 should reach the direct importers of db.py only; the
    # two-hop router (and its test) should be excluded.
    result = find_affected(
        g,
        [project / "app" / "libs" / "db.py"],
        project_root=project,
        depth=1,
    )

    impacted = set(result.impacted_modules) | set(result.impacted_tests)
    assert "app.services.auth" in impacted
    assert "tests.test_db" in impacted
    assert "app.routers.user" not in impacted
    assert "tests.integration.test_user_e2e" not in impacted


def test_affected_depth_zero_is_rejected(tmp_path: Path):
    project = _make_project(tmp_path)
    g = build_graph(project)
    with pytest.raises(ValueError):
        find_affected(g, [project / "app" / "libs" / "db.py"], project_root=project, depth=0)


def test_affected_custom_filter_overrides_auto_detection(tmp_path: Path):
    project = _make_project(tmp_path)
    g = build_graph(project)

    # User says only files under `tests/integration/` count as tests.
    # tests/test_db.py and tests/test_auth.py should now classify as
    # modules, not tests.
    result = find_affected(
        g,
        [project / "app" / "libs" / "db.py"],
        project_root=project,
        test_filter="tests/integration/**",
    )

    assert "tests.integration.test_user_e2e" in result.impacted_tests
    assert "tests.test_db" not in result.impacted_tests
    assert "tests.test_db" in result.impacted_modules
    assert result.test_filter == "tests/integration/**"


def test_affected_unresolved_files_reported(tmp_path: Path):
    project = _make_project(tmp_path)
    g = build_graph(project)

    bogus = project / "app" / "libs" / "nonexistent.py"
    result = find_affected(g, [bogus], project_root=project)

    assert result.changed == ()
    assert result.impacted_modules == ()
    assert result.impacted_tests == ()
    assert len(result.unresolved) == 1


def test_affected_outputs_sorted(tmp_path: Path):
    project = _make_project(tmp_path)
    g = build_graph(project)
    result = find_affected(
        g,
        [project / "app" / "libs" / "db.py"],
        project_root=project,
    )
    assert list(result.impacted_modules) == sorted(result.impacted_modules)
    assert list(result.impacted_tests) == sorted(result.impacted_tests)


def test_affected_disjoint_tests_and_modules(tmp_path: Path):
    project = _make_project(tmp_path)
    g = build_graph(project)
    result = find_affected(
        g,
        [project / "app" / "libs" / "db.py"],
        project_root=project,
    )
    assert set(result.impacted_modules).isdisjoint(set(result.impacted_tests))


def test_affected_changed_excluded_from_impacted(tmp_path: Path):
    project = _make_project(tmp_path)
    g = build_graph(project)
    result = find_affected(
        g,
        [project / "app" / "libs" / "db.py"],
        project_root=project,
    )
    assert "app.libs.db" not in result.impacted_modules
    assert "app.libs.db" not in result.impacted_tests


def test_compile_glob_recursive_star_star():
    # `tests/**` matches anything under a tests/ directory at the root.
    # Bare "tests" (directory name with no trailing path) is intentionally
    # not matched: file paths in real use always have a basename.
    p = _compile_glob("tests/**")
    assert p.fullmatch("tests/test_foo.py")
    assert p.fullmatch("tests/sub/test_bar.py")
    assert not p.fullmatch("src/tests/test_foo.py")


def test_compile_glob_filename_pattern():
    p = _compile_glob("**/test_*.py")
    assert p.fullmatch("test_foo.py")
    assert p.fullmatch("a/b/test_foo.py")
    assert not p.fullmatch("foo_test.py")


def test_compile_glob_escapes_regex_metachars():
    p = _compile_glob("file.with.dots.py")
    assert p.fullmatch("file.with.dots.py")
    assert not p.fullmatch("fileXwithXdotsXpy")
