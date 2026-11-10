"""Shared helper for tests that call an MCP tool in-process.

Lives in its own module, not in a `tests/test_mcp_*.py` module, so that a test file never
imports another test file: importing a test module from a test module couples
them and puts the importer one step deeper in archy's own import graph.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Annotated, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from archy.diff import take_snapshot, write_snapshot
from archy.graph import build_graph, internal_subgraph
from archy.mcp_runs import (
    run_check,
    run_dsm,
    run_snapshot,
)


def call_tool(server, name: str, args: dict) -> tuple[list, dict | None]:
    """Call a tool in-process and return `(content, structured)` on either SDK major.

    mcp 1.x returns that pair directly; 2.0 returns a `CallToolResult` instead.
    Normalized here rather than at each call site, and `structuredContent` is read
    from the wire form because 2.0 also renamed the attribute (see
    `archy.mcp_compat`).

    These in-process calls poke the SDK's own surface, which is why they are the
    only part of the suite a major bump touches. The protocol-level contract is
    covered by `tests/test_mcp_protocol.py`, which speaks stdio and passes
    unchanged on both majors.
    """
    result = asyncio.run(server.call_tool(name, args))
    if isinstance(result, tuple):  # mcp 1.x
        return result
    return result.content, result.model_dump(by_alias=True).get("structuredContent")


def _internal_graph(root: Path):
    # Build the same internal-only graph the MCP layer hands to callers,
    # for parity assertions against graph_to_dict.
    g = build_graph(root)
    external = {n for n, d in g.nodes(data=True) if d.get("external")}
    g.remove_nodes_from(external)
    return g


def _list_tools(server):
    return asyncio.run(server.list_tools())


def _output_schema(tool) -> dict | None:
    """Read `outputSchema` from the wire form, not the Python attribute.

    mcp 2.0 renamed every model attribute to snake_case (`outputSchema` ->
    `output_schema`) while keeping the serialization aliases identical, so
    attribute access breaks across the major and the protocol does not. Dumping
    by alias is both version-agnostic and closer to what a client actually
    parses. See `archy.mcp_compat`.
    """
    return tool.model_dump(by_alias=True).get("outputSchema")


def _return_models(fn) -> set[type[BaseModel]]:
    found: set[type[BaseModel]] = set()

    def walk(tp: object) -> None:
        if tp is None or tp is type(None):
            return
        origin = get_origin(tp)
        if origin is Annotated:
            walk(get_args(tp)[0])
            return
        if origin is not None:
            for arg in get_args(tp):
                walk(arg)
            return
        if isinstance(tp, type) and issubclass(tp, BaseModel):
            if tp in found:
                return
            found.add(tp)
            for field in tp.model_fields.values():
                walk(field.annotation)

    walk(get_type_hints(fn).get("return"))
    return found


def _as_object_dict(node: object) -> dict[str, object] | None:
    if isinstance(node, dict):
        return cast("dict[str, object]", node)
    return None


def _schema_properties_named(schema: dict[str, object], model_name: str) -> set[str]:
    """Property names of every schema object titled or `$defs`-keyed as `model_name`."""
    found: set[str] = set()

    def walk(node: object) -> None:
        mapping = _as_object_dict(node)
        if mapping is None:
            if isinstance(node, list):
                for value in node:
                    walk(value)
            return
        defs = _as_object_dict(mapping.get("$defs"))
        if defs is not None:
            sub = _as_object_dict(defs.get(model_name))
            props = _as_object_dict(None if sub is None else sub.get("properties"))
            if props is not None:
                found.update(props)
        titled = _as_object_dict(mapping.get("properties"))
        if mapping.get("title") == model_name and titled is not None:
            found.update(titled)
        for value in mapping.values():
            walk(value)

    walk(schema)
    return found


# Extra arguments each tool needs besides `path`. The set must match the
# registered tools: a new tool that forgets `_project_dir` has to land here
# or `test_project_path_guard_covers_every_registered_tool` fails, and then
# the parametrized test in `tests/test_mcp_surface.py` fails if the guard is not
# actually used.
_PROJECT_PATH_ARGS: dict[str, dict] = {
    "archy_score": {},
    "archy_cycles": {},
    "archy_check": {},
    "archy_impact": {"files": ["pkg/a.py"]},
    "archy_snapshot": {},
    "archy_diff": {},
    "archy_graph": {},
    "archy_what_to_refactor_next": {},
    "archy_dsm": {},
    "archy_simulate": {},
    "archy_duplicates": {},
    "archy_conventions": {},
    "archy_module_view": {"module": "pkg.a"},
    "archy_brief": {},
}


def _dsm(
    path: Path,
    response_format: str = "summary",
    *,
    group_by: str = "community",
    weight: str = "imports",
    focus: str | None = None,
    focus_depth: int = 1,
    package: str | None = None,
    baseline_path: str | None = None,
):
    return run_dsm(
        path,
        response_format=response_format,
        group_by=group_by,
        weight=weight,
        focus=focus,
        focus_depth=focus_depth,
        package=package,
        baseline_path=baseline_path,
    )


def _hub_project(tmp_path: Path, spokes: int = 3) -> Path:
    # pkg.hub imports pkg.dep (non-zero instability) and is imported by `spokes`
    # peers (high fan-in), so it dominates the edit-risk composite. Shared by the
    # structural-lens and structural-only tests.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "dep.py").write_text("")
    (pkg / "hub.py").write_text("from pkg.dep import thing\n")
    for i in range(spokes):
        (pkg / f"s{i}.py").write_text("from pkg.hub import x\n")
    return tmp_path


def _fan_out_project(tmp_path: Path, fan: int) -> Path:
    # Exactly `fan` non-zero DSM cells and no cycles, so the oversized-matrix
    # guard tests have a predictable cell count to trip against.
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    leaves = "".join(f"from pkg.m{i} import x\n" for i in range(fan))
    (pkg / "a.py").write_text(leaves)
    for i in range(fan):
        (pkg / f"m{i}.py").write_text("")
    return tmp_path


def _myapp_core_cli(tmp_path: Path, *, api: str = "", runner: str = "") -> Path:
    """A `myapp` package with `core.api` and `cli.runner`, returning the root.

    The three call sites differ only in which leaf imports the other, so that
    is the parameter; everything else was copied three times (#411).
    """
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for sub in ("core", "cli"):
        (pkg / sub).mkdir()
        (pkg / sub / "__init__.py").write_text("")
    (pkg / "core" / "api.py").write_text(api)
    (pkg / "cli" / "runner.py").write_text(runner)
    return tmp_path


def _write_core_cli_forbid_config(root: Path) -> None:
    # The canonical two-layer archy.yaml shared by the check / snapshot / fold
    # tests: a `core` layer forbidden from reaching `cli`. Kept as one helper so
    # the layer/forbid shape lives in a single place.
    (root / "archy.yaml").write_text(
        "layers:\n"
        "  core: {modules: [myapp.core.**]}\n"
        "  cli: {modules: [myapp.cli.**]}\n"
        "forbid:\n"
        "  - {from: core, to: cli}\n"
    )


def _seed_score_history(project: Path, overall: float) -> None:
    """Write one history row so the strict gate has a previous overall to compare."""
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
            "complexity": 1.0,
        },
        "inputs": {
            "module_count": 3,
            "edge_count": 1,
            "cycle_count": 0,
            "tangle_ratio": 0.0,
            "max_depth": 1,
            "community_count": 2,
        },
    }
    history.write_text(json.dumps(row) + "\n")


def _write_registry_project(tmp_path: Path, *, bootstrap: str) -> Path:
    """The reported incident, reduced. See tests/cli_helpers.py::_make_registry_project."""
    app = tmp_path / "app"
    (app / "core").mkdir(parents=True)
    (app / "commands").mkdir(parents=True)
    (app / "__init__.py").write_text("")
    (app / "core" / "__init__.py").write_text("")
    (app / "core" / "model_registry.py").write_text("REGISTRY = {}\n")
    (app / "commands" / "__init__.py").write_text(bootstrap)
    (app / "commands" / "setup_user.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text(
        "layers: {}\nforbid: []\n"
        "required:\n"
        "  - source: 'app.commands.*'\n"
        "    must_reach: app.core.model_registry\n"
        "    reason: standalone entrypoints need the full mapper registry\n"
    )
    return tmp_path


def _make_sdp_violating_project(tmp_path: Path) -> Path:
    # a is depended on by x1/x2/x3 (Ca=3) and depends on b (Ce=1) -> I(a)=0.25.
    # b is depended on by a (Ca=1) and depends on y1/y2/y3 (Ce=3) -> I(b)=0.75.
    # The a -> b edge is stable importing less-stable: an SDP violation.
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from myapp.b import thing\n")
    (pkg / "b.py").write_text("from myapp import y1, y2, y3\n")
    (pkg / "y1.py").write_text("")
    (pkg / "y2.py").write_text("")
    (pkg / "y3.py").write_text("")
    (pkg / "x1.py").write_text("from myapp.a import thing\n")
    (pkg / "x2.py").write_text("from myapp.a import thing\n")
    (pkg / "x3.py").write_text("from myapp.a import thing\n")
    return tmp_path


_FILTER_MISS = (
    "matches no file in the graph "
    "(paths are project-relative; only files inside a package, and test files, are scanned)"
)


def _project_with_a_test_of_a(root: Path) -> dict:
    """`tests/test_a.py` imports `pkg/a.py`; returns the `archy_impact` args for it."""
    for package in ("pkg", "tests"):
        (root / package).mkdir()
        (root / package / "__init__.py").write_text("")
    (root / "pkg" / "a.py").write_text("")
    (root / "pkg" / "lone.py").write_text("")
    (root / "tests" / "test_a.py").write_text("from pkg.a import thing\n")
    return {"path": str(root), "files": ["pkg/a.py"], "mode": "affected"}


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    """Run one git command in `repo`.

    `env` exists for the tests that need to backdate a commit; passing None
    inherits, which is what every other caller wants.
    """
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _init_git_repo(repo: Path) -> None:
    # Bare git init + the three configs every hotspot test needs (identity
    # so commits don't fail, and gpgsign=false so the CI runner without a
    # signing key still completes). Shared by every test module that
    # touches git history.
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")


def _init_hotspot_repo(repo: Path) -> None:
    _init_git_repo(repo)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
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
    (pkg / "hot.py").write_text((pkg / "hot.py").read_text() + "\n# tweak\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "touch hot")


def _cold_fallback_callers():
    """The three call sites that hand `max_modules` to the cache-backed scan (#502).

    Each one passes the limit itself, so a mutation at any of them (or at
    `_warm_or_cold`'s cold call) must turn the test red, not only the one in
    `_warm_or_cold`.
    """
    from archy.mcp_runs import _load_graph, run_duplicates

    return {
        "load_graph": lambda p: _load_graph(p, internal_only=True),
        "check": lambda p: run_check(p, config_path=None),
        "duplicates": lambda p: run_duplicates(
            p, response_format="summary", min_nodes=5, top_n=5, members=2
        ),
    }


def _transitive_project(tmp_path: Path) -> Path:
    """`store -> common -> api`: legal on every direct edge, illegal transitively."""
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "api.py").write_text("x = 1\n")
    (tmp_path / "app" / "common.py").write_text("from app import api\n")
    (tmp_path / "app" / "store.py").write_text("from app import common\n")
    (tmp_path / "archy.yaml").write_text(
        "layers:\n"
        "  api:\n    modules: ['app.api']\n"
        "  common:\n    modules: ['app.common']\n"
        "  store:\n    modules: ['app.store']\n"
        "forbid:\n  - {from: store, to: api}\n"
    )
    return tmp_path


def _write_cyclic_ratchet_project(tmp_path: Path) -> Path:
    """`pkg.a <-> pkg.b` plus acyclic `pkg.d`, `pkg.e`, with a committed
    findings-only baseline written at that state."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import pkg.b\n")
    (pkg / "b.py").write_text("import pkg.a\n")
    (pkg / "d.py").write_text("")
    (pkg / "e.py").write_text("")
    snap = take_snapshot(internal_subgraph(build_graph(tmp_path)))
    write_snapshot(snap, tmp_path / "archy-baseline.json", findings_only=True)
    return tmp_path


def _add_cycle_and_resnapshot(project: Path) -> None:
    """Add the `pkg.d <-> pkg.e` cycle, then re-snapshot as an agent told to
    snapshot at the start of each task would, which overwrites
    .archy/baseline.json with the new cycle already in it."""
    (project / "pkg" / "d.py").write_text("import pkg.e\n")
    (project / "pkg" / "e.py").write_text("import pkg.d\n")
    run_snapshot(project)


def _write_brief_project(tmp_path: Path, *, with_config: bool) -> Path:
    """Two homes with two-member suffix families each, one unlayered module.

    Two naming homes so `top_n=1` has something to drop; the unlayered module
    and the forbid rule so a direct-edge pass cannot prove the rule holds.
    """
    pkg = tmp_path / "pkg"
    (pkg / "core").mkdir(parents=True)
    (pkg / "ui").mkdir()
    for d in (pkg, pkg / "core", pkg / "ui"):
        (d / "__init__.py").write_text("")
    (pkg / "core" / "errors.py").write_text("class FooError: ...\nclass BarError: ...\n")
    (pkg / "ui" / "handlers.py").write_text("class AHandler: ...\nclass BHandler: ...\n")
    (pkg / "other.py").write_text("x = 1\n")
    if with_config:
        (tmp_path / "archy.yaml").write_text(
            "layers:\n"
            "  core: {modules: [pkg.core.**]}\n"
            "  ui: {modules: [pkg.ui.**]}\n"
            "forbid:\n"
            "  - {from: core, to: ui}\n"
        )
    return tmp_path


def _guarded_cycle_project(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from pkg import b\n")
    (pkg / "b.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import a\n"
    )
    return root
