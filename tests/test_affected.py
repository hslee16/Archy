from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from archy.affected import DELETED_OR_RENAMED_REASON, _compile_glob, find_affected
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

    missing = project / "notes.md"
    assert not missing.exists()
    missing_result = find_affected(g, [missing], project_root=project)
    assert missing_result.changed == ()
    assert missing_result.impacted_modules == ()
    assert missing_result.impacted_tests == ()
    assert missing_result.unresolved == (str(missing),)
    assert missing_result.deleted == ()
    assert missing_result.deleted_reason is None

    present = project / "README.md"
    present.write_text("not python\n")
    assert present.is_file()
    present_result = find_affected(g, [present], project_root=project)
    assert present_result.unresolved == (str(present),)
    assert present_result.deleted == ()
    assert present_result.model_dump()["deleted_reason"] is None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo_with_imported_module(tmp_path: Path) -> tuple[Path, Path]:
    """Commit `pkg/a.py` imported by `tests/test_a.py`, then return both paths."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    module = pkg / "a.py"
    module.write_text("x = 1\n")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_a.py").write_text("from pkg.a import x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    return repo, module


def test_deleted_in_project_py_is_not_unresolved(tmp_path: Path):
    repo, module = _repo_with_imported_module(tmp_path)
    present = find_affected(build_graph(repo), [module], project_root=repo)
    assert "tests.test_a" in present.impacted_tests  # fixture reaches the importer

    module.unlink()
    assert not module.exists()
    result = find_affected(build_graph(repo), [module], project_root=repo)

    assert result.deleted == (str(module),)
    assert result.unresolved == ()
    assert result.impacted_tests == ()
    assert result.deleted_reason == DELETED_OR_RENAMED_REASON
    dumped = result.model_dump()
    assert dumped["deleted"] == (str(module),)
    assert dumped["deleted_reason"] == DELETED_OR_RENAMED_REASON
    assert dumped["unresolved"] == ()


def test_renamed_py_old_path_is_deleted_not_unresolved(tmp_path: Path):
    repo, module = _repo_with_imported_module(tmp_path)
    renamed = module.with_name("b.py")
    module.rename(renamed)
    assert not module.exists()
    assert renamed.is_file()

    result = find_affected(build_graph(repo), [module], project_root=repo)

    assert result.deleted == (str(module),)
    assert result.unresolved == ()
    assert result.deleted_reason == DELETED_OR_RENAMED_REASON


def test_present_py_outside_the_graph_stays_unresolved(tmp_path: Path):
    project = _make_project(tmp_path)
    # `venv` is ignored, so a file there is on disk and still not a module.
    script = project / "venv" / "helper.py"
    script.parent.mkdir()
    script.write_text("print(1)\n")
    assert script.is_file()
    result = find_affected(build_graph(project), [script], project_root=project)

    assert result.unresolved == (str(script),)
    assert result.deleted == ()
    assert result.deleted_reason is None


def test_missing_py_outside_the_project_stays_unresolved(tmp_path: Path):
    project = tmp_path / "proj"
    project.mkdir()
    project = _make_project(project)
    outside = tmp_path / "gone.py"
    assert not outside.exists()
    result = find_affected(build_graph(project), [outside], project_root=project)

    assert result.unresolved == (str(outside),)
    assert result.deleted == ()
    assert result.deleted_reason is None


def test_deleted_paths_are_sorted(tmp_path: Path):
    project = _make_project(tmp_path)
    later = project / "app" / "z.py"
    earlier = project / "app" / "a.py"
    assert not later.exists()
    assert not earlier.exists()
    result = find_affected(build_graph(project), [later, earlier], project_root=project)

    assert result.deleted == (str(earlier), str(later))
    assert result.unresolved == ()


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


def test_compile_glob_question_mark_does_not_cross_separators():
    p = _compile_glob("tests?test_a.py")
    assert p.fullmatch("tests_test_a.py")  # one ordinary character matches
    assert not p.fullmatch("tests/test_a.py")  # but never a separator


def test_compile_glob_escapes_regex_metachars():
    p = _compile_glob("file.with.dots.py")
    assert p.fullmatch("file.with.dots.py")
    assert not p.fullmatch("fileXwithXdotsXpy")


def test_compile_glob_glued_star_star_does_not_cross_separators():
    # A `**` glued to other characters is NOT a complete path segment, so it
    # must not cross `/` (it degrades to an in-segment `*`). Regression for
    # #203: the old `.*` branch silently over-selected nested files. Ground
    # truth is pathlib's `PurePosixPath(...).full_match(pattern)`.
    p = _compile_glob("a/**b")
    assert p.fullmatch("a/xb")
    assert p.fullmatch("a/b")
    assert not p.fullmatch("a/x/y/b")  # would falsely match under old `.*`

    p = _compile_glob("**.py")
    assert p.fullmatch("foo.py")
    assert not p.fullmatch("a/b/foo.py")  # would falsely match under old `.*`

    p = _compile_glob("tests/**_spec.py")
    assert p.fullmatch("tests/foo_spec.py")
    assert not p.fullmatch("tests/deep/nested/foo_spec.py")


def test_compile_glob_left_glued_star_star_does_not_cross_separators():
    # `**` with a clean right boundary (`/`) but a glued left side is still
    # not a complete segment, so it must not cross `/` either.
    p = _compile_glob("a**/b")
    assert p.fullmatch("a/b")
    assert p.fullmatch("axyz/b")
    assert not p.fullmatch("a/x/b")


def test_compile_glob_is_total_over_regex_metachars():
    # Regression for #170 (F3): a user `--filter` containing regex metachars
    # that are NOT glob operators must compile and match literally rather than
    # raise `re.error`. The old hand-maintained escape allowlist omitted
    # `[`/`]`, so `--filter '['` crashed with an uncaught PatternError.
    for ch in "[](){}.^$+|\\":
        p = _compile_glob(ch)  # must not raise
        assert p.fullmatch(ch), f"{ch!r} should match itself literally"
    # a char class is treated as literal text, not a regex class
    p = _compile_glob("[abc].py")
    assert p.fullmatch("[abc].py")
    assert not p.fullmatch("a.py")


def test_affected_without_tests_carries_static_only_caveat(tmp_path: Path):
    """No impacted tests means no test imports it statically, not that none exercises it."""
    project = _make_project(tmp_path)
    graph = build_graph(project)
    covered = find_affected(graph, [project / "app" / "libs" / "db.py"], project_root=project)
    assert covered.impacted_tests  # fixture reaches the branch
    assert covered.model_dump()["empty_caveat"] is None
    # Nothing imports the suffix-style test file itself, so no test reaches it.
    orphan = project / "app" / "services" / "auth_test.py"
    none = find_affected(graph, [orphan], project_root=project)
    assert none.impacted_tests == ()
    assert "importlib.import_module" in none.model_dump()["empty_caveat"]


def _conftest_fixture_project(tmp_path: Path) -> Path:
    """factory imported only by tests/conftest.py; test_x uses the fixture."""
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "factory.py").write_text("def make():\n    return 1\n")
    (app / "core.py").write_text("x = 1\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "conftest.py").write_text(
        "import pytest\nfrom app.factory import make\n\n"
        "@pytest.fixture\ndef built():\n    return make()\n"
    )
    unit = tests / "unit"
    unit.mkdir()
    (unit / "__init__.py").write_text("")
    (unit / "test_x.py").write_text("def test_x(built):\n    assert built == 1\n")
    return tmp_path


def test_impacted_conftest_expands_to_tests_it_serves(tmp_path: Path):
    project = _conftest_fixture_project(tmp_path)
    g = build_graph(project)
    result = find_affected(g, [project / "app" / "factory.py"], project_root=project)
    assert "tests.conftest" not in result.impacted_tests
    assert "tests.unit.test_x" in result.impacted_tests
    assert "tests.conftest" not in result.impacted_modules
    dumped = result.model_dump()
    assert "tests.conftest" not in dumped["impacted_tests"]
    assert "tests.unit.test_x" in dumped["impacted_tests"]


def test_root_conftest_expands_the_same_way(tmp_path: Path):
    project = tmp_path
    app = project / "app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "factory.py").write_text("def make():\n    return 1\n")
    (project / "conftest.py").write_text(
        "import pytest\nfrom app.factory import make\n\n"
        "@pytest.fixture\ndef built():\n    return make()\n"
    )
    tests = project / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_x.py").write_text("def test_x(built):\n    assert built == 1\n")
    g = build_graph(project)
    result = find_affected(g, [project / "app" / "factory.py"], project_root=project)
    assert "tests.test_x" in result.impacted_tests
    for q in (*result.impacted_tests, *result.impacted_modules):
        path = g.nodes[q].get("path")
        assert path is None or Path(path).name != "conftest.py"


def test_conftest_expansion_respects_filter(tmp_path: Path):
    project = _conftest_fixture_project(tmp_path)
    g = build_graph(project)
    # Only unit tests match; expansion must not add paths the filter rejects.
    result = find_affected(
        g,
        [project / "app" / "factory.py"],
        project_root=project,
        test_filter="**/unit/test_*.py",
    )
    assert "tests.unit.test_x" in result.impacted_tests
    assert "tests.conftest" not in result.impacted_tests
