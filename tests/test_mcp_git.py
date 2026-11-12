"""The duplicates and what-to-refactor-next tools, and the git history they read."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from archy.gitlog import resolve_changed_paths
from archy.mcp import (
    create_server,
)
from archy.mcp_runs import (
    run_duplicates,
    run_what_to_refactor_next,
)
from tests.mcp_helpers import _git, _hub_project, _init_git_repo, _init_hotspot_repo, _list_tools
from tests.mcp_helpers import call_tool as _call_tool


@pytest.mark.parametrize(
    ("tool", "parameter", "bad", "message"),
    [
        ("archy_duplicates", "members", 1, "members must be >= 2; got 1"),
        ("archy_duplicates", "members", -5, "members must be >= 2; got -5"),
        ("archy_duplicates", "min_nodes", 0, "min_nodes must be >= 1; got 0"),
        ("archy_duplicates", "top_n", 0, "top_n must be >= 1; got 0"),
    ],
)
def test_mcp_refusal_names_the_parameter_the_caller_sent(
    acyclic_project: Path, tool: str, parameter: str, bad: int, message: str
):
    """A refusal must name the tool's own parameter, so the caller can correct it (#780).

    An agent acts on this text. `compute_duplicates` says `min_members`, which
    `archy_duplicates` does not take. Add a row for any other tool's refusal.
    """
    from archy.mcp_compat import ToolError

    server = create_server()
    listed = next(item for item in _list_tools(server) if item.name == tool)
    assert parameter in listed.model_dump(by_alias=True)["inputSchema"]["properties"]

    with pytest.raises(ToolError) as caught:
        _call_tool(server, tool, {"path": str(acyclic_project), parameter: bad})

    # \b: `min_members must be` holds `members must be`, and must not pass.
    assert re.search(rf"\b{re.escape(message)}$", str(caught.value))


def test_run_duplicates_refuses_below_two_and_takes_two(acyclic_project: Path):
    """`members` is refused by its own name, and its boundary stays open (#780)."""

    def run(members: int):
        return run_duplicates(
            acyclic_project, response_format="summary", min_nodes=20, top_n=20, members=members
        )

    with pytest.raises(ValueError, match=r"^members must be >= 2; got 1$"):
        run(1)
    assert run(2).duplicates == ()


def test_run_duplicates_says_why_it_found_nothing(acyclic_project: Path):
    """An empty result names the threshold to lower, so it is not read as a clean bill."""
    payload = run_duplicates(
        acyclic_project, response_format="summary", min_nodes=20, top_n=20, members=2
    )

    assert payload.total == 0
    assert payload.note == (
        "No likely-duplicate function bodies of >= 20 normalized nodes found; "
        "lower min_nodes to widen the search."
    )


def test_duplicates_note_mentions_demoted_variants_and_is_silent_when_there_are_duplicates():
    from archy.mcp_runs import _duplicates_note

    quiet = _duplicates_note(0, 0, 20)
    with_variants = _duplicates_note(0, 2, 20)

    assert quiet is not None
    assert "variant" not in quiet
    assert with_variants == (
        quiet + " (2 likely-intentional variant(s) were found but demoted: "
        "same-class / boilerplate / test / vendored / independent.)"
    )
    assert _duplicates_note(1, 1, 20) is None


def test_refactor_structural_lens_reports_unconsulted_signals(tmp_path: Path):
    """Two commits touch a.py; one branch gives CC 2, so fused measures (2, 2)."""
    from click.testing import CliRunner

    from archy.cli import main

    _init_git_repo(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    a = pkg / "a.py"
    a.write_text("from pkg import b\n\ndef f(x):\n    if x: return 1\n    return 0\n")
    (pkg / "b.py").write_text("")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    a.write_text(a.read_text() + "\n# tweak\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "touch a")

    fused = run_what_to_refactor_next(tmp_path, top_n=20, since=None, min_risk=0.0)
    assert fused.git_available is True
    measured = next(e for e in fused.priorities if e.module == "pkg.a")
    assert measured.cc_sum == 2
    assert measured.churn == 2
    assert measured.hotspot_score == 4

    structural = run_what_to_refactor_next(
        tmp_path, lens="structural", top_n=20, since=None, min_risk=0.0
    ).model_dump()
    assert structural["git_available"] is None
    unmeasured = next(e for e in structural["priorities"] if e["module"] == "pkg.a")
    assert unmeasured["cc_sum"] is None
    assert unmeasured["churn"] is None
    assert unmeasured["hotspot_score"] is None

    _content, wire = _call_tool(
        create_server(),
        "archy_what_to_refactor_next",
        {"path": str(tmp_path), "lens": "structural", "min_risk": 0.0},
    )
    assert wire is not None
    assert wire["git_available"] is None
    wire_a = next(e for e in wire["priorities"] if e["module"] == "pkg.a")
    assert wire_a["cc_sum"] is None
    assert wire_a["churn"] is None
    assert wire_a["hotspot_score"] is None

    # The CLI exposes only the fused lens, so it still reports measured numbers.
    args = ["what-to-refactor-next", str(tmp_path), "--min-risk", "0"]
    cli_json = CliRunner().invoke(main, [*args, "--format", "json"])
    assert cli_json.exit_code == 0
    cli_payload = json.loads(cli_json.output)
    assert cli_payload["git_available"] is True
    cli_a = next(e for e in cli_payload["priorities"] if e["module"] == "pkg.a")
    assert cli_a["cc_sum"] == 2
    assert cli_a["churn"] == 2
    cli_text = CliRunner().invoke(main, args)
    assert cli_text.exit_code == 0
    assert "pkg.a" in cli_text.output
    assert "cc_sum=2, churn=2" in cli_text.output
    assert "not a git repo" not in cli_text.output


def test_refactor_refuses_a_garbage_since_even_for_the_structural_lens(tmp_path: Path):
    """The structural lens never reads git, but a typo in `since` must still be refused.

    Otherwise `since="2025-O1-01"` reads as "nothing to prioritize" instead of as a
    mistake. The refusal needs a repository: with none there is no ref to resolve.
    """
    from archy.gitlog import InvalidSinceError

    _hub_project(tmp_path, spokes=1)
    _init_git_repo(tmp_path)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")

    with pytest.raises(InvalidSinceError):
        run_what_to_refactor_next(
            tmp_path, lens="structural", top_n=5, since="not-a-date-or-ref", min_risk=0.0
        )


def test_resolve_changed_paths_preserves_project_precedence_and_absolute_paths(tmp_path: Path):
    _init_git_repo(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "a.py").write_text("root = 1\n")
    (project / "a.py").write_text("project = 1\n")
    absolute_missing = tmp_path / "missing.py"
    assert resolve_changed_paths(project, [Path("a.py"), tmp_path / "a.py", absolute_missing]) == [
        project / "a.py",
        tmp_path / "a.py",
        absolute_missing,
    ]


@pytest.mark.parametrize("git_available", [True, False], ids=["not-a-worktree", "git-missing"])
def test_resolve_changed_paths_without_git_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_available: bool
):
    if git_available:
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    else:
        _init_git_repo(tmp_path)
        monkeypatch.setenv("PATH", "")
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "a.py").write_text("")
    assert resolve_changed_paths(project, [Path("a.py")]) == [project / "a.py"]


def test_refactor_validates_lens(acyclic_project: Path):
    with pytest.raises(ValueError, match="lens must be"):
        run_what_to_refactor_next(
            acyclic_project, lens="sideways", top_n=5, since=None, min_risk=0.15
        )


def test_refactor_behavioral_lens_shape_and_ranking(tmp_path: Path):
    # v0.36 (#227): the behavioral lens replaces archy_hotspots. It surfaces only
    # CC x churn hotspots, ranked by hotspot_score (== cc_sum * churn).
    _init_hotspot_repo(tmp_path)
    payload = run_what_to_refactor_next(
        tmp_path, lens="behavioral", top_n=20, since=None, min_risk=0.0
    )
    assert payload.note is None
    assert payload.since is None
    # `shown` is `len(entries)` and `priorities` IS `entries`, so comparing them
    # was an identity of the constructor (#441). The fixture has exactly two
    # modules carrying functions, both under the top_n of 20, so both are shown.
    assert payload.total == 2
    assert payload.shown == 2
    assert [e.module for e in payload.priorities] == ["pkg.hot", "pkg.cold"]
    top = payload.priorities[0]
    assert top.module == "pkg.hot"
    # The behavioral lens keeps only rows the hotspot lens fired on (a row may
    # also carry edit_risk when it clears the structural floor too).
    assert "hotspot" in top.lenses
    assert top.cc_sum is not None
    assert top.churn is not None
    assert top.hotspot_score == top.cc_sum * top.churn
    assert top.path is not None
    assert top.path.endswith("pkg/hot.py")


def test_refactor_behavioral_lens_top_n_caps_results(tmp_path: Path):
    _init_hotspot_repo(tmp_path)
    payload = run_what_to_refactor_next(
        tmp_path, lens="behavioral", top_n=1, since=None, min_risk=0.0
    )
    assert payload.shown <= 1
    # total reports the size of the candidate pool, not the slice.
    assert payload.total >= payload.shown


def test_refactor_validates_top_n(tmp_path: Path):
    with pytest.raises(ValueError, match="top_n"):
        run_what_to_refactor_next(tmp_path, lens="behavioral", top_n=0, since=None, min_risk=0.0)


def test_refactor_behavioral_lens_diagnostic_when_not_in_git_repo(tmp_path: Path):
    # Same Python project shape but no `git init` -> the behavioral lens must NOT
    # raise. The agent reads `note` and pivots to lens='structural' instead.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("def f():\n    if True: return 1\n")
    payload = run_what_to_refactor_next(
        tmp_path, lens="behavioral", top_n=20, since=None, min_risk=0.0
    )
    assert payload.priorities == ()
    assert payload.total == 0
    assert payload.note is not None
    assert "not inside a git repository" in payload.note
    assert "lens='structural'" in payload.note


def test_what_to_refactor_next_fuses_both_lenses(tmp_path: Path):
    # `pkg.hot` is churned+complex (hotspot) and, by being imported by peers
    # while importing a dep, also central+fragile (edit-risk). It should rank
    # first and report both lenses fired.
    _init_git_repo(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "dep.py").write_text("")
    (pkg / "hot.py").write_text(
        "from pkg.dep import thing\n\n"
        "def f(x):\n    if x: return 1\n    elif x == 2: return 2\n    return 0\n"
    )
    (pkg / "a.py").write_text("from pkg.hot import f\n")
    (pkg / "b.py").write_text("from pkg.hot import f\n")
    (pkg / "c.py").write_text("from pkg.hot import f\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    (pkg / "hot.py").write_text((pkg / "hot.py").read_text() + "\n# tweak\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "touch hot")

    payload = run_what_to_refactor_next(tmp_path, top_n=5, since=None, min_risk=0.1)
    assert payload.git_available is True
    assert payload.note is None
    # Only `pkg.hot` clears both floors, so the fused ranking is one row long.
    # `shown == len(priorities)` was an identity of the constructor (#441).
    assert payload.total == 1
    assert payload.shown == 1
    top = payload.priorities[0]
    assert top.module == "pkg.hot"
    assert top.lenses == ("hotspot", "edit_risk")
    assert "Both a complexity" in top.rationale


def test_what_to_refactor_next_honest_null(tmp_path: Path):
    # Git present but the floor excludes every module and there is no churn yet
    # (single commit, files unchanged since) -> a real "nothing to prioritize"
    # answer with an explanatory note, not a manufactured #1.
    _init_git_repo(tmp_path)
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")

    payload = run_what_to_refactor_next(tmp_path, top_n=5, since=None, min_risk=1.0)
    assert payload.priorities == ()
    assert payload.total == 0
    assert payload.note is not None
    assert "nothing to prioritize" in payload.note


def test_what_to_refactor_next_validates_args(acyclic_project: Path):
    with pytest.raises(ValueError, match="top_n"):
        run_what_to_refactor_next(acyclic_project, top_n=0, since=None, min_risk=0.15)
    with pytest.raises(ValueError, match="min_risk"):
        run_what_to_refactor_next(acyclic_project, top_n=5, since=None, min_risk=1.5)


def test_behavioral_lens_since_propagates_to_git_churn(tmp_path: Path):
    # Verifies the `since` arg is actually plumbed into `git_churn`, not
    # silently dropped. Regression guard: if someone refactored the behavioral
    # lens and forgot to forward `since`, every other test in this file would
    # still pass.
    repo = tmp_path
    _init_git_repo(repo)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # Two branchy files (cc_sum > 0 each) so both are eligible for the
    # top-K under full history.
    (pkg / "hot.py").write_text("def f(x):\n    if x: return 1\n    return 0\n")
    (pkg / "old.py").write_text("def g(x):\n    if x: return 1\n    return 0\n")

    old_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00",
        "GIT_COMMITTER_DATE": "2020-01-01T00:00:00",
    }
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "old init", env=old_env)
    # Touch `hot.py` again in a "recent" commit so the since filter keeps it
    # but drops `old.py`, which only appears in the 2020 commit.
    (pkg / "hot.py").write_text((pkg / "hot.py").read_text() + "\n# tweak\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "recent tweak hot")

    full = run_what_to_refactor_next(repo, lens="behavioral", top_n=20, since=None, min_risk=0.0)
    full_modules = {h.module for h in full.priorities}
    assert "pkg.hot" in full_modules
    assert "pkg.old" in full_modules
    hot_full = next(h for h in full.priorities if h.module == "pkg.hot")
    old_full = next(h for h in full.priorities if h.module == "pkg.old")
    assert hot_full.churn == 2
    assert old_full.churn == 1

    filtered = run_what_to_refactor_next(
        repo, lens="behavioral", top_n=20, since="2025-01-01", min_risk=0.0
    )
    assert filtered.since == "2025-01-01"
    filtered_modules = {h.module for h in filtered.priorities}
    # `old.py` was last touched in 2020 -> dropped (churn=0 -> filtered).
    # `hot.py` still has the 2026-tweak commit -> kept with churn=1.
    assert "pkg.old" not in filtered_modules
    hot_filtered = next(h for h in filtered.priorities if h.module == "pkg.hot")
    assert hot_filtered.churn == 1


def test_what_to_refactor_next_refuses_garbage_since(tmp_path: Path):
    """#663: MCP must refuse a typo `since`, not return an empty calm ranking."""
    repo = tmp_path
    _init_git_repo(repo)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "hot.py").write_text("def f(x):\n    if x: return 1\n    return 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")

    with pytest.raises(ValueError, match="garbage-ref"):
        run_what_to_refactor_next(
            repo, lens="behavioral", top_n=5, since="garbage-ref", min_risk=0.0
        )


