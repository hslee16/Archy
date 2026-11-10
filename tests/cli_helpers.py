"""Project builders and invokers shared by the `tests/test_cli_*.py` modules."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from archy.cli import (
    main,
)
from archy.diff import (
    ScoreDelta,
)
from archy.duplicates import DuplicateGroup, DuplicateMember


def _make_two_module_project(tmp_path: Path, *, cyclic: bool) -> Path:
    """`pkg.a` imports `pkg.b`; `pkg.b` imports `pkg.a` only when cyclic.

    Underlies both `_make_cyclic_project` and `_make_acyclic_project`,
    which keep descriptive call-site names while sharing the layout.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.a import other\n" if cyclic else "")
    return tmp_path


def _make_cyclic_project(tmp_path: Path) -> Path:
    return _make_two_module_project(tmp_path, cyclic=True)


def _make_acyclic_project(tmp_path: Path) -> Path:
    return _make_two_module_project(tmp_path, cyclic=False)


def _make_empty_package_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    return tmp_path


def _make_layered_project(tmp_path: Path, *, with_violation: bool) -> Path:
    """Two-layer project. core imports from cli iff with_violation=True."""
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    core = pkg / "core"
    core.mkdir()
    (core / "__init__.py").write_text("")
    cli = pkg / "cli"
    cli.mkdir()
    (cli / "__init__.py").write_text("")
    (cli / "runner.py").write_text("from myapp.core import api\n")
    if with_violation:
        (core / "api.py").write_text("from myapp.cli.runner import go\n")
    else:
        (core / "api.py").write_text("")
    cfg = tmp_path / "archy.yaml"
    cfg.write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )
    return tmp_path


def _make_absent_layers_project(tmp_path: Path) -> Path:
    """One module under `app`, and two declared layers that match nothing."""
    project = tmp_path / "proj"
    (project / "app").mkdir(parents=True)
    (project / "app" / "__init__.py").write_text("")
    (project / "app" / "everything.py").write_text("x = 1\n")
    (project / "archy.yaml").write_text(
        "min_layers_present: 2\n"
        "layers:\n"
        "  routes:\n    modules: ['app.routes.**']\n"
        "  services:\n    modules: ['app.services.**']\n"
        "forbid:\n  - {from: services, to: routes}\n"
    )
    return project


def _make_registry_project(tmp_path: Path, *, bootstrap: str) -> Path:
    """The reported incident, reduced: `commands/` modules run standalone, and
    each needs `core.database.model_registry` imported before the ORM configures
    its mappers. `bootstrap` is what `commands/__init__.py` contains.
    """
    app = tmp_path / "app"
    (app / "core" / "database").mkdir(parents=True)
    (app / "commands").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "core" / "__init__.py").write_text("")
    (app / "core" / "database" / "__init__.py").write_text("")
    (app / "core" / "database" / "model_registry.py").write_text("REGISTRY = {}\n")
    (app / "commands" / "__init__.py").write_text(bootstrap)
    (app / "commands" / "setup_user.py").write_text("x = 1\n")
    (app / "commands" / "backfill.py").write_text("x = 2\n")
    (tmp_path / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: 'app.commands.*'\n"
        "    must_reach: app.core.database.model_registry\n"
        "    reason: standalone entrypoints need the full mapper registry\n"
    )
    return tmp_path


def _make_entrypoint_project(tmp_path: Path, *, worker_body: str) -> Path:
    """The registry project, plus a runnable `app/worker.py` no glob names and
    a guarded test module the rule's `within` must leave alone."""
    project = _make_registry_project(
        tmp_path, bootstrap="from app.core.database import model_registry\n"
    )
    (project / "app" / "worker.py").write_text(
        worker_body + 'if __name__ == "__main__":\n    pass\n'
    )
    (project / "tests").mkdir()
    (project / "tests" / "__init__.py").write_text("")
    (project / "tests" / "test_worker.py").write_text('if __name__ == "__main__":\n    pass\n')
    (project / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: '@entrypoints'\n"
        "    within: 'app.**'\n"
        "    must_reach: app.core.database.model_registry\n"
        "    reason: entrypoints need the mapper registry\n"
    )
    return project


def _make_external_target_project(tmp_path: Path, *, bootstrap: str) -> Path:
    """A `required:` rule whose `must_reach` is an EXTERNAL package."""
    app = tmp_path / "app"
    (app / "commands").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "commands" / "__init__.py").write_text(bootstrap)
    (app / "commands" / "setup_user.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n  - source: 'app.commands.*'\n    must_reach: sqlalchemy\n"
    )
    return tmp_path


