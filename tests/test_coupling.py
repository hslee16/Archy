from __future__ import annotations

import json as _json
import subprocess
from pathlib import Path

import networkx as nx
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from archy.cli import main
from archy.coupling import (
    CoChangeData,
    CouplingPair,
    compute_coupling,
    git_cochange,
    internal_module_paths,
)

# --------------------------------------------------------------------------- #
# compute_coupling unit tests (constructed graph + CoChangeData, no disk)
# --------------------------------------------------------------------------- #


def _graph(modules: dict[str, str], edges: list[tuple[str, str]] | None = None) -> nx.DiGraph:
    """Graph whose internal nodes carry the given qualname -> path mapping."""
    g: nx.DiGraph = nx.DiGraph()
    for qual, path in modules.items():
        g.add_node(qual, path=path, external=False)
    for src, dst in edges or []:
        g.add_edge(src, dst)
    return g


def _cochange(counts: dict[str, int], pairs: dict[tuple[str, str], int]) -> CoChangeData:
    return CoChangeData(counts=counts, pair_support=pairs)


def test_confidence_is_support_over_rarer_count():
    g = _graph({"pkg.a": "/a.py", "pkg.b": "/b.py"})
    co = _cochange({"/a.py": 20, "/b.py": 10}, {("/a.py", "/b.py"): 8})
    [pair] = compute_coupling(g, co, min_support=1, min_confidence=0.0)
    # 8 co-change / min(20, 10) = 0.8, keyed off the rarer (b) module's history.
    assert pair.confidence == pytest.approx(0.8)
    assert pair.support == 8
    assert (pair.module_a, pair.module_b) == ("pkg.a", "pkg.b")


def test_structural_edge_pair_is_excluded():
    # a -> b import edge means the graph already captures this coupling.
    g = _graph({"pkg.a": "/a.py", "pkg.b": "/b.py"}, edges=[("pkg.a", "pkg.b")])
    co = _cochange({"/a.py": 10, "/b.py": 10}, {("/a.py", "/b.py"): 9})
    assert compute_coupling(g, co, min_support=1, min_confidence=0.0) == []


def test_reverse_structural_edge_also_excluded():
    g = _graph({"pkg.a": "/a.py", "pkg.b": "/b.py"}, edges=[("pkg.b", "pkg.a")])
    co = _cochange({"/a.py": 10, "/b.py": 10}, {("/a.py", "/b.py"): 9})
    assert compute_coupling(g, co, min_support=1, min_confidence=0.0) == []


def test_pair_with_non_module_endpoint_is_dropped():
    # /b.py maps to no graph node (e.g. a script outside the module set).
    g = _graph({"pkg.a": "/a.py"})
    co = _cochange({"/a.py": 10, "/b.py": 10}, {("/a.py", "/b.py"): 9})
    assert compute_coupling(g, co, min_support=1, min_confidence=0.0) == []


def test_min_support_floor():
    g = _graph({"pkg.a": "/a.py", "pkg.b": "/b.py"})
    co = _cochange({"/a.py": 4, "/b.py": 4}, {("/a.py", "/b.py"): 4})
    assert compute_coupling(g, co, min_support=5, min_confidence=0.0) == []
    assert len(compute_coupling(g, co, min_support=4, min_confidence=0.0)) == 1


def test_min_confidence_floor():
    g = _graph({"pkg.a": "/a.py", "pkg.b": "/b.py"})
    co = _cochange({"/a.py": 10, "/b.py": 10}, {("/a.py", "/b.py"): 4})  # conf 0.4
    assert compute_coupling(g, co, min_support=1, min_confidence=0.5) == []
    assert len(compute_coupling(g, co, min_support=1, min_confidence=0.4)) == 1


def test_ranked_by_confidence_then_support():
    g = _graph({"a": "/a.py", "b": "/b.py", "c": "/c.py", "d": "/d.py"})
    co = _cochange(
        {"/a.py": 10, "/b.py": 10, "/c.py": 10, "/d.py": 10},
        {("/a.py", "/b.py"): 9, ("/c.py", "/d.py"): 5},  # conf 0.9 vs 0.5
    )
    rows = compute_coupling(g, co, min_support=1, min_confidence=0.0)
    assert [(r.module_a, r.module_b) for r in rows] == [("a", "b"), ("c", "d")]


