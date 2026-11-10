"""The `graph`, `dsm`, `impact`, `affected` and `render` commands."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from archy.affected import DELETED_OR_RENAMED_REASON
from archy.cli import (
    main,
)
from archy.graph import build_graph
from tests.cli_helpers import (
    _DELETED_NOTE,
    _FILTER_MISS,
    _dsm_json,
    _git,
    _make_acyclic_project,
    _make_app_with_core_module,
    _make_app_with_test_module,
    _make_libs_to_routers_chain,
    _make_project_with_generated_dir,
    _make_three_module_project,
    _repo_with_deleted_module,
)


@pytest.mark.parametrize(
    "build_args",
    [
        lambda p: ["cycles", str(p)],
        lambda p: ["graph", str(p)],
        lambda p: ["score", str(p)],
        lambda p: ["dsm", str(p)],
        lambda p: ["hotspots", str(p)],
        lambda p: ["snapshot", str(p)],
        lambda p: ["brief", str(p)],
        lambda p: ["coupling", str(p)],
        lambda p: ["duplicates", str(p)],
        lambda p: ["conventions", str(p)],
        lambda p: ["what-to-refactor-next", str(p)],
        lambda p: ["impact", str(p), "--file", "pkg/__init__.py"],
        lambda p: ["simulate", str(p)],
        lambda p: ["render", str(p), "--view", "dsm"],
    ],
)
def test_malformed_archy_yaml_is_one_line_error_not_traceback(tmp_path: Path, build_args):
    """#524: every graph-loading CLI command must ClickException a bad archy.yaml.

    `check` and `contracts` already did; the rest used to dump a traceback because
    `_load_graph` / direct `graph_kwargs` let LayerConfigError propagate.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    yaml_path = tmp_path / "archy.yaml"
    yaml_path.write_text("layers: [\n")
    result = CliRunner().invoke(main, build_args(tmp_path))
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert "archy.yaml" in result.output


def test_graph_command_still_works(tmp_path: Path):
    project = _make_acyclic_project(tmp_path)
    result = CliRunner().invoke(main, ["graph", str(project), "--internal-only"])
    assert result.exit_code == 0
    assert "pkg.a" in result.output
    assert "pkg.b" in result.output