def _make_sdp_violating_project(tmp_path: Path) -> Path:
    # See test_run_check_reports_sdp_violations_when_enabled in test_mcp_check.py
    # for the I calculation; the a -> b edge is the SDP violation.
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from myapp.b import thing\n")
    (pkg / "b.py").write_text("from myapp import y1, y2, y3\n")
    for name in ("y1", "y2", "y3"):
        (pkg / f"{name}.py").write_text("")
    for name in ("x1", "x2", "x3"):
        (pkg / f"{name}.py").write_text("from myapp.a import thing\n")
    return tmp_path


def _seed_history(project: Path, overall: float) -> None:
    """Write a synthetic history row with a chosen `overall`.

    Geometric-mean composites are noisy on tiny fixtures - small structural
    changes can move overall in either direction - so we seed history
    directly to test the gate decision in isolation from the score.
    """
    history = project / ".archy" / "history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "timestamp": "2026-05-09T13:45:07Z",
        "commit": "deadbee",
        "branch": "main",
        "score": {
            "overall": overall,
            "modularity": 0.5,
            "acyclicity": 1.0,
            "depth": 0.5,
            "equality": 0.5,
        },
        "inputs": {
            "module_count": 3,
            "edge_count": 1,
            "cycle_count": 0,
            "max_depth": 1,
            "community_count": 2,
        },
    }
    history.write_text(json.dumps(row) + "\n")


def _make_project_with_generated_dir(tmp_path: Path) -> Path:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "real.py").write_text("import os\n")
    gen = pkg / "baml_client"
    gen.mkdir()
    (gen / "__init__.py").write_text("from myapp.baml_client.b import x\n")
    (gen / "b.py").write_text("from myapp.baml_client import other\n")
    return tmp_path


def _make_libs_to_routers_chain(tmp_path: Path) -> None:
    """`app.routers.user` imports `app.libs.db`.

    Shared by `archy impact` and `archy affected` CLI tests that need a
    two-module chain to exercise reverse-dependency traversal.
    """
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    libs = pkg / "libs"
    libs.mkdir()
    (libs / "__init__.py").write_text("")
    (libs / "db.py").write_text("")
    routers = pkg / "routers"
    routers.mkdir()
    (routers / "__init__.py").write_text("")
    (routers / "user.py").write_text("from app.libs.db import x\n")


def _make_two_cycle_project(tmp_path: Path) -> Path:
    """Clean 5-module baseline, then two fresh 2-cycles written on top of it.

    The two cycles give more regressions than any single `--top` value shows,
    so a `-1` that means "all but the last" cannot pass as "all", and a `0`
    that hides every regression cannot pass as "none found".
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name in ("a", "b", "c", "d", "e"):
        (pkg / f"{name}.py").write_text("")
    snap = _snapshot_findings_only(tmp_path)
    assert snap.exit_code == 0, snap.output
    (pkg / "a.py").write_text("import pkg.b\n")
    (pkg / "b.py").write_text("import pkg.a\n")
    (pkg / "c.py").write_text("import pkg.d\n")
    (pkg / "d.py").write_text("import pkg.c\n")
    return tmp_path


def _diff_json(project: Path, *extra: str):
    return CliRunner().invoke(
        main,
        [
            "diff",
            str(project),
            "--baseline",
            str(project / "base.json"),
            "--format",
            "json",
            *extra,
        ],
    )


def _make_app_with_test_module(tmp_path: Path) -> None:
    """Minimal app/core.py + tests/test_core.py importing it.

    Shared between the `archy affected` CLI tests that need a single
    test module mapped to a single source module.
    """
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_core.py").write_text("from app.core import x\n")


def _make_app_with_core_module(tmp_path: Path) -> Path:
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("")
    return pkg


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repo_with_deleted_module(tmp_path: Path, *, subdir: str = ".") -> Path:
    """A committed `pkg/a.py` imported by a test, then removed from the worktree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    project = repo / subdir
    project.mkdir(parents=True, exist_ok=True)
    pkg = project / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("x = 1\n")
    tests = project / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_a.py").write_text("from pkg.a import x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    (pkg / "a.py").unlink()
    assert not (pkg / "a.py").exists()
    return repo


_DELETED_NOTE = (
    "deleted or renamed .py file(s); "
    "archy scans the current tree, so its dependents are not selected:"
)


_FILTER_MISS = (
    "matches no file in the graph "
    "(paths are project-relative; only files inside a package, and test files, are scanned)"
)