def test_deterministic_module_ordering_and_paired_fields():
    # Pair key order is (/a, /z) but modules sort z-first? No: names decide.
    g = _graph({"z.mod": "/z.py", "a.mod": "/a.py"})
    co = _cochange({"/z.py": 10, "/a.py": 5}, {("/a.py", "/z.py"): 5})
    [pair] = compute_coupling(g, co, min_support=1, min_confidence=0.0)
    # module_a is the alphabetically-first qualname, with its own path + count.
    assert pair.module_a == "a.mod" and pair.path_a == "/a.py" and pair.count_a == 5
    assert pair.module_b == "z.mod" and pair.path_b == "/z.py" and pair.count_b == 10


def test_internal_module_paths_skips_external_and_pathless():
    g: nx.DiGraph = nx.DiGraph()
    g.add_node("pkg.a", path="/a.py", external=False)
    g.add_node("ext", external=True)
    g.add_node("nopath", external=False)
    assert internal_module_paths(g) == {str(Path("/a.py").resolve()): "pkg.a"}


# --------------------------------------------------------------------------- #
# git_cochange real-git tests (tmp_path)
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _write(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body)


def _p(repo: Path, name: str) -> str:
    """The resolved absolute path string a graph node / git_cochange key uses."""
    return str((repo / name).resolve())


def _key(a: str, b: str) -> tuple[str, str]:
    """The sorted pair key git_cochange stores (order-independent)."""
    return (min(a, b), max(a, b))


def test_git_cochange_returns_none_outside_repo(tmp_path: Path):
    assert git_cochange(tmp_path) is None