def test_graph_json_reports_a_mixed_import_pair_as_relative(tmp_path: Path):
    """The third surface. `is_relative` aggregates with `any` (#450), and the
    CLI JSON an agent pipes into `jq` has to say the same thing the MCP payload
    does."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("\nfrom pkg.b import x\nfrom .b import y\n")
    (pkg / "b.py").write_text("")
    (pkg / "c.py").write_text("from pkg.b import w\n")

    args = ["graph", str(tmp_path), "--internal-only", "--format", "json"]
    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0
    payload = json.loads(result.output)
    edges = {(e["source"], e["target"]): e for e in payload["edges"]}
    # Two import sites on the a->b pair, so the extend branch runs at all.
    assert edges[("pkg.a", "pkg.b")]["lines"] == [2, 3]
    assert edges[("pkg.a", "pkg.b")]["is_relative"] is True
    assert edges[("pkg.c", "pkg.b")]["is_relative"] is False


def test_exclude_drops_directory_from_graph(tmp_path: Path):
    project = _make_project_with_generated_dir(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nexclude: [baml_client]\n")
    args = ["graph", str(project), "--internal-only", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    qualnames = {n["id"] for n in payload["nodes"]}
    assert "myapp" in qualnames
    assert "myapp.real" in qualnames
    assert not any(q.startswith("myapp.baml_client") for q in qualnames)


def test_exclude_omitted_keeps_generated_dir(tmp_path: Path):
    project = _make_project_with_generated_dir(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\n")
    args = ["graph", str(project), "--internal-only", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    qualnames = {n["id"] for n in payload["nodes"]}
    assert any(q.startswith("myapp.baml_client") for q in qualnames)


def test_roots_makes_namespace_packages_visible(tmp_path: Path):
    # PEP 420 layout: no __init__.py at any level. archy.yaml declares
    # `roots: [app]` so descendants get qualnames rooted at app.
    app = tmp_path / "app"
    libs = app / "libs"
    libs.mkdir(parents=True)
    (libs / "db.py").write_text("import os\n")
    (app / "main.py").write_text("from app.libs.db import x\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nroots: [app]\n")

    args = ["graph", str(tmp_path), "--internal-only", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    qualnames = {n["id"] for n in payload["nodes"]}
    assert "app.main" in qualnames
    assert "app.libs.db" in qualnames


def test_impact_lists_transitive_dependents(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    args = ["impact", str(tmp_path), "--file", "app/libs/db.py", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["changed"] == ["app.libs.db"]
    assert payload["impacted"] == ["app.routers.user"]
    assert payload["unresolved"] == []


def test_impact_max_chains_rejects_zero(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    args = ["impact", str(tmp_path), "--file", "app/libs/db.py", "--max-chains", "0"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "--max-chains must be negative (for all) or positive (for a limit); got 0" in (
        result.output
    )


def test_affected_classifies_tests_and_modules_json(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_db.py").write_text("from app.libs.db import x\n")

    args = ["affected", str(tmp_path), "app/libs/db.py", "--json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changed"] == ["app.libs.db"]
    assert payload["impacted_modules"] == ["app.routers.user"]
    assert payload["impacted_tests"] == ["tests.test_db"]
    assert payload["depth"] == 5


def test_affected_json_expands_impacted_conftest(tmp_path: Path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "factory.py").write_text("def make():\n    return 1\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "conftest.py").write_text(
        "import pytest\nfrom app.factory import make\n\n"
        "@pytest.fixture\ndef built():\n    return make()\n"
    )
    (tests / "test_x.py").write_text("def test_x(built):\n    assert built == 1\n")

    result = CliRunner().invoke(main, ["affected", str(tmp_path), "app/factory.py", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "tests.conftest" not in payload["impacted_tests"]
    assert "tests.test_x" in payload["impacted_tests"]


def test_affected_text_names_a_deleted_py(tmp_path: Path):
    repo = _repo_with_deleted_module(tmp_path)
    result = CliRunner().invoke(main, ["affected", str(repo), "pkg/a.py"])
    assert result.exit_code == 0, result.output
    assert _DELETED_NOTE in result.stdout
    assert "pkg/a.py" in result.stdout
    assert "non-Python, excluded, or outside any package" not in result.stdout


def test_affected_quiet_reports_deleted_py_on_stderr(tmp_path: Path):
    repo = _repo_with_deleted_module(tmp_path)
    result = CliRunner().invoke(main, ["affected", str(repo), "--stdin", "-q"], input="pkg/a.py\n")
    assert result.exit_code == 0
    assert result.stdout == ""
    assert _DELETED_NOTE in result.stderr
    assert "pkg/a.py" in result.stderr
    assert "non-Python, excluded, or outside any package" not in result.stderr


def test_affected_json_carries_deleted_reason(tmp_path: Path):
    repo = _repo_with_deleted_module(tmp_path)
    result = CliRunner().invoke(main, ["affected", str(repo), "pkg/a.py", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["deleted"] == [str(repo / "pkg" / "a.py")]
    assert payload["deleted_reason"] == DELETED_OR_RENAMED_REASON
    assert payload["unresolved"] == []


def test_affected_quiet_emits_test_file_paths(tmp_path: Path):
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "app/core.py", "--quiet"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line]
    assert len(lines) == 1
    assert lines[0].endswith("tests/test_core.py")


def test_affected_stdin_reads_paths(tmp_path: Path):
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "--stdin", "--json"]
    result = CliRunner().invoke(main, args, input="app/core.py\n")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["impacted_tests"] == ["tests.test_core"]


def test_impact_accepts_git_root_paths_from_subdirectory(tmp_path: Path):
    repo = _repo_with_deleted_module(tmp_path, subdir="services/api")
    project = repo / "services/api"
    _git(repo, "restore", "services/api/pkg/a.py")

    args = ["impact", str(project), "--file", "services/api/pkg/a.py", "--format", "json"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["changed"] == ["pkg.a"]
    assert payload["unresolved"] == []
    assert "tests.test_a" in payload["impacted"]


def test_affected_json_and_quiet_mutually_exclusive(tmp_path: Path):
    _make_app_with_core_module(tmp_path)

    args = ["affected", str(tmp_path), "app/core.py", "--json", "--quiet"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_affected_depth_rejects_invalid_values(tmp_path: Path):
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "app/core.py", "--depth", "0"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "--depth must be >= 1; got 0" in result.output


def test_affected_filter_with_regex_metachars_does_not_crash(tmp_path: Path):
    # Regression for #170 (F3): a `--filter` containing regex metachars that
    # are not glob operators must be treated as literal text, not raise an
    # uncaught re.error (the old escape allowlist omitted `[`/`]`).
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "app/core.py", "--filter", "[", "--json"]
    result = CliRunner().invoke(main, args)
    # `[` matches nothing here, so it is refused (#660); the refusal is the clean
    # exit a ClickException gives, where a `re.error` would surface as itself.
    assert isinstance(result.exception, SystemExit), result.exception
    assert result.exit_code == 1


@pytest.mark.parametrize("output_flags", [(), ("--json",), ("--quiet",)])
@pytest.mark.parametrize(
    ("test_filter", "message"),
    [
        ("test/**", f"test filter 'test/**' {_FILTER_MISS}; did you mean tests/**?"),
        ("[", f"test filter '[' {_FILTER_MISS}"),
    ],
)
def test_affected_refuses_a_filter_that_matches_no_file(
    tmp_path: Path, output_flags: tuple[str, ...], test_filter: str, message: str
):
    """#660: text and JSON blamed static edges, and `-q` printed nothing and exited 0."""
    _make_app_with_test_module(tmp_path)
    args = ["affected", str(tmp_path), "app/core.py", *output_flags, "--filter"]
    # The fixture's edge is real: the correct filter selects the test on this same surface.
    assert build_graph(tmp_path).has_edge("tests.test_core", "app.core")
    sound = CliRunner().invoke(main, [*args, "tests/**"])
    assert sound.exit_code == 0, sound.output
    assert "test_core" in sound.stdout

    refused = CliRunner().invoke(main, [*args, test_filter])
    assert refused.exit_code == 1
    assert refused.stderr == f"Error: {message}\n"
    assert refused.stdout == ""


