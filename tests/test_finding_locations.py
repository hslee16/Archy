"""Findings carry the file their `lines` belong to, printed as `path:line:` (#522).

`archy check` printed `pkg.b -> pkg.a  (line: 4)` and the JSON had no path, so a
reader had to map a dotted name back to a file. The importing module's
repo-relative path now travels with the finding on every surface, and the text
output leads with `path:line:`, the shape editors and CI matchers already read.

Every expected path here is written by hand (`src/pkg/core/api.py`), never
computed by the code under test. The project uses a src layout on purpose: that
is the case where dropping the `src/` prefix would still look plausible.
"""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest
from click.testing import CliRunner

from archy.cli import main
from archy.cli_render import _location
from archy.graph import source_path
from archy.mcp_runs import run_check, run_cycles


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A src-layout project with one forbidden import and two cycles.

    `pkg.core.api` imports `pkg.cli.runner` on line 3 (forbidden, and half of the
    first cycle); `runner` imports `api` on line 1. `pkg.p` is a package whose own
    `__init__.py` is half of the second cycle, so its path must end in `__init__.py`.
    """
    src = tmp_path / "src" / "pkg"
    for sub in ("", "core", "cli", "p"):
        (src / sub).mkdir(parents=True, exist_ok=True)
        (src / sub / "__init__.py").write_text("")
    (src / "core" / "api.py").write_text("import os\n\nfrom pkg.cli.runner import go\n")
    (src / "cli" / "runner.py").write_text("from pkg.core import api\n")
    (src / "p" / "__init__.py").write_text("from pkg.p import q\n")
    (src / "p" / "q.py").write_text("from pkg.p import w\n")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  core: {modules: [pkg.core.**]}\n"
        "  cli: {modules: [pkg.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    # The fixture must really be a src layout whose modules the graph names `pkg.*`,
    # or every path below would be asserting against a different project.
    graph = CliRunner().invoke(main, ["graph", str(tmp_path), "--internal-only"])
    assert "pkg.core.api" in graph.output and "src.pkg" not in graph.output, graph.output
    return tmp_path


def _run(*args: str):
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code in (0, 1), result.output
    return result


def test_check_text_leads_with_path_and_line(project: Path):
    out = _run("check", str(project)).output
    assert "  src/pkg/core/api.py:3: pkg.core.api -> pkg.cli.runner  (line: 3)" in out


def test_check_json_carries_the_path(project: Path):
    payload = json.loads(_run("check", str(project), "--format", "json").stdout)
    [violation] = payload["violations"]
    assert violation["path"] == "src/pkg/core/api.py"
    assert violation["lines"] == [3]


def test_cycles_text_leads_with_path_and_line_on_both_edges(project: Path):
    out = _run("cycles", str(project)).output
    assert "  src/pkg/cli/runner.py:1: pkg.cli.runner -> pkg.core.api  (line: 1)" in out
    assert "  src/pkg/core/api.py:3: pkg.core.api -> pkg.cli.runner  (line: 3)" in out


def test_a_package_edge_points_at_its_init_file(project: Path):
    out = _run("cycles", str(project)).output
    assert "  src/pkg/p/__init__.py:1: pkg.p -> pkg.p.q  (line: 1)" in out
    assert "  src/pkg/p/q.py:1: pkg.p.q -> pkg.p  (line: 1)" in out


def test_cycles_json_carries_a_path_per_edge(project: Path):
    payload = json.loads(_run("cycles", str(project), "--format", "json").stdout)
    paths = {(e["source"], e["target"]): e["path"] for c in payload for e in c["edges"]}
    assert paths[("pkg.core.api", "pkg.cli.runner")] == "src/pkg/core/api.py"
    assert paths[("pkg.cli.runner", "pkg.core.api")] == "src/pkg/cli/runner.py"
    assert paths[("pkg.p", "pkg.p.q")] == "src/pkg/p/__init__.py"


@pytest.fixture
def sdp_project(tmp_path: Path) -> Path:
    """`a -> b` is the SDP violation (see `_make_sdp_violating_project` in test_cli)."""
    pkg = tmp_path / "src" / "myapp"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from myapp.b import thing\n")
    (pkg / "b.py").write_text("from myapp import y1, y2, y3\n")
    for name in ("y1", "y2", "y3"):
        (pkg / f"{name}.py").write_text("")
    for name in ("x1", "x2", "x3"):
        (pkg / f"{name}.py").write_text("from myapp.a import thing\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nsdp:\n  enabled: true\n")
    return tmp_path


def test_sdp_text_and_json_carry_the_path(sdp_project: Path):
    out = _run("check", str(sdp_project)).output
    assert "  src/myapp/a.py:1: myapp.a (I=" in out
    payload = json.loads(_run("check", str(sdp_project), "--format", "json").stdout)
    [violation] = [v for v in payload["sdp_violations"] if v["source"] == "myapp.a"]
    assert violation["path"] == "src/myapp/a.py"


def test_mcp_payloads_carry_the_path_in_model_dump(project: Path):
    """Assert the serialized form: this is what the wire carries."""
    checked = run_check(project, config_path=None).model_dump()
    assert [v["path"] for v in checked["violations"]] == ["src/pkg/core/api.py"]
    found = run_cycles(project, min_size=2, internal_only=True)
    assert isinstance(found, list)  # exclusion is off, so the bare-list shape
    cycles = [c.model_dump() for c in found]
    assert "src/pkg/cli/runner.py" in {e["path"] for c in cycles for e in c["edges"]}


def _added_violation_baseline(project: Path) -> Path:
    """Snapshot the project WITHOUT its forbidden import, then put the import back."""
    api = project / "src" / "pkg" / "core" / "api.py"
    violating = api.read_text()
    api.write_text("import os\n")
    baseline = project / "baseline.json"
    assert _run("snapshot", str(project), "--out", str(baseline)).exit_code == 0
    api.write_text(violating)
    return baseline


def test_diff_prefixes_an_added_violation_with_its_location(project: Path):
    baseline = _added_violation_baseline(project)
    out = _run("diff", str(project), "--baseline", str(baseline)).output
    assert "  + src/pkg/core/api.py:3: pkg.core.api -> pkg.cli.runner  (core -> cli)" in out


def test_a_resolved_violation_keeps_the_path_its_baseline_recorded(project: Path):
    """The snapshot reader must carry `path` through, not drop it to the default."""
    baseline = project / "baseline.json"
    assert _run("snapshot", str(project), "--out", str(baseline)).exit_code == 0
    api = project / "src" / "pkg" / "core" / "api.py"
    api.write_text("import os\n")  # the forbidden import is gone
    result = json.loads(
        _run("diff", str(project), "--baseline", str(baseline), "--format", "json").stdout
    )
    [resolved] = result["violations"]["resolved"]
    assert resolved["source"] == "pkg.core.api"
    assert resolved["path"] == "src/pkg/core/api.py"


def test_a_resolved_cycle_keeps_the_edge_paths_its_baseline_recorded(project: Path):
    baseline = project / "baseline.json"
    assert _run("snapshot", str(project), "--out", str(baseline)).exit_code == 0
    (project / "src" / "pkg" / "p" / "__init__.py").write_text("")  # breaks the pkg.p cycle
    result = json.loads(
        _run("diff", str(project), "--baseline", str(baseline), "--format", "json").stdout
    )
    [cycle] = [c for c in result["cycles"]["resolved"] if "pkg.p" in c["modules"]]
    paths = {(e["source"], e["target"]): e["path"] for e in cycle["edges"]}
    assert paths[("pkg.p", "pkg.p.q")] == "src/pkg/p/__init__.py"
    assert paths[("pkg.p.q", "pkg.p")] == "src/pkg/p/q.py"


def test_a_resolved_sdp_violation_keeps_the_path_its_baseline_recorded(sdp_project: Path):
    baseline = sdp_project / "baseline.json"
    assert _run("snapshot", str(sdp_project), "--out", str(baseline)).exit_code == 0
    (sdp_project / "src" / "myapp" / "a.py").write_text("X = 1\n")  # a no longer imports b
    result = json.loads(
        _run("diff", str(sdp_project), "--baseline", str(baseline), "--format", "json").stdout
    )
    [resolved] = [v for v in result["sdp_violations"]["resolved"] if v["source"] == "myapp.a"]
    assert resolved["path"] == "src/myapp/a.py"


def test_a_relative_root_still_yields_repo_relative_paths(
    project: Path, monkeypatch: pytest.MonkeyPatch
):
    """`archy check` run from the project directory scans `.`, which is not absolute."""
    monkeypatch.chdir(project)
    out = _run("check", ".").output
    assert "  src/pkg/core/api.py:3: pkg.core.api -> pkg.cli.runner  (line: 3)" in out


def test_a_symlinked_root_still_yields_repo_relative_paths(project: Path, tmp_path_factory):
    """A checkout reached through a symlink: node paths are resolved, the root is not."""
    alias = tmp_path_factory.mktemp("alias") / "link"
    alias.symlink_to(project, target_is_directory=True)
    out = _run("check", str(alias)).output
    assert "  src/pkg/core/api.py:3: pkg.core.api -> pkg.cli.runner  (line: 3)" in out


def test_a_baseline_written_before_path_existed_still_diffs_to_nothing(project: Path):
    """Identity stays keyed on module names: a missing `path` must not read as churn."""
    baseline = project / "baseline.json"
    assert _run("snapshot", str(project), "--out", str(baseline)).exit_code == 0
    payload = json.loads(baseline.read_text())
    assert payload["violations"][0]["path"] == "src/pkg/core/api.py"  # the field is written

    def drop_path(item):
        if isinstance(item, dict):
            return {k: drop_path(v) for k, v in item.items() if k != "path"}
        if isinstance(item, list):
            return [drop_path(v) for v in item]
        return item

    old = drop_path(payload)
    assert "path" not in json.dumps(old)
    baseline.write_text(json.dumps(old))
    result = json.loads(
        _run("diff", str(project), "--baseline", str(baseline), "--format", "json").stdout
    )
    assert result["violations"]["added"] == [] and result["violations"]["resolved"] == []
    assert result["cycles"]["added"] == [] and result["cycles"]["resolved"] == []


def test_a_committed_findings_only_baseline_does_not_record_locations(project: Path):
    """`lines` and `path` are where a finding sits, so neither belongs in the committed file."""
    baseline = project / "findings.json"
    assert _run("snapshot", str(project), "--out", str(baseline), "--findings-only").exit_code == 0
    text = baseline.read_text()
    assert '"path"' not in text and '"lines"' not in text
    assert "pkg.core.api" in text  # the findings themselves are still there


def test_location_is_empty_when_there_is_nothing_to_point_at():
    assert _location("src/pkg/b.py", (4, 9)) == "src/pkg/b.py:4: "
    assert _location("", (4,)) == ""  # an old snapshot has no path
    assert _location("src/pkg/b.py", ()) == ""  # an edge with no recorded line


def test_source_path_degrades_to_empty_rather_than_failing(tmp_path: Path):
    graph: nx.DiGraph = nx.DiGraph()
    graph.graph["root"] = str(tmp_path)
    graph.add_node("inside", path=str(tmp_path / "pkg" / "m.py"))
    graph.add_node("outside", path="/somewhere/else/m.py")
    graph.add_node("pathless")
    assert source_path(graph, "inside") == "pkg/m.py"
    assert source_path(graph, "outside") == ""
    assert source_path(graph, "pathless") == ""
    assert source_path(graph, "not-in-graph") == ""
    graph.graph.pop("root")
    assert source_path(graph, "inside") == ""
