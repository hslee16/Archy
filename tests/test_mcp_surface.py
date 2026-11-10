"""The MCP server surface: registry, output schemas, error model, warm index, cold fallback."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from archy.mcp import (
    create_server,
)
from tests.mcp_helpers import (
    _PROJECT_PATH_ARGS,
    _cold_fallback_callers,
    _list_tools,
    _output_schema,
    _return_models,
    _schema_properties_named,
)
from tests.mcp_helpers import call_tool as _call_tool


def test_all_tools_declare_human_friendly_title():
    # The 2025-06-18 revision added a display `title` so `name` stays a
    # programmatic id. Assert every tool sets a non-empty title distinct from
    # its name.
    server = create_server()
    tools = _list_tools(server)
    for tool in tools:
        assert tool.title, f"{tool.name} has no display title"
        assert tool.title != tool.name, f"{tool.name} title duplicates the name"


def test_all_tools_declare_output_schema():
    # 2025-06-18 structured output: the SDK derives an `outputSchema` (JSON
    # Schema) from each tool's return annotation. Assert every tool declares
    # one and it is an object schema, since `structuredContent` must be a JSON
    # object (sequence/union returns are wrapped under a `result` key to honor
    # this -- see the `archy.mcp` module docstring).
    server = create_server()
    tools = _list_tools(server)
    for tool in tools:
        schema = _output_schema(tool)
        assert schema is not None, f"{tool.name} has no outputSchema"
        assert schema.get("type") == "object", f"{tool.name} outputSchema is not an object"


def test_output_schema_advertises_every_computed_field():
    server = create_server()
    schemas = {tool.name: _output_schema(tool) for tool in asyncio.run(server.list_tools())}
    seen: set[tuple[str, str]] = set()
    missing: list[str] = []
    for tool in server._tool_manager.list_tools():
        schema = schemas[tool.name]
        assert schema is not None, tool.name
        for model in _return_models(tool.fn):
            props = _schema_properties_named(schema, model.__name__)
            for field_name in model.model_computed_fields:
                seen.add((model.__name__, field_name))
                if field_name not in props:
                    missing.append(f"{tool.name} {model.__name__}.{field_name}")

    assert ("Impact", "empty_caveat") in seen
    assert ("Affected", "empty_caveat") in seen
    assert ("Affected", "deleted_reason") in seen
    assert ("StrictVerdict", "passed") in seen
    assert ("StrictVerdict", "reason") in seen
    assert ("ConventionsReport", "gate_modules") in seen
    assert ("LayerCoverage", "layers_present") in seen
    assert missing == []


@pytest.mark.parametrize("name", sorted(_PROJECT_PATH_ARGS))
def test_mcp_tool_rejects_a_nonexistent_project_path(tmp_path: Path, name: str):
    """A typo path fails the call and is not created on disk (#552).

    The fixture is absent before the call. The CLI already rejects that with
    click.Path(exists=True, file_okay=False). These tools used to pass the
    string through, and open_index's mkdir(parents=True) created the path and
    `.archy/index.db`, then scored the empty tree as 1.0.
    """
    missing = tmp_path / "no-such-project"
    assert not missing.exists()

    from archy.mcp_compat import ToolError

    # mcp 2.x wraps the ValueError as ToolError("Error executing tool <name>")
    # without the underlying message; 1.x keeps it. Assert the raise + no mkdir.
    with pytest.raises(ToolError):
        _call_tool(
            create_server(),
            name,
            {"path": str(missing), **_PROJECT_PATH_ARGS[name]},
        )

    assert not missing.exists()
    assert not (missing / ".archy" / "index.db").exists()


def test_project_path_guard_covers_every_registered_tool():
    server = create_server()
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert set(_PROJECT_PATH_ARGS) == names


def test_create_server_registers_loop_prompt():
    server = create_server()
    prompts = asyncio.run(server.list_prompts())
    assert any(p.name == "loop" for p in prompts)


def test_manager_cache_key_distinguishes_config(tmp_path: Path):
    # Regression for the stale-manager blocker: the key must fold in the
    # graph-building kwargs, not just the root, or a config change is ignored.
    from archy.mcp_runs import _manager_cache_key

    base = _manager_cache_key(tmp_path, {})
    same = _manager_cache_key(tmp_path, {"extra_roots": ()})
    diff_roots = _manager_cache_key(tmp_path, {"extra_roots": ("src",)})
    diff_ignored = _manager_cache_key(tmp_path, {"ignored_dirs": frozenset({"build"})})

    # Empty kwargs and explicit defaults collapse to the same key (one manager).
    assert base == same
    # A different config yields a different key (a fresh manager).
    assert diff_roots != base
    assert diff_ignored != base
    assert diff_roots != diff_ignored


def test_manager_for_reuses_or_evicts_by_config(tmp_path: Path):
    from archy.mcp_runs import _MANAGERS, _manager_for

    created = []

    def make(**kwargs):
        # Track every manager the moment it is created so a mid-test failure
        # still tears down its watcher + connection in the finally block.
        manager = _manager_for(tmp_path, **kwargs)
        created.append(manager)
        return manager

    try:
        m1 = make(extra_roots=("src",))
        m1_again = make(extra_roots=("src",))
        # Same config -> same cached manager (no new watcher/connection).
        assert m1 is m1_again

        m2 = make(extra_roots=("lib",))
        # Different config -> distinct manager, and the superseded one is evicted
        # so a root never accumulates managers (one live config at a time).
        # Scope the count to this root: other tests leak managers into the
        # module-global for other roots.
        assert m2 is not m1
        root_key = str(tmp_path.resolve())
        same_root = [k for k in _MANAGERS if k[0] == root_key]
        assert len(same_root) == 1
        assert _MANAGERS[same_root[0]] is m2
    finally:
        for manager in set(created):
            manager.stop()
        _MANAGERS.clear()


def test_mcp_load_graph_trips_scan_size_guard(tmp_path: Path):
    from archy.graph import ScanTooLargeError
    from archy.mcp_runs import _load_graph

    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(5):
        (pkg / f"mod{i}.py").write_text("x = 1\n")
    (tmp_path / "archy.yaml").write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    # The guard fires inside `_manager_for` before the watcher is scheduled.
    with pytest.raises(ScanTooLargeError):
        _load_graph(tmp_path, internal_only=True)


@pytest.mark.parametrize("caller", ["load_graph", "check", "duplicates"])
def test_cold_fallback_keeps_the_scan_size_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: str
):
    """A cache that fails must not turn the scan-size guard off.

    The guard in `_manager_for` runs only when a manager is first created. If the
    cache then fails (`sqlite3.Error` / `OSError`) on a project that has grown
    past `max_modules`, the cold fallback is the only thing between the server and
    an unbounded scan of a vendored tree, the #216 failure mode. Dropping
    `max_modules` from the cold call passed the whole suite before this test.
    """
    import archy.mcp_runs_core as mcp_module
    from archy.graph import ScanTooLargeError

    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for i in range(5):
        (pkg / f"mod{i}.py").write_text("x = 1\n")
    config = tmp_path / "archy.yaml"
    call = _cold_fallback_callers()[caller]

    manager_attempts: list[Path] = []

    def broken(path: Path, **kwargs):
        manager_attempts.append(path)
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(mcp_module, "_manager_for", broken)

    # Control: the same broken cache and the same project, with room under the
    # limit, answers. This shows the cold path is the one running, and that the
    # raise below comes from the guard rather than from the broken cache.
    config.write_text("layers: {}\nforbid: []\nmax_modules: 1000\n")
    call(tmp_path)
    assert manager_attempts, "the cache was never tried, so the cold path was not exercised"

    manager_attempts.clear()
    config.write_text("layers: {}\nforbid: []\nmax_modules: 2\n")
    with pytest.raises(ScanTooLargeError) as caught:
        call(tmp_path)
    assert manager_attempts, "the cache was never tried, so the cold path was not exercised"
    assert caught.value.limit == 2
    assert caught.value.count > 2


# The tool set is pinned exactly: adding or removing a tool without a deliberate decision
# fails here. Folded tools live behind mode/view/param switches on the survivors (see the
# `archy.mcp` module docstring).
_EXPECTED_TOOLS = frozenset(
    {
        "archy_score",
        "archy_cycles",
        "archy_check",
        "archy_impact",
        "archy_snapshot",
        "archy_diff",
        "archy_graph",
        "archy_what_to_refactor_next",
        "archy_dsm",
        "archy_simulate",
        "archy_duplicates",
        # `archy_conventions` reports a project's derived house style (naming
        # families, mirrored surfaces, gate inventory, model census) so an
        # agent stops re-deriving conventions by reading source.
        "archy_conventions",
        # `archy_module_view` is the same census as a LOOKUP on one module,
        # complete and unranked. Kept a separate tool rather than a
        # parameter on `archy_conventions` because a union return type makes
        # FastMCP wrap every payload under a `result` key, which would change
        # the released tool's wire format for consumers that never asked for
        # this feature.
        "archy_module_view",
        # `archy_brief` composes conventions + coverage + contracts into one
        # call. It was CLI-only until a usage signal appeared (#427).
        "archy_brief",
    }
)


def test_create_server_registers_expected_tools():
    server = create_server()
    tools = _list_tools(server)
    names = {t.name for t in tools}
    assert names == _EXPECTED_TOOLS
    assert len(names) == 14


@pytest.mark.parametrize(
    "tool_name",
    sorted(_EXPECTED_TOOLS),
)
def test_all_tools_forbid_additional_properties(tool_name: str):
    """Every advertised inputSchema must reject undeclared properties (#662)."""
    server = create_server()
    tool = next(t for t in _list_tools(server) if t.name == tool_name)
    schema = tool.model_dump(by_alias=True)["inputSchema"]
    assert schema.get("additionalProperties") is False