def _write_transitive_violation_project(tmp_path: Path, *, store_pattern: str) -> Path:
    """top.store -> top.service -> top.api, forbidden store -> api.

    No direct store -> api edge, so only the transitive checker can see it.
    """
    pkg = tmp_path / "top"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "api.py").write_text("VALUE = 1\n")
    (pkg / "service.py").write_text("from top import api\n")
    (pkg / "store.py").write_text("from top import service\n")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        '  api:\n    modules: ["top.api.**"]\n'
        f'  store:\n    modules: ["{store_pattern}"]\n'
        "forbid:\n  - {from: store, to: api}\n"
    )
    return tmp_path


def _make_three_module_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg.b import thing\n")
    (pkg / "b.py").write_text("from pkg.c import other\n")
    (pkg / "c.py").write_text("")
    return tmp_path


def _dsm_json(project: Path, *args: str) -> dict:
    """`archy dsm --format json`, parsed. Every DSM knob reaches `build_dsm`
    through one helper shared with `render` (#422), so these assert the wiring,
    not the matrix maths, which `tests/test_dsm.py` covers directly."""
    result = CliRunner().invoke(main, ["dsm", str(project), "--format", "json", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _make_many_module_project(tmp_path: Path, n: int) -> Path:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(n):
        (pkg / f"mod{i}.py").write_text("x = 1\n")
    return tmp_path


def _make_conventions_project(tmp_path: Path) -> Path:
    """A project with one of each thing `archy conventions` reports."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "layers.py").write_text(
        "class Violation: pass\nclass ReachViolation: pass\nclass SdpViolation: pass\n"
    )
    (pkg / "cli.py").write_text(
        "import sys\n"
        "def _report_to_text(): pass\n"
        "def _report_to_json(): pass\n"
        "def run(strict):\n"
        "    if strict:\n"
        "        sys.exit(1)\n"
    )
    return tmp_path


def _make_bare_pattern_project(tmp_path: Path) -> Path:
    """The walkthrough shape: bare `modules:` qualnames over a nested tree."""
    app = tmp_path / "app"
    (app / "store").mkdir(parents=True)
    (app / "api").mkdir()
    (app / "__init__.py").write_text("")
    (app / "store" / "__init__.py").write_text("")
    (app / "api" / "__init__.py").write_text("")
    (app / "api" / "context.py").write_text("")
    (app / "store" / "repository.py").write_text("from app.api.context import ctx\n")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  store:\n    modules: ['app.store']\n"
        "  api:\n    modules: ['app.api']\n"
        "forbid:\n  - {from: store, to: api}\n"
    )
    return tmp_path


def _invoke_json(args: list[str]) -> dict:
    """Invoke the CLI and parse the JSON document out of its output.

    The slice is load-bearing, which is why this is a helper rather than five
    copies of an idiom: this CliRunner folds stderr into `output`, so a command
    that writes an advisory there first (`# --contracts unavailable: ...`)
    prefixes the JSON with a line `json.loads` cannot parse.
    """
    out = CliRunner().invoke(main, args).output
    return json.loads(out[out.index("{") :])


_SHIPPING_LAYERS_NO_FORBID = (
    "layers:\n  api:\n    modules: [shipping.api]\n  store:\n    modules: [shipping.store]\n"
)


def _make_uncovered_forbid_project(tmp_path: Path) -> Path:
    """A config whose layers govern a little, but not the edge that matters."""
    root = tmp_path / "proj"
    (root / "shipping" / "store").mkdir(parents=True)
    (root / "shipping" / "common").mkdir(parents=True)
    (root / "shipping" / "api").mkdir(parents=True)
    for pkg in ("", "store", "common", "api"):
        (root / "shipping" / pkg / "__init__.py").write_text("")
    (root / "shipping" / "store" / "repository.py").write_text(
        "from shipping.common.tenancy import current\n"
    )
    (root / "shipping" / "common" / "tenancy.py").write_text(
        "from shipping.api.context import ctx\n\ndef current(): return ctx\n"
    )
    (root / "shipping" / "api" / "context.py").write_text("ctx = 1\n")
    (root / "archy.yaml").write_text(
        "layers:\n"
        "  api:\n    modules: [shipping.api]\n"
        "  store:\n    modules: [shipping.store]\n"
        "forbid:\n  - {from: store, to: api}\n"
    )
    return root


def _stub_run_contracts_raising(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Make `run_contracts` raise `exc`, to exercise the no-verdict branch.

    The substitution has to land on the `archy.contracts` module object because
    `_run_check_contracts` resolves `run_contracts` through it at call time; a
    stub bound anywhere else would never be called.
    """
    import archy.contracts

    def _boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(archy.contracts, "run_contracts", _boom)


def _dup_group(category: str, *, exact: bool = False, similarity: float | None = None):
    members = (
        DuplicateMember(module="pkg.a", qualified_name="alpha", path="pkg/a.py", line=3),
        DuplicateMember(module="pkg.b", qualified_name="beta", path="pkg/b.py", line=7),
    )
    return DuplicateGroup(
        shape_hash="h",
        size=12,
        member_count=2,
        redundancy=12,
        members=members,
        category=category,
        exact=exact,
        similarity=similarity,
        variant_reason="test" if category == "variant" else None,
    )


# Signs, padding and precision chosen so every axis is distinguishable: a
# renderer that drops, reorders or reformats one fails on that line.
_DELTA = ScoreDelta(
    overall=0.012, modularity=-0.5, acyclicity=0.0, depth=0.25, equality=-0.001, complexity=1.0
)


_DELTA_LINES = [
    "  overall     +0.012",
    "  modularity  -0.500",
    "  acyclicity  +0.000",
    "  depth       +0.250",
    "  equality    -0.001",
    "  complexity  +1.000",
]


def _make_ratchet_project(tmp_path: Path) -> Path:
    """`pkg.a -> pkg.b -> pkg.c -> pkg.a` (one 3-cycle), plus acyclic d and e,
    with a committed findings-only baseline taken at that state."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name, body in [("a", "import pkg.b\n"), ("b", "import pkg.c\n"), ("c", "import pkg.a\n")]:
        (pkg / f"{name}.py").write_text(body)
    (pkg / "d.py").write_text("")
    (pkg / "e.py").write_text("")
    result = _snapshot_findings_only(tmp_path)
    assert result.exit_code == 0, result.output
    return tmp_path


def _snapshot_findings_only(project: Path):
    return CliRunner().invoke(
        main,
        ["snapshot", str(project), "--findings-only", "--out", str(project / "base.json")],
    )


def _strict(project: Path, *extra: str):
    return CliRunner().invoke(
        main, ["diff", str(project), "--strict", "--baseline", str(project / "base.json"), *extra]
    )


def _make_sdp_ratchet_project(tmp_path: Path) -> Path:
    """The ratchet project with SDP enabled and an edge that violates it,
    asserted present in a full snapshot so the fixture cannot drift into a
    tree with nothing for the SDP handling to act on."""
    project = _make_ratchet_project(tmp_path)
    (project / "archy.yaml").write_text("layers: {}\nforbid: []\nsdp: {enabled: true}\n")
    (project / "pkg" / "d.py").write_text("import pkg.e\n")
    (project / "pkg" / "e.py").write_text("import pkg.a\nimport pkg.b\n")
    full_path = project / "full.json"
    assert (
        CliRunner().invoke(main, ["snapshot", str(project), "--out", str(full_path)]).exit_code == 0
    )
    assert json.loads(full_path.read_text())["sdp_violations"], "fixture must carry SDP"
    return project


def _fresh_git_repo_without_commits(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "commit.gpgsign", "false"],
        check=True,
        capture_output=True,
    )
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "app.py").write_text("def f(x):\n    if x: return 1\n    return 0\n")


def _make_overlapping_layers_project(tmp_path: Path, *, with_edge: bool) -> Path:
    """`pkg.core.x` matches both `core` and `special`; `with_edge` puts it on an edge.

    Two shapes are needed because they reach different call sites: with an edge
    the overlap surfaces inside `find_violations`, without one only through
    `compute_coverage`.
    """
    pkg = tmp_path / "pkg"
    core = pkg / "core"
    core.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (core / "__init__.py").write_text("")
    (core / "x.py").write_text("import os\n" if with_edge else "X = 1\n")
    (pkg / "a.py").write_text("from pkg.core import x\n" if with_edge else "X = 1\n")
    (tmp_path / "archy.yaml").write_text(
        'layers:\n  core:\n    modules: ["pkg.core.**"]\n  special:\n    modules: ["pkg.core.x"]\n'
    )
    return tmp_path


def _assert_one_line_overlap_error(result: Result) -> None:
    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert "matches multiple layers" in result.output
    assert "pkg.core.x" in result.output
    assert "core, special" in result.output


_GUARDED_B = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import a\n"


def _make_guarded_project(tmp_path: Path, b_source: str = _GUARDED_B) -> Path:
    """`pkg.a` imports `pkg.b`; `pkg.b` imports `pkg.a` however `b_source` says."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg import b\n")
    (pkg / "b.py").write_text(b_source)
    return tmp_path