def test_affected_filter_reads_paths_against_a_relative_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`archy affected .` is the documented shape, and must not refuse a sound filter."""
    _make_app_with_test_module(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = ["affected", ".", "app/core.py", "--filter", "tests/**", "--quiet"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert result.output.strip().endswith("tests/test_core.py")


def test_affected_merges_stdin_and_positional_files(tmp_path: Path):
    # Regression for #170 (F9): --stdin and positional FILES are merged, not
    # mutually exclusive. Both sources should appear in `changed`.
    pkg = _make_app_with_core_module(tmp_path)
    (pkg / "other.py").write_text("")

    args = ["affected", str(tmp_path), "app/core.py", "--stdin", "--json"]
    result = CliRunner().invoke(main, args, input="app/other.py\n")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["changed"] == ["app.core", "app.other"]


def test_dsm_ascii_output_contains_module_names(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--group", "topological"])
    assert result.exit_code == 0
    assert "pkg.a" in result.output
    assert "pkg.b" in result.output
    assert "pkg.c" in result.output


def test_dsm_json_output_is_valid_json(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["n"] >= 3
    assert "cells" in payload
    assert "ordering" in payload


def test_dsm_focus_filter_narrows_output(tmp_path: Path):
    """Named for narrowing, so it has to narrow.

    It used to focus `pkg.b` on a three-module chain, where the default depth
    reaches every module, and then assert the result was all three. That holds
    with `--focus` removed entirely, so it never tested the filter.
    """
    project = _make_three_module_project(tmp_path)
    unfocused = _dsm_json(project)
    focused = _dsm_json(project, "--focus", "pkg.a", "--focus-depth", "1")

    assert set(focused["ordering"]) == {"pkg.a", "pkg.b"}
    assert len(focused["ordering"]) < len(unfocused["ordering"])


def test_dsm_group_flag_reaches_the_matrix(tmp_path: Path):
    """The CLI threads six knobs into `build_dsm`, and only two were visible to
    the CLI tests: the other four could each be hardcoded with every test still
    green. This one and `--focus-depth` are now pinned too. `--weight` and the
    internal-only load still are not, because neither has a visible effect on a
    fixture this small.

    The orderings differ because topological puts the package node first.
    """
    project = _make_three_module_project(tmp_path)

    assert (
        _dsm_json(project, "--group", "topological")["ordering"] != _dsm_json(project)["ordering"]
    )


def test_dsm_package_filter_excludes_other_packages(tmp_path: Path):
    pkg = tmp_path / "pkg"
    other = tmp_path / "other"
    pkg.mkdir()
    other.mkdir()
    (pkg / "__init__.py").write_text("")
    (other / "__init__.py").write_text("")
    (pkg / "a.py").write_text("")
    (other / "b.py").write_text("")
    result = CliRunner().invoke(
        main, ["dsm", str(tmp_path), "--package", "pkg", "--format", "json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert all(n.startswith("pkg") for n in payload["ordering"])


def test_dsm_diff_against_baseline_snapshot(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    baseline = tmp_path / "before.json"
    before = CliRunner().invoke(
        main, ["dsm", str(project), "--format", "json", "--group", "topological"]
    )
    assert before.exit_code == 0
    baseline.write_text(before.output)
    # c.py now imports a.py, reversing the topological order, so the diff must
    # surface a new back-edge against the baseline.
    (project / "pkg" / "c.py").write_text("from pkg.a import _\n")
    result = CliRunner().invoke(
        main,
        [
            "dsm",
            str(project),
            "--group",
            "topological",
            "--diff",
            str(baseline),
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    diff_payload = json.loads(result.output)
    assert diff_payload["new_back_edges"], "the c->a edit must surface as a new back-edge"


def test_dsm_diff_missing_baseline_errors_cleanly(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--diff", str(tmp_path / "nope.json")])
    assert result.exit_code != 0
    assert "no DSM snapshot" in result.output or "no DSM snapshot" in (result.stderr or "")


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("not json", "Expecting value: line 1 column 1 (char 0)"),
        ("{}", "missing 'ordering'"),
        ("graph", "missing 'ordering'"),
    ],
)
def test_dsm_diff_bad_snapshot_is_a_clean_error(tmp_path: Path, kind: str, reason: str) -> None:
    """`archy dsm --diff` on a non-snapshot file exits 1 with the path and no traceback.

    CliRunner stores an uncaught exception on Result.exception and leaves output
    empty, so "Traceback" would be absent and this test would miss the crash.
    """
    project = _make_three_module_project(tmp_path)
    bad = tmp_path / "bad.json"
    archy = str(Path(sys.executable).parent / "archy")
    if kind == "graph":
        graph = subprocess.run(
            [archy, "graph", str(project), "--format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert graph.returncode == 0, graph.stderr
        payload = json.loads(graph.stdout)
        assert "ordering" not in payload
        bad.write_text(graph.stdout)
    else:
        bad.write_text(kind)
        if kind == "{}":
            assert "ordering" not in json.loads(kind)

    proc = subprocess.run(
        [archy, "dsm", str(project), "--diff", str(bad)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = proc.stdout + proc.stderr
    message = f"{bad} is not a DSM snapshot (write one with `archy dsm --format json`): {reason}"

    assert proc.returncode == 1
    assert "Traceback" not in output
    assert str(bad) in output
    assert output == f"Error: {message}\n"


def test_dsm_ascii_rejects_oversized_with_helpful_message(tmp_path: Path):
    pkg = tmp_path / "big"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(20):
        (pkg / f"m{i}.py").write_text("")
    result = CliRunner().invoke(main, ["dsm", str(tmp_path), "--max-nodes", "5"])
    assert result.exit_code == 0
    assert "exceeds max_nodes" in result.output
    assert "--focus" in result.output


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--focus", "pkg.nope", "focus 'pkg.nope' is not an internal module"),
        ("--package", "nope", "package 'nope' matches no internal module"),
    ],
)
def test_dsm_refuses_a_name_that_matches_nothing(
    tmp_path: Path, flag: str, value: str, message: str
):
    """A mistyped name used to print an empty matrix, which reads as a clean project (#625)."""
    project = _make_three_module_project(tmp_path)
    assert _dsm_json(project)["ordering"]  # the unfiltered project is not empty
    result = CliRunner().invoke(main, ["dsm", str(project), flag, value, "--format", "json"])
    assert result.exit_code == 1
    assert f"Error: {message}" in result.output


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        (
            "--focus",
            "myapp.cor",
            "focus 'myapp.cor' is not an internal module; did you mean myapp.core?",
        ),
        (
            "--package",
            "myap.",
            "package 'myap.' matches no internal module; "
            "did you mean myapp, myapp.core, myapp.util?",
        ),
        ("--focus", "zzz", "focus 'zzz' is not an internal module"),
    ],
)
def test_dsm_refusal_names_the_near_misses(tmp_path: Path, flag: str, value: str, message: str):
    """A typo is answered with the name it was meant to be, not just "wrong" (#643)."""
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")
    (pkg / "util.py").write_text("from myapp.core import thing\n")
    result = CliRunner().invoke(main, ["dsm", str(tmp_path), flag, value, "--format", "json"])
    assert result.exit_code == 1
    assert result.output == f"Error: {message}\n"
    exact = CliRunner().invoke(main, ["dsm", str(tmp_path), "--focus", "myapp.core"])
    assert exact.exit_code == 0, exact.output