def test_git_cochange_counts_pair_support(tmp_path: Path):
    repo = tmp_path
    _init_repo(repo)
    # a's count (4) exceeds the pair support (3) because the 4th commit touched
    # a alone, so min(count_a, count_b) is b's 3 - the coupling denominator.
    for i in range(3):
        _write(repo, "a.py", f"x = {i}\n")
        _write(repo, "b.py", f"y = {i}\n")
        _git(repo, "add", "a.py", "b.py")
        _git(repo, "commit", "-q", "-m", f"both {i}")
    _write(repo, "a.py", "x = 99\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "a only")

    data = git_cochange(repo)
    assert data is not None
    a, b = _p(repo, "a.py"), _p(repo, "b.py")
    assert data.counts[a] == 4 and data.counts[b] == 3
    assert data.pair_support[_key(a, b)] == 3


def test_git_cochange_folds_rename_into_pair(tmp_path: Path):
    repo = tmp_path
    _init_repo(repo)
    _write(repo, "a.py", "x = 0\n")
    _write(repo, "b.py", "y = 0\n")
    _git(repo, "add", "a.py", "b.py")
    _git(repo, "commit", "-q", "-m", "init both")
    _git(repo, "mv", "a.py", "renamed.py")
    _write(repo, "b.py", "y = 1\n")
    _git(repo, "add", "renamed.py", "b.py")
    _git(repo, "commit", "-q", "-m", "rename a + touch b")

    data = git_cochange(repo)
    assert data is not None
    renamed, b = _p(repo, "renamed.py"), _p(repo, "b.py")
    # Both commits touched the (a->renamed) file together with b, folded onto
    # the current path, so support is 2 - not split across old/new names.
    assert data.pair_support[_key(renamed, b)] == 2


def test_git_cochange_skips_sweeping_commit(tmp_path: Path):
    repo = tmp_path
    _init_repo(repo)
    # 4 files in the sweep so it exceeds cap=3 below; 2 also touched in a
    # focused commit, so the same pair is reachable with or without the sweep.
    for name in ("a.py", "b.py", "c.py", "d.py"):
        _write(repo, name, "v = 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sweep")
    _write(repo, "a.py", "v = 1\n")
    _write(repo, "b.py", "v = 1\n")
    _git(repo, "add", "a.py", "b.py")
    _git(repo, "commit", "-q", "-m", "focused a+b")

    a, b = _p(repo, "a.py"), _p(repo, "b.py")
    key = _key(a, b)
    # cap=3 drops the 4-file sweep, leaving only the focused commit's pair.
    capped = git_cochange(repo, max_commit_files=3)
    assert capped is not None
    assert capped.pair_support[key] == 1
    assert capped.counts[a] == 1  # only the focused commit counts
    # cap=10 keeps both commits: the pair is supported by sweep + focused = 2.
    uncapped = git_cochange(repo, max_commit_files=10)
    assert uncapped is not None
    assert uncapped.pair_support[key] == 2


def test_git_cochange_keep_paths_restricts_counting(tmp_path: Path):
    repo = tmp_path
    _init_repo(repo)
    _write(repo, "a.py", "x = 0\n")
    _write(repo, "b.py", "y = 0\n")
    _write(repo, "vendor.py", "z = 0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "all three")

    a, b = _p(repo, "a.py"), _p(repo, "b.py")
    data = git_cochange(repo, keep_paths=frozenset({a, b}))
    assert data is not None
    # vendor.py is outside keep_paths, so no pair references it.
    assert set(data.counts) == {a, b}
    assert list(data.pair_support) == [_key(a, b)]


def test_git_cochange_ignores_non_python(tmp_path: Path):
    repo = tmp_path
    _init_repo(repo)
    _write(repo, "a.py", "x = 0\n")
    _write(repo, "README.md", "hi\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    data = git_cochange(repo)
    assert data is not None
    assert set(data.counts) == {_p(repo, "a.py")}
    assert data.pair_support == {}  # a.py alone -> no pair


# --------------------------------------------------------------------------- #
# CLI smoke
# --------------------------------------------------------------------------- #


def _make_coupled_project(repo: Path) -> None:
    _init_repo(repo)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # a and b co-change but never import each other -> a hidden-coupling pair.
    for i in range(6):
        (pkg / "a.py").write_text(f"VALUE_A = {i}\n")
        (pkg / "b.py").write_text(f"VALUE_B = {i}\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"tick {i}")


def test_cli_coupling_text_smoke(tmp_path: Path):
    _make_coupled_project(tmp_path)
    result = CliRunner().invoke(
        main, ["coupling", str(tmp_path), "--min-support", "3", "--min-confidence", "0.5"]
    )
    assert result.exit_code == 0, result.output
    assert "pkg.a <-> pkg.b" in result.output
    assert "hidden-coupling pair(s)" in result.output


def test_cli_coupling_json_smoke(tmp_path: Path):
    _make_coupled_project(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "coupling",
            str(tmp_path),
            "--min-support",
            "3",
            "--min-confidence",
            "0.5",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["total"] == 1
    assert payload["note"] is None
    pair = payload["pairs"][0]
    assert {pair["module_a"], pair["module_b"]} == {"pkg.a", "pkg.b"}
    assert pair["support"] == 6


def test_cli_coupling_empty_has_note(tmp_path: Path):
    _make_coupled_project(tmp_path)
    result = CliRunner().invoke(
        main, ["coupling", str(tmp_path), "--min-support", "999", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["total"] == 0
    assert "min-support" in payload["note"]


def test_cli_coupling_outside_git_errors(tmp_path: Path):
    (tmp_path / "m.py").write_text("x = 1\n")
    result = CliRunner().invoke(main, ["coupling", str(tmp_path)])
    assert result.exit_code != 0
    assert "not inside a git repository" in result.output


def test_cli_coupling_rejects_bad_flags(tmp_path: Path):
    _make_coupled_project(tmp_path)
    assert CliRunner().invoke(main, ["coupling", str(tmp_path), "--top", "0"]).exit_code != 0
    assert (
        CliRunner().invoke(main, ["coupling", str(tmp_path), "--min-support", "0"]).exit_code != 0
    )
    assert (
        CliRunner().invoke(main, ["coupling", str(tmp_path), "--min-confidence", "1.5"]).exit_code
        != 0
    )


def test_coupling_pair_is_frozen_model():
    p = CouplingPair(
        module_a="a",
        module_b="b",
        path_a="/a",
        path_b="/b",
        support=3,
        confidence=0.5,
        count_a=6,
        count_b=6,
    )
    with pytest.raises(ValidationError):
        p.support = 4  # frozen
