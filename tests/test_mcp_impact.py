"""The impact and affected tools, and resolving changed files."""

from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from archy.gitlog import resolve_changed_paths
from archy.graph import build_graph
from archy.mcp import (
    _advertise_serialization_output_schemas,
    create_server,
)
from archy.mcp_runs import (
    run_affected,
    run_impact,
)
from tests.mcp_helpers import (
    _FILTER_MISS,
    _git,
    _init_git_repo,
    _output_schema,
    _project_with_a_test_of_a,
    _schema_properties_named,
)
from tests.mcp_helpers import call_tool as _call_tool


def test_serialization_schema_replaces_a_cached_validation_schema():
    server = create_server()
    tool = next(item for item in server._tool_manager.list_tools() if item.name == "archy_impact")
    tool.__dict__["output_schema"] = {"type": "object", "properties": {}}
    _advertise_serialization_output_schemas(server)
    listed = next(item for item in asyncio.run(server.list_tools()) if item.name == "archy_impact")
    schema = _output_schema(listed)
    assert schema is not None
    assert "empty_caveat" in _schema_properties_named(schema, "Impact")


def test_archy_impact_rejects_max_chains_zero(acyclic_project: Path):
    """MCP archy_impact must reject max_chains=0 the way the CLI does (#640).

    CLI `impact --max-chains 0` raises ClickException with a fixed message.
    Without the matching MCP guard, max_chains=0 silently returns impacted
    modules with empty chains (chains_omitted > 0) and no refusal.
    """
    from archy.mcp_compat import ToolError

    server = create_server()
    path = str(acyclic_project)
    message = "--max-chains must be negative (for all) or positive (for a limit); got 0"
    with pytest.raises(ValueError) as caught:
        run_impact(acyclic_project, files=[Path("pkg/b.py")], max_chains=0)
    assert str(caught.value) == message
    # mcp 2.x wraps as ToolError("Error executing tool <name>: <msg>"); pin the
    # refusal reaches the agent, not the exact outer wording.
    with pytest.raises(ToolError):
        _call_tool(
            server,
            "archy_impact",
            {"path": path, "files": ["pkg/b.py"], "max_chains": 0},
        )


def test_run_impact_refuses_empty_files(acyclic_project: Path):
    """Direct helper refuses files=[] (#794): no input is a refusal, not blast=[]."""
    with pytest.raises(ValueError, match="pass at least one file"):
        run_impact(acyclic_project, files=[])


def test_run_affected_refuses_empty_files(acyclic_project: Path):
    """Direct helper refuses files=[] (#794): no input is a refusal, not impacted_tests=[]."""
    with pytest.raises(ValueError, match="pass at least one file"):
        run_affected(acyclic_project, files=[], depth=5, test_filter=None)


def test_archy_impact_refuses_empty_files_on_the_wire(acyclic_project: Path):
    """MCP tool boundary carries the refusal as ToolError in both modes (#794 / #636)."""
    from archy.mcp_compat import ToolError

    server = create_server()
    path = str(acyclic_project)
    message = "pass at least one file"
    with pytest.raises(ToolError, match=message):
        _call_tool(server, "archy_impact", {"path": path, "files": [], "mode": "blast"})
    with pytest.raises(ToolError, match=message):
        _call_tool(server, "archy_impact", {"path": path, "files": [], "mode": "affected"})


def test_run_impact_payload_shape(tmp_path: Path):
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "lib.py").write_text("")
    (pkg / "main.py").write_text("from app.lib import x\n")
    result = run_impact(tmp_path, files=[Path("app/lib.py")])
    assert result.changed == ("app.lib",)
    assert result.impacted == ("app.main",)


def test_impact_tool_suggests_the_file_for_an_unresolved_path(acyclic_project: Path):
    """On the wire: both new fields reach the agent, not just the model."""
    typo = acyclic_project / "pkg" / "bb.py"
    assert not typo.exists()
    _content, structured = _call_tool(
        create_server(), "archy_impact", {"path": str(acyclic_project), "files": ["pkg/bb.py"]}
    )
    assert structured is not None
    payload = structured["result"]  # a union return type wraps the payload under `result`
    assert payload["nothing_resolved"] is True
    assert payload["unresolved_hints"] == [
        {"ref": str(typo), "did_you_mean": [str(acyclic_project / "pkg" / "b.py")]}
    ]