def test_dsm_rejects_negative_focus_depth(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--focus-depth", "-1"])
    assert result.exit_code != 0
    assert "--focus-depth must be >= 0; got -1" in result.output


def test_dsm_rejects_invalid_max_nodes(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["dsm", str(project), "--max-nodes", "0"])
    assert result.exit_code != 0
    assert "--max-nodes must be >= 1; got 0" in result.output


def test_render_dsm_writes_a_self_contained_file(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    out = tmp_path / "out" / "dsm.html"
    result = CliRunner().invoke(main, ["render", str(project), "--out", str(out)])
    assert result.exit_code == 0
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "pkg.a" in html
    assert "<script" not in html


def test_render_defaults_to_stdout(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    result = CliRunner().invoke(main, ["render", str(project)])
    assert result.exit_code == 0
    assert result.output.startswith("<!DOCTYPE html>")


def test_render_errors_instead_of_writing_an_oversized_matrix(tmp_path: Path):
    project = _make_three_module_project(tmp_path)
    out = tmp_path / "dsm.html"
    result = CliRunner().invoke(
        main, ["render", str(project), "--max-nodes", "1", "--out", str(out)]
    )
    assert result.exit_code != 0
    assert "max_nodes" in result.output
    assert not out.exists()


def test_impact_empty_result_says_what_the_zero_means_in_text_and_json(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    leaf = ["impact", str(tmp_path), "--file", "app/routers/user.py"]
    as_json = json.loads(CliRunner().invoke(main, [*leaf, "--format", "json"]).output)
    assert as_json["impacted"] == []  # fixture reaches the branch
    assert "importlib.import_module" in as_json["empty_caveat"]
    text = CliRunner().invoke(main, leaf).output
    assert "does not mean safe to remove" in text
    populated = ["impact", str(tmp_path), "--file", "app/libs/db.py", "--format", "json"]
    assert json.loads(CliRunner().invoke(main, populated).output)["empty_caveat"] is None


@pytest.mark.parametrize("command", ["impact", "affected"])
def test_impact_and_affected_text_share_the_unresolved_and_changed_block(
    tmp_path: Path, command: str
):
    """Pins the lines both renderers emit for a file that is not a module.

    These strings had no assertion anywhere, so a refactor of the shared block
    could change them with the suite green. The expected block is spelled out
    here, not derived from the renderer.
    """
    _make_libs_to_routers_chain(tmp_path)
    (tmp_path / "README.md").write_text("")
    if command == "impact":
        args = ["impact", str(tmp_path), "--file", "app/libs/db.py", "--file", "README.md"]
    else:
        args = ["affected", str(tmp_path), "app/libs/db.py", "README.md"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    expected = (
        "# 1 file(s) did not resolve to a module "
        "(non-Python, excluded, or outside any package):\n"
        f"  ? {tmp_path / 'README.md'}\n"
        "\n"
        "Changed:\n"
        "  - app.libs.db\n"
    )
    assert expected in result.output


def test_impact_text_suggests_the_file_and_stops_reading_as_a_zero_blast_radius(
    tmp_path: Path,
):
    _make_libs_to_routers_chain(tmp_path)
    result = CliRunner().invoke(main, ["impact", str(tmp_path), "--file", "app/libs/dbb.py"])
    assert result.exit_code == 0, result.output  # deleted and renamed files take this path (#592)
    assert result.output.splitlines()[:3] == [
        "# nothing analysed: none of 1 file(s) resolved to a module, "
        "so no blast radius was computed",
        "# 1 file(s) did not resolve to a module (non-Python, excluded, or outside any package):",
        f"  ? {tmp_path / 'app/libs/dbb.py'}  (did you mean {tmp_path / 'app/libs/db.py'}?)",
    ]
    assert "module(s) depend on" not in result.output


def test_impact_json_carries_the_hint_and_the_verdict(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    args = ["impact", str(tmp_path), "--file", "app/libs/dbb.py", "--format", "json"]
    payload = json.loads(CliRunner().invoke(main, args).output)
    assert payload["nothing_resolved"] is True
    assert payload["unresolved_hints"] == [
        {
            "ref": str(tmp_path / "app/libs/dbb.py"),
            "did_you_mean": [str(tmp_path / "app/libs/db.py")],
        }
    ]


def test_impact_headline_is_unchanged_when_any_file_resolves(tmp_path: Path):
    _make_libs_to_routers_chain(tmp_path)
    args = ["impact", str(tmp_path), "--file", "app/libs/db.py", "--file", "app/libs/dbb.py"]
    result = CliRunner().invoke(main, args)
    assert result.output.startswith("# 1 module(s) depend on 1 changed module(s)")
    assert "(did you mean" in result.output