def test_conventions_tool_answers_a_negative_for_one_module(acyclic_project: Path):
    # The lookup has to reach the surface agents actually call. Shipped to the
    # CLI alone it would be unreachable from MCP, which is this repo's
    # most-repeated defect (AGENTS.md), and the point of the view is answering
    # in the NEGATIVE: the ranked report truncates, so absence from it proves
    # nothing, while absence from this COMPLETE list is the answer.
    (acyclic_project / "pkg" / "risk.py").write_text("from pkg.instability import inst\n")
    (acyclic_project / "pkg" / "instability.py").write_text("def inst(): ...\n")
    (acyclic_project / "pkg" / "hotspots.py").write_text("def hot(): ...\n")
    server = create_server()
    _c, view = _call_tool(
        server, "archy_module_view", {"path": str(acyclic_project), "module": "pkg.risk"}
    )
    assert isinstance(view, dict)
    assert view["module"] == "pkg.risk"
    assert view["status"] == "censused"
    assert view["imports_internal"] == ["pkg.instability"]
    assert "pkg.hotspots" not in view["imports_internal"]


def test_what_to_refactor_next_fresh_repo_note_mentions_commit_history(tmp_path: Path):
    # #648: a fresh `git init` has no commits; git_churn used to collapse that
    # into "not a git repository". The note must say what is actually missing.
    repo = tmp_path
    _init_git_repo(repo)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # Sanity: the fixture really is a repo with zero commits.
    inside = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"], capture_output=True
    )
    assert inside.returncode == 0
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True)
    assert head.returncode != 0

    payload = run_what_to_refactor_next(repo, top_n=20, since=None, min_risk=0.0)
    assert payload.git_available is False
    assert payload.note is not None
    assert "no commit history" in payload.note
    assert "not inside a git repository" not in payload.note