def test_impact_mode_blast_returns_chains(acyclic_project: Path):
    """`mode='blast'` (the default) answers "what breaks if I edit this", and a
    chain is the "because": the import path from an impacted module back to the
    changed one.

    `pkg.a` does `from pkg.b import thing` on line 1, so editing `pkg/b.py`
    reaches `pkg.a` in exactly one hop. The old test asserted only
    `isinstance(result, Impact)`, which the `-> Impact` annotation guarantees on
    every return path, so an entirely empty `Impact` passed it (#441).
    """
    result = run_impact(acyclic_project, files=[Path("pkg/b.py")])

    assert result.changed == ("pkg.b",)
    assert result.impacted == ("pkg.a",)
    assert result.chains_omitted == 0
    [chain] = result.chains
    assert chain.impacted == "pkg.a"
    assert chain.via == ("pkg.a", "pkg.b")
    assert [(h.source, h.target, h.lines) for h in chain.hops] == [("pkg.a", "pkg.b", (1,))]

    # The old comment claimed this covered `mode='blast'` routing, but
    # `run_impact` has no `mode` parameter, so nothing here went through it.
    # Route through the tool so the claim the test makes is one it tests.
    _, structured = _call_tool(
        create_server(),
        "archy_impact",
        {"path": str(acyclic_project), "files": ["pkg/b.py"], "mode": "blast"},
    )
    assert structured is not None
    routed = structured["result"]
    assert routed["impacted"] == ["pkg.a"]
    assert routed["chains"][0]["via"] == ["pkg.a", "pkg.b"]


def test_impact_mode_affected_returns_test_split(tmp_path: Path):
    # v0.36 (#227): archy_impact(mode='affected') replaces archy_affected and
    # returns the CI-shaped tests/modules split.
    from archy.affected import Affected

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "b.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("from pkg.a import thing\n")

    result = run_affected(tmp_path, files=[Path("pkg/a.py")], depth=5, test_filter=None)
    assert isinstance(result, Affected)
    assert "pkg.tests.test_a" in set(result.impacted_tests) or any(
        "test_a" in t for t in result.impacted_tests
    )


@pytest.mark.parametrize("layout", ["normal", "symlink", "nested", "worktree"])
def test_resolve_changed_paths_accepts_git_root_paths_from_subdirectory(
    tmp_path: Path, layout: str
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    if layout == "nested":
        repo = repo / "nested"
        repo.mkdir()
        _init_git_repo(repo)
    elif layout == "worktree":
        _git(repo, "commit", "--allow-empty", "-qm", "init")
        linked = tmp_path / "linked"
        _git(repo, "worktree", "add", "-b", "linked", str(linked))
        repo = linked
    project = repo / "services/api"
    project.mkdir(parents=True)
    _project_with_a_test_of_a(project)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "project")
    if layout == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(project, target_is_directory=True)
        project = alias
    changed = Path("services/api/pkg/a.py")
    (repo / changed).write_text("thing = 2\n")
    assert not (project / changed).exists()
    assert (repo / changed).exists()

    assert resolve_changed_paths(project, [changed]) == [repo / changed]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(resolve_changed_paths, project, [changed])
        second = pool.submit(resolve_changed_paths, project, [changed])
        assert first.result() == second.result() == [repo / changed]
    result = run_affected(project, files=[changed], depth=5, test_filter=None).model_dump(
        mode="json"
    )
    assert result["impacted_tests"] == ["tests.test_a"]
    assert result["deleted"] == []

    _git(repo, "rm", "-f", str(changed))
    assert resolve_changed_paths(project, [changed]) == [project / changed]
    deleted = run_affected(project, files=[changed], depth=5, test_filter=None).model_dump(
        mode="json"
    )
    assert deleted["impacted_tests"] == []
    assert deleted["deleted"] == [str(project / changed)]


