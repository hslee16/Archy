from __future__ import annotations

import subprocess
from pathlib import Path

import networkx as nx
from click.testing import CliRunner

from archy.cli import main
from archy.hotspots import compute_hotspots, git_churn


def _node(g: nx.DiGraph, name: str, *, path: str, cc_sum: int) -> None:
    g.add_node(name, external=False, path=path, cc_sum=cc_sum)


def test_compute_hotspots_ranks_by_product():
    g = nx.DiGraph()
    _node(g, "pkg.hot", path="/repo/pkg/hot.py", cc_sum=10)
    _node(g, "pkg.warm", path="/repo/pkg/warm.py", cc_sum=4)
    _node(g, "pkg.cold", path="/repo/pkg/cold.py", cc_sum=20)
    churn = {
        "/repo/pkg/hot.py": 50,
        "/repo/pkg/warm.py": 30,
        "/repo/pkg/cold.py": 1,
    }
    rows = compute_hotspots(g, churn=churn)
    assert [r.module for r in rows] == ["pkg.hot", "pkg.warm", "pkg.cold"]
    assert rows[0].score == 500
    assert rows[1].score == 120
    assert rows[2].score == 20


def test_compute_hotspots_drops_zero_cc_and_zero_churn():
    # A file is only a hotspot when both signals are non-zero. cc=0 (e.g. a
    # bare __init__.py) and churn=0 (never modified since the import window)
    # should both be filtered, not surfaced as score=0.
    g = nx.DiGraph()
    _node(g, "pkg.init", path="/repo/pkg/__init__.py", cc_sum=0)
    _node(g, "pkg.stable", path="/repo/pkg/stable.py", cc_sum=5)
    _node(g, "pkg.simple_but_busy", path="/repo/pkg/busy.py", cc_sum=0)
    churn = {
        "/repo/pkg/__init__.py": 3,
        "/repo/pkg/stable.py": 0,
        "/repo/pkg/busy.py": 99,
    }
    assert compute_hotspots(g, churn=churn) == []


def test_compute_hotspots_excludes_external_nodes():
    g = nx.DiGraph()
    g.add_node("ext", external=True, path="/whatever/ext.py", cc_sum=99)
    rows = compute_hotspots(g, churn={"/whatever/ext.py": 99})
    assert rows == []


def test_compute_hotspots_ties_break_on_churn_then_module():
    # Equal score -> higher churn first; if churn also ties, alphabetical.
    g = nx.DiGraph()
    _node(g, "pkg.b", path="/repo/b.py", cc_sum=5)
    _node(g, "pkg.a", path="/repo/a.py", cc_sum=5)
    _node(g, "pkg.c", path="/repo/c.py", cc_sum=10)
    churn = {"/repo/a.py": 4, "/repo/b.py": 4, "/repo/c.py": 2}
    rows = compute_hotspots(g, churn=churn)
    # Scores: c=20, a=20, b=20. churn: c=2 < a=b=4 -> a,b ahead of c;
    # a before b alphabetically.
    assert [r.module for r in rows] == ["pkg.a", "pkg.b", "pkg.c"]


def test_git_churn_returns_none_outside_repo(tmp_path: Path):
    assert git_churn(tmp_path) is None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def test_git_churn_counts_commits_per_file(tmp_path: Path):
    repo = tmp_path
    _init_repo(repo)
    (repo / "a.py").write_text("x = 1\n")
    (repo / "b.py").write_text("y = 1\n")
    _git(repo, "add", "a.py", "b.py")
    _git(repo, "commit", "-q", "-m", "init")

    (repo / "a.py").write_text("x = 2\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "tweak a")

    (repo / "a.py").write_text("x = 3\n")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-q", "-m", "tweak a again")

    churn = git_churn(repo)
    assert churn is not None
    a_key = str((repo / "a.py").resolve())
    b_key = str((repo / "b.py").resolve())
    assert churn[a_key] == 3
    assert churn[b_key] == 1


def test_git_churn_filters_non_python(tmp_path: Path):
    repo = tmp_path
    _init_repo(repo)
    (repo / "a.py").write_text("x = 1\n")
    (repo / "README.md").write_text("hi\n")
    _git(repo, "add", "a.py", "README.md")
    _git(repo, "commit", "-q", "-m", "init")
    churn = git_churn(repo)
    assert churn is not None
    assert all(k.endswith(".py") for k in churn)


def _make_hotspot_project(repo: Path) -> None:
    _init_repo(repo)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    # `hot.py` has high CC (multiple branches) and will be touched twice
    (pkg / "hot.py").write_text(
        "def f(x):\n"
        "    if x:\n"
        "        return 1\n"
        "    elif x == 2:\n"
        "        return 2\n"
        "    for y in range(x):\n"
        "        if y: pass\n"
        "    return 0\n"
    )
    (pkg / "cold.py").write_text("def g():\n    return 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    (pkg / "hot.py").write_text(
        (pkg / "hot.py").read_text() + "\n# tweak\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "touch hot")


def test_cli_hotspots_text_smoke(tmp_path: Path):
    _make_hotspot_project(tmp_path)
    result = CliRunner().invoke(main, ["hotspots", str(tmp_path)])
    assert result.exit_code == 0, result.output
    # `hot.py` must appear ahead of `cold.py` (both higher CC and higher churn).
    assert "pkg.hot" in result.output
    hot_pos = result.output.index("pkg.hot")
    cold_pos = result.output.find("pkg.cold")
    if cold_pos != -1:
        assert hot_pos < cold_pos


def test_cli_hotspots_json(tmp_path: Path):
    import json as _json

    _make_hotspot_project(tmp_path)
    result = CliRunner().invoke(main, ["hotspots", str(tmp_path), "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert payload["total"] >= 1
    top = payload["hotspots"][0]
    assert top["module"] == "pkg.hot"
    assert top["score"] == top["cc_sum"] * top["churn"]


def test_cli_hotspots_errors_when_not_in_git_repo(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("def f():\n    if True: return 1\n")
    result = CliRunner().invoke(main, ["hotspots", str(tmp_path)])
    assert result.exit_code != 0
    assert "not inside a git repository" in result.output


def test_cli_hotspots_top_limits_rows(tmp_path: Path):
    import json as _json

    _make_hotspot_project(tmp_path)
    result = CliRunner().invoke(
        main, ["hotspots", str(tmp_path), "--top", "1", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    payload = _json.loads(result.output)
    assert len(payload["hotspots"]) <= 1