def test_resolve_changed_paths_keeps_deleted_project_file_with_root_namesake(tmp_path: Path):
    _init_git_repo(tmp_path)
    project = tmp_path / "sub"
    project.mkdir()
    _project_with_a_test_of_a(project)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("unrelated = 1\n")
    (project / "pkg/a.py").unlink()
    assert resolve_changed_paths(project, [Path("pkg/a.py")]) == [project / "pkg/a.py"]
    deleted = run_affected(project, files=[Path("pkg/a.py")], depth=5, test_filter=None)
    assert deleted.model_dump(mode="json")["deleted"] == [str(project / "pkg/a.py")]


@pytest.mark.parametrize(
    ("test_filter", "message"),
    [
        (
            "test/**",
            f"test filter 'test/**' {_FILTER_MISS}; did you mean tests/**?",
        ),
        ("[", f"test filter '[' {_FILTER_MISS}"),
    ],
    ids=["typo", "invalid-glob"],
)
def test_archy_impact_refuses_a_test_filter_that_matches_no_file(
    tmp_path: Path, test_filter: str, message: str
):
    """#660: the agent was told nothing imports the code when its filter matched no file."""
    from archy.mcp_compat import ToolError

    args = _project_with_a_test_of_a(tmp_path)
    files = [Path("pkg/a.py")]
    assert build_graph(tmp_path).has_edge("tests.test_a", "pkg.a")  # the edge is real
    sound = run_affected(tmp_path, files=files, depth=5, test_filter="tests/**").model_dump()
    assert sound["impacted_tests"] == ("tests.test_a",)
    assert sound["empty_caveat"] is None

    with pytest.raises(ValueError, match=f"^{re.escape(message)}$"):
        run_affected(tmp_path, files=files, depth=5, test_filter=test_filter)
    # A refusal has no return model, so the wire form to pin here is the ToolError text.
    with pytest.raises(ToolError, match=f"{re.escape(message)}$"):
        _call_tool(create_server(), "archy_impact", {**args, "test_filter": test_filter})


def test_archy_impact_keeps_the_static_caveat_when_a_sound_filter_selects_nothing(tmp_path: Path):
    """The comparison case for the refusal: the filter matches `tests/test_a.py`, just not here."""
    args = {**_project_with_a_test_of_a(tmp_path), "files": ["pkg/lone.py"]}
    files = [Path("pkg/lone.py")]
    dumped = run_affected(tmp_path, files=files, depth=5, test_filter="tests/**").model_dump()
    assert dumped["impacted_tests"] == ()
    assert "importlib.import_module" in dumped["empty_caveat"]
    _, structured = _call_tool(create_server(), "archy_impact", {**args, "test_filter": "tests/**"})
    assert structured is not None
    # A union return type wraps the payload under `result`.
    assert "importlib.import_module" in structured["result"]["empty_caveat"]


def test_impact_tool_validates_mode(acyclic_project: Path):
    from archy.mcp_compat import ToolError

    server = create_server()
    with pytest.raises(ToolError):
        _call_tool(
            server,
            "archy_impact",
            {"path": str(acyclic_project), "files": ["pkg/a.py"], "mode": "sideways"},
        )


def test_impact_affected_expands_conftest(tmp_path: Path):
    """archy_impact(mode=affected) must expand conftest.py to the tests it serves."""
    from archy.affected import Affected

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

    result = run_affected(tmp_path, files=[Path("app/factory.py")], depth=5, test_filter=None)
    assert isinstance(result, Affected)
    assert "tests.conftest" not in result.impacted_tests
    assert "tests.test_x" in result.impacted_tests

    _, structured = _call_tool(
        create_server(),
        "archy_impact",
        {
            "path": str(tmp_path),
            "files": ["app/factory.py"],
            "mode": "affected",
        },
    )
    assert structured is not None
    payload = structured["result"]
    assert "tests.conftest" not in payload["impacted_tests"]
    assert "tests.test_x" in payload["impacted_tests"]
